"""Accounts voor de wedstrijdleiding: registreren, bevestigen, inloggen en uitloggen.

Een wedstrijdleider meldt zich aan met niet meer dan een e-mailadres en een wachtwoord, en
beheert daarna alleen de wedstrijden die hij zelf aanmaakt. Van het wachtwoord bewaart de
database een scrypt-hash met een eigen salt per account; de platte waarde staat nergens en is
er ook niet uit terug te rekenen.

Bevestigen gaat net als de spelerslinks: het token gaat in de mail, alleen de sha256-hash
gaat de database in, en de link werkt precies één keer. Zolang een adres niet bevestigd is
kan er niet mee worden ingelogd, dus een verkeerd ingetikt adres levert nooit een werkend
account op.

Aan het wachtwoord worden geen eisen gesteld: wie één letter wil, mag dat. Wel staat er een
grens op de lengte van beide velden, zodat een verzoek van een megabyte niet in de database
of in de scrypt-berekening terechtkomt. De hash zelf houdt altijd dezelfde lengte, hoe lang
het wachtwoord ook is.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import AppError, DbSession, hash_token, login_user, logout, new_token
from app.config import settings
from app.mail import account_bericht, verstuur
from app.models import User, now
from app.scoring import log, user_actor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["account"])
templates = Jinja2Templates(directory="app/templates")

# Grenzen tegen een verzoek dat de database of de scrypt-berekening laat ontsporen, niet
# tegen een slap wachtwoord. `MAX_EMAIL` is precies de kolombreedte van `User.email`.
MAX_EMAIL = 200
MAX_WACHTWOORD = 1024
# n=2**14 met r=8 kost ongeveer 16 MB en een tiende seconde per poging. Genoeg om raden
# onbetaalbaar te maken, weinig genoeg om een inlogscherm niet te laten hangen.
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    """Hash een wachtwoord met een verse salt. Levert `scrypt$<salt>$<hash>` op."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Vergelijk in constante tijd. Een onleesbare hash is 'nee', geen exception."""
    schema, _, rest = stored.partition("$")
    salt_hex, _, digest_hex = rest.partition("$")
    if schema != "scrypt" or not salt_hex or not digest_hex:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return secrets.compare_digest(digest.hex(), digest_hex)


def find_user(db: DbSession, email: str) -> User | None:
    """Zoek een account op adres. Adressen zijn hoofdletterongevoelig opgeslagen."""
    return db.scalar(select(User).where(User.email == email))


def _adres(email: str) -> str:
    """Normaliseer een ingetikt adres, zodat een hoofdletter geen tweede account oplevert."""
    return email.strip().lower()


def _scherm(request: Request, sjabloon: str, status: int = 200, **inhoud) -> HTMLResponse:
    return templates.TemplateResponse(request, sjabloon, inhoud, status_code=status)


def _login_scherm(request: Request, fout: str | None = None, status: int = 200) -> HTMLResponse:
    return _scherm(request, "admin_login.html", status, error=fout)


def _aanmeld_scherm(
    request: Request,
    fout: str | None = None,
    email: str = "",
    verstuurd: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    return _scherm(
        request,
        "admin_registreren.html",
        status,
        error=fout,
        email=email,
        verstuurd=verstuurd,
        max_email=MAX_EMAIL,
        max_wachtwoord=MAX_WACHTWOORD,
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    """Inlogformulier voor de wedstrijdleiding."""
    return _login_scherm(request)


@router.post("/login")
def do_login(
    request: Request, db: DbSession, email: str = Form(""), wachtwoord: str = Form("")
) -> Response:
    """Log in met een account."""
    adres = _adres(email)
    gebruiker = find_user(db, adres)
    if gebruiker is None or not verify_password(wachtwoord, gebruiker.password_hash):
        return _login_scherm(request, "Onbekend e-mailadres of onjuist wachtwoord.", 401)
    if gebruiker.confirmed_at is None:
        return _login_scherm(
            request,
            "Bevestig eerst je e-mailadres. De link staat in de mail die je bij het "
            "aanmaken hebt gekregen.",
            401,
        )
    response = RedirectResponse("/admin", status_code=303)
    login_user(response, gebruiker)
    return response


@router.post("/logout")
def do_logout() -> Response:
    """Uitloggen."""
    response = RedirectResponse("/admin/login", status_code=303)
    logout(response)
    return response


@router.get("/registreren", response_class=HTMLResponse)
def register_form(request: Request) -> HTMLResponse:
    """Aanmeldformulier: alleen een e-mailadres en een wachtwoord."""
    return _aanmeld_scherm(request)


def _bezwaar(adres: str, wachtwoord: str) -> str | None:
    """Wat er mis is met de opgegeven gegevens, of None als ze deugen.

    Alleen wat de app niet kan verwerken. Hoe sterk het wachtwoord is, is aan de eigenaar.
    """
    if "@" not in adres or "." not in adres.rpartition("@")[2]:
        return "Vul een geldig e-mailadres in. De bevestiging gaat daarheen."
    if len(adres) > MAX_EMAIL:
        return f"Dit e-mailadres is te lang. Er passen {MAX_EMAIL} tekens in."
    if not wachtwoord:
        return "Vul een wachtwoord in."
    if len(wachtwoord) > MAX_WACHTWOORD:
        return f"Dit wachtwoord is te lang. Er passen {MAX_WACHTWOORD} tekens in."
    return None


@router.post("/registreren", response_class=HTMLResponse)
def do_register(
    request: Request,
    db: DbSession,
    achtergrond: BackgroundTasks,
    email: str = Form(""),
    wachtwoord: str = Form(""),
) -> HTMLResponse:
    """Maak een account aan en mail de bevestigingslink.

    Bestaat het adres al maar is het nooit bevestigd, dan is dit een nieuwe poging: het
    account krijgt het opgegeven wachtwoord en een verse link. Een bevestigd adres blijft
    ongemoeid, want daar kan iemand anders achter zitten.
    """
    adres = _adres(email)
    bezwaar = _bezwaar(adres, wachtwoord)
    if bezwaar:
        return _aanmeld_scherm(request, bezwaar, adres, status=422)

    gebruiker = find_user(db, adres)
    if gebruiker is not None and gebruiker.confirmed_at is not None:
        return _aanmeld_scherm(
            request,
            "Dit e-mailadres heeft al een account. Log in met je wachtwoord.",
            adres,
            status=422,
        )

    token, token_hash = new_token()
    if gebruiker is None:
        gebruiker = User(email=adres)
        db.add(gebruiker)
    gebruiker.password_hash = hash_password(wachtwoord)
    gebruiker.confirm_token_hash = token_hash
    db.flush()
    log(db, user_actor(gebruiker.id), "registered", email=adres)
    db.commit()

    _stuur_bevestiging(achtergrond, adres, token)
    return _aanmeld_scherm(request, email=adres, verstuurd=adres)


def _stuur_bevestiging(achtergrond: BackgroundTasks, adres: str, token: str) -> None:
    """Mail de bevestigingslink, of zet hem in de log als er geen mailprovider is.

    Zonder die tweede weg is een lokale installatie zonder Brevo-sleutel niet te gebruiken:
    het account bestaat dan wel, maar niemand kan er ooit bij.
    """
    link = f"{settings.base_url.rstrip('/')}/admin/bevestigen/{token}"
    bericht = account_bericht(adres, link)
    if bericht is None:
        logger.warning("Mail staat uit. Bevestigingslink voor %s: %s", adres, link)
        return
    achtergrond.add_task(verstuur, bericht)


@router.get("/bevestigen/{token}")
def confirm(token: str, db: DbSession) -> Response:
    """Bevestig een adres en log de wedstrijdleider meteen in."""
    gebruiker = db.scalar(select(User).where(User.confirm_token_hash == hash_token(token)))
    if gebruiker is None:
        raise AppError(
            "Deze bevestigingslink is niet (meer) geldig. Meld je opnieuw aan, dan krijg je "
            "een verse link.",
            404,
        )
    gebruiker.confirmed_at = now()
    gebruiker.confirm_token_hash = None
    log(db, user_actor(gebruiker.id), "confirmed", email=gebruiker.email)
    db.commit()

    response = RedirectResponse("/admin", status_code=303)
    login_user(response, gebruiker)
    return response

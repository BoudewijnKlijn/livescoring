"""Tokens, cookies en autorisatie.

Spelers loggen in via een persoonlijke link `/t/{token}`. Alleen de sha256-hash van het
token staat in de database. De cookie bevat het entry-id plus een prefix van de tokenhash,
zodat het roteren van een link ook lopende sessies ongeldig maakt.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Entry, get_db

PLAYER_COOKIE = "speler"
ADMIN_COOKIE = "admin"
PLAYER_MAX_AGE = 7 * 24 * 3600
ADMIN_MAX_AGE = 12 * 3600

_serializer = URLSafeTimedSerializer(settings.secret_key)

DbSession = Annotated[Session, Depends(get_db)]


class AppError(Exception):
    """Een fout die als nette pagina aan de gebruiker getoond wordt."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class Unauthorized(AppError):
    """Geen geldige cookie of geen recht op deze actie."""

    def __init__(self, message: str = "Je hebt geen toegang tot deze pagina.") -> None:
        super().__init__(message, status_code=401)


def new_token() -> tuple[str, str]:
    """Genereer een nieuw token en de bijbehorende hash. Alleen de hash wordt opgeslagen."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    """Sha256-hash van een token, hex."""
    return hashlib.sha256(token.encode()).hexdigest()


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def login_player(response: Response, entry: Entry) -> None:
    """Zet de spelerscookie voor deze entry."""
    payload = {"e": entry.id, "h": entry.token_hash[:16]}
    _set_cookie(response, PLAYER_COOKIE, _serializer.dumps(payload), PLAYER_MAX_AGE)


def login_admin(response: Response) -> None:
    """Zet de admincookie."""
    _set_cookie(response, ADMIN_COOKIE, _serializer.dumps({"admin": True}), ADMIN_MAX_AGE)


def logout(response: Response) -> None:
    """Verwijder beide cookies."""
    response.delete_cookie(PLAYER_COOKIE, path="/")
    response.delete_cookie(ADMIN_COOKIE, path="/")


def _load(cookie: str | None, max_age: int) -> dict | None:
    if not cookie:
        return None
    try:
        return _serializer.loads(cookie, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def current_entry(request: Request, db: DbSession) -> Entry:
    """De ingelogde speler-in-ronde, of 401.

    Controleert ook of de tokenhash nog klopt: na een rotatie is de oude sessie dood.
    """
    payload = _load(request.cookies.get(PLAYER_COOKIE), PLAYER_MAX_AGE)
    if not payload:
        raise Unauthorized("Open je persoonlijke link opnieuw om verder te gaan.")
    entry = db.get(Entry, payload.get("e"))
    if entry is None or entry.token_hash[:16] != payload.get("h"):
        raise Unauthorized("Deze link is vervangen. Vraag de wedstrijdleiding om een nieuwe.")
    return entry


def require_admin(request: Request) -> bool:
    """Controleer de admincookie, of 401."""
    if not _load(request.cookies.get(ADMIN_COOKIE), ADMIN_MAX_AGE):
        raise Unauthorized("Log in als wedstrijdleiding.")
    return True


def check_admin_password(password: str) -> bool:
    """Vergelijk het adminwachtwoord in constante tijd."""
    return secrets.compare_digest(password, settings.admin_password)


CurrentEntry = Annotated[Entry, Depends(current_entry)]
AdminOnly = Annotated[bool, Depends(require_admin)]

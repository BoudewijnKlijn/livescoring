"""FastAPI-app: spelerroutes, leaderboard en opstart."""

from __future__ import annotations

import datetime as dt
import time
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import router as admin_router
from app.auth import AppError, CurrentEntry, DbSession, Unauthorized, hash_token, login_player
from app.models import DEFAULT_PARS, Competition, Entry, create_all
from app.scoring import ScoreError, build_card, leaderboard, set_score, sign_card

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Maak ontbrekende tabellen bij het opstarten."""
    create_all()
    yield


app = FastAPI(title="Live scoring", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(admin_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Voorkom dat tokens uitlekken via de Referer-header."""
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> HTMLResponse:
    """Toon een uitlegpagina in plaats van een kale foutcode."""
    return templates.TemplateResponse(
        request, "melding.html", {"message": exc.message}, status_code=exc.status_code
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Health check voor de pinger die de host wakker houdt."""
    return {"status": "ok"}


@app.get("/")
def home() -> RedirectResponse:
    """Stuur door naar het spelersscherm."""
    return RedirectResponse("/me/card", status_code=303)


@app.get("/t/{token}")
def open_link(token: str, db: DbSession) -> Response:
    """Persoonlijke link: zet de cookie en ga naar het spelersscherm."""
    entry = db.scalar(select(Entry).where(Entry.token_hash == hash_token(token)))
    if entry is None:
        raise Unauthorized("Deze link is niet (meer) geldig. Vraag de wedstrijdleiding erom.")
    response = RedirectResponse("/me/card", status_code=303)
    login_player(response, entry)
    return response


@app.get("/me/card", response_class=HTMLResponse)
def card_screen(request: Request, entry: CurrentEntry) -> HTMLResponse:
    """Het invoerscherm: eigen scores en de scores van de speler die je markt."""
    marks = [e for e in entry.flight.entries if e.marker_entry_id == entry.id]
    return templates.TemplateResponse(
        request,
        "card.html",
        {
            "entry": entry,
            "card": build_card(entry),
            "marked": [(e, build_card(e)) for e in marks],
        },
    )


def _cell(
    request: Request,
    actor: Entry,
    target: Entry,
    hole: int,
    source: str,
    status_code: int = 200,
) -> HTMLResponse:
    """Render één cel van de scorekaart."""
    card = build_card(target)
    return templates.TemplateResponse(
        request,
        "_cell.html",
        {
            "entry": target,
            "actor": actor,
            "row": card.rows[hole - 1],
            "source": source,
            "card": card,
        },
        status_code=status_code,
    )


@app.post("/api/score", response_class=HTMLResponse)
def post_score(
    request: Request,
    entry: CurrentEntry,
    db: DbSession,
    entry_id: int = Form(...),
    hole: int = Form(...),
    source: str = Form(...),
    strokes: str = Form(""),
) -> HTMLResponse:
    """Sla één score op en geef de bijgewerkte cel terug."""
    target = db.get(Entry, entry_id)
    if target is None:
        raise Unauthorized("Onbekende speler.")
    try:
        value = int(strokes) if strokes.strip() else None
    except ValueError:
        return _cell(request, entry, target, hole, source, status_code=422)
    try:
        set_score(db, entry, target, hole, source, value)
    except ScoreError:
        return _cell(request, entry, target, hole, source, status_code=422)
    return _cell(request, entry, target, hole, source)


@app.get("/me/sign", response_class=HTMLResponse)
def sign_screen(request: Request, entry: CurrentEntry) -> HTMLResponse:
    """Overzicht van de eigen kaart met de knop om te tekenen."""
    return templates.TemplateResponse(
        request, "sign.html", {"entry": entry, "card": build_card(entry), "error": None}
    )


@app.post("/me/sign", response_class=HTMLResponse)
def do_sign(
    request: Request,
    entry: CurrentEntry,
    db: DbSession,
    akkoord: str = Form(""),
) -> Response:
    """Onderteken de kaart, mits compleet en zonder conflicten."""
    if akkoord != "ja":
        error = "Zet eerst het vinkje dat de scores kloppen."
    else:
        try:
            sign_card(db, entry)
            return RedirectResponse("/me/card", status_code=303)
        except ScoreError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request,
        "sign.html",
        {"entry": entry, "card": build_card(entry), "error": error},
        status_code=422,
    )


_LB_CACHE: dict[str, tuple[float, str]] = {}
CACHE_SECONDS = 3.0
PAGINA_SECONDEN = 60
SPELERS_PER_SCHERM = 25


def _klok() -> str:
    """Lokale tijd als hh:mm, voor de regel onder het leaderboard."""
    return dt.datetime.now(ZoneInfo("Europe/Amsterdam")).strftime("%H:%M:%S")


def _blader(rijen: list, per_scherm: int) -> tuple[list, int, int]:
    """Kies het stuk van de stand dat nu aan de beurt is.

    Welk stuk dat is volgt uit de klok van de server, niet uit de browser: elk scherm dat
    dezelfde wedstrijd toont, toont dus dezelfde spelers. Past iedereen in één scherm, dan
    valt er niets te bladeren.
    """
    per_scherm = max(1, per_scherm)
    schermen = max(1, -(-len(rijen) // per_scherm))
    huidig = int(time.time() // PAGINA_SECONDEN) % schermen
    start = huidig * per_scherm
    return rijen[start : start + per_scherm], start, schermen


def _pars(competition: Competition) -> list[int]:
    """De pars van de baan, voor de parregel boven het leaderboard."""
    return competition.rounds[0].pars if competition.rounds else DEFAULT_PARS


def _competition(db: DbSession, slug: str) -> Competition:
    """Zoek een competitie op zijn leaderboard-slug."""
    competition = db.scalar(select(Competition).where(Competition.leaderboard_slug == slug))
    if competition is None:
        raise Unauthorized("Dit leaderboard bestaat niet.")
    return competition


@app.get("/l/{slug}", response_class=HTMLResponse)
def leaderboard_page(
    request: Request, slug: str, db: DbSession, n: int = SPELERS_PER_SCHERM
) -> HTMLResponse:
    """Publieke stand van een competitie. `n` is het aantal spelers per scherm."""
    competition = _competition(db, slug)
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {"competition": competition, "n": n, **_bord(db, competition, n)},
    )


@app.get("/l/{slug}/table", response_class=HTMLResponse)
def leaderboard_table(
    request: Request, slug: str, db: DbSession, n: int = SPELERS_PER_SCHERM
) -> HTMLResponse:
    """Alleen de tabel, voor de HTMX-polling. Drie seconden gecachet per scherm.

    Geen limiet per IP: achter de wifi van het clubhuis en achter de proxy van de hoster
    lijkt iedereen dezelfde bezoeker, en dan sluit zo'n limiet juist de toeschouwers buiten.
    De cache is de bescherming: hoeveel kijkers er ook zijn, de tabel wordt hooguit eens per
    drie seconden opgebouwd.
    """
    minuut = int(time.time() // PAGINA_SECONDEN)
    sleutel = f"{slug}:{n}:{minuut}"
    cached = _LB_CACHE.get(sleutel)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return HTMLResponse(cached[1])
    competition = _competition(db, slug)
    html = templates.get_template("_leaderboard_table.html").render(
        request=request, competition=competition, **_bord(db, competition, n)
    )
    if len(_LB_CACHE) > 50:
        _LB_CACHE.clear()  # sleutels bevatten de minuut, dus ruim ze af en toe op
    _LB_CACHE[sleutel] = (time.monotonic(), html)
    return HTMLResponse(html)


def _bord(db: Session, competition: Competition, per_scherm: int) -> dict:
    """Alles wat de leaderboardtabel nodig heeft, inclusief het juiste stuk van de stand."""
    alle = leaderboard(db, competition)
    rijen, start, schermen = _blader(alle, per_scherm)
    return {
        "rows": rijen,
        "offset": start,
        "totaal": len(alle),
        "schermen": schermen,
        "pars": _pars(competition),
        "bijgewerkt": _klok(),
    }

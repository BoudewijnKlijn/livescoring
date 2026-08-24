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

from app.admin import router as admin_router
from app.auth import AppError, CurrentEntry, DbSession, Unauthorized, hash_token, login_player
from app.models import Competition, Entry, create_all
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
    return RedirectResponse("/me", status_code=303)


@app.get("/t/{token}")
def open_link(token: str, db: DbSession) -> Response:
    """Persoonlijke link: zet de cookie en ga naar het spelersscherm."""
    entry = db.scalar(select(Entry).where(Entry.token_hash == hash_token(token)))
    if entry is None:
        raise Unauthorized("Deze link is niet (meer) geldig. Vraag de wedstrijdleiding erom.")
    response = RedirectResponse("/me", status_code=303)
    login_player(response, entry)
    return response


@app.get("/me", response_class=HTMLResponse)
def me(request: Request, entry: CurrentEntry) -> HTMLResponse:
    """Overzicht: wedstrijd, flight, marker, status van de kaart."""
    card = build_card(entry)
    marks = [e for e in entry.flight.entries if e.marker_entry_id == entry.id]
    return templates.TemplateResponse(
        request,
        "me.html",
        {"entry": entry, "card": card, "marks": marks},
    )


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
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render één cel plus een out-of-band update van de conflictteller."""
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
            "error": error,
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
        return _cell(request, entry, target, hole, source, error="Geen getal.", status_code=422)
    try:
        set_score(db, entry, target, hole, source, value)
    except ScoreError as exc:
        return _cell(request, entry, target, hole, source, error=str(exc), status_code=422)
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
            return RedirectResponse("/me", status_code=303)
        except ScoreError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request,
        "sign.html",
        {"entry": entry, "card": build_card(entry), "error": error},
        status_code=422,
    )


_LB_CACHE: dict[str, tuple[float, str]] = {}
_RATE: dict[str, tuple[float, int]] = {}
CACHE_SECONDS = 3.0


def _rate_limited(request: Request, limit: int = 90, window: float = 60.0) -> bool:
    """Simpele rate limit per IP voor de publieke pagina's."""
    ip = request.client.host if request.client else "?"
    start, count = _RATE.get(ip, (0.0, 0))
    moment = time.monotonic()
    if moment - start > window:
        _RATE[ip] = (moment, 1)
        return False
    _RATE[ip] = (start, count + 1)
    return count + 1 > limit


def _klok() -> str:
    """Lokale tijd als hh:mm, voor de regel onder het leaderboard."""
    return dt.datetime.now(ZoneInfo("Europe/Amsterdam")).strftime("%H:%M:%S")


def _competition(db: DbSession, slug: str) -> Competition:
    """Zoek een competitie op zijn leaderboard-slug."""
    competition = db.scalar(select(Competition).where(Competition.leaderboard_slug == slug))
    if competition is None:
        raise Unauthorized("Dit leaderboard bestaat niet.")
    return competition


@app.get("/l/{slug}", response_class=HTMLResponse)
def leaderboard_page(request: Request, slug: str, db: DbSession) -> HTMLResponse:
    """Publieke stand van een competitie."""
    competition = _competition(db, slug)
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {
            "competition": competition,
            "rows": leaderboard(db, competition),
            "bijgewerkt": _klok(),
        },
    )


@app.get("/l/{slug}/table", response_class=HTMLResponse)
def leaderboard_table(request: Request, slug: str, db: DbSession) -> HTMLResponse:
    """Alleen de tabel, voor de HTMX-polling. Drie seconden gecachet."""
    if _rate_limited(request):
        return HTMLResponse("<p>Even rustig aan.</p>", status_code=429)
    cached = _LB_CACHE.get(slug)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return HTMLResponse(cached[1])
    competition = _competition(db, slug)
    html = templates.get_template("_leaderboard_table.html").render(
        competition=competition,
        rows=leaderboard(db, competition),
        request=request,
        bijgewerkt=_klok(),
    )
    _LB_CACHE[slug] = (time.monotonic(), html)
    return HTMLResponse(html)

"""Adminroutes: import, links, live overzicht, correcties en export.

De admin is de enige die de invoer van een ander mag overschrijven, een kaart mag
ontgrendelen en een link mag vervangen. Elke ingreep gaat naar de audit log.
"""

from __future__ import annotations

import csv
import io
import secrets

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import (
    AdminOnly,
    AppError,
    DbSession,
    check_admin_password,
    login_admin,
    logout,
    new_token,
)
from app.config import settings
from app.importer import create_competition, import_csv
from app.models import HOLES, AuditLog, Competition, Entry, Flight, HoleScore, Round, now
from app.scoring import build_card, log

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


def confirm_code() -> str:
    """Vier hoofdletters die de admin moet overtypen bij een gevaarlijke actie."""
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4))


def _links_page(
    request: Request, competition: Competition, links: list[tuple[str, int, str]], note: str
) -> HTMLResponse:
    """Toon nieuwe links één keer: ze zijn daarna niet meer op te vragen."""
    lines = [
        f"{name} (ronde {round_no}): {settings.base_url}/t/{token}"
        for name, round_no, token in sorted(links, key=lambda x: (x[1], x[0]))
    ]
    return templates.TemplateResponse(
        request,
        "admin_links.html",
        {"competition": competition, "links": links, "text": "\n".join(lines), "note": note,
         "base_url": settings.base_url},
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    """Inlogformulier voor de wedstrijdleiding."""
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@router.post("/login")
def do_login(request: Request, wachtwoord: str = Form("")) -> Response:
    """Controleer het adminwachtwoord."""
    if not check_admin_password(wachtwoord):
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Onjuist wachtwoord."}, status_code=401
        )
    response = RedirectResponse("/admin", status_code=303)
    login_admin(response)
    return response


@router.post("/logout")
def do_logout() -> Response:
    """Uitloggen."""
    response = RedirectResponse("/admin/login", status_code=303)
    logout(response)
    return response


@router.get("", response_class=HTMLResponse)
def index(request: Request, db: DbSession, _: AdminOnly) -> HTMLResponse:
    """Overzicht van alle competities."""
    competitions = db.scalars(select(Competition).order_by(Competition.created_at.desc())).all()
    return templates.TemplateResponse(
        request, "admin_index.html", {"competitions": competitions}
    )


@router.post("/competition")
def new_competition(db: DbSession, _: AdminOnly, naam: str = Form(...)) -> Response:
    """Maak een nieuwe competitie."""
    competition = create_competition(db, naam.strip() or "Naamloze wedstrijd")
    return RedirectResponse(f"/admin/c/{competition.id}", status_code=303)


def _get_competition(db: DbSession, competition_id: int) -> Competition:
    competition = db.get(Competition, competition_id)
    if competition is None:
        raise AppError("Deze competitie bestaat niet.", 404)
    return competition


@router.get("/c/{competition_id}", response_class=HTMLResponse)
def competition_page(
    request: Request, competition_id: int, db: DbSession, _: AdminOnly
) -> HTMLResponse:
    """Beheerscherm van één competitie."""
    competition = _get_competition(db, competition_id)
    return templates.TemplateResponse(
        request,
        "admin_competition.html",
        {
            "competition": competition,
            "base_url": settings.base_url,
            "code": confirm_code(),
            "holes": range(1, HOLES + 1),
        },
    )


@router.post("/c/{competition_id}/import", response_class=HTMLResponse)
def do_import(
    request: Request,
    competition_id: int,
    db: DbSession,
    _: AdminOnly,
    csv_tekst: str = Form(""),
) -> HTMLResponse:
    """Importeer spelers, flights en markers uit geplakte CSV."""
    competition = _get_competition(db, competition_id)
    result = import_csv(db, competition, csv_tekst)
    if not result.ok:
        return templates.TemplateResponse(
            request,
            "admin_competition.html",
            {
                "competition": competition,
                "base_url": settings.base_url,
                "code": confirm_code(),
                "holes": range(1, HOLES + 1),
                "errors": result.errors,
                "csv_tekst": csv_tekst,
            },
            status_code=422,
        )
    note = (
        f"{result.created_entries} nieuwe deelnames, {result.updated_entries} bijgewerkt. "
        "Kopieer de links nu: ze zijn later niet meer op te vragen."
    )
    return _links_page(request, competition, result.new_links, note)


@router.post("/round/{round_id}/pars")
def set_pars(
    round_id: int, db: DbSession, _: AdminOnly, pars: str = Form("")
) -> Response:
    """Zet de 18 pars van een ronde, komma- of spatiegescheiden."""
    rnd = db.get(Round, round_id)
    if rnd is None:
        raise AppError("Deze ronde bestaat niet.", 404)
    values = [int(p) for p in pars.replace(",", " ").split() if p.strip().isdigit()]
    if len(values) == HOLES:
        rnd.pars = values
        log(db, "admin", "pars", round=round_id, pars=values)
        db.commit()
    return RedirectResponse(f"/admin/c/{rnd.competition_id}", status_code=303)


@router.get("/c/{competition_id}/live", response_class=HTMLResponse)
def live(request: Request, competition_id: int, db: DbSession, _: AdminOnly) -> HTMLResponse:
    """Live voortgang per flight, ververst elke 5 seconden."""
    competition = _get_competition(db, competition_id)
    return templates.TemplateResponse(
        request,
        "admin_live.html",
        {"competition": competition, "code": confirm_code(), **_live_data(competition)},
    )


@router.get("/c/{competition_id}/live/table", response_class=HTMLResponse)
def live_table(request: Request, competition_id: int, db: DbSession, _: AdminOnly) -> HTMLResponse:
    """Alleen de tabel van het live overzicht."""
    competition = _get_competition(db, competition_id)
    return templates.TemplateResponse(
        request, "_admin_live_table.html", {"competition": competition, **_live_data(competition)}
    )


def _live_data(competition: Competition) -> dict:
    """Voortgang per flight met kaarten en conflicten."""
    flights = []
    for rnd in competition.rounds:
        for flight in rnd.flights:
            flights.append(
                {
                    "round": rnd,
                    "flight": flight,
                    "cards": [(e, build_card(e)) for e in flight.entries],
                }
            )
    return {"flights": flights}


@router.post("/entry/{entry_id}/score")
def override_score(
    entry_id: int,
    db: DbSession,
    _: AdminOnly,
    hole: int = Form(...),
    strokes: str = Form(""),
    reden: str = Form(""),
) -> Response:
    """Overschrijf beide bronnen van één hole. Reden is verplicht."""
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise AppError("Deze deelname bestaat niet.", 404)
    if not reden.strip():
        return RedirectResponse(
            f"/admin/c/{entry.round.competition_id}/live?fout=reden", status_code=303
        )
    value = int(strokes) if strokes.strip() else None
    for source in ("self", "marker"):
        row = db.scalar(
            select(HoleScore).where(
                HoleScore.entry_id == entry.id,
                HoleScore.hole == hole,
                HoleScore.source == source,
            )
        )
        if row is None:
            row = HoleScore(
                entry_id=entry.id, hole=hole, source=source, entered_by_entry_id=entry.id
            )
            db.add(row)
        row.strokes = value
        row.updated_at = now()
    log(db, "admin", "override", entry=entry.id, hole=hole, strokes=value, reason=reden.strip())
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}/live", status_code=303)


@router.post("/entry/{entry_id}/status")
def set_status(
    entry_id: int, db: DbSession, _: AdminOnly, status: str = Form(...), reden: str = Form("")
) -> Response:
    """Zet de status van een deelname: ok, dq, nr of wd."""
    entry = db.get(Entry, entry_id)
    if entry is None or status not in ("ok", "dq", "nr", "wd"):
        raise AppError("Onbekende deelname of status.", 404)
    entry.status = status
    log(db, "admin", "status", entry=entry.id, status=status, reason=reden.strip())
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}/live", status_code=303)


@router.post("/entry/{entry_id}/unlock")
def unlock(entry_id: int, db: DbSession, _: AdminOnly, reden: str = Form("")) -> Response:
    """Ontgrendel een getekende kaart."""
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise AppError("Deze deelname bestaat niet.", 404)
    entry.locked = False
    entry.signed_at = None
    log(db, "admin", "unlock", entry=entry.id, reason=reden.strip())
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}/live", status_code=303)


def _rotate(db: DbSession, entries: list[Entry]) -> list[tuple[str, int, str]]:
    """Geef elke entry een nieuw token. Scores blijven staan."""
    links = []
    for entry in entries:
        token, token_hash = new_token()
        entry.token_hash = token_hash
        links.append((entry.player.name, entry.round.no, token))
        log(db, "admin", "rotate", entry=entry.id)
    db.commit()
    return links


def _check_code(getypt: str, verwacht: str) -> None:
    if getypt.strip().upper() != verwacht.strip().upper():
        raise AppError("De bevestigingscode klopt niet. Er is niets gewijzigd.", 400)


@router.post("/c/{competition_id}/rotate", response_class=HTMLResponse)
def rotate_scope(
    request: Request,
    competition_id: int,
    db: DbSession,
    _: AdminOnly,
    scope: str = Form("competition"),
    flight_id: str = Form(""),
    entry_id: str = Form(""),
    code: str = Form(""),
    verwacht: str = Form(""),
) -> HTMLResponse:
    """Maak nieuwe links voor een speler, een flight of de hele competitie."""
    competition = _get_competition(db, competition_id)
    if scope == "entry" and entry_id:
        entries = [db.get(Entry, int(entry_id))]
    elif scope == "flight" and flight_id:
        flight = db.get(Flight, int(flight_id))
        entries = list(flight.entries) if flight else []
    else:
        entries = [
            e
            for rnd in competition.rounds
            for e in db.scalars(select(Entry).where(Entry.round_id == rnd.id))
        ]
    entries = [e for e in entries if e is not None]
    heeft_scores = any(e.scores for e in entries)
    if heeft_scores:
        _check_code(code, verwacht)
    links = _rotate(db, entries)
    note = (
        f"{len(links)} nieuwe links gemaakt. De oude links werken niet meer. "
        "Scores zijn ongewijzigd. Kopieer de links nu."
    )
    return _links_page(request, competition, links, note)


@router.post("/entry/{entry_id}/reset")
def reset_card(
    entry_id: int,
    db: DbSession,
    _: AdminOnly,
    code: str = Form(""),
    verwacht: str = Form(""),
    reden: str = Form(""),
) -> Response:
    """Wis alle scores van een kaart. Losstaand van het vervangen van een link."""
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise AppError("Deze deelname bestaat niet.", 404)
    if entry.scores:
        _check_code(code, verwacht)
    for row in list(entry.scores):
        db.delete(row)
    entry.signed_at = None
    entry.locked = False
    log(db, "admin", "reset_card", entry=entry.id, reason=reden.strip())
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}/live", status_code=303)


@router.get("/c/{competition_id}/export.csv")
def export_csv(competition_id: int, db: DbSession, _: AdminOnly) -> StreamingResponse:
    """Alle kaarten als CSV: een regel per speler per ronde."""
    competition = _get_competition(db, competition_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        ["naam", "email", "ronde", "flight", "starthole"]
        + [f"hole{h}" for h in range(1, HOLES + 1)]
        + ["totaal", "tov_par", "status", "getekend", "tijdstip", "conflicten"]
    )
    for rnd in competition.rounds:
        for entry in sorted(rnd.entries, key=lambda e: e.player.name):
            card = build_card(entry)
            writer.writerow(
                [
                    entry.player.name,
                    entry.player.email or "",
                    rnd.no,
                    entry.flight.name,
                    entry.flight.start_hole,
                ]
                + [r.self_strokes if r.self_strokes is not None else "" for r in card.rows]
                + [
                    card.total,
                    card.to_par,
                    entry.status,
                    "ja" if entry.signed_at else "nee",
                    entry.signed_at.isoformat(timespec="seconds") if entry.signed_at else "",
                    " ".join(str(h) for h in card.conflicts),
                ]
            )
    buffer.seek(0)
    filename = f"uitslag-{competition.id}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/c/{competition_id}/audit.csv")
def export_audit(competition_id: int, db: DbSession, _: AdminOnly) -> StreamingResponse:
    """De audit log als CSV: alle correcties en ingrepen."""
    rows = db.scalars(select(AuditLog).order_by(AuditLog.at)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["tijdstip", "wie", "actie", "details"])
    for row in rows:
        writer.writerow([row.at.isoformat(timespec="seconds"), row.actor, row.action, row.detail])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="auditlog.csv"'},
    )

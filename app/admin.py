"""Adminroutes: import, spelerslijst, links, correcties en export.

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
    """Toon nieuwe links één keer: ze zijn daarna niet meer op te vragen.

    De tekst is tabgescheiden, want dat plakt in Excel meteen in kolommen.
    """
    rijen = [
        {"naam": naam, "ronde": ronde, "link": f"{settings.base_url}/t/{token}"}
        for naam, ronde, token in sorted(links, key=lambda x: (x[1], x[0]))
    ]
    kop = "Naam\tRonde\tLink"
    tekst = "\n".join([kop] + [f"{r['naam']}\t{r['ronde']}\t{r['link']}" for r in rijen])
    return templates.TemplateResponse(
        request,
        "admin_links.html",
        {"competition": competition, "rijen": rijen, "tekst": tekst, "note": note},
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
    """Overzicht van alle wedstrijden, met de verborgen wedstrijden apart."""
    alle = db.scalars(select(Competition).order_by(Competition.created_at.desc())).all()
    return templates.TemplateResponse(
        request,
        "admin_index.html",
        {
            "actief": [c for c in alle if c.status != "closed"],
            "verborgen": [c for c in alle if c.status == "closed"],
        },
    )


@router.post("/c/{competition_id}/verbergen")
def verbergen(competition_id: int, db: DbSession, _: AdminOnly, terug: str = Form("")) -> Response:
    """Zet een wedstrijd op verborgen of weer terug. Er wordt niets verwijderd."""
    competition = _get_competition(db, competition_id)
    competition.status = "live" if terug else "closed"
    log(db, "admin", "verbergen", competition=competition.id, status=competition.status)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


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


def _beheer(competition: Competition, **extra) -> dict:
    """De vaste inhoud van het beheerscherm.

    Eén plek, zodat een foutmelding nooit een halve pagina kan opleveren: zonder de
    spelerslijst lijkt het alsof iedereen verdwenen is terwijl er niets is gewijzigd.
    """
    return {
        "competition": competition,
        "base_url": settings.base_url,
        "code": confirm_code(),
        "holes": range(1, HOLES + 1),
        "deelnames": _deelnames(competition),
        **extra,
    }


@router.get("/c/{competition_id}", response_class=HTMLResponse)
def competition_page(
    request: Request, competition_id: int, db: DbSession, _: AdminOnly
) -> HTMLResponse:
    """Beheerscherm van één competitie."""
    competition = _get_competition(db, competition_id)
    return templates.TemplateResponse(request, "admin_competition.html", _beheer(competition))


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
            _beheer(competition, errors=result.errors, csv_tekst=csv_tekst),
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


def _deelnames(competition: Competition) -> list[dict]:
    """Alle deelnames met wat er is geïmporteerd, plus de stand van hun kaart."""
    regels = []
    for rnd in competition.rounds:
        for entry in sorted(rnd.entries, key=lambda e: (e.flight.name, e.player.name)):
            card = build_card(entry)
            regels.append(
                {
                    "entry": entry,
                    "round": rnd,
                    "marker": entry.marker.player.name if entry.marker else None,
                    "thru": card.thru,
                    "total": card.total,
                    "conflicts": card.conflicts,
                }
            )
    return regels


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
        raise AppError("Vul een reden in bij een correctie. Er is niets gewijzigd.", 400)
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
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


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
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


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
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


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
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


@router.post("/c/{competition_id}/wissen")
def wis_spelers(
    competition_id: int,
    db: DbSession,
    _: AdminOnly,
    code: str = Form(""),
    verwacht: str = Form(""),
) -> Response:
    """Verwijder alle spelers, flights, ronden en scores van deze competitie.

    Bedoeld om opnieuw te beginnen met een verbeterd CSV-bestand. De competitie zelf en de
    leaderboardlink blijven bestaan.
    """
    competition = _get_competition(db, competition_id)
    _check_code(code, verwacht)
    aantal = sum(len(rnd.entries) for rnd in competition.rounds)
    for rnd in competition.rounds:
        for entry in rnd.entries:
            entry.marker_entry_id = None  # spelers wijzen naar elkaar; eerst losknopen
    db.flush()
    for rnd in list(competition.rounds):
        db.delete(rnd)
    for player in list(competition.players):
        db.delete(player)
    log(db, "admin", "wis_spelers", competition=competition.id, deelnames=aantal)
    db.commit()
    return RedirectResponse(f"/admin/c/{competition_id}", status_code=303)


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

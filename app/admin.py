"""Adminroutes: het beheerscherm, de import, de links en de correcties.

De admin is de enige die de invoer van een ander mag overschrijven, een kaart mag
ontgrendelen en een link mag vervangen. Elke ingreep gaat naar de audit log.
De downloads staan in `app.export`; dat is het enige hier dat niets wijzigt. Het aanmaken van
een account, inloggen en uitloggen staat in `app.account`.

Elke route hier begint bij `get_competition`, `get_round` of `get_entry`. Die drie kijken naar
de eigenaar: een wedstrijdleider komt alleen bij zijn eigen wedstrijden, en een id van een
ander bestaat voor hem niet. Er is geen ingang die alles ziet.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.auth import AppError, CurrentAdmin, DbSession, new_token
from app.config import settings
from app.importer import create_competition, import_csv
from app.models import HOLES, Competition, Entry, Flight, HoleScore, Round, User, now
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
    regels = [
        f"{naam}\t{ronde}\t{settings.base_url}/t/{token}"
        for naam, ronde, token in sorted(links, key=lambda x: (x[1], x[0]))
    ]
    tekst = "\n".join(["Naam\tRonde\tLink"] + regels)
    return templates.TemplateResponse(
        request,
        "admin_links.html",
        {"competition": competition, "tekst": tekst, "note": note},
    )


@router.get("", response_class=HTMLResponse)
def index(request: Request, db: DbSession, gebruiker: CurrentAdmin) -> HTMLResponse:
    """Overzicht van de eigen wedstrijden, met de verborgen wedstrijden apart."""
    query = (
        select(Competition)
        .where(Competition.user_id == gebruiker.id)
        .order_by(Competition.created_at.desc())
    )
    alle = db.scalars(query).all()
    return templates.TemplateResponse(
        request,
        "admin_index.html",
        {
            "gebruiker": gebruiker,
            "actief": [c for c in alle if c.status != "closed"],
            "verborgen": [c for c in alle if c.status == "closed"],
        },
    )


@router.post("/c/{competition_id}/verbergen")
def verbergen(
    competition_id: int, db: DbSession, gebruiker: CurrentAdmin, terug: str = Form("")
) -> Response:
    """Zet een wedstrijd op verborgen of weer terug. Er wordt niets verwijderd."""
    competition = get_competition(db, competition_id, gebruiker)
    competition.status = "live" if terug else "closed"
    log(
        db, "admin", "verbergen", gebruiker.id,
        competition=competition.id, status=competition.status,
    )
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/competition")
def new_competition(db: DbSession, gebruiker: CurrentAdmin, naam: str = Form(...)) -> Response:
    """Maak een nieuwe competitie, op naam van wie hem aanmaakt."""
    competition = create_competition(db, naam.strip() or "Naamloze wedstrijd", gebruiker)
    return RedirectResponse(f"/admin/c/{competition.id}", status_code=303)


def get_competition(db: DbSession, competition_id: int, gebruiker: User) -> Competition:
    """Zoek een wedstrijd van deze gebruiker op, of geef een nette 404.

    Andermans wedstrijd bestaat niet in plaats van dat hij verboden is: dan verklapt het
    raden van een id ook niet dat er iets te vinden was. Ook gebruikt door `app.export`.
    """
    competition = db.get(Competition, competition_id)
    if competition is None or not _van(competition, gebruiker):
        raise AppError("Deze competitie bestaat niet.", 404)
    return competition


def _van(competition: Competition, gebruiker: User) -> bool:
    """Mag deze gebruiker bij deze wedstrijd?"""
    return competition.user_id == gebruiker.id


def get_round(db: DbSession, round_id: int, gebruiker: User) -> Round:
    """Zoek een ronde uit een eigen wedstrijd op, of geef een nette 404."""
    rnd = db.get(Round, round_id)
    if rnd is None or not _van(rnd.competition, gebruiker):
        raise AppError("Deze ronde bestaat niet.", 404)
    return rnd


def get_entry(db: DbSession, entry_id: int, gebruiker: User) -> Entry:
    """Zoek een deelname uit een eigen wedstrijd op, of geef een nette 404."""
    entry = db.get(Entry, entry_id)
    if entry is None or not _van(entry.round.competition, gebruiker):
        raise AppError("Deze deelname bestaat niet.", 404)
    return entry


PANELEN = (
    "spelers", "leaderboard", "score", "status", "ontgrendelen", "link",
    "importeren", "ronden", "export", "leegmaken", "links", "wissen",
)


def _stand(deelnames: list[dict]) -> dict:
    """De vier getallen waar de wedstrijdleiding op stuurt, voor de kop en de rail."""
    return {
        "deelnames": len(deelnames),
        "bezig": sum(1 for d in deelnames if 0 < d["thru"] < HOLES),
        "getekend": sum(1 for d in deelnames if d["entry"].locked),
        "verschillen": sum(1 for d in deelnames if d["conflicts"]),
    }


def _beheer(competition: Competition, paneel: str = "spelers", **extra) -> dict:
    """De vaste inhoud van het beheerscherm.

    Eén plek, zodat een foutmelding nooit een halve pagina kan opleveren: zonder de
    spelerslijst lijkt het alsof iedereen verdwenen is terwijl er niets is gewijzigd.
    """
    deelnames = _deelnames(competition)
    return {
        "competition": competition,
        "base_url": settings.base_url,
        "code": confirm_code(),
        "deelnames": deelnames,
        "stand": _stand(deelnames),
        "paneel": paneel if paneel in PANELEN else "spelers",
        **extra,
    }


@router.get("/c/{competition_id}", response_class=HTMLResponse)
def competition_page(
    request: Request,
    competition_id: int,
    db: DbSession,
    gebruiker: CurrentAdmin,
    p: str = "spelers",
) -> HTMLResponse:
    """Beheerscherm van één competitie. `p` kiest het paneel dat rechts opengaat."""
    competition = get_competition(db, competition_id, gebruiker)
    return templates.TemplateResponse(
        request, "admin_competition.html", _beheer(competition, p)
    )


@router.post("/c/{competition_id}/import", response_class=HTMLResponse)
def do_import(
    request: Request,
    competition_id: int,
    db: DbSession,
    gebruiker: CurrentAdmin,
    csv_tekst: str = Form(""),
) -> HTMLResponse:
    """Importeer spelers, flights en markers uit geplakte CSV."""
    competition = get_competition(db, competition_id, gebruiker)
    result = import_csv(db, competition, csv_tekst)
    if not result.ok:
        return templates.TemplateResponse(
            request,
            "admin_competition.html",
            _beheer(competition, "importeren", errors=result.errors, csv_tekst=csv_tekst),
            status_code=422,
        )
    note = (
        f"{result.created_entries} nieuwe deelnames, {result.updated_entries} bijgewerkt. "
        "Kopieer de links nu: ze zijn later niet meer op te vragen."
    )
    return _links_page(request, competition, result.new_links, note)


@router.post("/round/{round_id}/pars")
def set_pars(
    round_id: int, db: DbSession, gebruiker: CurrentAdmin, pars: str = Form("")
) -> Response:
    """Zet de 18 pars van een ronde, komma- of spatiegescheiden."""
    rnd = get_round(db, round_id, gebruiker)
    values = [int(p) for p in pars.replace(",", " ").split() if p.strip().isdigit()]
    if len(values) == HOLES:
        rnd.pars = values
        log(db, "admin", "pars", gebruiker.id, round=round_id, pars=values)
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
                    "thru": card.agreed_thru,
                    "total": card.total,
                    "conflicts": card.conflicts,
                    "holes": [r.agreed for r in card.rows],
                }
            )
    return regels


@router.post("/entry/{entry_id}/score")
def override_score(
    entry_id: int,
    db: DbSession,
    gebruiker: CurrentAdmin,
    hole: int = Form(...),
    strokes: str = Form(""),
    reden: str = Form(""),
) -> Response:
    """Overschrijf beide bronnen van één hole. Reden is verplicht."""
    entry = get_entry(db, entry_id, gebruiker)
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
    log(
        db, "admin", "override", gebruiker.id,
        entry=entry.id, hole=hole, strokes=value, reason=reden.strip(),
    )
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


@router.post("/entry/{entry_id}/status")
def set_status(
    entry_id: int,
    db: DbSession,
    gebruiker: CurrentAdmin,
    status: str = Form(...),
    reden: str = Form(""),
) -> Response:
    """Zet de status van een deelname: ok, dq, nr of wd."""
    entry = get_entry(db, entry_id, gebruiker)
    if status not in ("ok", "dq", "nr", "wd"):
        raise AppError("Onbekende status.", 404)
    entry.status = status
    log(db, "admin", "status", gebruiker.id, entry=entry.id, status=status, reason=reden.strip())
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


@router.post("/entry/{entry_id}/unlock")
def unlock(
    entry_id: int, db: DbSession, gebruiker: CurrentAdmin, reden: str = Form("")
) -> Response:
    """Ontgrendel een getekende kaart."""
    entry = get_entry(db, entry_id, gebruiker)
    entry.locked = False
    entry.signed_at = None
    log(db, "admin", "unlock", gebruiker.id, entry=entry.id, reason=reden.strip())
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


def _rotate(db: DbSession, entries: list[Entry], eigenaar_id: int) -> list[tuple[str, int, str]]:
    """Geef elke entry een nieuw token. Scores blijven staan."""
    links = []
    for entry in entries:
        token, token_hash = new_token()
        entry.token_hash = token_hash
        links.append((entry.player.name, entry.round.no, token))
        log(db, "admin", "rotate", eigenaar_id, entry=entry.id)
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
    gebruiker: CurrentAdmin,
    scope: str = Form("competition"),
    flight_id: str = Form(""),
    entry_id: str = Form(""),
    code: str = Form(""),
    verwacht: str = Form(""),
) -> HTMLResponse:
    """Maak nieuwe links voor een speler, een flight of de hele competitie."""
    competition = get_competition(db, competition_id, gebruiker)
    # Elke keuze wijst zichzelf aan. Een lege keuze is een fout, nooit stilzwijgend
    # "dan maar iedereen": dat kost negenendertig spelers hun link om er één te helpen.
    if scope == "entry":
        if not entry_id:
            raise AppError("Kies eerst een speler. Er is niets gewijzigd.", 400)
        entries = [get_entry(db, int(entry_id), gebruiker)]
    elif scope == "flight":
        if not flight_id:
            raise AppError("Kies eerst een flight. Er is niets gewijzigd.", 400)
        flight = db.get(Flight, int(flight_id))
        entries = list(flight.entries) if flight else []
    elif scope == "competition":
        entries = [
            e
            for rnd in competition.rounds
            for e in db.scalars(select(Entry).where(Entry.round_id == rnd.id))
        ]
    else:
        raise AppError("Onbekende keuze. Er is niets gewijzigd.", 400)
    # Een gekozen speler of flight kan uit een andere wedstrijd komen: het formulier is te
    # vervalsen. Wat niet bij deze wedstrijd hoort valt af, dus niemand raakt zijn link kwijt
    # door het id van een ander mee te sturen.
    entries = [
        e
        for e in entries
        if e is not None and e.round.competition_id == competition.id
    ]
    _check_code(code, verwacht)
    links = _rotate(db, entries, gebruiker.id)
    note = (
        f"{len(links)} nieuwe link(s) gemaakt. De vorige link van deze speler(s) werkt niet "
        "meer; die van de anderen blijft gewoon werken. Scores zijn ongewijzigd."
    )
    return _links_page(request, competition, links, note)


@router.post("/entry/{entry_id}/reset")
def reset_card(
    entry_id: int,
    db: DbSession,
    gebruiker: CurrentAdmin,
    code: str = Form(""),
    verwacht: str = Form(""),
    reden: str = Form(""),
) -> Response:
    """Wis alle scores van een kaart. Losstaand van het vervangen van een link."""
    entry = get_entry(db, entry_id, gebruiker)
    if entry.scores:
        _check_code(code, verwacht)
    for row in list(entry.scores):
        db.delete(row)
    entry.signed_at = None
    entry.locked = False
    log(db, "admin", "reset_card", gebruiker.id, entry=entry.id, reason=reden.strip())
    db.commit()
    return RedirectResponse(f"/admin/c/{entry.round.competition_id}", status_code=303)


@router.post("/c/{competition_id}/wissen")
def wis_spelers(
    competition_id: int,
    db: DbSession,
    gebruiker: CurrentAdmin,
    code: str = Form(""),
    verwacht: str = Form(""),
) -> Response:
    """Verwijder alle spelers, flights, ronden en scores van deze competitie.

    Bedoeld om opnieuw te beginnen met een verbeterd CSV-bestand. De competitie zelf en de
    leaderboardlink blijven bestaan.
    """
    competition = get_competition(db, competition_id, gebruiker)
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
    log(db, "admin", "wis_spelers", gebruiker.id, competition=competition.id, deelnames=aantal)
    db.commit()
    return RedirectResponse(f"/admin/c/{competition_id}", status_code=303)

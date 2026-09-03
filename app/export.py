"""Downloads voor de wedstrijdleiding: de uitslag en de audit log als CSV.

Apart van `app.admin` omdat dit het enige is wat geen scherm oplevert maar een bestand, en
omdat het als enige niets wijzigt. Beide routes zijn puur lezen.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.admin import get_competition
from app.auth import CurrentAdmin, DbSession
from app.models import HOLES, AuditLog, Competition
from app.scoring import build_card

router = APIRouter(prefix="/admin", tags=["export"])


def _csv_response(kop: list[str], rijen: list[list], bestandsnaam: str) -> StreamingResponse:
    """Zet rijen om in een CSV-download. Puntkomma's, want dat opent Excel meteen goed."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(kop)
    writer.writerows(rijen)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{bestandsnaam}"'},
    )


@router.get("/c/{competition_id}/export.csv")
def export_csv(
    competition_id: int, db: DbSession, gebruiker: CurrentAdmin
) -> StreamingResponse:
    """Alle kaarten van één wedstrijd: een regel per speler per ronde."""
    competition = get_competition(db, competition_id, gebruiker)
    rijen = []
    for rnd in competition.rounds:
        for entry in sorted(rnd.entries, key=lambda e: e.player.name):
            card = build_card(entry)
            rijen.append(
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
    kop = (
        ["naam", "email", "ronde", "flight", "starthole"]
        + [f"hole{h}" for h in range(1, HOLES + 1)]
        + ["totaal", "tov_par", "status", "getekend", "tijdstip", "conflicten"]
    )
    return _csv_response(kop, rijen, f"uitslag-{competition.id}.csv")


AUDIT_KOP = ["tijdstip", "wie", "actie", "details"]


def _audit_rijen(db: DbSession, waar) -> list[list]:
    """De logregels die aan `waar` voldoen, op tijd.

    Regels over de installatie zelf hangen aan geen enkele wedstrijd. Elke voorwaarde hier
    stelt een eis aan `competition_id`, dus die vallen er vanzelf buiten.
    """
    return [
        [row.at.isoformat(timespec="seconds"), row.actor, row.action, row.detail]
        for row in db.scalars(select(AuditLog).where(waar).order_by(AuditLog.at))
    ]


@router.get("/c/{competition_id}/audit.csv")
def export_competition_audit(
    competition_id: int, db: DbSession, gebruiker: CurrentAdmin
) -> StreamingResponse:
    """De audit log van één wedstrijd: elke correctie en ingreep, met tijdstip en reden.

    Dit is de vraag die je stelt als er iets misging -- wat is er in déze wedstrijd
    gebeurd -- en pas sinds de regels hun wedstrijd zelf noemen is die te beantwoorden.
    Het wissen van alle spelers raakt dit niet: de wedstrijd blijft staan, en zijn log dus
    ook.
    """
    competition = get_competition(db, competition_id, gebruiker)
    rijen = _audit_rijen(db, AuditLog.competition_id == competition.id)
    return _csv_response(AUDIT_KOP, rijen, f"auditlog-{competition.id}.csv")


@router.get("/audit.csv")
def export_audit(db: DbSession, gebruiker: CurrentAdmin) -> StreamingResponse:
    """De audit log van al je wedstrijden in één bestand, voor als je niet weet waar te
    zoeken. Voor één wedstrijd is er de export op het beheerscherm van die wedstrijd."""
    eigen = select(Competition.id).where(Competition.user_id == gebruiker.id)
    rijen = _audit_rijen(db, AuditLog.competition_id.in_(eigen))
    return _csv_response(AUDIT_KOP, rijen, "auditlog.csv")

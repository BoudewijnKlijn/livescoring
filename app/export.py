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


@router.get("/audit.csv")
def export_audit(db: DbSession, gebruiker: CurrentAdmin) -> StreamingResponse:
    """De audit log van alle eigen wedstrijden: elke correctie en ingreep, met reden.

    Elke regel noemt zijn wedstrijd, dus dit filtert op de wedstrijden van deze
    wedstrijdleider. Het wissen van alle spelers raakt dat niet: de wedstrijd zelf blijft
    staan, en daarmee de regels erover. Wat over de installatie gaat hangt aan geen enkele
    wedstrijd en valt er via de join dus buiten.
    """
    rijen = [
        [row.at.isoformat(timespec="seconds"), row.actor, row.action, row.detail]
        for row in db.scalars(
            select(AuditLog)
            .join(Competition, AuditLog.competition_id == Competition.id)
            .where(Competition.user_id == gebruiker.id)
            .order_by(AuditLog.at)
        )
    ]
    return _csv_response(["tijdstip", "wie", "actie", "details"], rijen, "auditlog.csv")

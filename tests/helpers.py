"""Gedeelde hulpjes voor de tests.

Hier staat niets over hoe een pagina eruitziet. Tests die naar de opmaak kijken horen in
`test_weergave.py`; alle andere tests praten met de domeinfuncties, zodat een wijziging in
een template of een stylesheet ze niet kan raken.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from app.models import Entry, Player, Round
from app.scoring import leaderboard, set_score


def entry_van(db, naam: str, ronde: int = 1) -> Entry:
    """De deelname van een speler in een ronde."""
    db.expire_all()
    return db.scalar(
        select(Entry)
        .join(Player)
        .join(Round, Entry.round_id == Round.id)
        .where(Player.name == naam, Round.no == ronde)
    )


def vul_kaart(
    db, entry: Entry, strokes: int = 4, holes: int = 18, marker_strokes: int | None = None
) -> None:
    """Vul een kaart namens de speler en zijn marker. Verschillende waarden = conflict."""
    for hole in range(1, holes + 1):
        set_score(db, entry, entry, hole, "self", strokes)
        set_score(db, entry.marker, entry, hole, "marker", marker_strokes or strokes)


def stand(db, competition, ronde: int = 1) -> dict:
    """De stand van één ronde, op naam."""
    db.expire_all()
    return {r.name: r for r in leaderboard(db, competition, ronde)}


def bevestigingscode(client, url: str) -> str:
    """De code die de admin moet overtypen, uit het verborgen veld van dat formulier.

    Dit leest een formulierveld, niet de opmaak: de naam `verwacht` hoort bij het
    HTTP-contract van de route en verandert niet mee met het ontwerp.
    """
    pagina = client.get(url).text
    return re.search(r'name="verwacht" value="([A-Z]{4})"', pagina).group(1)

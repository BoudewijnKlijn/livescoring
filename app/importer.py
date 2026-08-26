"""CSV-import van spelers, flights en markers.

Formaat, een regel per speler per ronde:

    naam,email,ronde,flight,starthole,marker

Het hele bestand wordt eerst gevalideerd en pas daarna toegepast: bij één fout wordt niets
geïmporteerd. Herimporteren voegt toe en werkt flight en marker bij, maar verwijdert nooit
een speler, een score of een bestaand token.
"""

from __future__ import annotations

import csv
import io
import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import new_token
from app.models import DEFAULT_PARS, Competition, Entry, Flight, Player, Round
from app.scoring import log

COLUMNS = ("naam", "email", "ronde", "flight", "starthole", "marker")


@dataclass
class Row:
    """Eén gevalideerde regel uit het bestand."""

    line: int
    name: str
    email: str | None
    round_no: int
    flight: str
    start_hole: int
    marker: str | None


@dataclass
class ImportResult:
    """Uitkomst van een import."""

    errors: list[str] = field(default_factory=list)
    new_links: list[tuple[str, int, str]] = field(default_factory=list)
    created_entries: int = 0
    updated_entries: int = 0

    @property
    def ok(self) -> bool:
        """Geen fouten gevonden."""
        return not self.errors


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def parse(text: str) -> tuple[list[Row], list[str]]:
    """Parse en valideer de CSV. Geeft de regels en de gevonden fouten terug."""
    errors: list[str] = []
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], ["Er stond niets in het vak. Plak eerst de regels uit je bestand."]

    headers = [h.strip().casefold() for h in reader.fieldnames]
    missing = [c for c in ("naam", "ronde", "flight") if c not in headers]
    if missing:
        return [], [f"Kolommen ontbreken: {', '.join(missing)}. Verwacht: {', '.join(COLUMNS)}."]

    rows: list[Row] = []
    seen: set[tuple[str, int]] = set()
    for i, raw in enumerate(reader, start=2):
        clean = {(k or "").strip().casefold(): (v or "").strip() for k, v in raw.items()}
        name = clean.get("naam", "")
        if not name:
            errors.append(f"Regel {i}: naam ontbreekt.")
            continue
        try:
            round_no = int(clean.get("ronde") or 1)
        except ValueError:
            errors.append(f"Regel {i}: ronde '{clean.get('ronde')}' is geen getal.")
            continue
        if round_no < 1:
            errors.append(f"Regel {i}: ronde moet 1 of hoger zijn.")
            continue
        flight = clean.get("flight", "")
        if not flight:
            errors.append(f"Regel {i}: flight ontbreekt.")
            continue
        try:
            start_hole = int(clean.get("starthole") or 1)
        except ValueError:
            errors.append(f"Regel {i}: starthole '{clean.get('starthole')}' is geen getal.")
            continue
        if start_hole not in (1, 10):
            errors.append(f"Regel {i}: starthole moet 1 of 10 zijn, niet {start_hole}.")
            continue
        if (_key(name), round_no) in seen:
            errors.append(f"Regel {i}: {name} staat twee keer in ronde {round_no}.")
            continue
        seen.add((_key(name), round_no))
        marker = clean.get("marker") or None
        if marker and _key(marker) == _key(name):
            errors.append(f"Regel {i}: {name} kan niet zijn eigen marker zijn.")
            continue
        rows.append(
            Row(
                line=i,
                name=" ".join(name.split()),
                email=clean.get("email") or None,
                round_no=round_no,
                flight=flight,
                start_hole=start_hole,
                marker=" ".join(marker.split()) if marker else None,
            )
        )

    errors.extend(_check_markers(rows))
    return rows, errors


def _check_markers(rows: list[Row]) -> list[str]:
    """Controleer dat elke marker in dezelfde flight en ronde zit."""
    errors: list[str] = []
    per_round: dict[int, dict[str, Row]] = {}
    for row in rows:
        per_round.setdefault(row.round_no, {})[_key(row.name)] = row
    for row in rows:
        if not row.marker:
            continue
        other = per_round[row.round_no].get(_key(row.marker))
        if other is None:
            errors.append(
                f"Regel {row.line}: marker '{row.marker}' speelt niet in ronde {row.round_no}."
            )
        elif other.flight.casefold() != row.flight.casefold():
            errors.append(
                f"Regel {row.line}: marker '{row.marker}' zit in flight {other.flight}, "
                f"{row.name} in flight {row.flight}."
            )
    for round_no, by_name in per_round.items():
        flights: dict[str, set[int]] = {}
        for row in by_name.values():
            flights.setdefault(row.flight.casefold(), set()).add(row.start_hole)
        for flight, holes in flights.items():
            if len(holes) > 1:
                errors.append(
                    f"Ronde {round_no}, flight {flight}: verschillende startholes {sorted(holes)}."
                )
    return errors


def import_csv(db: Session, competition: Competition, text: str) -> ImportResult:
    """Importeer een CSV in een competitie. Bij fouten wordt niets weggeschreven."""
    rows, errors = parse(text)
    result = ImportResult(errors=errors)
    if errors or not rows:
        if not rows and not errors:
            result.errors.append("Geen bruikbare regels gevonden.")
        return result

    players = {
        _key(p.name): p
        for p in db.scalars(select(Player).where(Player.competition_id == competition.id))
    }
    rounds = {r.no: r for r in competition.rounds}
    entries: dict[tuple[int, int], Entry] = {}

    for row in rows:
        player = players.get(_key(row.name))
        if player is None:
            player = Player(competition_id=competition.id, name=row.name, email=row.email)
            db.add(player)
            db.flush()
            players[_key(row.name)] = player
        elif row.email and not player.email:
            player.email = row.email

        rnd = rounds.get(row.round_no)
        if rnd is None:
            rnd = Round(competition_id=competition.id, no=row.round_no, pars=list(DEFAULT_PARS))
            db.add(rnd)
            db.flush()
            rounds[row.round_no] = rnd

        flight = db.scalar(
            select(Flight).where(Flight.round_id == rnd.id, Flight.name == row.flight)
        )
        if flight is None:
            flight = Flight(round_id=rnd.id, name=row.flight, start_hole=row.start_hole)
            db.add(flight)
            db.flush()
        else:
            flight.start_hole = row.start_hole

        entry = db.scalar(
            select(Entry).where(Entry.round_id == rnd.id, Entry.player_id == player.id)
        )
        if entry is None:
            token, token_hash = new_token()
            entry = Entry(
                round_id=rnd.id,
                player_id=player.id,
                flight_id=flight.id,
                token_hash=token_hash,
                position=len(flight.entries),
            )
            db.add(entry)
            db.flush()
            result.new_links.append((player.name, row.round_no, token))
            result.created_entries += 1
        else:
            if entry.flight_id != flight.id:
                entry.flight_id = flight.id
            result.updated_entries += 1
        entries[(rnd.id, player.id)] = entry

    for row in rows:
        if not row.marker:
            continue
        rnd = rounds[row.round_no]
        entry = entries[(rnd.id, players[_key(row.name)].id)]
        marker_entry = entries[(rnd.id, players[_key(row.marker)].id)]
        entry.marker_entry_id = marker_entry.id

    log(
        db,
        "admin",
        "import",
        competition=competition.id,
        created=result.created_entries,
        updated=result.updated_entries,
    )
    db.commit()
    return result


def create_competition(db: Session, name: str) -> Competition:
    """Maak een competitie met een onraadbare leaderboard-slug."""
    competition = Competition(name=name, leaderboard_slug=secrets.token_urlsafe(9), status="live")
    db.add(competition)
    log(db, "admin", "competition_created", name=name)
    db.commit()
    return competition

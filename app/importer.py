"""CSV-import van spelers, flights en markers.

Formaat, een regel per speler per ronde:

    naam,email,ronde,flight,starthole,marker

Bij één fout wordt niets geïmporteerd: het bestand wordt eerst gecontroleerd, en wat daarna
alleen aan de eindstand te zien is wordt na het schrijven gecontroleerd en zo nodig
teruggedraaid.

De regels van een herimport:

1. Een speler hoort bij een competitie en wordt herkend aan zijn naam, ongeacht hoofdletters
   en dubbele spaties. Een andere schrijfwijze is een andere speler. Een deelname is die
   speler in die ronde.
2. Het bestand wint voor wat het zegt: flight, starthole, marker en e-mail volgen het
   bestand. Wat het bestand niet noemt blijft zoals het was, tot en met spelers die er niet
   in staan en een lege markerkolom.
3. Scores en persoonlijke links blijven altijd staan. Ook bij een verhuizing naar een andere
   flight, bij een nieuwe marker en op een getekende kaart. Een getekende kaart blijft
   getekend.
4. Na afloop heeft elke speler precies één marker, zit die marker in dezelfde ronde en
   flight, en markt niemand meer dan één speler. Klopt daar iets niet aan, dan gaat de hele
   import niet door en zegt de melding welke speler het betreft en wat eraan te doen is. Dit
   wordt tegen de database gecontroleerd, niet alleen tegen het bestand: een gedeeltelijke
   import kan een speler stranden die er zelf niet in staat, en een verkeerd gespelde naam
   levert een tweede speler op die de marker van iemand anders inpikt. In beide gevallen kan
   er daarna niemand meer tekenen.
5. Een marker die al in het systeem staat telt mee, ook als hij niet in dit bestand staat.
   Zo kun je één flight opnieuw aanleveren zonder de rest erbij te plakken.
6. Wie naar een andere flight verhuist, komt daar achteraan te staan.
"""

from __future__ import annotations

import csv
import difflib
import io
import secrets
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import new_token
from app.models import DEFAULT_PARS, Competition, Entry, Flight, Player, Round, User
from app.scoring import log, user_actor

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
    """Controleer wat aan het bestand alleen te zien is: markers binnen dezelfde flight.

    Een marker die niet in het bestand staat is hier geen fout: hij kan al in het systeem
    staan. Of dat zo is, en of hij in de juiste flight zit, blijkt pas uit de eindstand;
    `_check_eindstand` kijkt daarnaar.
    """
    errors: list[str] = []
    per_round: dict[int, dict[str, Row]] = {}
    for row in rows:
        per_round.setdefault(row.round_no, {})[_key(row.name)] = row
    for row in rows:
        if not row.marker:
            continue
        other = per_round[row.round_no].get(_key(row.marker))
        if other is not None and other.flight.casefold() != row.flight.casefold():
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


def _achteraan(db: Session, flight: Flight, geteld: dict[int, int]) -> int:
    """De plek achteraan een flight.

    Eenmaal per flight uit de database geteld: `flight.entries` is binnen één import niet te
    vertrouwen, want die relatie ziet de deelnames van deze import nog niet.
    """
    if flight.id not in geteld:
        geteld[flight.id] = (
            db.scalar(select(func.count()).select_from(Entry).where(Entry.flight_id == flight.id))
            or 0
        )
    geteld[flight.id] += 1
    return geteld[flight.id] - 1


def _marker_entry(
    db: Session, entries: dict[tuple[int, int], Entry], rnd: Round, player: Player | None
) -> Entry | None:
    """De deelname van een marker: eerst uit dit bestand, anders uit de database."""
    if player is None:
        return None
    uit_bestand = entries.get((rnd.id, player.id))
    if uit_bestand is not None:
        return uit_bestand
    return db.scalar(
        select(Entry).where(Entry.round_id == rnd.id, Entry.player_id == player.id)
    )


def _lijkt_op(naam: str, anderen: list[str]) -> str:
    """Wijs op een naam die er sterk op lijkt. Bijna altijd een tikfout in het bestand."""
    buren = difflib.get_close_matches(naam, [a for a in anderen if a != naam], n=1, cutoff=0.8)
    if not buren:
        return ""
    return (
        f" Let op: '{naam}' en '{buren[0]}' worden als twee verschillende spelers gezien. "
        "Is dat een tikfout, schrijf de naam dan overal hetzelfde."
    )


def _tikfout(namen: list[str]) -> str:
    """De eerste twee namen uit de lijst die verdacht veel op elkaar lijken."""
    for naam in namen:
        hint = _lijkt_op(naam, namen)
        if hint:
            return hint
    return ""


def _check_eindstand(db: Session, rondes: list[Round]) -> list[str]:
    """Klopt de markerindeling na deze import nog?

    Drie eisen, en pas samen sluiten ze: elke speler heeft precies één marker, die marker
    zit in dezelfde ronde en flight, en niemand markt meer dan één speler. Dit kan alleen
    achteraf, tegen de database: een verhuizing in het bestand kan een speler stranden die
    er zelf niet in staat, en een verkeerd gespelde naam levert een tweede speler op die de
    marker van iemand anders inpikt. In beide gevallen kan er daarna niemand meer tekenen,
    zonder dat er iets op het scherm verschijnt.
    """
    errors: list[str] = []
    for rnd in rondes:
        entries = sorted(
            db.scalars(select(Entry).where(Entry.round_id == rnd.id)),
            key=lambda e: (e.flight.name, e.player.name),
        )
        namen = [e.player.name for e in entries]
        markt: dict[int, list[Entry]] = {}
        for entry in entries:
            if entry.marker_entry_id is None:
                errors.append(
                    f"Ronde {rnd.no}, flight {entry.flight.name}: {entry.player.name} heeft "
                    f"geen marker. Zet in de kolom marker de naam van een andere speler uit "
                    f"flight {entry.flight.name}." + _lijkt_op(entry.player.name, namen)
                )
                continue
            marker = db.get(Entry, entry.marker_entry_id)
            if marker is None or marker.round_id != entry.round_id:
                errors.append(
                    f"Ronde {rnd.no}: de marker van {entry.player.name} speelt niet in deze "
                    f"ronde. Kies een marker uit flight {entry.flight.name}."
                )
                continue
            markt.setdefault(marker.id, []).append(entry)
            if marker.flight_id != entry.flight_id:
                errors.append(
                    f"Ronde {rnd.no}: {entry.player.name} zit in flight {entry.flight.name} en "
                    f"wordt gemarkeerd door {marker.player.name} uit flight "
                    f"{marker.flight.name}. Zet ze in dezelfde flight, anders kan niemand zijn "
                    "kaart bevestigen."
                )
        for marker_id, spelers in sorted(markt.items()):
            if len(spelers) == 1:
                continue
            marker = db.get(Entry, marker_id)
            namen_van_spelers = [e.player.name for e in spelers]
            errors.append(
                f"Ronde {rnd.no}, flight {marker.flight.name}: {marker.player.name} staat als "
                f"marker bij {', '.join(namen_van_spelers)}. Iedereen markt er precies één, dus "
                "geef één van hen een andere marker uit die flight."
                + _tikfout(namen_van_spelers)
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
    # Uit de database, niet uit competition.rounds: die relatie kan een ronde missen die in
    # dezelfde sessie is aangemaakt, en dan botst de import op zijn eigen ronde.
    rounds = {
        r.no: r
        for r in db.scalars(select(Round).where(Round.competition_id == competition.id))
    }
    entries: dict[tuple[int, int], Entry] = {}
    geteld: dict[int, int] = {}

    for row in rows:
        player = players.get(_key(row.name))
        if player is None:
            player = Player(competition_id=competition.id, name=row.name, email=row.email)
            db.add(player)
            db.flush()
            players[_key(row.name)] = player
        elif row.email:
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
                position=_achteraan(db, flight, geteld),
            )
            db.add(entry)
            db.flush()
            result.new_links.append((player.name, row.round_no, token))
            result.created_entries += 1
        else:
            if entry.flight_id != flight.id:
                # Eerst tellen, dan verhuizen: een query spoelt de sessie door, en na de
                # verhuizing zou de speler zichzelf meetellen.
                entry.position = _achteraan(db, flight, geteld)
                entry.flight_id = flight.id
            result.updated_entries += 1
        entries[(rnd.id, player.id)] = entry

    for row in rows:
        if not row.marker:
            continue
        rnd = rounds[row.round_no]
        entry = entries[(rnd.id, players[_key(row.name)].id)]
        marker_entry = _marker_entry(db, entries, rnd, players.get(_key(row.marker)))
        if marker_entry is None:
            result.errors.append(
                f"Regel {row.line}: marker '{row.marker}' staat niet in dit bestand en speelt "
                f"ook niet in ronde {row.round_no}. Let op de schrijfwijze van de naam."
            )
            continue
        entry.marker_entry_id = marker_entry.id

    db.flush()
    result.errors.extend(
        _check_eindstand(db, [rounds[no] for no in sorted({r.round_no for r in rows})])
    )
    if result.errors:
        db.rollback()
        return ImportResult(errors=result.errors)

    log(
        db,
        user_actor(competition.user_id),
        "import",
        competition.id,
        created=result.created_entries,
        updated=result.updated_entries,
    )
    db.commit()
    return result


def create_competition(db: Session, name: str, user: User) -> Competition:
    """Maak een competitie met een onraadbare leaderboard-slug, op naam van `user`."""
    competition = Competition(
        name=name,
        user_id=user.id,
        leaderboard_slug=secrets.token_urlsafe(9),
        status="live",
    )
    db.add(competition)
    # Eerst flushen: zonder id valt deze regel aan geen wedstrijd te hangen, en juist die
    # regel hoort te vertellen wélke wedstrijd er is aangemaakt.
    db.flush()
    log(db, user_actor(user.id), "competition_created", competition.id, name=name)
    db.commit()
    return competition

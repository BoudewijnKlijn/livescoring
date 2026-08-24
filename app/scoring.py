"""Domeinlogica: scores opslaan, conflicten bepalen, kaart tekenen, leaderboard.

Autorisatie zit hier, niet alleen in de templates: `set_score` weigert elke invoer door
iemand die geen recht heeft op die bron.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HOLES, AuditLog, Competition, Entry, HoleScore, Round, now


class ScoreError(Exception):
    """Een score mag niet worden opgeslagen."""


def play_order(start_hole: int) -> list[int]:
    """Holes in speelvolgorde, gegeven de starthole (1 of 10)."""
    holes = list(range(1, HOLES + 1))
    i = holes.index(start_hole)
    return holes[i:] + holes[:i]


def scores_of(entry: Entry) -> dict[tuple[int, str], int | None]:
    """Alle ingevoerde scores van een entry, op (hole, bron)."""
    return {(s.hole, s.source): s.strokes for s in entry.scores}


@dataclass
class HoleRow:
    """Eén hole op de scorekaart."""

    hole: int
    par: int
    self_strokes: int | None = None
    marker_strokes: int | None = None

    @property
    def conflict(self) -> bool:
        """Beide bronnen ingevuld en verschillend."""
        return (
            self.self_strokes is not None
            and self.marker_strokes is not None
            and self.self_strokes != self.marker_strokes
        )

    @property
    def agreed(self) -> bool:
        """Beide bronnen ingevuld en gelijk."""
        return (
            self.self_strokes is not None
            and self.self_strokes == self.marker_strokes
        )


@dataclass
class Card:
    """De volledige kaart van een entry."""

    entry: Entry
    rows: list[HoleRow] = field(default_factory=list)

    @property
    def conflicts(self) -> list[int]:
        """Holes waar speler en marker het oneens zijn."""
        return [r.hole for r in self.rows if r.conflict]

    @property
    def missing_self(self) -> list[int]:
        """Holes zonder eigen score."""
        return [r.hole for r in self.rows if r.self_strokes is None]

    @property
    def missing_marker(self) -> list[int]:
        """Holes zonder score van de marker."""
        return [r.hole for r in self.rows if r.marker_strokes is None]

    @property
    def total(self) -> int:
        """Totaal aantal slagen volgens de eigen invoer."""
        return sum(r.self_strokes for r in self.rows if r.self_strokes is not None)

    @property
    def thru(self) -> int:
        """Aantal holes met een eigen score."""
        return sum(1 for r in self.rows if r.self_strokes is not None)

    @property
    def to_par(self) -> int:
        """Slagen ten opzichte van par over de gespeelde holes."""
        return sum(
            r.self_strokes - r.par for r in self.rows if r.self_strokes is not None
        )

    @property
    def complete(self) -> bool:
        """Alle holes door beide bronnen ingevuld."""
        return not self.missing_self and not self.missing_marker

    @property
    def signable(self) -> bool:
        """Mag deze kaart getekend worden?"""
        return self.complete and not self.conflicts and not self.entry.locked

    def first_open_hole(self) -> int:
        """Eerste hole in speelvolgorde zonder eigen score, voor het autoscrollen."""
        by_hole = {r.hole: r for r in self.rows}
        for hole in play_order(self.entry.flight.start_hole):
            if by_hole[hole].self_strokes is None:
                return hole
        return self.entry.flight.start_hole


def build_card(entry: Entry) -> Card:
    """Bouw de kaart van een entry uit zijn scores."""
    pars = entry.round.pars or [4] * HOLES
    found = scores_of(entry)
    rows = [
        HoleRow(
            hole=h,
            par=pars[h - 1],
            self_strokes=found.get((h, "self")),
            marker_strokes=found.get((h, "marker")),
        )
        for h in range(1, HOLES + 1)
    ]
    return Card(entry=entry, rows=rows)


def may_write(actor: Entry, target: Entry, source: str) -> bool:
    """Mag `actor` een score van `target` met deze bron schrijven?

    Eigen scores mag je alleen zelf invoeren. Markerscores alleen als je de aangewezen
    marker van die speler bent.
    """
    if source == "self":
        return actor.id == target.id
    if source == "marker":
        return target.marker_entry_id == actor.id
    return False


def set_score(
    db: Session,
    actor: Entry,
    target: Entry,
    hole: int,
    source: str,
    strokes: int | None,
) -> HoleScore:
    """Sla een score op na controle van rechten, hole, waarde en vergrendeling."""
    if not may_write(actor, target, source):
        raise ScoreError("Je mag deze score niet invoeren.")
    if not 1 <= hole <= HOLES:
        raise ScoreError("Onbekende hole.")
    if strokes is not None and not 1 <= strokes <= 20:
        raise ScoreError("Vul een aantal slagen tussen 1 en 20 in.")
    if target.locked:
        raise ScoreError("Deze kaart is getekend en vergrendeld.")

    row = db.scalar(
        select(HoleScore).where(
            HoleScore.entry_id == target.id,
            HoleScore.hole == hole,
            HoleScore.source == source,
        )
    )
    if row is None:
        row = HoleScore(
            entry_id=target.id, hole=hole, source=source, entered_by_entry_id=actor.id
        )
        db.add(row)
    row.strokes = strokes
    row.entered_by_entry_id = actor.id
    row.updated_at = now()
    log(db, f"player:{actor.id}", "score", entry=target.id, hole=hole, source=source,
        strokes=strokes)
    db.commit()
    db.refresh(target)
    return row


def sign_card(db: Session, entry: Entry) -> None:
    """Teken de kaart. Faalt bij een conflict, een ontbrekende hole of vergrendeling."""
    card = build_card(entry)
    if entry.locked:
        raise ScoreError("Deze kaart is al vergrendeld.")
    if card.conflicts:
        raise ScoreError("Er zijn nog holes met een verschil.")
    if not card.complete:
        raise ScoreError("Nog niet alle holes zijn door beide spelers ingevuld.")
    entry.signed_at = now()
    entry.locked = True
    log(db, f"player:{entry.id}", "sign", entry=entry.id, total=card.total)
    db.commit()


def log(db: Session, actor: str, action: str, **detail) -> None:
    """Schrijf een regel in de audit log. Commit gebeurt door de aanroeper."""
    db.add(AuditLog(actor=actor, action=action, detail=detail))


@dataclass
class LeaderboardRow:
    """Eén speler op het leaderboard, opgeteld over alle ronden."""

    player_id: int
    name: str
    status: str
    round_totals: dict[int, int]
    thru: int
    total: int
    to_par: int
    conflicts: int
    signed_rounds: int
    round_count: int

    @property
    def playing(self) -> bool:
        """Telt deze speler mee in de rangschikking?"""
        return self.status == "ok"

    @property
    def done(self) -> bool:
        """Alle ronden getekend."""
        return self.signed_rounds == self.round_count


def leaderboard(db: Session, competition: Competition) -> list[LeaderboardRow]:
    """Stand van een competitie, opgeteld over de ronden.

    Gesorteerd op slagen ten opzichte van par: bij een live stand is het totaal aantal
    slagen alleen zinnig als iedereen even ver is, en dat is tijdens de ronde nooit zo.
    Spelers met DQ, NR of WD staan onderaan.
    """
    rounds = db.scalars(
        select(Round).where(Round.competition_id == competition.id).order_by(Round.no)
    ).all()
    round_count = len(rounds)
    entries = db.scalars(
        select(Entry).where(Entry.round_id.in_([r.id for r in rounds] or [0]))
    ).all()

    by_player: dict[int, list[Entry]] = {}
    for entry in entries:
        by_player.setdefault(entry.player_id, []).append(entry)

    rows: list[LeaderboardRow] = []
    for player_id, player_entries in by_player.items():
        cards = [build_card(e) for e in player_entries]
        status = next((e.status for e in player_entries if e.status != "ok"), "ok")
        rows.append(
            LeaderboardRow(
                player_id=player_id,
                name=player_entries[0].player.name,
                status=status,
                round_totals={
                    e.round.no: c.total
                    for e, c in zip(player_entries, cards, strict=True)
                },
                thru=sum(c.thru for c in cards),
                total=sum(c.total for c in cards),
                to_par=sum(c.to_par for c in cards),
                conflicts=sum(len(c.conflicts) for c in cards),
                signed_rounds=sum(1 for e in player_entries if e.signed_at),
                round_count=round_count,
            )
        )

    rows.sort(key=lambda r: (not r.playing, r.to_par, -r.thru, r.name))
    return rows

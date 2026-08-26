"""Domeinlogica: scores opslaan, conflicten bepalen, kaart tekenen, leaderboard.

Autorisatie zit hier, niet alleen in de templates: `set_score` weigert elke invoer door
iemand die geen recht heeft op die bron.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DEFAULT_PARS,
    HOLES,
    AuditLog,
    Competition,
    Entry,
    HoleScore,
    Round,
    now,
)


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
    def agreed_strokes(self) -> int | None:
        """De score, maar alleen als speler en marker hetzelfde invulden."""
        return self.self_strokes if self.agreed else None

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
    def started(self) -> bool:
        """Is er op deze kaart al iets ingevuld, door wie dan ook?"""
        return any(
            r.self_strokes is not None or r.marker_strokes is not None for r in self.rows
        )

    @property
    def agreed_thru(self) -> int:
        """Aantal holes waarover speler en marker het eens zijn."""
        return sum(1 for r in self.rows if r.agreed)

    @property
    def agreed_total(self) -> int:
        """Totaal over de holes waarover overeenstemming is."""
        return sum(r.agreed_strokes for r in self.rows if r.agreed)

    @property
    def agreed_to_par(self) -> int:
        """Ten opzichte van par, over de holes waarover overeenstemming is."""
        return sum(r.agreed_strokes - r.par for r in self.rows if r.agreed)

    def nine(self, eerste: int, laatste: int) -> int | None:
        """Totaal van een negen, of None zolang er nog een hole open of betwist is."""
        rows = [r for r in self.rows if eerste <= r.hole <= laatste]
        if not all(r.agreed for r in rows):
            return None
        return sum(r.agreed_strokes for r in rows)

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
    pars = entry.round.pars or DEFAULT_PARS
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


def par_klasse(strokes: int | None, par: int) -> str:
    """CSS-klasse van een score ten opzichte van par."""
    if strokes is None:
        return ""
    verschil = strokes - par
    if verschil <= -2:
        return "eagle"
    if verschil == -1:
        return "birdie"
    if verschil == 0:
        return "par"
    if verschil == 1:
        return "bogey"
    return "dubbel"


@dataclass
class LeaderboardRow:
    """Eén speler in één ronde op het leaderboard."""

    name: str
    round_no: int
    status: str
    holes: list[tuple[int | None, str]]
    to_par: int
    thru: int
    out: int | None
    back: int | None
    total: int | None

    @property
    def playing(self) -> bool:
        """Telt deze speler mee in de rangschikking?"""
        return self.status == "ok"


def leaderboard(db: Session, competition: Competition) -> list[LeaderboardRow]:
    """Stand van een competitie, per speler per ronde.

    Een hole telt pas mee als speler en marker dezelfde score invulden. Zolang een ronde
    loopt staat er geen totaal, alleen de stand ten opzichte van par. Spelers die nog geen
    enkele score hebben ingevuld staan er niet bij.
    """
    rows: list[LeaderboardRow] = []
    rounds = db.scalars(
        select(Round).where(Round.competition_id == competition.id).order_by(Round.no)
    ).all()
    for rnd in rounds:
        for entry in db.scalars(select(Entry).where(Entry.round_id == rnd.id)):
            card = build_card(entry)
            if not card.started:
                continue
            rows.append(
                LeaderboardRow(
                    name=entry.player.name,
                    round_no=rnd.no,
                    status=entry.status,
                    holes=[
                        (r.agreed_strokes, par_klasse(r.agreed_strokes, r.par))
                        for r in card.rows
                    ],
                    to_par=card.agreed_to_par,
                    thru=card.agreed_thru,
                    out=card.nine(1, 9),
                    back=card.nine(10, HOLES),
                    total=card.agreed_total if card.agreed_thru == HOLES else None,
                )
            )
    rows.sort(key=lambda r: (not r.playing, r.to_par, -r.thru, r.name))
    return rows

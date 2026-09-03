"""Domeinlogica: scores opslaan, conflicten bepalen, kaart tekenen, leaderboard.

Autorisatie zit hier, niet alleen in de templates: `set_score` weigert elke invoer door
iemand die geen recht heeft op die bron.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
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

    def bron_totaal(self, source: str, eerste: int = 1, laatste: int = HOLES) -> int:
        """Wat één bron invulde over een reeks holes. Nog lege holes tellen als niets.

        Anders dan `nine` wacht dit niet op overeenstemming: op je eigen kaart wil je je
        lopende totaal zien, ook als je marker nog een paar holes achterloopt.
        """
        return sum(
            (r.self_strokes if source == "self" else r.marker_strokes) or 0
            for r in self.rows
            if eerste <= r.hole <= laatste
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
    log(db, entry_actor(actor.id), "score", target.round.competition_id,
        entry=target.id, hole=hole, source=source, strokes=strokes)
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
    log(db, entry_actor(entry.id), "sign", entry.round.competition_id,
        entry=entry.id, total=card.total)
    db.commit()


def user_actor(user_id: int) -> str:
    """De actor-tekst van een wedstrijdleider."""
    return f"user:{user_id}"


def entry_actor(entry_id: int) -> str:
    """De actor-tekst van een speler. Het id is van zijn deelname, niet van de speler zelf."""
    return f"entry:{entry_id}"


def log(
    db: Session, actor: str, action: str, competition_id: int | None = None, **detail
) -> None:
    """Schrijf een regel in de audit log. Commit gebeurt door de aanroeper.

    `competition_id` is de wedstrijd waar de regel over gaat. Daarop filtert de export, en
    daarmee verhuist de geschiedenis mee als een wedstrijd een andere eigenaar krijgt. Een
    regel zonder wedstrijd gaat over de installatie zelf en komt in geen export terecht.

    Bouw `actor` met `user_actor` of `entry_actor` en nooit met de hand: een kaal woord
    zegt niet wie het was, en een los getal niet uit welke tabel het komt.
    """
    db.add(
        AuditLog(actor=actor, action=action, competition_id=competition_id, detail=detail)
    )


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
    """Eén speler in één ronde op het leaderboard.

    De velden zonder `prev` gaan over de getoonde ronde. `earlier` is het totaal van elke
    eerdere ronde op volgorde, met None voor een ronde die deze speler niet speelde; die
    lijst is voor iedereen even lang, zodat de kolommen op het bord uitlijnen.
    """

    name: str
    round_no: int
    status: str
    holes: list[tuple[int | None, str]]
    to_par: int
    thru: int
    out: int | None
    back: int | None
    total: int | None
    earlier: list[int | None] = field(default_factory=list)
    prev_to_par: int = 0

    @property
    def playing(self) -> bool:
        """Telt deze speler mee in de rangschikking?"""
        return self.status == "ok"

    @property
    def heeft_resultaat(self) -> bool:
        """Valt er iets te rangschikken: een bevestigde hole of een eerdere ronde?"""
        return self.thru > 0 or self.prev_total is not None

    @property
    def prev_total(self) -> int | None:
        """Slagen uit alle eerdere ronden samen, of None als de speler er geen speelde."""
        bekend = [t for t in self.earlier if t is not None]
        return sum(bekend) if bekend else None

    @property
    def total_to_par(self) -> int:
        """Stand ten opzichte van par over alle ronden samen. Hierop wordt gerangschikt."""
        return self.prev_to_par + self.to_par

    @property
    def grand_total(self) -> int | None:
        """Slagen over alle ronden samen, pas zodra deze ronde helemaal rond is."""
        if self.total is None:
            return None
        return (self.prev_total or 0) + self.total


def _rangorde(row: LeaderboardRow) -> tuple[int, int, int, str]:
    """Sorteersleutel van het bord: eerst wie een resultaat heeft, dan wie nog moet beginnen.

    Wie nog geen bevestigde hole heeft valt niet te rangschikken. Zijn stand is nul, en op
    stand gesorteerd zou hij tussen de leiders belanden. Die spelers krijgen daarom allemaal
    dezelfde sleutel en houden door de stabiele sort de volgorde waarin ze in de database
    staan, oftewel die van de startlijst. Uitvallers zakken hoe dan ook naar de bodem.
    """
    if not row.heeft_resultaat:
        return (1 if row.playing else 3, 0, 0, "")
    return (0 if row.playing else 2, row.total_to_par, -row.thru, row.name)


def huidige_ronde(db: Session, competition: Competition, keuze: int | None = None) -> int:
    """Welke ronde het bord toont.

    Met een expliciete keuze die ronde, zolang die bestaat. Zonder keuze de laatste ronde
    waarin al iets is ingevuld, zodat de gedeelde link vanzelf meeschuift naar de ronde die
    nu gespeeld wordt.
    """
    nos = [r.no for r in competition.rounds]
    if not nos:
        return 1
    if keuze in nos:
        return keuze
    laatste = db.scalar(
        select(func.max(Round.no))
        .join(Entry, Entry.round_id == Round.id)
        .join(HoleScore, HoleScore.entry_id == Entry.id)
        .where(Round.competition_id == competition.id, HoleScore.strokes.is_not(None))
    )
    return laatste or min(nos)


def eerdere_rondenummers(competition: Competition, round_no: int) -> list[int]:
    """De ronden vóór deze, op volgorde. Bepaalt de kolommen en hun volgorde op het bord."""
    return [r.no for r in competition.rounds if r.no < round_no]


@dataclass
class Eerder:
    """Wat een speler meebrengt uit de ronden vóór de getoonde ronde."""

    totals: list[int | None]
    to_par: int
    status: str

    @property
    def gespeeld(self) -> bool:
        """Heeft hij in een eerdere ronde een score neergezet?"""
        return any(t is not None for t in self.totals)


def _eerdere_ronden(
    db: Session, competition: Competition, round_no: int
) -> dict[int, Eerder]:
    """Per speler zijn totaal per eerdere ronde, zijn stand t.o.v. par en zijn status.

    Ook hier telt alleen wat speler en marker samen hebben goedgekeurd, net als in de
    ronde die nu loopt. Een ronde die de speler niet speelde blijft None. Wie in een eerdere
    ronde uitviel draagt die status mee: DQ, NR en WD gelden voor de hele wedstrijd, niet
    alleen voor de ronde waarin ze zijn gezet.
    """
    nummers = eerdere_rondenummers(competition, round_no)
    per_ronde = {r.id: r.no for r in competition.rounds if r.no < round_no}
    totalen: dict[int, dict[int, int]] = {}
    tegen_par: dict[int, int] = {}
    statussen: dict[int, str] = {}
    for entry in db.scalars(select(Entry).where(Entry.round_id.in_(list(per_ronde)))):
        if entry.status != "ok":
            statussen[entry.player_id] = entry.status
        card = build_card(entry)
        if not card.started:
            continue
        totalen.setdefault(entry.player_id, {})[per_ronde[entry.round_id]] = card.agreed_total
        tegen_par[entry.player_id] = tegen_par.get(entry.player_id, 0) + card.agreed_to_par
    return {
        player_id: Eerder(
            totals=[totalen.get(player_id, {}).get(no) for no in nummers],
            to_par=tegen_par.get(player_id, 0),
            status=statussen.get(player_id, "ok"),
        )
        for player_id in set(totalen) | set(statussen)
    }


def leaderboard(
    db: Session, competition: Competition, round_no: int = 1
) -> list[LeaderboardRow]:
    """Stand van één ronde van een competitie.

    Een hole telt pas mee als speler en marker dezelfde score invulden. Zolang een ronde
    loopt staat er geen totaal, alleen de stand ten opzichte van par. Vanaf ronde 2 draagt
    elke speler het resultaat van zijn eerdere ronden mee: daarop wordt gerangschikt, ook
    als hij vandaag nog moet starten. Het hele veld van deze ronde staat op het bord, ook
    wie nog niets heeft: die spelers staan onderaan, op de volgorde van de startlijst.
    """
    rnd = db.scalar(
        select(Round).where(Round.competition_id == competition.id, Round.no == round_no)
    )
    if rnd is None:
        return []
    eerder = _eerdere_ronden(db, competition, round_no)
    leeg = Eerder([None] * len(eerdere_rondenummers(competition, round_no)), 0, "ok")
    rows: list[LeaderboardRow] = []
    # Op id, want dat is de volgorde waarin de startlijst is ingelezen: flight na flight.
    for entry in db.scalars(select(Entry).where(Entry.round_id == rnd.id).order_by(Entry.id)):
        card = build_card(entry)
        vorig = eerder.get(entry.player_id, leeg)
        rows.append(
            LeaderboardRow(
                name=entry.player.name,
                round_no=rnd.no,
                status=entry.status if entry.status != "ok" else vorig.status,
                holes=[
                    (r.agreed_strokes, par_klasse(r.agreed_strokes, r.par))
                    for r in card.rows
                ],
                to_par=card.agreed_to_par,
                thru=card.agreed_thru,
                out=card.nine(1, 9),
                back=card.nine(10, HOLES),
                total=card.agreed_total if card.agreed_thru == HOLES else None,
                earlier=list(vorig.totals),
                prev_to_par=vorig.to_par,
            )
        )
    rows.sort(key=_rangorde)
    return rows

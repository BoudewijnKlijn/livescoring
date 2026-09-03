"""SQLAlchemy-modellen en database-setup.

Acht tabellen: app_user, competition, player, round, flight, entry, hole_score, audit_log.
Een `entry` is een speler in een ronde: daaraan hangt het token, de kaart en de status.
Statussen zijn tekstkolommen met een check constraint in plaats van native enums, zodat het
schema zonder migratietooling te wijzigen is.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings

HOLES = 18
# Pars van de thuisbaan. Per ronde aan te passen op het beheerscherm.
DEFAULT_PARS = [5, 4, 4, 3, 5, 4, 4, 3, 4, 5, 4, 3, 4, 4, 4, 4, 3, 5]
SOURCES = ("self", "marker")
ENTRY_STATUSES = ("ok", "dq", "nr", "wd")
# Geen geldige scrypt-hash, dus er valt niet mee in te loggen. Een account dat hierop staat
# is aangemaakt door de migratie en wacht tot de eigenaar het opeist via /admin/registreren.
GEEN_WACHTWOORD = "geen-wachtwoord"


def now() -> dt.datetime:
    """Huidige tijd in UTC, timezone-aware."""
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    """Basisklasse voor alle modellen."""

    type_annotation_map = {dict[str, Any]: JSON, list[int]: JSON}


class User(Base):
    """Een wedstrijdleider met een eigen account. Ziet alleen zijn eigen wedstrijden.

    De tabel heet `app_user`, want `user` is een gereserveerd woord in Postgres. Van het
    wachtwoord staat alleen een scrypt-hash in de database, van de bevestigingslink alleen
    een sha256-hash. Zolang `confirmed_at` leeg is kan er niet ingelogd worden.
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    competitions: Mapped[list[Competition]] = relationship(back_populates="owner")


class Competition(Base):
    """Een clubkampioenschap, bestaand uit een of meer ronden."""

    __tablename__ = "competition"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="setup")
    leaderboard_slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    owner: Mapped[User] = relationship(back_populates="competitions")
    rounds: Mapped[list[Round]] = relationship(
        back_populates="competition", cascade="all, delete-orphan", order_by="Round.no"
    )
    players: Mapped[list[Player]] = relationship(
        back_populates="competition", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("status in ('setup', 'live', 'closed')", name="ck_competition_status"),
    )


class Player(Base):
    """Een deelnemer binnen een competitie. Per competitie een eigen rij."""

    __tablename__ = "player"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    competition: Mapped[Competition] = relationship(back_populates="players")
    entries: Mapped[list[Entry]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )


class Round(Base):
    """Een speelronde binnen een competitie."""

    __tablename__ = "round"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id", ondelete="CASCADE"))
    no: Mapped[int] = mapped_column(Integer)
    pars: Mapped[list[int]] = mapped_column(JSON, default=lambda: list(DEFAULT_PARS))

    competition: Mapped[Competition] = relationship(back_populates="rounds")
    flights: Mapped[list[Flight]] = relationship(
        back_populates="round", cascade="all, delete-orphan", order_by="Flight.name"
    )
    entries: Mapped[list[Entry]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("competition_id", "no", name="uq_round_competition_no"),)


class Flight(Base):
    """Een groepje spelers dat samen loopt, binnen een ronde."""

    __tablename__ = "flight"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("round.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(50))
    start_hole: Mapped[int] = mapped_column(Integer, default=1)

    round: Mapped[Round] = relationship(back_populates="flights")
    entries: Mapped[list[Entry]] = relationship(
        back_populates="flight", order_by="Entry.position"
    )

    __table_args__ = (
        UniqueConstraint("round_id", "name", name="uq_flight_round_name"),
        CheckConstraint("start_hole in (1, 10)", name="ck_flight_start_hole"),
    )


class Entry(Base):
    """Een speler in een ronde: token, flight, marker, kaartstatus."""

    __tablename__ = "entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("round.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"))
    flight_id: Mapped[int] = mapped_column(ForeignKey("flight.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    marker_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("entry.id", ondelete="SET NULL"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(10), default="ok")
    signed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    round: Mapped[Round] = relationship(back_populates="entries")
    player: Mapped[Player] = relationship(back_populates="entries")
    flight: Mapped[Flight] = relationship(back_populates="entries")
    # post_update: twee spelers markeren elkaar, dus de rijen wijzen naar elkaar. Zonder dit
    # kan SQLAlchemy die twee updates niet op volgorde zetten en klapt de flush.
    marker: Mapped[Entry | None] = relationship(
        remote_side=[id], foreign_keys=[marker_entry_id], post_update=True
    )
    scores: Mapped[list[HoleScore]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        foreign_keys="HoleScore.entry_id",
    )

    __table_args__ = (
        UniqueConstraint("round_id", "player_id", name="uq_entry_round_player"),
        CheckConstraint("status in ('ok', 'dq', 'nr', 'wd')", name="ck_entry_status"),
        CheckConstraint("marker_entry_id is null or marker_entry_id <> id", name="ck_entry_marker"),
    )


class HoleScore(Base):
    """Een ingevoerde score voor een hole, per bron (speler zelf of zijn marker)."""

    __tablename__ = "hole_score"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entry.id", ondelete="CASCADE"), index=True)
    hole: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(10))
    strokes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entered_by_entry_id: Mapped[int] = mapped_column(ForeignKey("entry.id", ondelete="CASCADE"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )

    entry: Mapped[Entry] = relationship(back_populates="scores", foreign_keys=[entry_id])

    __table_args__ = (
        UniqueConstraint("entry_id", "hole", "source", name="uq_hole_score"),
        CheckConstraint("hole >= 1 and hole <= 18", name="ck_hole_score_hole"),
        CheckConstraint("source in ('self', 'marker')", name="ck_hole_score_source"),
        CheckConstraint("strokes is null or (strokes >= 1 and strokes <= 20)", name="ck_strokes"),
    )


class AuditLog(Base):
    """Logregel voor elke mutatie die niet triviaal terug te draaien is.

    Hangt aan de wedstrijd en niet aan de wedstrijdleider: die kan wisselen, de wedstrijd
    niet. Daarop filtert de export, zodat niemand de correcties van een ander te zien
    krijgt, en zo verhuist de geschiedenis mee bij een overdracht. Leeg = een regel over de
    installatie zelf, zoals een aanmelding; die staat in geen enkele export. Verdwijnt de
    wedstrijd, dan zegt zijn geschiedenis niets meer en gaat die mee.

    `actor` is altijd `soort:id` -- `user:{app_user.id}` of `entry:{entry.id}` -- en nooit
    een kaal woord. Beide tabellen tellen vanaf 1, dus zonder dat voorvoegsel is `39` uit de
    ene niet te onderscheiden van `39` uit de andere en wijst een regel de verkeerde persoon
    aan. Wie iets deed staat er los van waar het over ging: bij twee wedstrijdleiders op één
    wedstrijd is dat niet meer uit de eigenaar af te leiden.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    competition_id: Mapped[int | None] = mapped_column(
        ForeignKey("competition.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


engine = create_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    connect_args={"prepare_threshold": None} if "psycopg" in settings.database_url else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


# Kolommen die na de eerste versie zijn bijgekomen. `create_all` maakt alleen ontbrekende
# tabellen aan, geen ontbrekende kolommen, en een draaiende database heeft de tabel al.
NIEUWE_KOLOMMEN = {
    "competition": {
        "user_id": (
            "alter table competition add column if not exists user_id integer "
            "references app_user(id) on delete cascade",
            "create index if not exists ix_competition_user_id on competition (user_id)",
        )
    },
    "audit_log": {
        "competition_id": (
            "alter table audit_log add column if not exists competition_id integer "
            "references competition(id) on delete cascade",
            "create index if not exists ix_audit_log_competition_id on audit_log "
            "(competition_id)",
        )
    },
}


def create_all() -> None:
    """Maak ontbrekende tabellen en kolommen aan. Vervangt Alembic voor dit project."""
    Base.metadata.create_all(engine)
    _voeg_kolommen_toe()
    _geef_wedstrijden_een_eigenaar()
    _verhuis_auditlog_naar_de_wedstrijd()


def _voeg_kolommen_toe() -> None:
    """Voeg kolommen toe die een oudere database nog niet heeft.

    Eerst kijken, dan pas wijzigen. Een `alter table` neemt de zwaarste lock die er is, en
    die blind bij elke start uitvoeren zet de app achter elke lezer in de rij. Op een
    database die al bij is gebeurt er nu niets.
    """
    inspecteur = inspect(engine)
    for tabel, kolommen in NIEUWE_KOLOMMEN.items():
        bestaand = {kolom["name"] for kolom in inspecteur.get_columns(tabel)}
        opdrachten = [
            opdracht
            for naam, groep in kolommen.items()
            if naam not in bestaand
            for opdracht in groep
        ]
        if not opdrachten:
            continue
        with engine.begin() as conn:
            for opdracht in opdrachten:
                conn.execute(text(opdracht))


def _geef_wedstrijden_een_eigenaar() -> None:
    """Zet de wedstrijden van voor de accounts op naam van `OWNER_EMAIL`.

    Elke wedstrijd hoort bij een wedstrijdleider, maar de wedstrijden die er al waren
    hebben nog niemand: die stonden achter het beheerderswachtwoord dat er niet meer is.
    Die krijgen hier hun eigenaar, waarna de kolom verplicht wordt.

    Bestaat dat account nog niet, dan wordt het aangemaakt zonder bruikbaar wachtwoord en
    zonder bevestiging. De eigenaar meldt zich daarna gewoon aan op `/admin/registreren`
    met datzelfde adres: dat is de weg voor een adres dat nog niet bevestigd is, en hij
    kiest zijn wachtwoord dus zelf. Zo staat er nergens een wachtwoord in een instelling.

    De kolom die nog leeg mag zijn is het enige wat hier gecontroleerd wordt. Is die
    verplicht, dan is deze database bij en gebeurt er niets meer.
    """
    if not _mag_leeg_zijn("competition", "user_id"):
        return
    with SessionLocal() as db:
        _zet_eigenaar(db)
    with engine.begin() as conn:
        conn.execute(text("alter table competition alter column user_id set not null"))


def _mag_leeg_zijn(tabel: str, kolom: str) -> bool:
    """Of een kolom nog null toestaat."""
    return any(k["nullable"] for k in inspect(engine).get_columns(tabel) if k["name"] == kolom)


def _zet_eigenaar(db) -> None:
    """Geef elke wedstrijd zonder eigenaar het account van `OWNER_EMAIL`."""
    zonder = db.scalars(select(Competition).where(Competition.user_id.is_(None))).all()
    if not zonder:
        return
    adres = settings.owner_email.strip().lower()
    if not adres:
        raise RuntimeError(
            f"{len(zonder)} wedstrijd(en) hebben nog geen eigenaar. Zet OWNER_EMAIL op het "
            "e-mailadres van de wedstrijdleiding en start opnieuw."
        )
    eigenaar = db.scalar(select(User).where(User.email == adres))
    if eigenaar is None:
        eigenaar = User(email=adres, password_hash=GEEN_WACHTWOORD)
        db.add(eigenaar)
        db.flush()
    for competition in zonder:
        competition.user_id = eigenaar.id
    db.commit()


# De verhuizing van de audit log van de wedstrijdleider naar de wedstrijd, op volgorde. De
# wedstrijd staat per actie onder een andere sleutel in `detail`, dus dat zijn vier pogingen
# in plaats van één. Wat daarna nog geen wedstrijd heeft ging over een wedstrijd die niet
# meer bestaat, en verdwijnt. Pas daarna de actoren: dat scheelt werk op regels die weggaan.
VERHUIZING = (
    """update audit_log a set competition_id = c.id from competition c
       where a.competition_id is null and c.id = (a.detail::jsonb->>'competition')::int""",
    """update audit_log a set competition_id = r.competition_id
       from entry e join round r on r.id = e.round_id
       where a.competition_id is null and e.id = (a.detail::jsonb->>'entry')::int""",
    """update audit_log a set competition_id = r.competition_id from round r
       where a.competition_id is null and r.id = (a.detail::jsonb->>'round')::int""",
    # `competition_created` legde alleen de naam vast. Bij twee wedstrijden met dezelfde
    # naam is niet te zeggen welke het was, en dan is niets invullen het eerlijke antwoord.
    """update audit_log a set competition_id = c.id from competition c
       where a.competition_id is null and a.action = 'competition_created'
         and c.name = a.detail::jsonb->>'name'
         and (select count(*) from competition c2 where c2.name = c.name) = 1""",
    """delete from audit_log
       where competition_id is null and action not in ('registered', 'confirmed')""",
    # `player:` telde altijd al een deelname, niet een speler. Alleen de naam was mis.
    """update audit_log set actor = 'entry:' || split_part(actor, ':', 2)
       where actor like 'player:%'""",
    """update audit_log set actor = 'user:' || user_id
       where actor not like '%:%' and user_id is not null""",
    """update audit_log a set actor = 'user:' || u.id from app_user u
       where a.actor not like '%:%' and u.email = a.detail::jsonb->>'email'""",
    # Een database van voor de accounts heeft nergens een `user_id`. Daar was één beheerder,
    # dus is de eigenaar van de wedstrijd degene die het deed.
    """update audit_log a set actor = 'user:' || c.user_id from competition c
       where a.actor not like '%:%' and c.id = a.competition_id""",
    "drop index if exists ix_audit_log_user_id",
    "alter table audit_log drop column user_id",
)


def _verhuis_auditlog_naar_de_wedstrijd() -> None:
    """Zet de audit log over van de wedstrijdleider naar de wedstrijd.

    Vroeger hing elke regel aan een `user_id`: een kopie van de eigenaar op het moment van
    schrijven. Die kopie liep achter zodra een wedstrijd van eigenaar wisselde, en hij gaf
    geen antwoord op de vraag die je stelt -- wat is er in deze wedstrijd gebeurd. Dat werd
    afgeleid uit `detail`, maar daar staat een deelname in, en die verdwijnt bij het wissen
    van de spelers. Nu staat de wedstrijd zelf op de regel en is er niets meer af te leiden.

    Alles in één transactie: half verhuisd is erger dan niet verhuisd. Blijft er een kale
    actor over, dan is dat een account dat niet meer bestaat; die regel liegt liever niet.
    """
    if "user_id" not in {k["name"] for k in inspect(engine).get_columns("audit_log")}:
        return
    with engine.begin() as conn:
        for opdracht in VERHUIZING:
            conn.execute(text(opdracht))


def get_db():
    """FastAPI-dependency die een sessie opent en altijd sluit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

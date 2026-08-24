"""SQLAlchemy-modellen en database-setup.

Zeven tabellen: competition, player, round, flight, entry, hole_score, audit_log.
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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings

HOLES = 18
SOURCES = ("self", "marker")
ENTRY_STATUSES = ("ok", "dq", "nr", "wd")


def now() -> dt.datetime:
    """Huidige tijd in UTC, timezone-aware."""
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    """Basisklasse voor alle modellen."""

    type_annotation_map = {dict[str, Any]: JSON, list[int]: JSON}


class Competition(Base):
    """Een clubkampioenschap, bestaand uit een of meer ronden."""

    __tablename__ = "competition"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="setup")
    leaderboard_slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

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
    pars: Mapped[list[int]] = mapped_column(JSON, default=lambda: [4] * HOLES)

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
    marker: Mapped[Entry | None] = relationship(remote_side=[id], foreign_keys=[marker_entry_id])
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
    """Logregel voor elke mutatie die niet triviaal terug te draaien is."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
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


def create_all() -> None:
    """Maak ontbrekende tabellen aan. Vervangt Alembic voor dit project."""
    Base.metadata.create_all(engine)


def get_db():
    """FastAPI-dependency die een sessie opent en altijd sluit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

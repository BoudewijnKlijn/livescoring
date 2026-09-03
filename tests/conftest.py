"""Testfixtures. Draait tegen een aparte testdatabase op dezelfde lokale Postgres."""

from __future__ import annotations

import os

TEST_DB = "livescoring_test"
BASE = "postgresql+psycopg://livescoring:livescoring@localhost:5434"
os.environ["DATABASE_URL"] = f"{BASE}/{TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = "false"  # TestClient praat http, geen https
# Mail hard uit. Zonder dit pikken de tests de sleutel uit .env.local op en stuurt elke test
# die /me/sign aanroept een echte mail naar een verzonnen adres als jan@x.nl. Die bouncen,
# en bounces kosten je de reputatie van je afzender, precies wanneer je hem nodig hebt.
os.environ["BREVO_API_KEY"] = ""
os.environ["MAIL_FROM"] = ""

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _ensure_database() -> None:
    """Maak de testdatabase aan als die nog niet bestaat."""
    dsn = "postgresql://livescoring:livescoring@localhost:5434/livescoring"
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            exists = conn.execute(
                "select 1 from pg_database where datname = %s", (TEST_DB,)
            ).fetchone()
            if not exists:
                conn.execute(f'create database "{TEST_DB}"')
    except psycopg.OperationalError as exc:  # pragma: no cover
        pytest.exit(
            f"Lokale Postgres niet bereikbaar ({exc}). Start hem met: docker compose up -d",
            returncode=1,
        )


_ensure_database()

from app.account import hash_password  # noqa: E402
from app.importer import create_competition, import_csv  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, SessionLocal, User, engine, now  # noqa: E402
from tests.helpers import LEIDING, WACHTWOORD  # noqa: E402

# Eén keer hashen voor de hele run: scrypt is met opzet traag, en elke test heeft een
# wedstrijdleider nodig omdat elke wedstrijd een eigenaar heeft.
WACHTWOORD_HASH = hash_password(WACHTWOORD)

CSV = """naam,email,ronde,flight,starthole,marker
Jan,jan@x.nl,1,A,1,Piet
Piet,piet@x.nl,1,A,1,Jan
Anne,anne@x.nl,1,B,10,Kees
Kees,kees@x.nl,1,B,10,Anne
"""


@pytest.fixture
def db():
    """Verse database per test."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        yield session


@pytest.fixture
def gebruiker(db):
    """Een bevestigde wedstrijdleider, de eigenaar van de wedstrijden in de tests."""
    user = User(email=LEIDING, password_hash=WACHTWOORD_HASH, confirmed_at=now())
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def wedstrijd(db, gebruiker):
    """Een competitie met vier spelers, twee flights en markers over en weer."""
    competition = create_competition(db, "Testwedstrijd", gebruiker)
    result = import_csv(db, competition, CSV)
    assert result.ok, result.errors
    tokens = {name: token for name, _, token in result.new_links}
    return competition, tokens


TWEE_RONDEN = """naam,email,ronde,flight,starthole,marker
Jan,jan@x.nl,1,A,1,Piet
Piet,piet@x.nl,1,A,1,Jan
Jan,jan@x.nl,2,A,1,Piet
Piet,piet@x.nl,2,A,1,Jan
"""


@pytest.fixture
def toernooi(db, gebruiker):
    """Twee spelers die elkaars marker zijn, in twee ronden."""
    competition = create_competition(db, "Clubkampioenschap", gebruiker)
    result = import_csv(db, competition, TWEE_RONDEN)
    assert result.ok, result.errors
    db.refresh(competition)
    return competition, {(naam, ronde): token for naam, ronde, token in result.new_links}


@pytest.fixture
def client():
    """HTTP-client zonder ingelogde gebruiker."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def als_speler(client, wedstrijd):
    """Log in als speler op naam en geef de client terug."""

    def _login(naam: str) -> TestClient:
        _, tokens = wedstrijd
        client.cookies.clear()
        response = client.get(f"/t/{tokens[naam]}", follow_redirects=False)
        assert response.status_code == 303
        return client

    return _login

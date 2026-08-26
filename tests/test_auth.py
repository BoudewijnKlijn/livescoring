"""Autorisatie per request. De belangrijkste tests van dit project."""

from __future__ import annotations

import re

from sqlalchemy import select

from app.auth import new_token
from app.models import Competition, Entry, Player


def _entry_van(db, naam: str) -> Entry:
    player = db.scalar(select(Player).where(Player.name == naam))
    return db.scalar(select(Entry).where(Entry.player_id == player.id))


def test_score_invoeren_voor_speler_zonder_marker_rol_faalt(db, wedstrijd, als_speler):
    """Jan markt Piet, niet Anne. Een score voor Anne moet geweigerd worden."""
    client = als_speler("Jan")
    anne = _entry_van(db, "Anne")

    response = client.post(
        "/api/score",
        data={"entry_id": anne.id, "hole": 1, "source": "marker", "strokes": 4},
    )

    assert response.status_code == 422
    db.expire_all()
    assert _entry_van(db, "Anne").scores == []


def test_eigen_score_van_ander_invoeren_faalt(db, wedstrijd, als_speler):
    """Ook met source 'self' mag je niet in andermans kaart schrijven."""
    client = als_speler("Jan")
    piet = _entry_van(db, "Piet")

    response = client.post(
        "/api/score",
        data={"entry_id": piet.id, "hole": 1, "source": "self", "strokes": 3},
    )

    assert response.status_code == 422
    db.expire_all()
    assert _entry_van(db, "Piet").scores == []


def test_ongeldig_token_geeft_401(client):
    """Een verzonnen token logt niemand in."""
    response = client.get("/t/dit-token-bestaat-niet", follow_redirects=False)

    assert response.status_code == 401
    assert "niet (meer) geldig" in response.text


def test_geroteerd_token_geeft_401(db, wedstrijd, client):
    """Na het vervangen van een link werken de oude link en de oude sessie niet meer."""
    _, tokens = wedstrijd
    oud = tokens["Jan"]
    assert client.get(f"/t/{oud}", follow_redirects=False).status_code == 303
    assert client.get("/me/card").status_code == 200

    jan = _entry_van(db, "Jan")
    _, nieuwe_hash = new_token()
    jan.token_hash = nieuwe_hash
    db.commit()

    assert client.get("/me/card").status_code == 401
    assert client.get(f"/t/{oud}", follow_redirects=False).status_code == 401


def test_zonder_cookie_geen_toegang(client):
    """Spelerspagina's zijn niet publiek."""
    assert client.get("/me/card").status_code == 401
    assert client.get("/me/card").status_code == 401
    assert client.get("/admin").status_code == 401


def test_leaderboard_is_publiek_op_slug(client, wedstrijd):
    """Het leaderboard mag zonder cookie, maar alleen met de juiste slug."""
    competition, _ = wedstrijd

    assert client.get(f"/l/{competition.leaderboard_slug}").status_code == 200
    assert client.get("/l/bestaat-niet").status_code == 401


def test_admin_wist_alle_spelers(db, wedstrijd, client):
    """Alles verwijderen maakt de competitie leeg, maar laat de wedstrijd zelf staan.

    De spelers zijn elkaars marker, dus de verwijzingen moeten eerst los voordat de rijen
    weg kunnen.
    """
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})
    pagina = client.get(f"/admin/c/{competition.id}")
    assert "Jan" in pagina.text

    code = re.search(r'name="verwacht" value="([A-Z]{4})"', pagina.text).group(1)
    antwoord = client.post(
        f"/admin/c/{competition.id}/wissen",
        data={"verwacht": code, "code": code},
        follow_redirects=False,
    )

    assert antwoord.status_code == 303
    db.expire_all()
    ververst = db.get(Competition, competition.id)
    assert ververst is not None, "de competitie zelf blijft bestaan"
    assert ververst.rounds == []
    assert ververst.players == []
    assert db.scalars(select(Entry)).all() == []


def test_wissen_zonder_juiste_code_verandert_niets(db, wedstrijd, client):
    """Een verkeerde bevestigingscode laat alles staan."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    antwoord = client.post(
        f"/admin/c/{competition.id}/wissen", data={"verwacht": "ABCD", "code": "ZZZZ"}
    )

    assert antwoord.status_code == 400
    db.expire_all()
    assert db.scalars(select(Entry)).all() != []


def test_mislukte_import_toont_de_spelers_gewoon(db, wedstrijd, client):
    """Een geweigerde import laat de spelerslijst staan.

    Anders lijkt het alsof iedereen verdwenen is terwijl er niets is gewijzigd, en dat is
    op een wedstrijddag precies het moment waarop iemand in paniek opnieuw gaat importeren.
    """
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})
    voor = len(db.scalars(select(Entry)).all())

    antwoord = client.post(f"/admin/c/{competition.id}/import", data={"csv_tekst": ""})

    assert antwoord.status_code == 422
    assert "niet uitgevoerd" in antwoord.text
    assert "Jan" in antwoord.text, "de spelerslijst hoort er nog te staan"
    assert "Nog geen spelers" not in antwoord.text
    db.expire_all()
    assert len(db.scalars(select(Entry)).all()) == voor


def test_import_met_fout_in_een_regel_wijzigt_niets(db, wedstrijd, client):
    """Bij een fout in het bestand blijft alles zoals het was."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})
    voor = len(db.scalars(select(Entry)).all())

    antwoord = client.post(
        f"/admin/c/{competition.id}/import",
        data={"csv_tekst": "naam,ronde,flight,marker\nNieuw Iemand,1,Z,Bestaat Niet"},
    )

    assert antwoord.status_code == 422
    assert "Jan" in antwoord.text
    db.expire_all()
    assert len(db.scalars(select(Entry)).all()) == voor

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


def test_admin_zonder_cookie_gaat_naar_het_inlogscherm(client):
    """Wie /admin intikt wil inloggen, niet lezen dat het niet mag."""
    for pad in ("/admin", "/admin/c/1", "/admin/c/1/export.csv"):
        antwoord = client.get(pad, follow_redirects=False)
        assert antwoord.status_code == 303, pad
        assert antwoord.headers["location"] == "/admin/login", pad

    assert client.get("/admin").url.path == "/admin/login", "en volgt hij hem, dan staat hij er"


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


def test_links_zijn_tabgescheiden_voor_excel(db, wedstrijd, client):
    """De linkenlijst plakt in kolommen: tabs tussen naam, ronde en link."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    antwoord = client.post(
        f"/admin/c/{competition.id}/import",
        data={"csv_tekst": "naam,ronde,flight,marker\nNieuw Een,2,Z,Nieuw Twee\n"
                           "Nieuw Twee,2,Z,Nieuw Een"},
    )

    assert antwoord.status_code == 200
    assert "Naam\tRonde\tLink" in antwoord.text
    assert re.search(r"Nieuw Een\t2\thttp\S+/t/\S+", antwoord.text)


def test_wedstrijd_verbergen_en_terugzetten(db, wedstrijd, client):
    """Verbergen haalt een wedstrijd uit de lijst zonder iets te verwijderen."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    client.post(f"/admin/c/{competition.id}/verbergen", follow_redirects=False)

    db.expire_all()
    verborgen = db.get(Competition, competition.id)
    assert verborgen.status == "closed"
    assert verborgen.players != [], "de gegevens blijven staan"
    overzicht = client.get("/admin").text
    assert "verborgen wedstrijd" in overzicht

    client.post(f"/admin/c/{competition.id}/verbergen", data={"terug": "ja"})

    db.expire_all()
    assert db.get(Competition, competition.id).status == "live"


def test_verborgen_wedstrijd_houdt_werkend_leaderboard(db, wedstrijd, client):
    """De leaderboardlink blijft werken; verbergen is alleen opruimen in het admingedeelte."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})
    client.post(f"/admin/c/{competition.id}/verbergen")

    assert client.get(f"/l/{competition.leaderboard_slug}").status_code == 200


def test_nieuwe_link_voor_een_speler_laat_de_rest_werken(db, wedstrijd, client):
    """Eén speler krijgt een nieuwe link; de anderen houden hun oude link en hun scores."""
    competition, tokens = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})
    jan = _entry_van(db, "Jan")
    pagina = client.get(f"/admin/c/{competition.id}").text
    code = re.search(r'name="verwacht" value="([A-Z]{4})"', pagina).group(1)

    antwoord = client.post(
        f"/admin/c/{competition.id}/rotate",
        data={"scope": "entry", "entry_id": jan.id, "verwacht": code, "code": code},
    )

    assert antwoord.status_code == 200
    nieuw = re.search(r"/t/([A-Za-z0-9_-]{20,})", antwoord.text).group(1)
    assert nieuw != tokens["Jan"]

    client.cookies.clear()
    assert client.get(f"/t/{tokens['Jan']}", follow_redirects=False).status_code == 401
    assert client.get(f"/t/{nieuw}", follow_redirects=False).status_code == 303
    client.cookies.clear()
    assert client.get(f"/t/{tokens['Piet']}", follow_redirects=False).status_code == 303


def test_nieuwe_link_vraagt_altijd_om_de_code(db, wedstrijd, client):
    """Zonder de juiste code verandert er niets aan de link."""
    competition, tokens = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})
    jan = _entry_van(db, "Jan")

    antwoord = client.post(
        f"/admin/c/{competition.id}/rotate",
        data={"scope": "entry", "entry_id": jan.id, "verwacht": "ABCD", "code": "ZZZZ"},
    )

    assert antwoord.status_code == 400
    client.cookies.clear()
    assert client.get(f"/t/{tokens['Jan']}", follow_redirects=False).status_code == 303


def test_lege_spelerkeuze_vervangt_niet_stilzwijgend_alle_links(db, wedstrijd, client):
    """Zonder gekozen speler gebeurt er niets.

    Viel dit terug op de hele competitie, dan raakt iedereen zijn link kwijt omdat er één
    speler geholpen moest worden.
    """
    competition, tokens = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})
    pagina = client.get(f"/admin/c/{competition.id}").text
    code = re.search(r'name="verwacht" value="([A-Z]{4})"', pagina).group(1)

    antwoord = client.post(
        f"/admin/c/{competition.id}/rotate",
        data={"scope": "entry", "entry_id": "", "verwacht": code, "code": code},
    )

    assert antwoord.status_code == 400
    client.cookies.clear()
    for naam in ("Jan", "Piet", "Anne", "Kees"):
        assert client.get(f"/t/{tokens[naam]}", follow_redirects=False).status_code == 303
        client.cookies.clear()


def test_beheerpagina_groepeert_alle_functies_in_panelen(db, wedstrijd, client):
    """Alles dichtgeklapt onder een eigen titel, gegroepeerd naar wanneer je het nodig hebt."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    pagina = client.get(f"/admin/c/{competition.id}").text

    groepen = re.findall(r'<h2 class="groep">(.*?)</h2>', pagina)
    ruw = re.findall(r"<summary>(.*?)</summary>", pagina, re.S)
    titels = [re.sub(r"<[^>]+>", "", t).strip() for t in ruw]
    assert groepen == ["Tijdens de wedstrijd", "Opzetten en afronden", "Onomkeerbaar"]
    assert titels[0].startswith("Spelers"), "de spelerslijst staat bovenaan"
    assert titels[-1] == "Alle spelers en scores verwijderen", "het gevaarlijkste onderaan"
    assert len(titels) == 12, titels
    assert 'class="paneel gevaarlijk"' in pagina
    assert "open>" not in pagina, "niets staat open bij het laden"


def test_onomkeerbare_acties_staan_onder_de_dagelijkse(db, wedstrijd, client):
    """Alles verwijderen mag niet meer verstopt zitten onder de spelerslijst."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    pagina = client.get(f"/admin/c/{competition.id}").text

    assert pagina.index("Score corrigeren") < pagina.index('<h2 class="groep">Onomkeerbaar</h2>')
    assert pagina.index('<h2 class="groep">Onomkeerbaar</h2>') < pagina.index("/wissen")


def test_importpaneel_gaat_open_als_de_import_faalt(db, wedstrijd, client):
    """Anders staat de foutmelding in beeld terwijl het geplakte bestand eronder verstopt zit."""
    competition, _ = wedstrijd
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    antwoord = client.post(
        f"/admin/c/{competition.id}/import",
        data={"csv_tekst": "naam,ronde,flight,starthole,marker\nJan,1,B,10,\n"},
    )

    assert antwoord.status_code == 422
    assert '<details class="paneel" open>' in antwoord.text

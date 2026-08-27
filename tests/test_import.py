"""De regels van een herimport.

Zie de docstring van `app.importer` voor de regels zelf. Elke test hieronder legt er één
vast, en de gevaarlijkste is de laatste: een gedeeltelijke import mag nooit stilzwijgend
twee spelers achterlaten die niemand meer kan markeren.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.importer import create_competition, import_csv
from app.models import Entry, Player, Round
from app.scoring import build_card, set_score, sign_card

BASIS = """naam,email,ronde,flight,starthole,marker
Jan,jan@x.nl,1,A,1,Piet
Piet,piet@x.nl,1,A,1,Jan
Anne,anne@x.nl,1,B,10,Kees
Kees,kees@x.nl,1,B,10,Anne
"""


@pytest.fixture
def veld(db):
    """Vier spelers in twee flights, markers over en weer."""
    competition = create_competition(db, "Herimport")
    result = import_csv(db, competition, BASIS)
    assert result.ok, result.errors
    return competition


def _entry(db, naam: str, ronde: int = 1) -> Entry:
    db.expire_all()
    return db.scalar(
        select(Entry)
        .join(Player)
        .join(Round, Entry.round_id == Round.id)
        .where(Player.name == naam, Round.no == ronde)
    )


def _foto(db) -> dict[str, tuple]:
    """Wie zit waar, met welke marker, welk token en hoeveel scores."""
    db.expire_all()
    return {
        e.player.name: (
            e.flight.name,
            e.flight.start_hole,
            e.marker.player.name if e.marker else None,
            e.token_hash,
            build_card(e).thru,
            e.locked,
        )
        for e in db.scalars(select(Entry))
    }


def _scoor(db, entry: Entry, holes: range, strokes: int = 4) -> None:
    for hole in holes:
        set_score(db, entry, entry, hole, "self", strokes)
        set_score(db, entry.marker, entry, hole, "marker", strokes)


# --- regel 2 en 3: het bestand wint, scores en links blijven ---------------------------


def test_hetzelfde_bestand_nog_een_keer_verandert_niets(db, veld):
    """Een herimport van hetzelfde bestand is een lege operatie."""
    _scoor(db, _entry(db, "Jan"), range(1, 4))
    voor = _foto(db)

    result = import_csv(db, veld, BASIS)

    assert result.ok
    assert (result.created_entries, result.updated_entries) == (0, 4)
    assert result.new_links == [], "geen nieuwe links, dus geen kwijtgeraakte links"
    assert _foto(db) == voor


def test_verhuizing_naar_een_andere_flight_laat_link_en_scores_staan(db, veld):
    """Een speler verplaatsen raakt zijn token en zijn kaart niet."""
    _scoor(db, _entry(db, "Jan"), range(1, 6))
    voor = _foto(db)["Jan"]

    result = import_csv(
        db,
        veld,
        BASIS.replace("Jan,jan@x.nl,1,A,1,Piet", "Jan,jan@x.nl,1,B,10,Kees")
        .replace("Kees,kees@x.nl,1,B,10,Anne", "Kees,kees@x.nl,1,B,10,Jan")
        .replace("Anne,anne@x.nl,1,B,10,Kees", "Anne,anne@x.nl,1,A,1,Piet")
        .replace("Piet,piet@x.nl,1,A,1,Jan", "Piet,piet@x.nl,1,A,1,Anne"),
    )

    assert result.ok, result.errors
    na = _foto(db)["Jan"]
    assert na[0] == "B", "verhuisd"
    assert na[2] == "Kees", "nieuwe marker"
    assert na[3] == voor[3], "zelfde token"
    assert na[4] == 5, "zelfde scores"


def test_een_getekende_kaart_blijft_getekend(db, veld):
    """Een herimport ontgrendelt niets."""
    jan = _entry(db, "Jan")
    _scoor(db, jan, range(1, 19))
    sign_card(db, jan)

    assert import_csv(db, veld, BASIS).ok
    assert _foto(db)["Jan"][5] is True


def test_het_bestand_wint_voor_het_e_mailadres(db, veld):
    """Een gewijzigd adres in het bestand overschrijft het opgeslagen adres."""
    assert import_csv(db, veld, BASIS.replace("jan@x.nl", "nieuw@x.nl")).ok

    db.expire_all()
    assert db.scalar(select(Player).where(Player.name == "Jan")).email == "nieuw@x.nl"


def test_wat_het_bestand_niet_noemt_blijft_staan(db, veld):
    """Spelers die niet in het bestand staan houden alles wat ze hadden."""
    _scoor(db, _entry(db, "Anne"), range(1, 4))
    voor = _foto(db)

    result = import_csv(db, veld, "naam,ronde,flight,starthole,marker\nJan,1,A,1,Piet\n")

    assert result.ok, result.errors
    na = _foto(db)
    assert na["Anne"] == voor["Anne"]
    assert na["Kees"] == voor["Kees"]


def test_lege_markerkolom_laat_de_bestaande_marker_staan(db, veld):
    """Een leeg vakje zegt niets, dus verandert er niets aan de marker."""
    result = import_csv(db, veld, BASIS.replace("Jan,jan@x.nl,1,A,1,Piet", "Jan,jan@x.nl,1,A,1,"))

    assert result.ok, result.errors
    assert _foto(db)["Jan"][2] == "Piet"


def test_een_nieuwe_marker_laat_de_scores_van_de_oude_staan(db, veld):
    """De oude marker heeft die holes echt gezien, dus die scores blijven geldig."""
    jan = _entry(db, "Jan")
    _scoor(db, jan, range(1, 4))

    result = import_csv(
        db,
        veld,
        BASIS.replace("Jan,jan@x.nl,1,A,1,Piet", "Jan,jan@x.nl,1,A,1,Anne")
        .replace("Anne,anne@x.nl,1,B,10,Kees", "Anne,anne@x.nl,1,A,1,Jan")
        .replace("Piet,piet@x.nl,1,A,1,Jan", "Piet,piet@x.nl,1,B,10,Kees")
        .replace("Kees,kees@x.nl,1,B,10,Anne", "Kees,kees@x.nl,1,B,10,Piet"),
    )

    assert result.ok, result.errors
    kaart = build_card(_entry(db, "Jan"))
    assert _foto(db)["Jan"][2] == "Anne"
    assert kaart.rows[0].marker_strokes == 4
    assert kaart.agreed_thru == 3, "de holes blijven bevestigd"


def test_een_verkeerd_gespelde_naam_wordt_geweigerd(db, veld):
    """Zo valt een tikfout op: de nieuwe speler pikt de marker van de goed gespelde in."""
    voor = _foto(db)

    result = import_csv(db, veld, BASIS.replace("Jan,jan@x.nl,1,A,1,Piet", "Jann,,1,A,1,Piet"))

    assert not result.ok
    fout = result.errors[0]
    assert "Piet staat als marker bij Jan, Jann" in fout
    assert "tikfout" in fout, "de melding wijst op de gelijkende namen"
    assert _foto(db) == voor
    assert db.scalar(select(Player).where(Player.name == "Jann")) is None


def test_starthole_geldt_voor_de_hele_flight(db, veld):
    """De starthole hangt aan de flight, dus hij schuift voor iedereen erin mee."""
    result = import_csv(db, veld, "naam,ronde,flight,starthole,marker\nJan,1,A,10,Piet\n")

    assert result.ok, result.errors
    na = _foto(db)
    assert na["Jan"][1] == 10
    assert na["Piet"][1] == 10, "Piet stond niet in het bestand maar zit in dezelfde flight"


# --- regel 4: de eindstand moet kloppen ------------------------------------------------


def test_verhuizing_die_twee_spelers_zou_stranden_gaat_niet_door(db, veld):
    """Alleen Jan verhuizen laat hem en Piet onmarkeerbaar achter, dus dit wordt geweigerd."""
    voor = _foto(db)

    result = import_csv(db, veld, "naam,ronde,flight,starthole,marker\nJan,1,B,10,\n")

    assert not result.ok
    assert "Jan" in result.errors[0] and "flight" in result.errors[0]
    assert _foto(db) == voor, "er mag niets zijn weggeschreven"


def test_een_geweigerde_import_laat_ook_geen_halve_spelers_achter(db, veld):
    """Bij een fout wordt alles teruggedraaid, ook de spelers die wél goed waren."""
    voor = _foto(db)

    result = import_csv(
        db,
        veld,
        "naam,ronde,flight,starthole,marker\nNieuwe Speler,1,C,1,\nJan,1,B,10,\n",
    )

    assert not result.ok
    assert result.new_links == [], "geen links tonen voor spelers die er niet zijn"
    assert result.created_entries == 0
    assert _foto(db) == voor
    assert db.scalar(select(Player).where(Player.name == "Nieuwe Speler")) is None


def test_marker_uit_een_andere_flight_in_hetzelfde_bestand_wordt_geweigerd(db, veld):
    """De oude controle op het bestand zelf blijft staan, met regelnummer."""
    result = import_csv(
        db, veld, BASIS.replace("Jan,jan@x.nl,1,A,1,Piet", "Jan,jan@x.nl,1,A,1,Anne")
    )

    assert not result.ok
    assert "Regel 2" in result.errors[0]


# --- regel 5: een marker mag uit het systeem komen -------------------------------------


def test_marker_die_al_in_het_systeem_staat_telt_mee(db, veld):
    """Eén flight opnieuw aanleveren hoeft niet het hele veld mee te nemen."""
    voor = _foto(db)

    result = import_csv(db, veld, "naam,ronde,flight,starthole,marker\nJan,1,A,1,Piet\n")

    assert result.ok, result.errors
    assert _foto(db)["Jan"][2] == "Piet"
    assert _foto(db)["Piet"] == voor["Piet"]


def test_onbekende_marker_geeft_een_bruikbare_fout(db, veld):
    """Wie nergens te vinden is levert een melding op die naar de schrijfwijze wijst."""
    result = import_csv(db, veld, "naam,ronde,flight,starthole,marker\nJan,1,A,1,Onbekend\n")

    assert not result.ok
    assert "Onbekend" in result.errors[0]
    assert "schrijfwijze" in result.errors[0]


# --- regel 6: verhuizen betekent achteraan --------------------------------------------


def test_verhuizen_zet_je_achteraan_de_flight(db, veld):
    """Binnen een flight houden de plekken een oplopende volgorde, zonder botsingen."""
    result = import_csv(
        db,
        veld,
        BASIS.replace("Anne,anne@x.nl,1,B,10,Kees", "Anne,anne@x.nl,1,A,1,Jan")
        .replace("Piet,piet@x.nl,1,A,1,Jan", "Piet,piet@x.nl,1,B,10,Kees")
        .replace("Jan,jan@x.nl,1,A,1,Piet", "Jan,jan@x.nl,1,A,1,Anne")
        .replace("Kees,kees@x.nl,1,B,10,Anne", "Kees,kees@x.nl,1,B,10,Piet"),
    )

    assert result.ok, result.errors
    db.expire_all()
    for rnd in db.scalars(select(Round)):
        for flight in rnd.flights:
            posities = [e.position for e in flight.entries]
            assert len(set(posities)) == len(posities), f"flight {flight.name} botst: {posities}"


def test_nieuwe_spelers_krijgen_oplopende_plekken(db, veld):
    """Twee spelers in één nieuwe flight komen niet allebei op plek 0 terecht."""
    result = import_csv(
        db,
        veld,
        "naam,ronde,flight,starthole,marker\nDirk,1,C,1,Erik\nErik,1,C,1,Dirk\n",
    )

    assert result.ok, result.errors
    db.expire_all()
    dirk = _entry(db, "Dirk")
    assert sorted(e.position for e in dirk.flight.entries) == [0, 1]


# --- de adminpagina --------------------------------------------------------------------


def test_geweigerde_import_toont_de_beheerpagina_met_de_fouten(db, veld, client):
    """Na een teruggedraaide import moet de pagina het nog gewoon doen."""
    client.post("/admin/login", data={"wachtwoord": "testwachtwoord"})

    response = client.post(
        f"/admin/c/{veld.id}/import",
        data={"csv_tekst": "naam,ronde,flight,starthole,marker\nJan,1,B,10,\n"},
    )

    assert response.status_code == 422
    assert "niet uitgevoerd" in response.text
    assert "dezelfde flight" in response.text
    assert "Jan" in response.text, "de spelerslijst staat er nog"


# --- regel 4: precies één marker per speler --------------------------------------------


def test_iedere_speler_moet_een_marker_hebben(db, veld):
    """Een nieuwe speler zonder marker kan door niemand bevestigd worden."""
    result = import_csv(db, veld, "naam,ronde,flight,starthole,marker\nDirk,1,A,1,\n")

    assert not result.ok
    assert "Dirk heeft geen marker" in result.errors[0]
    assert "flight A" in result.errors[0], "de melding zegt uit welke flight hij mag kiezen"


def test_een_marker_mag_niet_bij_twee_spelers_staan(db, veld):
    """Twee spelers die dezelfde marker aanwijzen laten er één onbevestigd achter."""
    voor = _foto(db)

    result = import_csv(
        db,
        veld,
        BASIS + "Dirk,dirk@x.nl,1,A,1,Piet\n",
    )

    assert not result.ok
    assert "Piet staat als marker bij" in result.errors[0]
    assert "een andere marker uit die flight" in result.errors[0]
    assert _foto(db) == voor


def test_een_eerste_import_zonder_markers_gaat_niet_door(db):
    """De eis geldt ook meteen bij het opzetten van de wedstrijd."""
    competition = create_competition(db, "Zonder markers")

    result = import_csv(
        db, competition, "naam,ronde,flight,starthole,marker\nJan,1,A,1,\nPiet,1,A,1,\n"
    )

    assert not result.ok
    assert len(result.errors) == 2, "allebei de spelers worden genoemd"
    assert db.scalar(select(Entry)) is None


def test_een_kring_van_drie_mag(db, veld):
    """Niet alleen paren: A markt B, B markt C, C markt A is een geldige flight."""
    result = import_csv(
        db,
        veld,
        "naam,ronde,flight,starthole,marker\n"
        "Dirk,1,C,1,Erik\nErik,1,C,1,Frank\nFrank,1,C,1,Dirk\n",
    )

    assert result.ok, result.errors
    na = _foto(db)
    assert (na["Dirk"][2], na["Erik"][2], na["Frank"][2]) == ("Erik", "Frank", "Dirk")


def test_een_flight_van_een_speler_kan_niet(db, veld):
    """In je eentje is er niemand die je kaart kan tekenen."""
    result = import_csv(db, veld, "naam,ronde,flight,starthole,marker\nDirk,1,C,1,Jan\n")

    assert not result.ok
    assert "flight" in result.errors[0]


def test_verhuizen_van_een_heel_paar_blijft_gewoon_werken(db, veld):
    """De strengere controle mag een normale herindeling niet in de weg zitten."""
    result = import_csv(
        db,
        veld,
        BASIS.replace("Jan,jan@x.nl,1,A,1,Piet", "Jan,jan@x.nl,1,B,10,Kees")
        .replace("Kees,kees@x.nl,1,B,10,Anne", "Kees,kees@x.nl,1,B,10,Jan")
        .replace("Anne,anne@x.nl,1,B,10,Kees", "Anne,anne@x.nl,1,A,1,Piet")
        .replace("Piet,piet@x.nl,1,A,1,Jan", "Piet,piet@x.nl,1,A,1,Anne"),
    )

    assert result.ok, result.errors
    na = _foto(db)
    assert (na["Jan"][0], na["Jan"][2]) == ("B", "Kees")
    assert (na["Anne"][0], na["Anne"][2]) == ("A", "Piet")

"""Een competitie met meer dan één ronde.

Twee dingen worden hier vastgelegd. De import zet een speler in één keer in alle ronden en
geeft hem per ronde een eigen link; welke hij wanneer opent controleren we niet. En het
bord toont één ronde tegelijk, vanaf ronde 2 met het resultaat van de eerdere ronden erbij.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.importer import create_competition, import_csv
from app.models import Entry, Player, Round
from app.scoring import huidige_ronde, leaderboard, set_score

TWEE_RONDEN = """naam,email,ronde,flight,starthole,marker
Jan,jan@x.nl,1,A,1,Piet
Piet,piet@x.nl,1,A,1,Jan
Jan,jan@x.nl,2,A,1,Piet
Piet,piet@x.nl,2,A,1,Jan
"""


@pytest.fixture
def toernooi(db):
    """Twee spelers die elkaars marker zijn, in twee ronden."""
    competition = create_competition(db, "Clubkampioenschap")
    result = import_csv(db, competition, TWEE_RONDEN)
    assert result.ok, result.errors
    db.refresh(competition)
    tokens = {(naam, ronde): token for naam, ronde, token in result.new_links}
    return competition, tokens


def _entry(db, naam: str, ronde: int) -> Entry:
    """De deelname van een speler in een ronde."""
    return db.scalar(
        select(Entry)
        .join(Player)
        .join(Round, Entry.round_id == Round.id)
        .where(Player.name == naam, Round.no == ronde)
    )


def _vul(db, entry: Entry, strokes: int, holes: int = 18, marker_strokes: int | None = None):
    """Vul een kaart namens de speler en zijn marker. Verschillende waarden = conflict."""
    for hole in range(1, holes + 1):
        set_score(db, entry, entry, hole, "self", strokes)
        set_score(db, entry.marker, entry, hole, "marker", marker_strokes or strokes)


def _stand(db, competition, ronde: int) -> dict:
    """De stand van één ronde, op naam."""
    db.expire_all()
    return {r.name: r for r in leaderboard(db, competition, ronde)}


# --- de import zet beide ronden in één keer klaar --------------------------------------


def test_import_maakt_beide_ronden_in_een_keer(db, toernooi):
    """Eén import levert per speler per ronde een deelname met een eigen link op."""
    competition, tokens = toernooi

    assert [r.no for r in competition.rounds] == [1, 2]
    assert sorted(tokens) == [("Jan", 1), ("Jan", 2), ("Piet", 1), ("Piet", 2)]
    assert len(set(tokens.values())) == 4, "elke deelname een eigen token"
    assert len(competition.players) == 2, "één speler, twee deelnames"
    for ronde in (1, 2):
        assert _entry(db, "Jan", ronde).marker.player.name == "Piet"


def test_elke_link_opent_de_kaart_van_zijn_eigen_ronde(db, toernooi, client):
    """De speler kiest zelf welke ronde hij invult; beide links werken vanaf het begin."""
    _, tokens = toernooi

    for ronde in (2, 1):  # bewust in de verkeerde volgorde: dat mag
        client.cookies.clear()
        client.get(f"/t/{tokens[('Jan', ronde)]}")
        eigen = _entry(db, "Jan", ronde)
        response = client.post(
            "/api/score",
            data={"entry_id": eigen.id, "hole": 1, "source": "self", "strokes": 3 + ronde},
        )
        assert response.status_code == 200

    db.expire_all()
    assert _entry(db, "Jan", 1).scores[0].strokes == 4
    assert _entry(db, "Jan", 2).scores[0].strokes == 5


def test_link_van_ronde_1_schrijft_niet_in_ronde_2(db, toernooi, client):
    """De ene ronde van een speler is de andere niet: de kaarten staan los van elkaar."""
    _, tokens = toernooi
    client.get(f"/t/{tokens[('Jan', 1)]}")

    response = client.post(
        "/api/score",
        data={"entry_id": _entry(db, "Jan", 2).id, "hole": 1, "source": "self", "strokes": 4},
    )

    assert response.status_code == 422
    db.expire_all()
    assert _entry(db, "Jan", 2).scores == []


# --- het bord toont één ronde ----------------------------------------------------------


def test_bord_van_ronde_1_toont_alleen_ronde_1(db, toernooi):
    """Ronde 1 is het bord zoals het altijd was: één regel per speler, zonder voorgeschiedenis."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)
    _vul(db, _entry(db, "Jan", 2), 5)

    rijen = _stand(db, competition, 1)

    assert list(rijen) == ["Jan"], "de tweede ronde hoort hier niet bij"
    assert rijen["Jan"].round_no == 1
    assert rijen["Jan"].to_par == 0
    assert rijen["Jan"].total == 72
    assert rijen["Jan"].prev_total is None
    assert rijen["Jan"].total_to_par == 0
    assert rijen["Jan"].grand_total == 72


def test_bord_van_ronde_2_telt_ronde_1_mee(db, toernooi):
    """Ronde 2 draagt het totaal van ronde 1 mee in de extra kolommen en in de +/-."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)  # 72 slagen, par
    _vul(db, _entry(db, "Jan", 2), 5)  # 90 slagen, +18

    jan = _stand(db, competition, 2)["Jan"]

    assert jan.round_no == 2
    assert jan.prev_total == 72
    assert jan.prev_to_par == 0
    assert jan.to_par == 18, "de ronde zelf"
    assert jan.total == 90
    assert jan.total_to_par == 18, "beide ronden samen"
    assert jan.grand_total == 162


def test_totaal_pas_zodra_de_ronde_rond_is(db, toernooi):
    """Halverwege ronde 2 loopt de +/- al mee, maar het totaal blijft leeg."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 3)  # 54 slagen, -18
    _vul(db, _entry(db, "Jan", 2), 6, holes=9)  # negen holes van 6

    jan = _stand(db, competition, 2)["Jan"]

    assert jan.thru == 9
    assert jan.prev_total == 54
    assert jan.to_par == 54 - sum(_entry(db, "Jan", 2).round.pars[:9])
    assert jan.total_to_par == jan.prev_to_par + jan.to_par
    assert jan.total is None
    assert jan.grand_total is None, "een halve ronde geeft geen eindstand"


def test_wie_nog_moet_starten_staat_er_met_zijn_eerdere_resultaat(db, toernooi):
    """Een speler die vandaag nog niet is begonnen hoort met zijn ronde 1 op het bord."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)

    rijen = _stand(db, competition, 2)

    assert list(rijen) == ["Jan"]
    assert rijen["Jan"].prev_total == 72
    assert rijen["Jan"].thru == 0
    assert rijen["Jan"].to_par == 0
    assert rijen["Jan"].total_to_par == 0
    assert rijen["Jan"].grand_total is None


def test_wie_nergens_iets_heeft_staat_er_niet_bij(db, toernooi):
    """Zonder score in deze ronde én zonder eerdere ronde geen regel."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 2), 4)

    assert list(_stand(db, competition, 2)) == ["Jan"], "Piet heeft nergens iets"
    assert _stand(db, competition, 2)["Jan"].prev_total is None


def test_rangschikking_gaat_over_alle_ronden_samen(db, toernooi):
    """Wie vandaag slechter speelt maar in totaal beter staat, staat bovenaan."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 3)   # -18
    _vul(db, _entry(db, "Jan", 2), 5)   # +18, samen par
    _vul(db, _entry(db, "Piet", 1), 5)  # +18
    _vul(db, _entry(db, "Piet", 2), 4)  # par, samen +18

    dagstand = leaderboard(db, competition, 1)
    eindstand = leaderboard(db, competition, 2)

    assert [r.name for r in dagstand] == ["Jan", "Piet"]
    assert [r.name for r in eindstand] == ["Jan", "Piet"], "Jan staat voor op zijn ronde 1"
    assert [r.to_par for r in eindstand] == [18, 0], "vandaag speelde Piet beter"
    assert [r.total_to_par for r in eindstand] == [0, 18]
    assert [r.grand_total for r in eindstand] == [144, 162]


def test_uitgevallen_speler_zakt_naar_onderen(db, toernooi):
    """DQ in ronde 2 haalt de speler uit de rangschikking, zijn regel blijft staan."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 3)
    _vul(db, _entry(db, "Piet", 1), 5)
    _vul(db, _entry(db, "Piet", 2), 4)
    jan = _entry(db, "Jan", 2)
    jan.status = "dq"
    db.commit()

    stand = leaderboard(db, competition, 2)

    assert [r.name for r in stand] == ["Piet", "Jan"]
    assert stand[1].prev_total == 54, "zijn ronde 1 blijft zichtbaar"


def test_alleen_holes_waarover_beiden_het_eens_zijn_tellen_mee_uit_ronde_1(db, toernooi):
    """Een openstaand verschil in ronde 1 telt niet mee in wat de speler meebrengt."""
    competition, _ = toernooi
    jan1 = _entry(db, "Jan", 1)
    _vul(db, jan1, 4)
    set_score(db, jan1.marker, jan1, 4, "marker", 9)  # hole 4 blijft betwist
    _vul(db, _entry(db, "Jan", 2), 4)

    jan = _stand(db, competition, 2)["Jan"]

    assert jan.prev_total == 68, "zeventien holes van vier, hole 4 telt niet"
    assert jan.prev_to_par == -1, "hole 4 is een par 3 die hij in vier deed"
    assert jan.total_to_par == -1


def test_drie_ronden_tellen_ronde_1_en_2_op(db, toernooi):
    """In ronde 3 draagt de speler zowel ronde 1 als ronde 2 mee."""
    competition, _ = toernooi
    result = import_csv(
        db,
        competition,
        "naam,ronde,flight,starthole,marker\nJan,3,A,1,Piet\nPiet,3,A,1,Jan\n",
    )
    assert result.ok, result.errors
    db.refresh(competition)
    _vul(db, _entry(db, "Jan", 1), 4)  # 72
    _vul(db, _entry(db, "Jan", 2), 5)  # 90
    _vul(db, _entry(db, "Jan", 3), 3)  # 54

    jan = _stand(db, competition, 3)["Jan"]

    assert [r.no for r in competition.rounds] == [1, 2, 3]
    assert jan.prev_total == 162
    assert jan.prev_to_par == 18
    assert jan.total_to_par == 0
    assert jan.grand_total == 216


def test_onbekende_ronde_geeft_een_leeg_bord(db, toernooi):
    """Een ronde die niet bestaat levert geen regels op in plaats van een fout."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)

    assert leaderboard(db, competition, 9) == []


# --- welke ronde het bord kiest --------------------------------------------------------


def test_zonder_keuze_de_laatste_ronde_waarin_gescoord_is(db, toernooi):
    """De gedeelde link schuift mee: zolang ronde 2 leeg is toont hij ronde 1."""
    competition, _ = toernooi

    assert huidige_ronde(db, competition, None) == 1, "nog niets ingevuld"

    _vul(db, _entry(db, "Jan", 1), 4)
    db.expire_all()
    assert huidige_ronde(db, competition, None) == 1

    set_score(db, _entry(db, "Piet", 2), _entry(db, "Piet", 2), 1, "self", 4)
    db.expire_all()
    assert huidige_ronde(db, competition, None) == 2


def test_een_gekozen_ronde_wint_van_de_klok(db, toernooi):
    """Met ?r=1 blijft het bord bij ronde 1, ook als ronde 2 al loopt."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 2), 4)
    db.expire_all()

    assert huidige_ronde(db, competition, 1) == 1
    assert huidige_ronde(db, competition, 2) == 2
    assert huidige_ronde(db, competition, 7) == 2, "onzin valt terug op de lopende ronde"


def test_competitie_zonder_ronden_valt_terug_op_een(db):
    """Een verse competitie zonder import mag het bord niet laten klappen."""
    competition = create_competition(db, "Nog niets")

    assert huidige_ronde(db, competition, None) == 1
    assert leaderboard(db, competition, 1) == []


# --- de pagina -------------------------------------------------------------------------


def _kolommen(html: str) -> list[str]:
    """De koppen van de tabel, op volgorde."""
    kop = html[html.index("<thead>") : html.index("</tr>", html.index("<thead>"))]
    return [" ".join(c.split()) for c in re.findall(r"<th\b[^>]*>(.*?)</th>", kop, re.S)]


def test_ronde_1_houdt_de_oude_kolommen(db, toernooi, client):
    """Bij één ronde staat er achter de holes alleen het totaal."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)

    kolommen = _kolommen(client.get(f"/l/{competition.leaderboard_slug}?r=1").text)

    assert kolommen[:3] == ["", "Speler", "+/-"]
    assert kolommen[-3:] == ["18", "In", "Tot"]
    assert "Totaal" not in kolommen
    assert "R1" not in kolommen


def test_totalen_per_ronde_staan_achter_de_holes(db, toernooi, client):
    """Ronde 2: +/-, dan de holes met Out en In, dan R1, R2 en het totaal."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)
    _vul(db, _entry(db, "Jan", 2), 5)

    tekst = client.get(f"/l/{competition.leaderboard_slug}?r=2").text
    kolommen = _kolommen(tekst)

    assert kolommen[:3] == ["", "Speler", "+/-"]
    assert kolommen[-5:] == ["18", "In", "R1", "R2", "Totaal"]
    assert ">72<" in tekst, "het resultaat van ronde 1"
    assert ">90<" in tekst, "de ronde van vandaag"
    assert ">162<" in tekst, "beide ronden samen"


def test_elke_ronde_een_eigen_kolom(db, toernooi, client):
    """Bij drie ronden staan R1, R2 en R3 los van elkaar op het bord."""
    competition, _ = toernooi
    import_csv(
        db, competition, "naam,ronde,flight,starthole,marker\nJan,3,A,1,Piet\nPiet,3,A,1,Jan\n"
    )
    db.refresh(competition)
    _vul(db, _entry(db, "Jan", 1), 4)
    _vul(db, _entry(db, "Jan", 3), 3)

    kolommen = _kolommen(client.get(f"/l/{competition.leaderboard_slug}?r=3").text)

    assert kolommen[-5:] == ["In", "R1", "R2", "R3", "Totaal"]


def test_parregel_telt_per_ronde_op(db, toernooi, client):
    """Onder elke rondekolom staat de par van die ronde, en achteraan de par van alles."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)
    _vul(db, _entry(db, "Jan", 2), 4)

    tekst = client.get(f"/l/{competition.leaderboard_slug}?r=2").text
    parregel = tekst[tekst.index('<tr class="pars">') : tekst.index("</thead>")]

    assert re.findall(r'<td class="som">(\d*)</td>', parregel)[-3:] == ["72", "72", "144"]


def test_pagina_haalt_de_tabel_van_dezelfde_ronde_op(db, toernooi, client):
    """De poll mag niet stiekem naar een andere ronde springen."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)

    pagina = client.get(f"/l/{competition.leaderboard_slug}?r=1").text

    assert f"/l/{competition.leaderboard_slug}/table?n=25&amp;r=1" in pagina
    assert "Ronde 1" in pagina, "de kop noemt de ronde bij meer dan één ronde"


def test_tabelcache_houdt_de_ronden_uit_elkaar(db, toernooi, client):
    """Twee ronden achter elkaar opvragen mag niet twee keer hetzelfde bord geven."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)
    _vul(db, _entry(db, "Jan", 2), 5)
    slug = competition.leaderboard_slug

    eerst = client.get(f"/l/{slug}/table?r=1").text
    daarna = client.get(f"/l/{slug}/table?r=2").text
    nogmaals = client.get(f"/l/{slug}/table?r=1").text

    assert "Totaal" not in eerst
    assert "Totaal" in daarna
    assert nogmaals == eerst, "dezelfde ronde komt wel uit de cache"


def test_parregel_volgt_de_getoonde_ronde(db, toernooi, client):
    """Twee banen, twee parregels: het bord toont die van de ronde die je bekijkt."""
    competition, _ = toernooi
    ronde2 = db.scalar(
        select(Round).where(Round.competition_id == competition.id, Round.no == 2)
    )
    ronde2.pars = [3] * 18
    db.commit()
    _vul(db, _entry(db, "Jan", 1), 4)
    _vul(db, _entry(db, "Jan", 2), 4)
    slug = competition.leaderboard_slug

    assert ">72<" in client.get(f"/l/{slug}?r=1").text, "par 72"
    assert ">54<" in client.get(f"/l/{slug}?r=2").text, "par 54"


def test_een_ronde_toont_geen_rondenummer_achter_de_naam(db, toernooi, client):
    """Het bord gaat over één ronde, dus hoeft de naam geen R1 of R2 meer te dragen."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 2), 4)

    tekst = client.get(f"/l/{competition.leaderboard_slug}?r=2").text

    assert "· R2" not in tekst
    assert "Jan" in tekst


def test_een_gewiste_score_maakt_geen_ronde_actief(db, toernooi):
    """Wie zich vertikt en zijn invoer weghaalt, verplaatst het bord niet naar zijn ronde."""
    competition, _ = toernooi
    _vul(db, _entry(db, "Jan", 1), 4)
    jan2 = _entry(db, "Jan", 2)
    set_score(db, jan2, jan2, 1, "self", 4)
    set_score(db, jan2, jan2, 1, "self", None)
    db.expire_all()

    assert huidige_ronde(db, competition, None) == 1

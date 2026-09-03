"""Een competitie met meer dan één ronde.

Twee dingen worden hier vastgelegd. De import zet een speler in één keer in alle ronden en
geeft hem per ronde een eigen link; welke hij wanneer opent controleren we niet. En het
bord toont één ronde tegelijk, vanaf ronde 2 met het resultaat van de eerdere ronden erbij.
"""

from __future__ import annotations

from app.importer import create_competition, import_csv
from app.main import _bord
from app.scoring import huidige_ronde, leaderboard, set_score
from tests.helpers import entry_van, stand, vul_kaart

# --- de import zet beide ronden in één keer klaar --------------------------------------


def test_import_maakt_beide_ronden_in_een_keer(db, toernooi):
    """Eén import levert per speler per ronde een deelname met een eigen link op."""
    competition, tokens = toernooi

    assert [r.no for r in competition.rounds] == [1, 2]
    assert sorted(tokens) == [("Jan", 1), ("Jan", 2), ("Piet", 1), ("Piet", 2)]
    assert len(set(tokens.values())) == 4, "elke deelname een eigen token"
    assert len(competition.players) == 2, "één speler, twee deelnames"
    for ronde in (1, 2):
        assert entry_van(db, "Jan", ronde).marker.player.name == "Piet"


def test_elke_link_opent_de_kaart_van_zijn_eigen_ronde(db, toernooi, client):
    """De speler kiest zelf welke ronde hij invult; beide links werken vanaf het begin."""
    _, tokens = toernooi

    for ronde in (2, 1):  # bewust in de verkeerde volgorde: dat mag
        client.cookies.clear()
        client.get(f"/t/{tokens[('Jan', ronde)]}")
        eigen = entry_van(db, "Jan", ronde)
        response = client.post(
            "/api/score",
            data={"entry_id": eigen.id, "hole": 1, "source": "self", "strokes": 3 + ronde},
        )
        assert response.status_code == 200

    db.expire_all()
    assert entry_van(db, "Jan", 1).scores[0].strokes == 4
    assert entry_van(db, "Jan", 2).scores[0].strokes == 5


def test_link_van_ronde_1_schrijft_niet_in_ronde_2(db, toernooi, client):
    """De ene ronde van een speler is de andere niet: de kaarten staan los van elkaar."""
    _, tokens = toernooi
    client.get(f"/t/{tokens[('Jan', 1)]}")

    response = client.post(
        "/api/score",
        data={"entry_id": entry_van(db, "Jan", 2).id, "hole": 1, "source": "self", "strokes": 4},
    )

    assert response.status_code == 422
    db.expire_all()
    assert entry_van(db, "Jan", 2).scores == []


# --- het bord toont één ronde ----------------------------------------------------------


def test_bord_van_ronde_1_toont_alleen_ronde_1(db, toernooi):
    """Ronde 1 is het bord zoals het altijd was: één regel per speler, zonder voorgeschiedenis."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Jan", 2), 5)

    rijen = stand(db, competition, 1)

    assert rijen["Jan"].round_no == 1
    assert rijen["Piet"].thru == 0, "Piet staat er, maar zonder score uit ronde 2"
    assert rijen["Jan"].to_par == 0
    assert rijen["Jan"].total == 72
    assert rijen["Jan"].prev_total is None
    assert rijen["Jan"].total_to_par == 0
    assert rijen["Jan"].grand_total == 72


def test_bord_van_ronde_2_telt_ronde_1_mee(db, toernooi):
    """Ronde 2 draagt het totaal van ronde 1 mee in de extra kolommen en in de +/-."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)  # 72 slagen, par
    vul_kaart(db, entry_van(db, "Jan", 2), 5)  # 90 slagen, +18

    jan = stand(db, competition, 2)["Jan"]

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
    vul_kaart(db, entry_van(db, "Jan", 1), 3)  # 54 slagen, -18
    vul_kaart(db, entry_van(db, "Jan", 2), 6, holes=9)  # negen holes van 6

    jan = stand(db, competition, 2)["Jan"]

    assert jan.thru == 9
    assert jan.prev_total == 54
    assert jan.to_par == 54 - sum(entry_van(db, "Jan", 2).round.pars[:9])
    assert jan.total_to_par == jan.prev_to_par + jan.to_par
    assert jan.total is None
    assert jan.grand_total is None, "een halve ronde geeft geen eindstand"


def test_wie_nog_moet_starten_staat_er_met_zijn_eerdere_resultaat(db, toernooi):
    """Een speler die vandaag nog niet is begonnen hoort met zijn ronde 1 op het bord."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)

    rijen = stand(db, competition, 2)

    assert list(rijen) == ["Jan", "Piet"], "Jan telt mee, Piet heeft nog niets"
    assert rijen["Jan"].prev_total == 72
    assert rijen["Jan"].thru == 0
    assert rijen["Jan"].to_par == 0
    assert rijen["Jan"].total_to_par == 0
    assert rijen["Jan"].grand_total is None


def test_wie_nergens_iets_heeft_staat_onderaan(db, toernooi):
    """Zonder score in deze ronde én zonder eerdere ronde toch een regel, maar achteraan.

    Het bord is ook de startlijst: het veld moet er vanaf het eerste uur op staan, anders
    kan niemand zien of hij is ingedeeld.
    """
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 2), 4)

    rijen = stand(db, competition, 2)

    assert list(rijen) == ["Jan", "Piet"], "Piet heeft nergens iets en zakt naar onderen"
    assert rijen["Jan"].prev_total is None
    assert not rijen["Piet"].heeft_resultaat
    assert rijen["Piet"].playing, "nog niets ingevuld is geen uitvallen"


def test_rangschikking_gaat_over_alle_ronden_samen(db, toernooi):
    """Wie vandaag slechter speelt maar in totaal beter staat, staat bovenaan."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 3)   # -18
    vul_kaart(db, entry_van(db, "Jan", 2), 5)   # +18, samen par
    vul_kaart(db, entry_van(db, "Piet", 1), 5)  # +18
    vul_kaart(db, entry_van(db, "Piet", 2), 4)  # par, samen +18

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
    vul_kaart(db, entry_van(db, "Jan", 1), 3)
    vul_kaart(db, entry_van(db, "Piet", 1), 5)
    vul_kaart(db, entry_van(db, "Piet", 2), 4)
    jan = entry_van(db, "Jan", 2)
    jan.status = "dq"
    db.commit()

    stand = leaderboard(db, competition, 2)

    assert [r.name for r in stand] == ["Piet", "Jan"]
    assert stand[1].prev_total == 54, "zijn ronde 1 blijft zichtbaar"


def test_alleen_holes_waarover_beiden_het_eens_zijn_tellen_mee_uit_ronde_1(db, toernooi):
    """Een openstaand verschil in ronde 1 telt niet mee in wat de speler meebrengt."""
    competition, _ = toernooi
    jan1 = entry_van(db, "Jan", 1)
    vul_kaart(db, jan1, 4)
    set_score(db, jan1.marker, jan1, 4, "marker", 9)  # hole 4 blijft betwist
    vul_kaart(db, entry_van(db, "Jan", 2), 4)

    jan = stand(db, competition, 2)["Jan"]

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
    vul_kaart(db, entry_van(db, "Jan", 1), 4)  # 72
    vul_kaart(db, entry_van(db, "Jan", 2), 5)  # 90
    vul_kaart(db, entry_van(db, "Jan", 3), 3)  # 54

    jan = stand(db, competition, 3)["Jan"]

    assert [r.no for r in competition.rounds] == [1, 2, 3]
    assert jan.prev_total == 162
    assert jan.prev_to_par == 18
    assert jan.total_to_par == 0
    assert jan.grand_total == 216


def test_onbekende_ronde_geeft_een_leeg_bord(db, toernooi):
    """Een ronde die niet bestaat levert geen regels op in plaats van een fout."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)

    assert leaderboard(db, competition, 9) == []


# --- welke ronde het bord kiest --------------------------------------------------------


def test_zonder_keuze_de_laatste_ronde_waarin_gescoord_is(db, toernooi):
    """De gedeelde link schuift mee: zolang ronde 2 leeg is toont hij ronde 1."""
    competition, _ = toernooi

    assert huidige_ronde(db, competition, None) == 1, "nog niets ingevuld"

    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    db.expire_all()
    assert huidige_ronde(db, competition, None) == 1

    set_score(db, entry_van(db, "Piet", 2), entry_van(db, "Piet", 2), 1, "self", 4)
    db.expire_all()
    assert huidige_ronde(db, competition, None) == 2


def test_een_gekozen_ronde_wint_van_de_klok(db, toernooi):
    """Met ?r=1 blijft het bord bij ronde 1, ook als ronde 2 al loopt."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 2), 4)
    db.expire_all()

    assert huidige_ronde(db, competition, 1) == 1
    assert huidige_ronde(db, competition, 2) == 2
    assert huidige_ronde(db, competition, 7) == 2, "onzin valt terug op de lopende ronde"


def test_competitie_zonder_ronden_valt_terug_op_een(db, gebruiker):
    """Een verse competitie zonder import mag het bord niet laten klappen."""
    competition = create_competition(db, "Nog niets", gebruiker)

    assert huidige_ronde(db, competition, None) == 1
    assert leaderboard(db, competition, 1) == []


def test_een_gewiste_score_maakt_geen_ronde_actief(db, toernooi):
    """Wie zich vertikt en zijn invoer weghaalt, verplaatst het bord niet naar zijn ronde."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    jan2 = entry_van(db, "Jan", 2)
    set_score(db, jan2, jan2, 1, "self", 4)
    set_score(db, jan2, jan2, 1, "self", None)
    db.expire_all()

    assert huidige_ronde(db, competition, None) == 1


# --- een status uit een eerdere ronde geldt voor de hele wedstrijd ---------------------


def test_uitvaller_blijft_uitgevallen_in_de_volgende_ronde(db, toernooi):
    """Wie in ronde 1 op WD staat, speelt niet meer mee: ook niet op het bord van ronde 2."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 3)   # de beste ronde van het veld
    vul_kaart(db, entry_van(db, "Piet", 1), 5)
    vul_kaart(db, entry_van(db, "Jan", 2), 3)
    vul_kaart(db, entry_van(db, "Piet", 2), 5)
    jan1 = entry_van(db, "Jan", 1)
    jan1.status = "wd"
    db.commit()

    stand = leaderboard(db, competition, 2)

    assert entry_van(db, "Jan", 2).status == "ok", "in de database staat alleen ronde 1 op wd"
    rijen = {r.name: r for r in stand}
    assert rijen["Jan"].status == "wd", "maar op het bord telt hij niet meer mee"
    assert not rijen["Jan"].playing
    assert [r.name for r in stand] == ["Piet", "Jan"], "hij zakt naar onderen ondanks zijn score"


def test_een_status_in_deze_ronde_wint_van_de_eerdere(db, toernooi):
    """Zet de leiding hem later op DQ, dan is dat wat er staat."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Jan", 2), 4)
    entry_van(db, "Jan", 1).status = "nr"
    entry_van(db, "Jan", 2).status = "dq"
    db.commit()

    assert stand(db, competition, 2)["Jan"].status == "dq"


def test_ronde_1_blijft_naar_zijn_eigen_status_kijken(db, toernooi):
    """De overdracht loopt één kant op: ronde 2 raakt de stand van ronde 1 niet."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Jan", 2), 4)
    entry_van(db, "Jan", 2).status = "wd"
    db.commit()

    assert stand(db, competition, 1)["Jan"].status == "ok"
    assert stand(db, competition, 1)["Jan"].playing
    assert stand(db, competition, 2)["Jan"].status == "wd"


def test_uitvaller_zonder_score_in_ronde_2_houdt_zijn_regel(db, toernooi):
    """Zijn ronde 1 staat er nog, met de reden waarom er niets meer bijkomt."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    entry_van(db, "Jan", 1).status = "wd"
    db.commit()

    jan = stand(db, competition, 2)["Jan"]

    assert jan.status == "wd"
    assert jan.prev_total == 72
    assert jan.thru == 0


def test_uitvaller_zonder_score_zakt_onder_wie_nog_moet_starten(db, toernooi):
    """Een uitvaller die nooit een score inleverde staat helemaal onderaan."""
    competition, _ = toernooi
    entry_van(db, "Jan", 1).status = "wd"
    vul_kaart(db, entry_van(db, "Piet", 2), 4)
    db.commit()

    assert list(stand(db, competition, 2)) == ["Piet", "Jan"]


def test_wie_nog_niets_heeft_houdt_de_volgorde_van_de_startlijst(db, wedstrijd):
    """Vier spelers, niemand gestart: het bord is de startlijst, flight na flight."""
    competition, _ = wedstrijd

    assert [r.name for r in leaderboard(db, competition, 1)] == ["Jan", "Piet", "Anne", "Kees"]


def test_een_score_tilt_je_boven_wie_nog_moet_starten(db, wedstrijd):
    """Wie als enige iets bevestigd heeft, staat bovenaan; de rest houdt zijn volgorde."""
    competition, _ = wedstrijd
    vul_kaart(db, entry_van(db, "Anne"), 4, holes=1)

    assert [r.name for r in leaderboard(db, competition, 1)] == ["Anne", "Jan", "Piet", "Kees"]


# --- wat de pagina aan gegevens meekrijgt ----------------------------------------------


def test_bord_geeft_de_pars_van_elke_ronde_mee(db, toernooi):
    """De parregel telt per ronde op, en achteraan de par van alle ronden samen."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)

    bord = _bord(db, competition, 25, 2)

    assert bord["eerdere"] == [(1, 72)], "ronde 1 met zijn eigen par"
    assert sum(bord["pars"]) == 72
    assert bord["par_totaal"] == 144


def test_de_tabelcache_houdt_de_ronden_uit_elkaar(db, toernooi, client):
    """Twee ronden achter elkaar opvragen mag niet twee keer hetzelfde bord geven."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Jan", 2), 5)
    slug = competition.leaderboard_slug

    eerst = client.get(f"/l/{slug}/table?r=1").text
    daarna = client.get(f"/l/{slug}/table?r=2").text
    nogmaals = client.get(f"/l/{slug}/table?r=1").text

    assert eerst != daarna, "elke ronde zijn eigen tabel"
    assert nogmaals == eerst, "dezelfde ronde komt wel uit de cache"

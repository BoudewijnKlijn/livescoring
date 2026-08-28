"""Scores opslaan, conflicten, tekenen en vergrendelen."""

from __future__ import annotations

from app.models import DEFAULT_PARS, HOLES
from app.scoring import build_card
from tests.helpers import entry_van


def _vul_kaart(als_speler, db, eigen: str, marker: str, strokes: int = 4, holes: int = 18):
    """Vul een kaart volledig: de speler zelf en zijn marker voeren dezelfde scores in."""
    doel = entry_van(db, eigen)
    client = als_speler(eigen)
    for hole in range(1, holes + 1):
        client.post(
            "/api/score",
            data={"entry_id": doel.id, "hole": hole, "source": "self", "strokes": strokes},
        )
    client = als_speler(marker)
    for hole in range(1, holes + 1):
        client.post(
            "/api/score",
            data={"entry_id": doel.id, "hole": hole, "source": "marker", "strokes": strokes},
        )
    return doel


def test_score_opslaan_en_teruglezen(db, wedstrijd, als_speler):
    """Een ingevoerde score staat in de database en op het invoerscherm."""
    client = als_speler("Jan")
    jan = entry_van(db, "Jan")

    response = client.post(
        "/api/score", data={"entry_id": jan.id, "hole": 3, "source": "self", "strokes": 5}
    )

    assert response.status_code == 200
    db.expire_all()
    kaart = build_card(entry_van(db, "Jan"))
    assert kaart.rows[2].self_strokes == 5
    assert kaart.total == 5
    assert kaart.thru == 1
    assert 'value="5"' in client.get("/me/card").text


def test_conflict_tussen_speler_en_marker(db, wedstrijd, als_speler):
    """Verschillende invoer voor dezelfde hole levert een conflict op."""
    jan = entry_van(db, "Jan")
    als_speler("Jan").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 4}
    )
    response = als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "marker", "strokes": 6}
    )

    assert response.status_code == 200
    assert "conflict" in response.text
    db.expire_all()
    kaart = build_card(entry_van(db, "Jan"))
    assert kaart.conflicts == [1]
    assert not kaart.signable


def test_gelijke_invoer_is_geen_conflict(db, wedstrijd, als_speler):
    """Dezelfde score van beide kanten is akkoord."""
    jan = entry_van(db, "Jan")
    als_speler("Jan").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 4}
    )
    als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "marker", "strokes": 4}
    )

    db.expire_all()
    kaart = build_card(entry_van(db, "Jan"))
    assert kaart.conflicts == []
    assert kaart.rows[0].agreed


def test_tekenen_faalt_bij_conflict(db, wedstrijd, als_speler):
    """Een openstaand verschil blokkeert de handtekening."""
    _vul_kaart(als_speler, db, "Jan", "Piet")
    jan = entry_van(db, "Jan")
    als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 7, "source": "marker", "strokes": 9}
    )

    client = als_speler("Jan")
    response = client.post("/me/sign", data={"akkoord": "ja"}, follow_redirects=False)

    assert response.status_code == 422
    assert "verschil" in response.text
    db.expire_all()
    assert entry_van(db, "Jan").signed_at is None
    assert not entry_van(db, "Jan").locked


def test_tekenen_faalt_bij_ontbrekende_hole(db, wedstrijd, als_speler):
    """Zonder alle 18 holes van beide bronnen kan er niet getekend worden."""
    _vul_kaart(als_speler, db, "Jan", "Piet", holes=17)

    client = als_speler("Jan")
    response = client.post("/me/sign", data={"akkoord": "ja"}, follow_redirects=False)

    assert response.status_code == 422
    assert "niet alle holes" in response.text
    db.expire_all()
    assert entry_van(db, "Jan").signed_at is None


def test_kaart_is_vergrendeld_na_tekenen(db, wedstrijd, als_speler):
    """Na tekenen is de kaart dicht en wordt verdere invoer geweigerd."""
    jan = _vul_kaart(als_speler, db, "Jan", "Piet")

    client = als_speler("Jan")
    response = client.post("/me/sign", data={"akkoord": "ja"}, follow_redirects=False)
    assert response.status_code == 303

    db.expire_all()
    ververst = entry_van(db, "Jan")
    assert ververst.signed_at is not None
    assert ververst.locked

    daarna = client.post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 2}
    )
    assert daarna.status_code == 422
    db.expire_all()
    assert build_card(entry_van(db, "Jan")).rows[0].self_strokes == 4


def test_tekenen_zonder_vinkje_doet_niets(db, wedstrijd, als_speler):
    """De verklaring moet aangevinkt zijn."""
    _vul_kaart(als_speler, db, "Jan", "Piet")

    response = als_speler("Jan").post("/me/sign", data={}, follow_redirects=False)

    assert response.status_code == 422
    db.expire_all()
    assert not entry_van(db, "Jan").locked


def test_onmogelijke_score_wordt_geweigerd(db, wedstrijd, als_speler):
    """Nul slagen of 21 slagen slaat niemand op."""
    jan = entry_van(db, "Jan")
    client = als_speler("Jan")

    for waarde in (0, 21):
        response = client.post(
            "/api/score",
            data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": waarde},
        )
        assert response.status_code == 422

    db.expire_all()
    assert entry_van(db, "Jan").scores == []


def test_leaderboard_toont_alleen_scores_waar_beiden_het_eens_zijn(db, wedstrijd, als_speler):
    """Eens = zichtbaar en gekleurd naar par; oneens = leeg; niet gestart = lege regel."""
    from app.scoring import leaderboard

    competition, _ = wedstrijd
    jan = entry_van(db, "Jan")
    als_speler("Jan").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 3}
    )
    als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "marker", "strokes": 3}
    )
    als_speler("Jan").post(
        "/api/score", data={"entry_id": jan.id, "hole": 2, "source": "self", "strokes": 5}
    )
    als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 2, "source": "marker", "strokes": 6}
    )

    db.expire_all()
    rijen = {r.name: r for r in leaderboard(db, competition)}

    assert rijen["Jan"].holes[0] == (3, "eagle")    # hole 1 is een par 5
    assert rijen["Jan"].holes[1] == (None, "")      # verschil: niet tonen
    assert rijen["Jan"].to_par == -2
    assert rijen["Jan"].thru == 1
    assert rijen["Jan"].total is None               # ronde loopt nog
    assert not rijen["Anne"].heeft_resultaat        # nog niet gestart, wel op het bord
    assert not rijen["Piet"].heeft_resultaat        # zelf nog niets ingevuld


def test_geweigerde_score_geeft_opgeslagen_waarde_terug(db, wedstrijd, als_speler):
    """Een 422 bevat de stand uit de database, zodat het vakje terugspringt.

    Daar leunt de kaart op: blijft je invoer staan, dan is hij opgeslagen.
    """
    jan = entry_van(db, "Jan")
    client = als_speler("Jan")
    client.post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 4}
    )

    geweigerd = client.post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 99}
    )

    assert geweigerd.status_code == 422
    assert 'value="4"' in geweigerd.text
    assert 'value="99"' not in geweigerd.text


def test_geweigerde_eerste_score_geeft_leeg_vakje_terug(db, wedstrijd, als_speler):
    """Zonder eerdere score levert een weigering een leeg vakje op."""
    jan = entry_van(db, "Jan")

    geweigerd = als_speler("Jan").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 0}
    )

    assert geweigerd.status_code == 422
    assert 'value=""' in geweigerd.text


def test_nieuwe_ronde_krijgt_de_pars_van_de_baan(db, wedstrijd):
    """Een geïmporteerde ronde staat meteen op de pars van de thuisbaan."""
    assert entry_van(db, "Jan").round.pars == DEFAULT_PARS
    assert sum(DEFAULT_PARS) == 72
    assert len(DEFAULT_PARS) == HOLES

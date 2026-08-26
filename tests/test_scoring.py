"""Scores opslaan, conflicten, tekenen en vergrendelen."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Entry, Player
from app.scoring import build_card


def _entry_van(db, naam: str) -> Entry:
    player = db.scalar(select(Player).where(Player.name == naam))
    return db.scalar(select(Entry).where(Entry.player_id == player.id))


def _vul_kaart(als_speler, db, eigen: str, marker: str, strokes: int = 4, holes: int = 18):
    """Vul een kaart volledig: de speler zelf en zijn marker voeren dezelfde scores in."""
    doel = _entry_van(db, eigen)
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
    jan = _entry_van(db, "Jan")

    response = client.post(
        "/api/score", data={"entry_id": jan.id, "hole": 3, "source": "self", "strokes": 5}
    )

    assert response.status_code == 200
    db.expire_all()
    kaart = build_card(_entry_van(db, "Jan"))
    assert kaart.rows[2].self_strokes == 5
    assert kaart.total == 5
    assert kaart.thru == 1
    assert 'value="5"' in client.get("/me/card").text


def test_conflict_tussen_speler_en_marker(db, wedstrijd, als_speler):
    """Verschillende invoer voor dezelfde hole levert een conflict op."""
    jan = _entry_van(db, "Jan")
    als_speler("Jan").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 4}
    )
    response = als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "marker", "strokes": 6}
    )

    assert response.status_code == 200
    assert "conflict" in response.text
    db.expire_all()
    kaart = build_card(_entry_van(db, "Jan"))
    assert kaart.conflicts == [1]
    assert not kaart.signable


def test_gelijke_invoer_is_geen_conflict(db, wedstrijd, als_speler):
    """Dezelfde score van beide kanten is akkoord."""
    jan = _entry_van(db, "Jan")
    als_speler("Jan").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 4}
    )
    als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "marker", "strokes": 4}
    )

    db.expire_all()
    kaart = build_card(_entry_van(db, "Jan"))
    assert kaart.conflicts == []
    assert kaart.rows[0].agreed


def test_tekenen_faalt_bij_conflict(db, wedstrijd, als_speler):
    """Een openstaand verschil blokkeert de handtekening."""
    _vul_kaart(als_speler, db, "Jan", "Piet")
    jan = _entry_van(db, "Jan")
    als_speler("Piet").post(
        "/api/score", data={"entry_id": jan.id, "hole": 7, "source": "marker", "strokes": 9}
    )

    client = als_speler("Jan")
    response = client.post("/me/sign", data={"akkoord": "ja"}, follow_redirects=False)

    assert response.status_code == 422
    assert "verschil" in response.text
    db.expire_all()
    assert _entry_van(db, "Jan").signed_at is None
    assert not _entry_van(db, "Jan").locked


def test_tekenen_faalt_bij_ontbrekende_hole(db, wedstrijd, als_speler):
    """Zonder alle 18 holes van beide bronnen kan er niet getekend worden."""
    _vul_kaart(als_speler, db, "Jan", "Piet", holes=17)

    client = als_speler("Jan")
    response = client.post("/me/sign", data={"akkoord": "ja"}, follow_redirects=False)

    assert response.status_code == 422
    assert "niet alle holes" in response.text
    db.expire_all()
    assert _entry_van(db, "Jan").signed_at is None


def test_kaart_is_vergrendeld_na_tekenen(db, wedstrijd, als_speler):
    """Na tekenen is de kaart dicht en wordt verdere invoer geweigerd."""
    jan = _vul_kaart(als_speler, db, "Jan", "Piet")

    client = als_speler("Jan")
    response = client.post("/me/sign", data={"akkoord": "ja"}, follow_redirects=False)
    assert response.status_code == 303

    db.expire_all()
    ververst = _entry_van(db, "Jan")
    assert ververst.signed_at is not None
    assert ververst.locked

    daarna = client.post(
        "/api/score", data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": 2}
    )
    assert daarna.status_code == 422
    db.expire_all()
    assert build_card(_entry_van(db, "Jan")).rows[0].self_strokes == 4


def test_tekenen_zonder_vinkje_doet_niets(db, wedstrijd, als_speler):
    """De verklaring moet aangevinkt zijn."""
    _vul_kaart(als_speler, db, "Jan", "Piet")

    response = als_speler("Jan").post("/me/sign", data={}, follow_redirects=False)

    assert response.status_code == 422
    db.expire_all()
    assert not _entry_van(db, "Jan").locked


def test_onmogelijke_score_wordt_geweigerd(db, wedstrijd, als_speler):
    """Nul slagen of 21 slagen slaat niemand op."""
    jan = _entry_van(db, "Jan")
    client = als_speler("Jan")

    for waarde in (0, 21):
        response = client.post(
            "/api/score",
            data={"entry_id": jan.id, "hole": 1, "source": "self", "strokes": waarde},
        )
        assert response.status_code == 422

    db.expire_all()
    assert _entry_van(db, "Jan").scores == []


def test_leaderboard_toont_alleen_scores_waar_beiden_het_eens_zijn(db, wedstrijd, als_speler):
    """Eens = zichtbaar en gekleurd naar par; oneens = leeg; niet gestart = geen regel."""
    from app.scoring import leaderboard

    competition, _ = wedstrijd
    jan = _entry_van(db, "Jan")
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

    assert rijen["Jan"].holes[0] == (3, "birdie")   # par 4, dus birdie
    assert rijen["Jan"].holes[1] == (None, "")      # verschil: niet tonen
    assert rijen["Jan"].to_par == -1
    assert rijen["Jan"].thru == 1
    assert rijen["Jan"].total is None               # ronde loopt nog
    assert "Anne" not in rijen                      # nog niet gestart
    assert "Piet" not in rijen                      # zelf nog niets ingevuld

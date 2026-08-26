"""Het leaderboard bladert op de klok van de server door alle spelers."""

from __future__ import annotations

import pytest

from app.main import PAGINA_SECONDEN, _blader


@pytest.fixture
def klok(monkeypatch):
    """Zet de servertijd op een gekozen minuut."""

    def _zet(minuut: int) -> None:
        monkeypatch.setattr("app.main.time.time", lambda: minuut * PAGINA_SECONDEN)

    return _zet


def test_alles_past_dan_niet_bladeren(klok):
    """Bij minder spelers dan een scherm aankan verandert er niets, welke minuut ook."""
    rijen = list(range(10))
    for minuut in range(5):
        klok(minuut)
        zichtbaar, start, schermen = _blader(rijen, 25)
        assert zichtbaar == rijen
        assert (start, schermen) == (0, 1)


def test_elke_minuut_de_volgende_groep(klok):
    """63 spelers bij 25 per scherm zijn drie schermen die elkaar opvolgen."""
    rijen = list(range(63))

    klok(0)
    assert _blader(rijen, 25) == (rijen[0:25], 0, 3)
    klok(1)
    assert _blader(rijen, 25) == (rijen[25:50], 25, 3)
    klok(2)
    assert _blader(rijen, 25) == (rijen[50:63], 50, 3)
    klok(3)
    assert _blader(rijen, 25) == (rijen[0:25], 0, 3), "na het laatste scherm weer vooraan"


def test_iedereen_komt_een_keer_langs(klok):
    """Over een hele ronde schermen is elke speler precies één keer te zien."""
    rijen = list(range(63))
    gezien: list[int] = []
    for minuut in range(3):
        klok(minuut)
        gezien.extend(_blader(rijen, 25)[0])

    assert sorted(gezien) == rijen


def test_onzinnig_aantal_valt_terug_op_een(klok):
    """Een 0 of negatief getal in de URL mag de pagina niet laten klappen."""
    klok(0)
    zichtbaar, _, schermen = _blader(list(range(4)), 0)
    assert zichtbaar == [0]
    assert schermen == 4

"""De bevestigingsmail na het tekenen.

Er gaat hier niets echt de deur uit: getest wordt of het bericht klopt en of het alleen
gebouwd wordt wanneer dat mag. Het versturen zelf is één POST naar Brevo.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.mail import kaart_bericht
from app.scoring import build_card, sign_card
from tests.helpers import entry_van, vul_kaart


@pytest.fixture
def mail_aan(monkeypatch):
    """Zet de mailinstellingen aan voor de duur van één test."""
    monkeypatch.setattr(settings, "brevo_api_key", "test-sleutel")
    monkeypatch.setattr(settings, "mail_from", "wedstrijd@club.nl")
    monkeypatch.setattr(settings, "base_url", "https://scoring.club.nl")


@pytest.fixture
def getekend(db, wedstrijd):
    """Een getekende kaart van Jan: 18 holes van 4 slagen, marker eens."""
    entry = entry_van(db, "Jan")
    vul_kaart(db, entry, strokes=4)
    sign_card(db, entry)
    return entry


def test_bericht_bevat_de_uitslag(db, getekend, mail_aan):
    """Adres, onderwerp en totaal komen uit de getekende kaart."""
    bericht = kaart_bericht(getekend, build_card(getekend))

    assert bericht["to"] == [{"email": "jan@x.nl", "name": "Jan"}]
    assert bericht["sender"]["email"] == "wedstrijd@club.nl"
    assert "ronde 1" in bericht["subject"]
    assert "Testwedstrijd" in bericht["subject"]
    # 18 holes van 4 slagen op een par-72-baan.
    assert "72 slagen" in bericht["textContent"]
    assert "72" in bericht["htmlContent"]
    assert "Piet" in bericht["textContent"]  # de marker
    assert "https://scoring.club.nl/l/" in bericht["textContent"]


def test_zonder_sleutel_geen_bericht(db, getekend):
    """Zonder API-sleutel staat mailen uit en wordt er niets gebouwd."""
    assert kaart_bericht(getekend, build_card(getekend)) is None


def test_zonder_adres_geen_bericht(db, getekend, mail_aan):
    """Een speler zonder e-mailadres in de CSV krijgt niets."""
    getekend.player.email = None
    assert kaart_bericht(getekend, build_card(getekend)) is None


def test_tekenen_werkt_zonder_mailinstellingen(db, als_speler):
    """De route blijft werken als er geen mailprovider is ingesteld."""
    entry = entry_van(db, "Jan")
    vul_kaart(db, entry, strokes=4)
    client = als_speler("Jan")

    response = client.post("/me/sign", data={"akkoord": "ja"}, follow_redirects=False)

    assert response.status_code == 303
    assert entry_van(db, "Jan").locked

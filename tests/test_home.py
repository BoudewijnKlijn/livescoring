"""De voordeur op /. Wie livescoring.nl intypt hoort geen inlogfout te krijgen."""

from __future__ import annotations


def test_home_is_geen_redirect_naar_de_kaart(client):
    """/ stuurde door naar /me/card, en dat geeft zonder cookie een inlogfout."""
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200


def test_home_werkt_zonder_login(client):
    """Een toevallige bezoeker zonder cookie krijgt gewoon een pagina."""
    response = client.get("/")

    assert response.status_code == 200
    assert "Live scoring" in response.text

"""Bevestigingsmail naar de speler zodra hij zijn kaart getekend heeft.

Verstuurd via de HTTP-API van Brevo en bewust niet via SMTP. De gratis web services van
Render blokkeren sinds 26 september 2025 al het uitgaande verkeer naar poort 25, 465 en 587,
dus `smtplib` komt daar niet langs: dat werkt op je laptop en faalt stil in productie. Een
POST naar poort 443 gaat er wel uit. Brevo mag daarbij vanaf één geverifieerd afzenderadres
sturen, dus je hebt geen eigen domein nodig.

Staat `brevo_api_key` of `mail_from` leeg, dan is mailen uit en gebeurt er niets. Zo draaien
de tests en een lokale start zonder sleutel, en kun je de mail op de dag zelf uitzetten door
de variabele weg te halen.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import Entry
from app.scoring import Card, par_klasse

log = logging.getLogger(__name__)

API = "https://api.brevo.com/v3/smtp/email"
TIJDZONE = ZoneInfo("Europe/Amsterdam")

# Dezelfde kleuren als op het scherm, maar als losse hex-waarden: mailclients gooien een
# stylesheet met CSS-variabelen weg, inline stijlen laten ze staan.
ACHTERGROND = {"eagle": "#ffd84d", "birdie": "#ed5b5b", "bogey": "#a9d5e9", "dubbel": "#3975a5"}
WITTE_TEKST = {"birdie", "dubbel"}


def kaart_bericht(entry: Entry, card: Card) -> dict | None:
    """Bouw het bericht voor een zojuist getekende kaart, of None als er niets te sturen valt.

    Wordt aangeroepen terwijl de databasesessie nog openstaat en levert platte tekst op, zodat
    het versturen daarna zonder database kan.
    """
    adres = (entry.player.email or "").strip()
    if not (settings.brevo_api_key and settings.mail_from) or "@" not in adres:
        return None

    ronde = entry.round
    competitie = ronde.competition
    onderwerp = f"Scorekaart getekend - {competitie.name}, ronde {ronde.no}"
    return {
        "sender": {"name": settings.mail_from_name, "email": settings.mail_from},
        "to": [{"email": adres, "name": entry.player.name}],
        "subject": onderwerp,
        "textContent": _tekst(entry, card),
        "htmlContent": _html(entry, card, onderwerp),
    }


def verstuur(bericht: dict) -> None:
    """Stuur één bericht. Faalt nooit hard: een mail mag een getekende kaart niet raken."""
    ontvanger = bericht["to"][0]["email"]
    verzoek = urllib.request.Request(
        API,
        data=json.dumps(bericht).encode("utf-8"),
        headers={
            "api-key": settings.brevo_api_key,
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(verzoek, timeout=20) as antwoord:
            log.info("Mail naar %s verstuurd (HTTP %s).", ontvanger, antwoord.status)
    except urllib.error.HTTPError as exc:
        uitleg = exc.read().decode("utf-8", "replace")[:400]
        log.error("Brevo weigerde de mail naar %s: HTTP %s %s", ontvanger, exc.code, uitleg)
    except Exception as exc:  # netwerk plat, DNS stuk, timeout
        log.error("Mail naar %s mislukt: %s", ontvanger, exc)


def _tov_par(slagen: int) -> str:
    """Het aantal slagen ten opzichte van par, zoals je het op een leaderboard leest."""
    return f"{slagen:+d}" if slagen else "par"


def _negen(card: Card, eerste: int, laatste: int) -> int:
    """Totaal van een negen. Op een getekende kaart is elke hole ingevuld."""
    return sum(r.self_strokes or 0 for r in card.rows if eerste <= r.hole <= laatste)


def _tijdstip(entry: Entry) -> str:
    """Het moment van tekenen in Nederlandse tijd. De database bewaart UTC."""
    return entry.signed_at.astimezone(TIJDZONE).strftime("%d-%m-%Y om %H:%M")


def _marker(entry: Entry) -> str:
    """De naam van de marker. Kan ontbreken als de wedstrijdleiding hem heeft losgekoppeld."""
    return entry.marker.player.name if entry.marker else "onbekend"


def _stand_link(entry: Entry) -> str:
    """De publieke leaderboardlink van deze wedstrijd."""
    slug = entry.round.competition.leaderboard_slug
    return f"{settings.base_url.rstrip('/')}/l/{slug}"


def _tekst(entry: Entry, card: Card) -> str:
    """De platte-tekstversie, voor clients die geen HTML tonen."""
    ronde = entry.round

    def blok(eerste: int, laatste: int, naam: str) -> str:
        rijen = [r for r in card.rows if eerste <= r.hole <= laatste]

        def regel(kop: str, waarden: list, totaal) -> str:
            return kop.ljust(6) + "".join(str(w).rjust(4) for w in waarden) + str(totaal).rjust(6)

        return "\n".join(
            [
                regel("Hole", [r.hole for r in rijen], naam),
                regel("Par", [r.par for r in rijen], sum(r.par for r in rijen)),
                regel("Score", [r.self_strokes for r in rijen], _negen(card, eerste, laatste)),
            ]
        )

    return f"""Beste {entry.player.name},

Je scorekaart is getekend op {_tijdstip(entry)}.

{ronde.competition.name} - ronde {ronde.no}
Totaal: {card.total} slagen ({_tov_par(card.to_par)})
Marker: {_marker(entry)}

{blok(1, 9, "Uit")}

{blok(10, 18, "In")}

De stand volg je hier: {_stand_link(entry)}

Dit bericht is automatisch verstuurd. Klopt er iets niet, meld je dan bij de
wedstrijdleiding: na het tekenen kan alleen zij de kaart nog openen.
"""


def _html(entry: Entry, card: Card, onderwerp: str) -> str:
    """De opgemaakte versie: twee negens onder elkaar, zodat het op een telefoon past."""
    ronde = entry.round
    naam = html.escape(entry.player.name)
    wedstrijd = html.escape(ronde.competition.name)
    return f"""\
<div style="font-family:Helvetica,Arial,sans-serif;color:#333;max-width:520px;margin:0 auto;
            padding:20px 4px;line-height:1.5">
  <p>Beste {naam},</p>
  <p>Je scorekaart is getekend op {_tijdstip(entry)}.</p>

  <div style="background:#e6eff6;border-left:4px solid #005b9a;padding:14px 16px;
              margin-bottom:22px">
    <div style="font-size:2rem;font-weight:600;color:#005b9a;line-height:1.1">
      {card.total} <span style="font-size:0.95rem;font-weight:400;color:#555">slagen</span>
    </div>
    <div style="color:#555;font-size:0.9rem;margin-top:4px">
      {_tov_par(card.to_par)} &middot; {wedstrijd} &middot; ronde {ronde.no}
      &middot; marker {html.escape(_marker(entry))}
    </div>
  </div>

  {_html_negen(card, 1, 9, "Uit")}
  {_html_negen(card, 10, 18, "In")}

  <p style="margin:22px 0 0">
    <a href="{html.escape(_stand_link(entry))}"
       style="background:#005b9a;color:#fff;text-decoration:none;padding:11px 18px;
              display:inline-block;font-weight:600">Bekijk de stand</a>
  </p>
  <p style="color:#777;font-size:0.8rem;margin-top:26px;border-top:1px solid #e2e2e2;
            padding-top:12px">
    {html.escape(onderwerp)}. Dit bericht is automatisch verstuurd. Klopt er iets niet, meld
    je dan bij de wedstrijdleiding: na het tekenen kan alleen zij de kaart nog openen.
  </p>
</div>"""


def _html_negen(card: Card, eerste: int, laatste: int, naam: str) -> str:
    """Eén negen als tabelletje, met dezelfde kleuren als de kaart in de app."""
    rijen = [r for r in card.rows if eerste <= r.hole <= laatste]
    rand = "border:1px solid #e2e2e2;padding:6px 0;text-align:center;font-size:0.85rem"
    kop = "".join(f'<th style="{rand};color:#777;font-weight:400">{r.hole}</th>' for r in rijen)
    pars = "".join(f'<td style="{rand};color:#777">{r.par}</td>' for r in rijen)

    scores = ""
    for r in rijen:
        klasse = par_klasse(r.self_strokes, r.par)
        kleur = ACHTERGROND.get(klasse, "#ffffff")
        tekst = "#ffffff" if klasse in WITTE_TEKST else "#333333"
        scores += (
            f'<td style="{rand};background:{kleur};color:{tekst};font-weight:600">'
            f"{r.self_strokes}</td>"
        )

    totaal = f"{rand};background:#f4f5f7;width:46px;font-weight:600"
    return f"""
  <table style="border-collapse:collapse;width:100%;margin-bottom:14px" cellpadding="0"
         cellspacing="0">
    <tr><th style="{totaal};color:#777">{naam}</th>{kop}</tr>
    <tr><td style="{totaal};color:#777">{sum(r.par for r in rijen)}</td>{pars}</tr>
    <tr><td style="{totaal}">{_negen(card, eerste, laatste)}</td>{scores}</tr>
  </table>"""

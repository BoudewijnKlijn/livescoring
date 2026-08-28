"""De enige tests die naar de opmaak kijken.

Alleen hier staan tags, klassen en kolomvolgordes. Overal elders praten de tests met de
domeinfuncties, zodat een nieuw ontwerp nooit een test over scores of imports kan breken.
Breekt er iets in dit bestand na een ontwerpwijziging, dan is dat de bedoeling: dan is de
weergave veranderd en hoort de verwachting mee te veranderen.
"""

from __future__ import annotations

import re

from app.importer import import_csv
from tests.helpers import entry_van, vul_kaart

ADMIN = {"wachtwoord": "testwachtwoord"}


def _kolommen(html: str) -> list[str]:
    """De koppen van de leaderboardtabel, op volgorde."""
    kop = html[html.index("<thead>") : html.index("</tr>", html.index("<thead>"))]
    return [
        " ".join(re.sub(r"<[^>]+>", "", c).split())
        for c in re.findall(r"<th\b[^>]*>(.*?)</th>", kop, re.S)
    ]


def _bord(client, competition, ronde: int) -> str:
    return client.get(f"/l/{competition.leaderboard_slug}?r={ronde}").text


# --- het leaderboard -------------------------------------------------------------------


def test_een_ronde_toont_de_holes_en_een_totaal(db, toernooi, client):
    """Bij één ronde staat er achter de holes alleen het totaal van die ronde."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)

    kolommen = _kolommen(_bord(client, competition, 1))

    assert kolommen[:3] == ["", "Speler", "+/-"]
    assert kolommen[-3:] == ["18", "In", "Tot"]
    assert "Uit" in kolommen, "de eerste negen tellen op onder Uit"
    assert "R1" not in kolommen, "één ronde heeft geen rondekolommen nodig"


def test_meer_ronden_krijgen_een_kolom_per_ronde(db, toernooi, client):
    """Ronde 2: +/-, dan de holes met Uit en In, dan R1, R2 en het totaal."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Jan", 2), 5)

    tekst = _bord(client, competition, 2)

    assert _kolommen(tekst)[-5:] == ["18", "In", "R1", "R2", "Tot"]
    assert ">72<" in tekst, "het resultaat van ronde 1"
    assert ">90<" in tekst, "de ronde van vandaag"
    assert ">162<" in tekst, "beide ronden samen"


def test_drie_ronden_geven_drie_kolommen(db, toernooi, client):
    """Bij drie ronden staan R1, R2 en R3 los van elkaar op het bord."""
    competition, _ = toernooi
    import_csv(
        db, competition, "naam,ronde,flight,starthole,marker\nJan,3,A,1,Piet\nPiet,3,A,1,Jan\n"
    )
    db.refresh(competition)
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Jan", 3), 3)

    assert _kolommen(_bord(client, competition, 3))[-5:] == ["In", "R1", "R2", "R3", "Tot"]


def test_scores_krijgen_een_blokje_naar_hun_resultaat(db, toernooi, client):
    """Elke hole een blokje, met de klasse van eagle tot dubbel bogey."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 3, holes=1)  # hole 1 is een par 5: een eagle

    tekst = _bord(client, competition, 1)

    assert '<span class="score eagle">3</span>' in tekst
    assert "Eagle of beter" in tekst, "de kleursleutel staat onder het bord"
    assert tekst.count('<td class="vak"></td>') == 35, "lege holes krijgen geen blokje"


def test_de_pagina_polt_de_tabel_van_dezelfde_ronde(db, toernooi, client):
    """De poll mag niet stiekem naar een andere ronde springen."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)

    pagina = _bord(client, competition, 1)

    assert f"/l/{competition.leaderboard_slug}/table?n=25&amp;r=1" in pagina
    assert "Ronde 1" in pagina, "de kop noemt de ronde bij meer dan één ronde"


def test_naam_draagt_geen_rondenummer_meer(db, toernooi, client):
    """Het bord gaat over één ronde, dus hoeft de naam geen R1 of R2 te dragen."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 2), 4)

    tekst = _bord(client, competition, 2)

    assert "· R2" not in tekst
    assert "Jan" in tekst


def test_uitvaller_staat_er_zonder_plaats_bij(db, toernooi, client):
    """Wie uitviel houdt zijn regel, maar krijgt geen positie in de rangschikking."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Piet", 1), 5)
    entry_van(db, "Jan", 1).status = "wd"
    db.commit()

    tekst = _bord(client, competition, 1)

    rijen = re.findall(r'<tr class="([^"]*)">.*?class="speler">\s*([^<\n]+)', tekst, re.S)
    assert ("uitgevallen", "Jan") in [(k.strip(), n.strip()) for k, n in rijen]
    assert '<span class="pil rood">WD</span>' in tekst


def test_wie_nog_niets_heeft_krijgt_geen_plaats(db, toernooi, client):
    """Een nummer naast een lege regel zou een rangschikking suggereren die er niet is."""
    competition, _ = toernooi
    vul_kaart(db, entry_van(db, "Jan", 1), 4)

    tekst = _bord(client, competition, 1)

    assert tekst.count('<td class="pos">1</td>') == 1, "alleen Jan staat gerangschikt"
    assert tekst.count('<td class="pos"></td>') == 1, "Piet staat er zonder plaats"


# --- de scorekaart van de speler -------------------------------------------------------


def test_kaartkop_noemt_de_wedstrijd_de_ronde_en_de_rollen(db, wedstrijd, als_speler):
    """Boven de kaart staat waar je bent, wie jou markt en wie jij markt."""
    pagina = als_speler("Jan").get("/me/card").text

    kop = pagina[pagina.index('class="kaartkop"') : pagina.index("</dl>")]
    assert "Testwedstrijd" in kop
    assert "Ronde 1" in kop
    labels = re.findall(r"<dt>(.*?)</dt>", kop)
    velden = re.findall(r"<dd>(.*?)</dd>", kop, re.S)
    namen = [" ".join(re.sub(r"<[^>]+>", "", d).split()) for d in velden]
    assert labels == ["Speler", "Jouw marker", "Jij markeert"]
    assert namen == ["Jan", "Piet", "Piet"], "in een tweebal markeren ze elkaar"


def test_de_kaart_zelf_blijft_het_grootst(db, wedstrijd, als_speler):
    """De kop is er om één keer te lezen; de invoervakken zijn het werk."""
    pagina = als_speler("Jan").get("/me/card").text

    assert pagina.index('class="kaartkop"') < pagina.index('class="kaarttabel"')
    assert 'class="kop"' in pagina, "de kaart houdt zijn eigen namenkolom"


# --- het beheerscherm ------------------------------------------------------------------


def test_de_rail_noemt_alles_wat_je_kunt_doen(db, wedstrijd, client):
    """Gegroepeerd naar wanneer je het nodig hebt, het gevaarlijkste onderaan."""
    competition, _ = wedstrijd
    client.post("/admin/login", data=ADMIN)

    pagina = client.get(f"/admin/c/{competition.id}").text

    groepen = re.findall(r'<p class="raillabel">(.*?)</p>', pagina)
    items = re.findall(r'href="/admin/c/\d+\?p=(\w+)"', pagina)
    assert groepen == ["Tijdens de wedstrijd", "Opzetten en afronden", "Onomkeerbaar"]
    assert items[:3] == ["spelers", "leaderboard", "score"]
    assert items[-3:] == ["leegmaken", "links", "wissen"]
    assert len(set(items)) == 12


def test_de_rail_wijst_het_paneel_aan_dat_openstaat(db, wedstrijd, client):
    """Zonder keuze staat de spelerslijst open; met ?p= dat paneel."""
    competition, _ = wedstrijd
    client.post("/admin/login", data=ADMIN)

    standaard = client.get(f"/admin/c/{competition.id}").text
    export = client.get(f"/admin/c/{competition.id}?p=export").text
    onzin = client.get(f"/admin/c/{competition.id}?p=bestaatniet").text

    assert "?p=spelers" in re.search(r'class="railitem nu"[^>]*href="([^"]+)"', standaard).group(1)
    assert "<h1>Spelers</h1>" in standaard
    assert "<h1>Export</h1>" in export
    assert "Uitslag CSV" in export
    assert "Uitslag CSV" not in standaard, "één paneel tegelijk in het werkblad"
    assert "<h1>Spelers</h1>" in onzin, "een onbekend paneel valt terug op de lijst"


def test_voortgangsstrip_volgt_de_bevestigde_holes(db, wedstrijd, client):
    """Elke hole een streepje, gevuld zodra speler en marker het eens zijn."""
    competition, _ = wedstrijd
    vul_kaart(db, entry_van(db, "Jan"), 4, holes=3)
    client.post("/admin/login", data=ADMIN)

    pagina = client.get(f"/admin/c/{competition.id}").text

    strip = re.search(r'<span class="strip"[^>]*>(.*?)</span>', pagina, re.S).group(1)
    assert strip.count('class="vol"') == 3
    assert strip.count('class="leeg"') == 15
    assert "<b>1</b> onderweg" in pagina


def test_mislukte_import_opent_het_importpaneel(db, wedstrijd, client):
    """Anders staat de foutmelding in beeld terwijl het geplakte bestand elders zit."""
    competition, _ = wedstrijd
    client.post("/admin/login", data=ADMIN)

    antwoord = client.post(
        f"/admin/c/{competition.id}/import",
        data={"csv_tekst": "naam,ronde,flight,starthole,marker\nJan,1,B,10,\n"},
    )

    assert antwoord.status_code == 422
    assert "<h1>Spelers importeren</h1>" in antwoord.text
    assert "dezelfde flight" in antwoord.text
    assert 'class="railitem nu"' in antwoord.text


def test_pars_van_de_getoonde_ronde_staan_boven_het_bord(db, toernooi, client):
    """Twee banen, twee parregels: het bord toont die van de ronde die je bekijkt."""
    competition, _ = toernooi
    ronde2 = [r for r in competition.rounds if r.no == 2][0]
    ronde2.pars = [3] * 18
    db.commit()
    vul_kaart(db, entry_van(db, "Jan", 1), 4)
    vul_kaart(db, entry_van(db, "Jan", 2), 4)

    assert ">72<" in _bord(client, competition, 1), "par 72"
    assert ">54<" in _bord(client, competition, 2), "par 54"

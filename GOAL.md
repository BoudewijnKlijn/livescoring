# Opdracht: digitale scorekaart + live leaderboard voor clubkampioenschappen

Bouw een werkende webapp waarmee deelnemers aan een golfwedstrijd via een persoonlijke
link inloggen, een marker kiezen, per hole bruto scores invoeren, verschillen oplossen en
hun kaart digitaal ondertekenen. Een admin beheert alles en exporteert het resultaat.

Dit is een hobbyproject dat komend weekend draait bij echte clubkampioenschappen.
Prioriteit is een kloppende flow en betrouwbare opslag, niet volledigheid van de golfregels.

## Stack

Gebruik dit, tenzij je een concreet blokkerend probleem tegenkomt. Meld het dan eerst.

- Python 3.12, dependencies beheerd met `uv` (`uv init`, `uv add`, `uv run`)
- FastAPI met Jinja2 templates, server-rendered HTML
- HTMX voor partial updates en polling, geen SPA, geen build step
- Tailwind via de Play CDN, geen bundler
- Postgres via SQLAlchemy 2.0 async + asyncpg, Alembic voor migraties
- Pydantic Settings voor config uit environment variables
- pytest voor tests

Geen React, geen Next.js, geen Supabase client libraries. De database is gewoon Postgres.

## Codestijl

Weinig inline comments, wel docstrings op modules, klassen en niet-triviale functies.
Kort en bondig maar compleet. Type hints overal. Ruff-clean.

## Kernbeslissingen die vastliggen

1. **De server is de enige bron van waarheid.** Elke score-invoer gaat direct via een
   POST naar de database. Sluit iemand de tab, dan staat alles er al. localStorage mag
   hooguit als tijdelijke retry-buffer dienen bij netwerkfouten, nooit als primaire opslag.
2. **Bruto strokeplay.** Geen handicap, geen stableford, geen course rating of slope.
   Aantal slagen per hole, opgeteld. Meer niet.
3. **Dubbele invoer.** Elke speler voert zijn eigen scores in, en de scores van de speler
   voor wie hij marker is. Die twee moeten per hole gelijk zijn voordat er getekend kan
   worden.
4. **Toegang via een persoonlijke link.** Geen wachtwoorden voor spelers. De admin heeft
   wel een wachtwoord.
5. **Alles moet werken op een telefoon in de zon met dikke vingers.** Grote tapdoelen,
   minimaal 44px, geen kleine dropdowns, geen hover-afhankelijke UI.

## Datamodel

Maak deze tabellen met Alembic-migraties.

**competition**: id, name, date, course_name, hole_count (default 18), pars (JSON array van
18 ints, default allemaal 4), status enum (`setup`, `live`, `closed`), created_at.

**player**: id, competition_id FK, name, email (nullable), token_hash (unieke index),
created_at. Het token zelf staat nooit in de database, alleen de sha256-hash.

**flight**: id, competition_id FK, name, tee_time (nullable), start_hole (default 1).

**flight_player**: id, flight_id FK, player_id FK unieke index, marker_player_id FK
nullable, position int. De marker moet in dezelfde flight zitten en mag niet de speler
zelf zijn. Forceer dat in de applicatielaag en met een check constraint waar het kan.

**hole_score**: id, flight_player_id FK, hole int (1..18), strokes int nullable,
source enum (`self`, `marker`), entered_by_player_id FK, updated_at.
Unieke constraint op (flight_player_id, hole, source).
Let op: `flight_player_id` is de speler over wie de score gaat, `entered_by_player_id` is
wie hem intikte. Bij source `self` zijn die gelijk.

**card**: id, flight_player_id FK uniek, signed_by_player_at nullable,
attested_by_marker_at nullable, locked bool default false, unlocked_by_admin_at nullable.

**audit_log**: id, at, actor (tekst, bv `player:12` of `admin`), action, detail JSON.
Log elke score-wijziging, elke markerkeuze, elke handtekening en elke admin-ingreep.

## Authenticatie

Spelerslink: genereer bij het aanmaken van een speler een token van 32 random bytes
(`secrets.token_urlsafe(32)`). Sla alleen de sha256-hash op. De volledige link is
`/t/{token}`.

`GET /t/{token}` zoekt de hash op, zet een httpOnly, secure, samesite=lax cookie met een
door `itsdangerous` ondertekende player_id, en redirect naar `/me`. De cookie is 7 dagen
geldig, zodat niemand tijdens de ronde de link opnieuw hoeft te openen.

Zet op alle pagina's `<meta name="robots" content="noindex, nofollow">` en stuur
`Referrer-Policy: no-referrer`, zodat tokens niet uitlekken via de Referer-header.

Admin: één wachtwoord uit environment variable `ADMIN_PASSWORD`, formulier op
`/admin/login`, aparte ondertekende cookie. Geen gebruikersnaam nodig.

Zonder geldige cookie geeft elke route behalve `/t/{token}` en `/admin/login` een 401 met
een uitlegpagina. Ook het leaderboard. Niets is publiek.

## Mail

Verstuur mail via Resend als `RESEND_API_KEY` gezet is. Is die niet gezet, log de links dan
alleen.

Belangrijk: bouw het admin-scherm zo dat het ook zonder mail werkt. De adminpagina toont
per speler de volledige link met een kopieerknop, plus een knop "kopieer alle links als
tekst" die een lijst `Naam: https://...` op het klembord zet. Dat is de primaire manier
waarop de wedstrijdcommissie de links verspreidt, via WhatsApp of mail-merge. Mail is
optioneel extraatje.

## Schermen

### Speler

`GET /me` toont de wedstrijd, de flight met medespelers, de eigen markerstatus en de status
van de flight. Grote knop naar het invoerscherm zodra iedereen in de flight een marker heeft.

`GET/POST /me/marker` laat de speler een marker kiezen uit de andere spelers in zijn flight.
Heeft de admin al een marker toegewezen, toon die dan als vaststaand en sla het scherm over.
Zolang niet iedereen gekozen heeft, toon wie er nog ontbreekt. Ververs dat blok elke 5
seconden met HTMX.

`GET /me/card` is het invoerscherm. Dit is het belangrijkste scherm van de app.
Toon per hole een rij met het holenummer, de par, een invoer voor de eigen score en een
invoer voor de score van de speler voor wie deze gebruiker marker is.
Invoer gebeurt met plus- en minknoppen rond een getal, plus een numeriek toetsenbordveld.
Standaardwaarde is leeg, niet par.
Elke wijziging stuurt direct een `POST /api/score` met flight_player_id, hole, strokes en
source. De response geeft de nieuwe stand van die hole terug, inclusief of er nu een
conflict is.
Toon rechtsboven permanent een sync-indicator met drie toestanden: opgeslagen, bezig,
mislukt. Bij mislukt komt er een herhaalknop en blijft de invoer in een queue in het
geheugen staan die automatisch elke 3 seconden opnieuw probeert.
Waarschuw met `beforeunload` als de queue niet leeg is.

Conflicten: als de eigen invoer en de invoer van de marker voor dezelfde hole verschillen,
kleur die rij rood en toon beide getallen met wie ze invoerde. Bovenaan een teller
"3 holes met een verschil". Beide partijen kunnen hun eigen invoer aanpassen. Niemand kan
de invoer van de ander overschrijven, alleen de admin kan dat.

`GET/POST /me/sign` toont het overzicht van de eigen kaart met totaal, en tekent pas als
alle 18 holes voor beide bronnen ingevuld zijn en er geen conflicten zijn. Anders een
duidelijke lijst van wat er nog mist.
Ondertekenen is een checkbox met de tekst "Ik verklaar dat deze scores juist zijn" plus een
knop. Sla `signed_by_player_at` op. De marker krijgt daarna op zijn eigen `/me` een blok
"bevestig de kaart van X", wat `attested_by_marker_at` zet. Zodra beide gezet zijn wordt
`locked` true en is verdere invoer geblokkeerd.

### Leaderboard

`GET /leaderboard` toont alle spelers gesorteerd op totaal aantal slagen, oplopend, met
kolommen naam, holes gespeeld, totaal en status (bezig, getekend, conflict).
Voor het totaal telt de scores met source `self`, en markeer per speler of er onbevestigde
of conflicterende holes zijn.
Ververs met HTMX polling elke 5 seconden op alleen de tabel, niet de hele pagina.
Gebruik geen websockets en geen SSE. Polling is voor dit aantal spelers ruim genoeg en
gaat niet stuk op een instabiel mobiel netwerk.

### Admin

`GET /admin` met daarin:

- Wedstrijd aanmaken en bewerken, inclusief de 18 pars.
- Spelers toevoegen, los en via een plak-veld waarin je regels `Naam, email` plakt.
- Flights aanmaken en spelers erin slepen of via dropdowns toewijzen. Markers optioneel
  vooraf vastleggen.
- Overzicht van alle links met kopieerknoppen zoals hierboven beschreven.
- Live overzicht van alle flights met voortgang en conflicten, elke 5 seconden ververst.
- Elke score handmatig overschrijven, met verplichte reden die in de audit log komt.
- Kaart ontgrendelen na ondertekening.
- `GET /admin/export.csv` met per speler een regel: naam, email, flight, hole 1 t/m 18,
  totaal, getekend ja/nee, tijdstip ondertekening. Ook een tweede export met alle
  conflicten en admin-correcties.

## Seed en testen

Maak een CLI-command `uv run python -m app.seed --demo` dat een testwedstrijd aanmaakt met
2 flights van 4 spelers, met markers al toegewezen, en de 8 links naar stdout print.
Daarmee kan ik in acht browsertabs in mijn eentje de hele flow doorlopen.

Schrijf pytest-tests voor in elk geval:

- een score opslaan en terugvinden
- conflictdetectie tussen self en marker
- tekenen mislukt bij een openstaand conflict
- tekenen mislukt bij een ontbrekende hole
- een kaart is na ondertekening en bevestiging vergrendeld
- iemand kan geen score invoeren voor een speler waarvan hij geen marker is
- een ongeldig of hergebruikt token geeft 401

Die laatste twee zijn de belangrijkste. Autorisatie per request controleren, niet alleen in
de template verbergen.

## Deployment

Schrijf een `Dockerfile` en een `fly.toml` voor Fly.io, met `min_machines_running = 1` zodat
de machine tijdens de wedstrijd niet in slaap valt.
De database is een gratis Supabase Postgres. Gebruik de transaction pooler connection
string en zet `?prepared_statement_cache_size=0` voor asyncpg, anders gaat pgbouncer
klagen.
Zet in de README exact welke environment variables nodig zijn en de commando's om lokaal te
draaien met `uv run uvicorn app.main:app --reload` en een lokale Postgres via docker compose.

## Buiten scope

Bouw deze dingen niet, ook niet "vast een beetje":

- handicapberekening, stableford, playing handicap, course rating, slope
- koppeling met NGF, GOLF.NL, e-Golf4U of welk clubsysteem dan ook
- offline-first met service workers
- meerdere wedstrijden tegelijk live
- meertaligheid, alles is Nederlands
- e-mailnotificaties buiten de magic link

## Aanpak

Werk in deze volgorde en commit per stap:

1. Project opzetten met uv, FastAPI, Alembic, docker compose voor lokale Postgres.
2. Datamodel en migraties.
3. Auth met tokens en admin login, inclusief de tests voor autorisatie.
4. Admin-CRUD en het linkenoverzicht.
5. Seed-command.
6. Spelerflow: /me, markerkeuze, invoerscherm met directe opslag.
7. Conflictdetectie en ondertekenen.
8. Leaderboard.
9. Export.
10. Dockerfile, fly.toml, README.

Vraag me niet om bevestiging tussen de stappen. Draai na elke stap de tests. Kom je een
ontwerpkeuze tegen die deze opdracht niet dekt, maak dan de simpelste keuze en noteer hem
in een lijst `OPEN-KEUZES.md` in de repo.
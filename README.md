# Live scoring

Digitale scorekaart en live leaderboard voor clubkampioenschappen. Spelers loggen in via een
persoonlijke link, voeren hun eigen scores in plus die van de speler voor wie ze marker zijn,
lossen verschillen op en tekenen hun kaart. Toeschouwers volgen de stand live.

## Lokaal draaien

```bash
docker compose up -d                          # Postgres op poort 5434
uv run uvicorn app.main:app --reload --port 8001   # http://localhost:8001
uv run python -m app.seed --demo              # demo-wedstrijd + 8 links
uv run pytest                                 # de testsuite
uv run ruff check app tests                   # lint
```

De instellingen komen uit `.env.local`, dat naar de lokale Postgres wijst. De volgorde is:
echte environment variables winnen altijd, daarna `.env.local`, daarna `.env`. Zo draait een
lokale start nooit per ongeluk tegen de productiedatabase. Op Render bestaan beide bestanden
niet en komen de waarden uit het dashboard.

Let op de driver in `DATABASE_URL`: dit project gebruikt psycopg 3, dus het schema is
`postgresql+psycopg://`. Supabase toont een voorbeeld met `postgresql+psycopg2://`, dat is de
oudere driver en die zit hier niet in.

De demo maakt vier spelers in twee ronden. Open de links van ronde 1 in vier tabbladen en je
kunt de hele flow in je eentje doorlopen, inclusief het forceren van een conflict.

## Environment variables

| Variabele | Nodig | Betekenis |
|---|---|---|
| `DATABASE_URL` | ja | `postgresql+psycopg://...`. Lokaal die uit `.env.example`, in productie de Supabase **session pooler**. |
| `SECRET_KEY` | ja | Ondertekent de cookies. Wijzig je hem, dan is iedereen uitgelogd. |
| `ADMIN_PASSWORD` | ja | Wachtwoord voor `/admin/login`. |
| `BASE_URL` | ja | Publieke URL zonder slash op het eind. Staat in de gedeelde links. |
| `COOKIE_SECURE` | productie | `true` achter HTTPS, `false` lokaal. |
| `BREVO_API_KEY` | nee | Sleutel voor de bevestigingsmail. Leeg = geen mail. Zie hieronder. |
| `MAIL_FROM` | bij mail | Het afzenderadres dat je bij Brevo geverifieerd hebt. |
| `MAIL_FROM_NAME` | nee | Naam voor de afzender. Standaard `Wedstrijdleiding`. |

## CSV-import

Eén regel per speler per ronde. Speelt iemand twee ronden, dan staat hij er twee keer in.

```csv
naam,email,ronde,flight,starthole,marker
Jan de Vries,jan@x.nl,1,A,1,Piet Bakker
Piet Bakker,piet@x.nl,1,A,1,Jan de Vries
```

`starthole` is 1 of 10 en geldt voor de hele flight, dus ook voor spelers uit die flight die
niet in het bestand staan. `marker` is verplicht en is de naam van iemand anders in dezelfde
flight en ronde. Twee spelers die elkaar markeren mag, en een kring van drie ook.

Bij één fout wordt er niets geïmporteerd. Opnieuw importeren mag, en gaat zo:

- Een speler wordt herkend aan zijn naam, ongeacht hoofdletters en dubbele spaties. Een
  andere schrijfwijze is een andere speler.
- Het bestand wint voor wat het zegt: flight, starthole, marker en e-mail volgen het bestand.
  Wat het bestand niet noemt blijft zoals het was, tot en met spelers die er niet in staan en
  een lege markerkolom.
- Scores en persoonlijke links blijven altijd staan, ook bij een verhuizing, bij een nieuwe
  marker en op een getekende kaart. Die blijft ook getekend.
- Na afloop heeft elke speler precies één marker, zit die marker in dezelfde ronde en
  flight, en markt niemand meer dan één speler. Klopt dat niet, dan gaat de hele import niet
  door en noemt de melding de speler en de flight, zodat je het bestand kunt verbeteren. Dat
  is met opzet streng: anders blijven er spelers achter die niemand kan bevestigen en die
  dus nooit kunnen tekenen. Hierdoor valt een verkeerd gespelde naam meteen op, want die
  pikt de marker van de goed gespelde speler in.
- Een marker die al in het systeem staat telt mee, ook als hij niet in dit bestand staat. Zo
  kun je één flight opnieuw aanleveren zonder de rest erbij te plakken.
- Wisselt een speler van marker terwijl de oude marker al scores schreef, dan blijven die
  scores staan: die holes zijn echt samen gelopen. De nieuwe marker kan ze overschrijven.

## Bevestigingsmail

Tekent een speler zijn kaart, dan krijgt hij zijn scores per mail: het totaal, de twee
negens hole voor hole en de link naar de stand. Dat is meteen zijn eigen bewijs van wat er
is ingeleverd. Spelers zonder e-mailadres in de CSV krijgen niets, de rest van de flight
wel.

De mail gaat de deur uit *nadat* de speler zijn bevestiging op het scherm heeft. Ligt Brevo
plat, dan blijft de kaart gewoon getekend en staat de fout in de log. Tekenen mag nooit
stukgaan op een mailserver.

### Waarom niet gewoon Gmail

Omdat het niet werkt waar deze app draait. Render blokkeert op de gratis web services sinds
26 september 2025 al het uitgaande verkeer naar poort 25, 465 en 587. `smtplib` met een
Gmail-app-wachtwoord doet het dus prima op je laptop en faalt stil op Render, precies op de
dag dat het moet werken. Brevo wordt daarom over HTTPS aangesproken, en poort 443 is niet
geblokkeerd. Wil je toch SMTP, dan kost dat de goedkoopste betaalde Render-instance of een
verhuizing naar Fly.io.

### Brevo instellen

1. Maak een gratis account op [brevo.com](https://www.brevo.com). Geen creditcard nodig,
   300 mails per dag, en dat blijft gratis. Ruim genoeg: elke speler krijgt één mail per
   ronde.
2. Ga naar **Senders, Domains & Dedicated IPs → Senders → Add a sender** en zet daar het
   adres neer waar de mail vandaan moet komen, bijvoorbeeld je eigen adres of dat van de
   wedstrijdcommissie. Brevo stuurt er een bevestigingsmail heen; klik die link aan. Een
   eigen domein heb je hiervoor niet nodig, één geverifieerd adres is genoeg. Dit adres
   wordt `MAIL_FROM`, precies zoals je het daar invult.
3. Ga naar **SMTP & API → API Keys → Generate a new API key**. Kopieer de sleutel meteen:
   Brevo laat hem net als de spelerslinks maar één keer zien. Dit wordt `BREVO_API_KEY`.
4. Zet `BREVO_API_KEY` en `MAIL_FROM` in het Render-dashboard, en lokaal in `.env.local`.
   Zonder sleutel is de mail uit, dus je tests en je lokale werk sturen nooit per ongeluk
   iets naar echte spelers.
5. Test het een keer voor de wedstrijd: importeer een CSV met je eigen adres erin, vul een
   kaart en teken hem. Komt er niets aan, kijk dan in de Render-logs (`Mail naar ...`) en in
   het **Transactional → Logs**-scherm van Brevo. De meest gemaakte fout is een `MAIL_FROM`
   die niet exact het geverifieerde adres is: Brevo weigert dat met een 400.

Zet je de mail liever uit op de dag zelf, haal dan `BREVO_API_KEY` weg en herstart.

## Deployen (Render + Supabase, gratis)

1. **Supabase**: maak een gratis project. Neem uit *Connect* de **session pooler** URI en
   vervang `postgresql://` door `postgresql+psycopg://`. Die string is `DATABASE_URL`.
2. **Render**: nieuwe web service uit deze repo, runtime Docker, plan free. Zet de
   environment variables uit de tabel hierboven. Health check pad `/healthz`.
   De tabellen worden bij de eerste start automatisch aangemaakt.
3. **Pinger**: maak op [cron-job.org](https://cron-job.org) of UptimeRobot een taak die elke
   5 minuten `https://<jouw-app>.onrender.com/healthz` opvraagt. Zonder pinger valt de gratis
   Render-service na 15 minuten stilte in slaap en wacht de eerste speler ~50 seconden.

Draait het niet naar wens, dan draait dezelfde image ongewijzigd op Fly.io (~€2/maand) met
dezelfde `DATABASE_URL`.

## De dag zelf

1. Maak de wedstrijd aan op `/admin` en plak de CSV.
2. **Kopieer meteen de linkenlijst.** Alleen de hash van een token staat in de database, dus
   de links zijn achteraf niet meer op te vragen. Kwijt? Gebruik *Nieuwe links maken*: dat
   maakt verse links en laat alle scores staan.
3. Deel de leaderboard-link met toeschouwers. Passen niet alle spelers op het scherm, dan
   toont het bord er 25 tegelijk en springt het elke minuut naar de volgende groep, net zo
   lang tot het weer vooraan begint. Een ander aantal per scherm zet je in de link:
   `?n=40` voor een grote televisie, `?n=10` voor een telefoon. De adminpagina bouwt die
   link voor je. Alle schermen die dezelfde wedstrijd tonen lopen gelijk, want welk groepje
   aan de beurt is volgt uit de klok van de server.
   Het bord toont één ronde tegelijk. De gewone link volgt vanzelf de ronde waarin het laatst
   is gescoord; met `?r=2` zet je hem vast op een ronde. Vanaf ronde 2 staan er twee kolommen
   bij: *Vorig* is het totaal uit de eerdere ronden, *Totaal* het aantal slagen over alles
   samen. De +/- kolom telt vanaf dan alle ronden op, en daarop wordt gerangschikt. Wie
   vandaag nog moet starten staat er dus al bij, met zijn eerdere resultaat.
4. Op de beheerpagina staat de spelerslijst: wie is geïmporteerd, in welke flight, met
   welke marker en hoe ver ze zijn. De voortgang per flight volg je op het leaderboard.
5. Na afloop: *Uitslag CSV* en *Audit log CSV* downloaden. Dat is meteen je back-up.

## Correcties tijdens de wedstrijd

Alles staat op de beheerpagina van de wedstrijd:

- **Score corrigeren** overschrijft beide invoeren van één hole, ook op een getekende kaart.
  Reden is verplicht en komt in de audit log.
- **Status** zet iemand op DQ, NR of WD. Die valt dan uit de rangschikking.
- **Ontgrendelen** maakt een getekende kaart weer bewerkbaar.
- **Nieuwe links maken** vervangt links (bijvoorbeeld bij een gewijzigde flight of een
  verloren telefoon) zonder scores te raken.
- **Kaart leegmaken** wist wél alle scores. Vraagt om een viercijferige bevestigingscode.
- **Alle spelers en scores verwijderen** maakt de wedstrijd leeg zodat je een verbeterd
  CSV-bestand kunt importeren. De wedstrijd zelf en de leaderboardlink blijven bestaan.
  Vraagt ook om de bevestigingscode. Gaat het maar om één speler die niet meespeelt, zet
  hem dan op WD, NR of DQ: dan blijft zijn kaart bewaard.

Een flightwijziging vraagt géén nieuwe link: `/me` leest flight en marker bij elke pagina
opnieuw.

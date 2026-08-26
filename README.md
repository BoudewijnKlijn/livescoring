# Live scoring

Digitale scorekaart en live leaderboard voor clubkampioenschappen. Spelers loggen in via een
persoonlijke link, voeren hun eigen scores in plus die van de speler voor wie ze marker zijn,
lossen verschillen op en tekenen hun kaart. Toeschouwers volgen de stand live.

## Lokaal draaien

```bash
docker compose up -d                          # Postgres op poort 5434
uv run uvicorn app.main:app --reload --port 8001   # http://localhost:8001
uv run python -m app.seed --demo              # demo-wedstrijd + 8 links
uv run pytest                                 # 15 tests
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

## CSV-import

Eén regel per speler per ronde. Speelt iemand twee ronden, dan staat hij er twee keer in.

```csv
naam,email,ronde,flight,starthole,marker
Jan de Vries,jan@x.nl,1,A,1,Piet Bakker
Piet Bakker,piet@x.nl,1,A,1,Jan de Vries
```

`starthole` is 1 of 10 en geldt voor de hele flight. `marker` is de naam van iemand anders in
dezelfde flight en ronde. Het hele bestand wordt eerst gecontroleerd: bij één fout wordt er
niets geïmporteerd. Opnieuw importeren voegt toe en werkt flights bij, maar verwijdert nooit
spelers of scores.

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

# Open keuzes en afwijkingen van GOAL.md

Gemaakt tijdens het uitvragen van het ontwerp, en tijdens het bouwen. Alles hieronder is een
bewuste keuze, geen vergeten onderdeel.

## Afgesproken afwijkingen

| GOAL.md | Nu | Reden |
|---|---|---|
| Speler tekent, marker bevestigt daarna | Eén handtekening; de match tussen self en marker *is* de bevestiging | Scheelt een tweede state machine en het scenario "mijn marker is al naar huis" |
| Markerkeuze-scherm met polling | Markers staan in de CSV | Scheelt een scherm, een POST en een polling-blok |
| Postgres + asyncpg + Alembic | Postgres + psycopg (sync), `create_all` | Geen migratietooling nodig vóór de eerste wedstrijd; schema wijzigen = tabellen weg, CSV opnieuw |
| Plus- en minknoppen | Getypt getal | Expliciete keuze: sneller met 18 holes |
| Retry-queue + localStorage + `beforeunload` | Directe POST; een niet-opgeslagen score verdwijnt weer uit het vakje | Geen extra uitleg of kleur nodig: wat in het vakje staat, staat in de database |
| Tailwind Play CDN | ~180 regels eigen CSS | Play CDN is een render-blocking script dat in de browser compileert |
| Resend voor mail | Geen mail | Admin verspreidt de links zelf; scheelt een API-key en een deliverability-faalpad |
| Leaderboard achter login | Publiek op een onraadbare slug | "Echt live" is de reden van de app; toeschouwers moeten mee kunnen kijken |
| Fly.io + Supabase | Render free + Supabase free + pinger | Eis was €0 |
| Volledige admin-CRUD | CSV-import + correcties | Setup doe je één keer aan een laptop; een drag-and-drop flightbouwer is pure kost |

## Keuzes die tijdens het bouwen zijn gemaakt

1. **Leaderboard sorteert op slagen ten opzichte van par, niet op totaal aantal slagen.**
   GOAL.md vroeg om sorteren op totaal. Tijdens een ronde is dat onzin: wie na 3 holes 12
   slagen heeft, staat dan boven wie na 18 holes 76 heeft. De kolom *Totaal* staat er
   gewoon bij, en na afloop, als iedereen 18 holes heeft, geven beide sorteringen dezelfde
   volgorde. Wil je toch strikt op totaal sorteren, dan is dat één regel in
   `app/scoring.py` (`rows.sort(...)`).

2. **Op het leaderboard telt alleen wat speler en marker allebei invulden.** Een hole waar
   ze het oneens zijn blijft leeg tot ze het eens worden; de stand ten opzichte van par gaat
   over de holes waar wel overeenstemming over is. Zolang een ronde loopt staat er geen
   totaal, alleen +/-. Wie nog geen enkele score heeft, staat niet op het bord.

3. **Wat in het vakje staat, staat in de database.** Er is geen sync-icoon en geen
   foutkleur. Slaagt een invoer niet, dan springt het vakje terug naar wat de server heeft:
   leeg, of de vorige score. Een weigering (422) levert de opgeslagen stand op, en bij een
   netwerkfout of een verzoek dat na tien seconden nog hangt zet de pagina de vorige waarde
   terug. Rood op de kaart betekent daardoor maar één ding: speler en marker vulden iets
   anders in.

4. **Het leaderboard bladert op de servertijd, niet in de browser.** Bij meer spelers dan
   op een scherm passen toont het bord er 25 tegelijk en gaat het elke minuut naar de
   volgende groep. Welke groep dat is volgt uit `minuut % aantal schermen`, dus zonder
   toestand in de browser en zonder extra verzoek: elk scherm dat dezelfde wedstrijd toont
   laat dezelfde spelers zien. Het aantal per scherm staat in de link (`?n=40`) in plaats
   van in de database, zodat een televisie en een telefoon allebei hun eigen maat kunnen
   hebben en er geen kolom bij hoefde tijdens het toernooi.

5. **Links zijn na de import eenmalig zichtbaar.** Alleen de sha256-hash staat in de
   database, dus een linkenoverzicht achteraf kan niet zonder de tokens op te slaan.
   In plaats daarvan: kopieer de lijst direct na de import, en ben je hem kwijt, gebruik dan
   *Nieuwe links maken*. Dat maakt verse links en raakt geen enkele score aan.

6. **Roteren en wissen zijn losse acties.** Oorspronkelijk gevraagd als één actie ("nieuwe
   link, oude ongeldig, scores weg"). Het geval dat dit oplost, een 3-bal die een 2-bal
   wordt, vraagt om een nieuwe marker, niet om een gewiste kaart. Beide acties vragen een
   viercijferige bevestigingscode zodra er scores staan.

7. **Statussen zijn tekstkolommen met een check constraint**, geen Postgres enums. Een enum
   uitbreiden vraagt om een migratie, en die is er niet.

8. **De correctieformulieren staan buiten het blok dat elke 5 seconden ververst.** Anders
   typt de admin een reden in een veld dat een seconde later wordt weggegooid.

9. **Eén speler per competitie, geen globale spelersidentiteit.** Wie twee competities
   speelt, is twee rijen met twee links. Een gedeelde identiteit vraagt om matchen op e-mail
   en een samenvoegprobleem bij "J. de Vries" versus "Jan de Vries".

10. **Het leaderboard heeft een cache van 3 seconden en geen limiet per IP.** De tabel
   wordt hooguit eens per drie seconden opgebouwd; 120 kijkers die elke 5 seconden
   verversen kosten daardoor ongeveer één query per drie seconden en één
   databaseverbinding. Een limiet per IP stond er eerst wel, maar die werkt hier averechts:
   op de wifi van het clubhuis en achter de proxy van de hoster lijkt iedereen dezelfde
   bezoeker, dus de limiet raakte de toeschouwers en niet een aanvaller. Gemeten met 120
   gelijktijdige kijkers: 600 verzoeken, 0 fouten, 1 verbinding.

11. **De verbindingen naar de database zijn er hooguit tien**, ongeacht het aantal spelers:
   `pool_size=5` plus `max_overflow=5` in `app/models.py`. Een speler of kijker is geen
   verbinding; een verzoek leent er kort een en geeft hem meteen terug.

## Nog niet gebouwd

- Meerdere competities tegelijk live is mogelijk, maar niet doorgetest met echte gelijktijdige
  belasting.
- Er is geen automatische back-up. De *Uitslag CSV* en *Audit log CSV* zijn de back-up;
  download ze na elke ronde. Supabase free maakt geen automatische snapshots.
- Geen mail, geen handicapberekening, geen koppeling met clubsystemen. Zoals afgesproken.

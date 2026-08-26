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
| Retry-queue + localStorage + `beforeunload` | Directe POST, cel wordt geel bij mislukken | Expliciete keuze: bij geen bereik probeer je het bij de volgende hole nog eens; de database onthoudt de rest |
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

3. **Links zijn na de import eenmalig zichtbaar.** Alleen de sha256-hash staat in de
   database, dus een linkenoverzicht achteraf kan niet zonder de tokens op te slaan.
   In plaats daarvan: kopieer de lijst direct na de import, en ben je hem kwijt, gebruik dan
   *Nieuwe links maken*. Dat maakt verse links en raakt geen enkele score aan.

4. **Roteren en wissen zijn losse acties.** Oorspronkelijk gevraagd als één actie ("nieuwe
   link, oude ongeldig, scores weg"). Het geval dat dit oplost, een 3-bal die een 2-bal
   wordt, vraagt om een nieuwe marker, niet om een gewiste kaart. Beide acties vragen een
   viercijferige bevestigingscode zodra er scores staan.

5. **Statussen zijn tekstkolommen met een check constraint**, geen Postgres enums. Een enum
   uitbreiden vraagt om een migratie, en die is er niet.

6. **De correctieformulieren staan buiten het blok dat elke 5 seconden ververst.** Anders
   typt de admin een reden in een veld dat een seconde later wordt weggegooid.

7. **Eén speler per competitie, geen globale spelersidentiteit.** Wie twee competities
   speelt, is twee rijen met twee links. Een gedeelde identiteit vraagt om matchen op e-mail
   en een samenvoegprobleem bij "J. de Vries" versus "Jan de Vries".

8. **Rate limit en cache op het leaderboard**: de tabel wordt maximaal eens per 3 seconden
   gerenderd en per IP zijn 90 verzoeken per minuut toegestaan. Bij 100 kijkers die elke 5
   seconden pollen is dat ~1 query per 3 seconden in plaats van 20 per seconde.

## Nog niet gebouwd

- Meerdere competities tegelijk live is mogelijk, maar niet doorgetest met echte gelijktijdige
  belasting.
- Er is geen automatische back-up. De *Uitslag CSV* en *Audit log CSV* zijn de back-up;
  download ze na elke ronde. Supabase free maakt geen automatische snapshots.
- Geen mail, geen handicapberekening, geen koppeling met clubsystemen. Zoals afgesproken.

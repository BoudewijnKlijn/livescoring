# Todo

Dingen die kunnen wachten tot na de eerste wedstrijd. Met genoeg context erbij om ze later
op te pakken zonder alles opnieuw uit te zoeken.

## Eigen domein voor de afzender van de mail

**Wat.** Een domein dat je zelf beheert, geverifieerd in Brevo, en `MAIL_FROM` op een adres
van dat domein: `wedstrijd@jouwgolfclub.nl` in plaats van een gmail-adres.

**Waarom.** De bevestigingsmail gaat nu de deur uit met `livescoring.signed@gmail.com` als
afzender, verstuurd door de servers van Brevo. SPF en DKIM tekenen daarbij voor het domein
van Brevo, terwijl de From-regel `gmail.com` claimt. Die twee lijnen niet uit, dus DMARC
faalt per definitie. Getest op 28-08-2026: de mail werd netjes bezorgd, maar belandde bij
Outlook in de spambox. Gmail is nog strenger op post die zegt van `gmail.com` te komen
zonder door Google verstuurd te zijn. Aan de template valt hier niets te verbeteren, het zit
volledig in de afzender.

**Hoe.**

1. Heeft de club al een domein voor de website, gebruik dat en regel DNS-toegang. Zo niet,
   dan kost een eigen domein ongeveer €5 tot €10 per jaar.
2. In Brevo: **Senders, Domains & Dedicated IPs → Domains → Add a domain**. Brevo geeft je
   een paar DNS-records terug, waaronder een TXT-record voor DKIM.
3. Zet die records bij je domeinprovider neer en laat Brevo verifiëren.
4. Zet `MAIL_FROM` op een adres van dat domein, zowel in `.env.local` als in het
   Render-dashboard. Verder verandert er niets aan de code.
5. Test opnieuw naar een Gmail- én een Outlook-adres. Dat zijn samen zo ongeveer het hele
   deelnemersveld.

**Tot die tijd.** Zeg bij de eerste wedstrijd tegen de spelers dat de bevestiging in de
spambox kan belanden. De mail is een extraatje: de kaart is getekend en telt mee, of de mail
nu aankomt of niet.

## Persoonlijke links per mail naar de spelers

**Wat.** Een knop op de beheerpagina die elke speler zijn eigen link mailt, zodat je ze niet
meer met de hand hoeft te verspreiden. De mailkant staat er al: Brevo is aangesloten en
`app/mail.py` doet het versturen. Dit is geen open keuze meer, alleen nog werk.

**Waarom het nu nog niet zo is.** De bevestigingsmail na het tekenen was er eerst, en die
gaat naar één speler tegelijk. Het rondsturen van de links is een tweede moment met een
andere vorm: veel ontvangers in één keer, en met een inlogtoken erin.

**Let op bij het bouwen.**

- Doe het achter een aparte knop, niet automatisch bij de import. Tijdens het opzetten
  importeer je hetzelfde bestand vaak een paar keer achter elkaar; automatisch versturen zou
  de spelers dan evenzoveel keer mailen.
- Het moet gebeuren in dezelfde request als de import of als *Nieuwe links maken*. Alleen de
  hash van een token staat in de database, dus daarna zijn de links niet meer op te vragen.
- Spelers zonder e-mailadres in de CSV blijven over. Laat het scherm zien wie dat zijn, want
  die moet je nog steeds zelf een link geven.
- Een veld van 80 spelers is 80 mails ineens. Dat past binnen de 300 per dag van Brevo,
  maar tel het even na als je met meerdere ronden tegelijk werkt.
- Doe dit pas na het eigen domein hierboven. Een bevestigingsmail in de spambox is jammer;
  een inloglink in de spambox betekent dat een speler niet kan scoren.

## `player.id` en `entry.id` zijn makkelijk te verwisselen

**Wat.** Twee tabellen die allebei vanaf 1 tellen, en een `entry` heet in de wandelgangen
ook gewoon "speler". Een los getal zegt niet uit welke van de twee het komt.

**Waarom het uitmaakt.** In de audit log stond `player:39` terwijl er een entry-id bedoeld
werd. Entry 39 en speler 39 waren twee verschillende deelnemers, dus die regels wezen de
verkeerde persoon aan. De log is opgelost (`entry:39`), maar de valkuil zelf zit er nog.

**Hoe.** Geen herschrijving nodig. Bij elk nieuw veld, logregel of scherm dat een id
opslaat of toont: zet in de naam welke tabel het is (`entry_id`, niet `speler`), en zet er
in tekst het soort voor, zoals de audit log nu doet.

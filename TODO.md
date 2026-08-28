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

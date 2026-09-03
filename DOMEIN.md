# Domein en DNS

Werkaantekening bij het koppelen van `livescoring.nl` aan de app op Render. Nog niet af,
dus hier staat ook wat er nog moet gebeuren en waarom.

## Wat we hebben

| | |
|---|---|
| Domein | `livescoring.nl`, geregistreerd 29-08-2026 |
| Registrar | **Vimexx**. SIDN noemt het ZXCS BV (Vondellaan 47, Leiden) |
| Nameservers | `ns.zxcs.nl`, `ns.zxcs.be`, `ns.zxcs.eu` — dat zijn Vimexx' eigen nameservers |
| DNS-beheer | Vimexx klantenpaneel: **Mijn Domeinen → livescoring.nl → DNS** |
| Webhosting | geen, en dat hoeft ook niet |
| DNSSEC | aan, DS-record staat bij SIDN (keytag 17065, alg 13) |
| App | `livescoring.onrender.com`, Render web service, plan free |
| Database | Supabase, plan free |

ZXCS en Vimexx zijn hetzelfde bedrijf (KvK 70570078), Vimexx is de handelsnaam. Zoek je
dus in het Vimexx-paneel en zegt SIDN "ZXCS", dan is dat geen fout.

## Wat er in de zone staat

Zoals het paneel het teruggeeft, 03-09-2026:

```
@       A       216.24.57.1                Render load balancer
@       NS      ns.zxcs.{nl,be,eu}
@       TXT     v=spf1 a mx -all
www     CNAME   livescoring.onrender.com.
ftp     A       185.104.28.238             restant van de parkeerpagina
ftp     AAAA    2a06:2ec0:1::ffed          restant van de parkeerpagina
mail    A       185.104.28.238             restant van de parkeerpagina
mail    AAAA    2a06:2ec0:1::ffed          restant van de parkeerpagina
_dmarc  TXT     v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s
_domainkey TXT  o=~                        verouderd DomainKeys-record
        MX      geen                       het domein ontvangt geen mail
        CAA     geen                       elke CA mag certificaten geven
```

Er staat géén AAAA-record op `@`. Dat is dus goed verwijderd, en daarmee staat vast dat het
aan Vimexx ligt dat één nameserver hem nog teruggeeft.

De TTL staat overal op 86400, dus een dag. Publieke resolvers laten een oude waarde daarom
tot 24 uur na een wijziging nog zien. Zet de TTL op 300 voordat je aan een volgende
wijziging begint, dan ben je in vijf minuten klaar in plaats van een dag.

`ftp` en `mail` wijzen naar de oude parkeerserver en doen verder niets. Ze zitten Render
niet in de weg, want Render kijkt alleen naar `@` en `www`. Opruimen mag, hoeft niet.

## Wat werkt

`https://www.livescoring.nl/healthz` geeft `{"status":"ok"}` met een geldig certificaat.
De koppeling met Render staat dus.

## Wat nog moet

1. **Vimexx moet zijn nameservers gelijktrekken.** Het AAAA-record is op 03-09-2026
   verwijderd en dat is ook goed opgeslagen, maar één van de drie nameservers blijft het
   uitserveren. Zie de volgende paragraaf.
2. **Terug naar de apex zodra dat kan.** Zolang `ns.zxcs.nl` vastzit draaien we op
   `www.livescoring.nl`, met `BASE_URL=https://www.livescoring.nl`. Komt het certificaat
   voor het kale domein er, zet dan `livescoring.nl` weer als primair domein in Render en
   `BASE_URL` mee. Oude links blijven werken: `/t/{token}` zoekt op het token en kijkt niet
   naar de hostnaam.
3. **Landingspagina op `/` vullen.** Sinds 03-09-2026 is `/` een lege pagina in plaats van
   een redirect naar `/me/card`, want dat gaf zonder cookie een inlogfout aan iedereen die
   het domein intypte. De pagina bestaat (`app/templates/home.html`), er staat alleen nog
   niets op.
4. **Apex van `A` naar `ALIAS`** op `livescoring.onrender.com`, pas als alles werkt. Dan
   staat er geen vast IP-adres meer in de zone. Doe dit als losse stap, niet tegelijk met
   iets anders.

## Het probleem van 03-09-2026: één nameserver loopt uit de pas

Na het verwijderen van het AAAA-record op `@`:

```
ns.zxcs.nl  185.104.28.19   serial 2026090305   AAAA=2a06:2ec0:1::ffed   AA=1
ns.zxcs.be  46.101.179.64   serial 2026090305   geen antwoord           AA=1
ns.zxcs.eu  178.62.208.8    serial 2026090305   geen antwoord           AA=1
```

Alle drie zeggen dat ze gezaghebbend zijn (AA=1) en alle drie noemen hetzelfde
serienummer, maar `ns.zxcs.nl` serveert een record dat de andere twee niet meer hebben.
Het is geen cache: het AA-bit staat aan, recursie staat uit en de TTL telt niet af. Het is
ook geen gewone vertraging, want het serienummer is al opgehoogd. Er staat gewoon een
node uit de pas.

Waarom dat erg is: een resolver kiest min of meer willekeurig een van de drie. Ongeveer een
op de drie keer komt het spook-AAAA dus terug, en Let's Encrypt kiest IPv6 boven IPv4. De
certificaataanvraag mislukt daardoor wisselend in plaats van altijd, en dat is vervelender:
hij kan vandaag lukken en over zestig dagen bij het verlengen alsnog omvallen.

**Eerst zelf proberen:** zet de TTL van het A-record op `@` van 86400 op 300. Dat is toch
al wenselijk, en het hoogt het serienummer op, waardoor Vimexx de zone opnieuw naar alle
drie de nameservers duwt. Grote kans dat de node daarmee bijtrekt.

**Helpt dat niet, dan is het een supportvraag.** Vermeld het domein, dat `ns.zxcs.nl` op
serienummer 2026090305 een AAAA-record voor `livescoring.nl` teruggeeft dat op
`ns.zxcs.be` en `ns.zxcs.eu` bij hetzelfde serienummer niet bestaat, en vraag of ze de zone
op die node opnieuw willen laden.

## Let op bij de mail: DMARC staat op `reject`

Vimexx heeft er standaard dit record bij gezet:

```
_dmarc  TXT  v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s
```

Dat is de strengste stand die er bestaat. `p=reject` betekent dat ontvangers mail die de
controle niet doorstaat moeten wéigeren, niet in de spambox leggen. `adkim=s` en `aspf=s`
zetten de uitlijning op strikt: het domein in de From-regel moet exact gelijk zijn aan het
domein dat de controle doorstaat, niet alleen in dezelfde familie zitten.

Voor een domein dat helemaal geen mail verstuurt is dat een prima beveiliging, dus laat het
voorlopig staan. Vandaag raakt het ons ook niet, want `MAIL_FROM` is nog een gmail-adres en
dan geldt het DMARC-beleid van gmail.com, niet dat van ons.

**Maar het moment dat `MAIL_FROM` op `wedstrijd@livescoring.nl` gaat, gaat dit stuk.** Dan
is de bevestigingsmail niet meer spam, maar geweigerd, en dat is erger. Nu belandt hij nog
in de spambox, straks komt hij helemaal niet meer aan.

DMARC slaagt als SPF óf DKIM slaagt én uitlijnt. Reken beide na:

- **SPF gaat het niet redden.** Brevo verstuurt met een eigen Return-Path op een
  Brevo-domein. Bij `aspf=s` moet dat exact `livescoring.nl` zijn, en dat wordt het niet,
  ook niet als je `include:spf.brevo.com` toevoegt.
- **Het moet dus van DKIM komen.** Doorloop in Brevo *Domains → Add a domain* helemaal, tot
  en met de DKIM-records. Die ondertekenen met `d=livescoring.nl`, en daarmee is `adkim=s`
  tevreden en slaagt DMARC.

Volgorde als het zover is, en niet anders:

1. Domein verifiëren in Brevo en de DKIM-records in de DNS zetten.
2. SPF bijwerken naar `v=spf1 include:spf.brevo.com -all`. Let op dat `a` en `mx` eruit
   gaan: `a` wijst nu naar `216.24.57.1`, de gedeelde load balancer van Render, en dat wil
   je niet als toegestane afzender voor je domein hebben staan.
3. Tijdelijk `p=none` in plaats van `p=reject`, zodat een fout zichtbaar wordt in plaats van
   dat de mail verdwijnt.
4. Testen naar een Gmail- én een Outlook-adres, headers nakijken op `dmarc=pass`.
5. Pas daarna terug naar `p=reject`.

Nog iets om te bedenken: er is geen MX-record, dus `livescoring.nl` kan geen mail
óntvangen. Antwoordt een speler op de bevestigingsmail, dan bounct dat. Zet er een
`Reply-To` op met een adres dat wel bestaat, of regel doorsturen bij Vimexx.

Het record `_domainkey` met `o=~` is een oud DomainKeys-record, de voorloper van DKIM. Het
doet niets kwaads en niets goeds.

## Niet doen zonder na te denken

Nameservers verplaatsen, bijvoorbeeld naar Cloudflare. Het DS-record voor DNSSEC staat bij
SIDN en wijst naar de sleutels van Vimexx. Verhuis je de nameservers zonder DNSSEC eerst
uit te zetten, dan is het domein onbereikbaar voor iedereen wiens resolver DNSSEC
controleert. Records aanpassen bínnen Vimexx is veilig: die ondertekenen automatisch
opnieuw.

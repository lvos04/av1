# Native installatie op Ubuntu Server 26.04

Dit pakket draait zonder Docker. De backend krijgt toegang tot de Intel-GPU
via de normale Linux-groepen `render` en `video`; de frontend draait als een
aparte gebruiker zonder die groepen. De backendservice krijgt daarnaast alleen
`CAP_PERFMON`, zodat `intel_gpu_top` GPU-belasting en Intel RAPL-vermogen kan
lezen en `turbostat` het PSys-systeemtotaal kan meten, zonder sudo- of algemene
beheerrechten.

1. Plaats de uiteindelijke projectmap op de host, bijvoorbeeld
   `/opt/av1-pipeline`.
2. Laat `/srv/av1-pipeline` als gewone map op de 1-TB-systeemschijf staan. Het
   installatiescript controleert dat dit werkelijk dezelfde rootdisk is en
   maakt alleen de beheerde submappen.
3. Draai als root: `sudo bash install/installeer-ubuntu-26.04.sh /opt/av1-pipeline`.
4. Maak `backend/.venv`, installeer de Python-dependencies en bouw de
   frontend volgens fase 0 en de volgende prompts.
5. Kopieer `systemd/av1-pipeline.env.example` naar
   `/etc/av1-pipeline.env`. Kies latere werkopslag via de UI, niet door losse
   cachepaden handmatig uit elkaar te trekken.
6. Start pas de backendservice nadat de healthcheck is geïmplementeerd.

Na de installatie zijn backend, frontend en de begrensde SMB-socket voor boot
ingeschakeld. Een onderbroken encode wordt bij de volgende start uitsluitend
vanuit tijdelijke cache verwijderd en opnieuw in de wachtrij gezet; het
bronbestand blijft onaangeraakt. De services blijven met vijf seconden
tussenruimte opnieuw starten, ook na een onverwacht schoon proces-einde.

Voor volledig herstel na weggevallen netspanning moet de firmware de NUC zelf
weer inschakelen. Stel eenmalig in het BIOS via **F2 → Power → Secondary Power
Settings → After Power Failure → Power On** in en sla op met **F10**. Deze
firmwarekeuze is op de NUC14RVS niet vanuit Ubuntu schrijfbaar.

Standaard delen cache, tijdelijke verwerking, database/logs en herstelkopieën
de systeemschijf. Minimaal 150 GB blijft buiten bereik van nieuwe jobs. Het
pakket gebruikt geen prullenbak: een geverifieerd resultaat
vervangt de bron atomisch op de mediavolume.

Krijg je later een extra schijf, mount die dan eerst blijvend met Ubuntu,
bijvoorbeeld op `/mnt/av1-werkruimte`, en geef `av1pipeline` toegang. Kies
daarna in **Instellingen → Opslag kiezen** voor een aparte werkschijf, voer de
droge controle uit en bevestig de wijziging. Cache en werk verhuizen als één
indeling; nieuwe back-ups kunnen apart op systeem- of werkschijf blijven.
Workers blijven na de wissel gepauzeerd tot je de diagnose hebt gecontroleerd.

## Bedienen vanaf een andere computer

De FastAPI-backend blijft veilig op `127.0.0.1:8000`. De frontend-gateway
luistert op poort 3000, maar weigert standaard alle andere computers. Open de
interface eerst lokaal en ga naar **Instellingen → Toegang vanaf andere
computers**. Kies daar een aparte gebruikersnaam en een wachtwoord van minimaal
12 tekens en typ de getoonde bevestiging exact over. De pagina toont daarna de
bruikbare LAN-URL's, bijvoorbeeld `http://192.168.0.81:3000`.

Bij een bestaande installatie activeer je de gebouwde gatewayservice eenmalig
vanuit een gewone Ubuntu-terminal met:

```bash
sudo bash /opt/av1-pipeline/install/activeer-lan-service.sh
```

Als UFW actief is, sta alleen het eigen subnet toe; voor dit netwerk is dat:

```bash
sudo ufw allow from 192.168.0.0/24 to any port 3000 proto tcp comment 'AV1 Pipeline LAN'
```

Open poort 8000 niet. Stuur poort 3000 niet door in de router en gebruik voor
bediening van buiten het thuisnetwerk eerst een VPN. Vijf mislukte aanmeldingen
vanaf hetzelfde adres geven tijdelijk een blokkade. Het wachtwoord wordt met
scrypt gehasht opgeslagen en verschijnt niet in API-antwoorden of logs.

## Lokale en SMB-bibliotheekbronnen

De bibliotheekpagina accepteert iedere bestaande lokale mediamap en ieder
`smb://server/share/map`-adres. Een `smb://`-adres uit de desktop is niet
rechtstreeks toegankelijk voor de achtergrondservice; de begrensde helper
maakt daarom per server/share een blijvende CIFS-automount onder
`/library/netwerk`. Activeer die mogelijkheid bij een bestaande installatie
eenmalig met:

```bash
sudo bash /opt/av1-pipeline/install/activeer-bibliotheekbronnen.sh
```

De backend behoudt daarmee `NoNewPrivileges` en krijgt geen sudo- of algemene
mountrechten. Alleen de root-owned systemd-sockethelper kan de streng begrensde
mountactie uitvoeren. De helper accepteert uitsluitend SMB-servers op
privé-IP-adressen, mount alleen onder de vaste netwerkroot en gebruikt
`nosuid,nodev,noexec`. Accountgegevens staan root-only en verschijnen nooit in
URI's, API-antwoorden of logs.

Bij **Test bron en voeg toe** maakt de backend in de gekozen map één verborgen
klein testbestand. Hij controleert schrijven, fsync, metadata terugzetten en
atomisch hernoemen en verwijdert het testbestand altijd. Alleen een groene bron
wordt opgeslagen; er start nog geen scan of encode. Verdwenen SMB-mounts worden
voor iedere scan en job opnieuw op bronidentiteit gecontroleerd, zodat een lege
onderliggende mountmap nooit als bibliotheek wordt aangezien.

Het installatiescript maakt één lokale sleutel in
`/etc/av1-pipeline-secrets.key`, leesbaar voor root en de backendservice. Die
is nodig om optionele Jellyfin-, Radarr- en Sonarr-sleutels versleuteld in de
database op te slaan. Bewaar deze sleutel bij een back-up van de configuratie;
zonder de sleutel kunnen opgeslagen integratiesleutels niet meer worden gelezen.

Het script installeert de vastgezette upstream-release `dovi_tool 2.3.3` voor
Dolby-Vision-profiel 7. `install/installeer-dovi-tool.sh` controleert vóór de
installatie de door GitHub gepubliceerde SHA-256 van de x86_64-musl-archive en
plaatst het binaire bestand op `/usr/local/bin/dovi_tool`. De veilige standaard
voor Dolby Vision blijft desondanks `dv_modus=skip` totdat afspelen op de echte
TV afzonderlijk is gecontroleerd.

Na pakketupdates of een kernelupdate controleer je opnieuw `vainfo`,
`ffmpeg -encoders` en een korte echte `av1_qsv`-encode vóór je de wachtrij
weer start.

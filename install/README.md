# Native installatie op Ubuntu Server 26.04

Dit pakket draait zonder Docker. De backend krijgt toegang tot de Intel-GPU
via de normale Linux-groepen `render` en `video`; de frontend draait als een
aparte gebruiker zonder die groepen.

1. Plaats de uiteindelijke projectmap op de host, bijvoorbeeld
   `/opt/av1-pipeline`.
2. Koppel de aparte 1 TB-SSD permanent aan `/srv/av1-pipeline`. Laat Ubuntu
   dit met UUID doen; controleer na een herstart dat `mountpoint
   /srv/av1-pipeline` nog steeds slaagt. Het installatiescript stopt bewust
   als dit geen echte mount is.
3. Draai als root: `sudo bash install/installeer-ubuntu-26.04.sh /opt/av1-pipeline`.
4. Maak `backend/.venv`, installeer de Python-dependencies en bouw de
   frontend volgens fase 0 en de volgende prompts.
5. Kopieer `systemd/av1-pipeline.env.example` naar
   `/etc/av1-pipeline.env` en pas uitsluitend de paden aan.
6. Start pas de backendservice nadat de healthcheck is geïmplementeerd.

Gebruik de SSD voor cache, tijdelijke verwerking, database/logs en recente
back-ups. Het pakket gebruikt geen prullenbak: een geverifieerd resultaat
vervangt de bron atomisch op de mediavolume.

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

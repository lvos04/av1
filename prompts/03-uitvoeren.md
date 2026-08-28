# Fase 3 — Uitvoeren: één bestand, veilig

**Doel:** één goedgekeurd besluit kunnen uitvoeren, van encode tot vervanging,
met verificatie. Vanaf hier heb je iets dat werkelijk ruimte bespaart.

**Duur:** 4-5 uur. Dit is de fase waar de meeste bugs zitten.

---

## Lees eerst

- **`spec/04-veiligheidsregels.md` — helemaal, elke regel**
- `spec/02-domeinkennis.md` punt 7 (DV profiel 7) en 15 (hardlinks)
- `spec/06-dolby-vision.md` — vooral het onderdeel over afspelen

## Opdracht

Bouw `app/uitvoerder.py` met één functie die één job draait. De volgorde ligt
vast:

```
1.  claim de job in de database  (status=bezig, worker_id)
2.  controleer dat het bronbestand nog dezelfde mtime en grootte heeft
      -> zo niet: afbreken, iets anders heeft het aangeraakt
3.  leg de probe van het ORIGINEEL vast op de job (bron_probe)
      -> hiermee wordt straks geverifieerd; zie veiligheidsregel 3
4.  bouw het commando
5.  bij DV profiel 7: eerst de drie stappen naar 8.1, in een eigen werkmap
      -> ontbreekt dovi_tool, dan job overslaan met een duidelijke reden,
         niet half uitvoeren. Profiel 8.1 heeft dovi_tool NIET nodig
6.  draai naar <CACHE_ROOT>/<job-id>/<naam>.mkv   -- NOOIT over het origineel
7.  verifieer:
      - is het resultaat leesbaar
      - is HDR10 er nog, en identiek
      - is DV er nog
      - is het resultaat niet groter dan de bron
      - zit er audio in
      - bevat de bron HDR10+, dan hoort deze job hier nooit te komen:
        stop veilig met status `overslaan` en log de detectie
8.  geslaagd  -> leg vervangtransactie vast, kopieer resultaat als verborgen
                 tijdelijk bestand naar de doelmap op de bibliotheekschijf,
                 herstel uid/gid/mode/ACL/xattrs en `fsync` alles
                 -> hernoem tijdelijk bestand atomisch naar het oorspronke-
                    lijke pad; dit vervangt de bron permanent
                 -> markeer transactie als klaar
      mislukt -> origineel onaangeraakt, resultaat in de cache laten staan
9.  ruim de werkmap van stap 5 op          -- tientallen GB per film
10. schrijf de besparing weg
```

**Voortgang** lees je uit `-progress pipe:1`. Bewaar elke seconde `percent`,
`fps`, `speed` en `eta_seconden` op de job. Werk je database niet vaker bij dan
één keer per seconde per job.

**Afbreken** moet werken: het subprocess krijgt SIGTERM, de cache wordt
opgeruimd, en het origineel blijft staan.

**Hardlinks:** `nlink > 1` is standaard niet uitvoerbaar. Is de expliciete
instelling `hardlink_modus = verbreek_na_bevestiging` gekozen, vraag dan vóór
het inplannen én vóór de vervanging om een gerichte bevestiging. Verplaats de
oude inode door de atomische vervanging en plaats het resultaat als nieuw
bestand; doe niet alsof de oude hardlink hersteld kan worden. Log de verwachte netto
ruimte-impact.

**Herstel bij starten:** vóór de planner draait, handel je alle open
`vervang_transactie`-rijen af. Controleer daarbij device, inode, grootte en
mtime; verander niets als de gebruiker of een ander programma het bronpad
ondertussen heeft aangepast. Toon zo'n conflict in de UI.

**Bestandsmetadata:** leg vóór iedere job uid, gid, mode, POSIX-ACL's en
relevante xattrs vast. Pas die op het tijdelijke resultaat toe en lees ze
daarna terug. Een fout in die stap is een mislukte job; het origineel blijft
staan. Gebruik geen `shutil.move` als vervangingsmechanisme tussen volumes.

**CLI:**

```bash
python -m app.cli draai <job-id>
python -m app.cli draai-besluit <besluit-id>
```

## Acceptatietest

Deze vier moeten allemaal kloppen:

```bash
# 1. HDR overleeft
python -m app.cli draai-besluit <hdr10-besluit>
python -m app.cli probe testdata/hdr10.mkv | jq .is_hdr10     # true

# 2. Het resultaat staat op het oorspronkelijke pad; de bron is alleen na
#    succesvolle verificatie atomisch vervangen
test -f testdata/hdr10.mkv

# 3. Een mislukte verificatie laat het origineel met rust
#    (forceer het: laat de encoder een SDR-bestand maken van een HDR-bron)
#    -> origineel ongewijzigd, job=mislukt, reden in de UI leesbaar

# 4. Afbreken laat niets achter
#    start een job, breek af na 5 seconden
#    -> cache leeg, origineel ongewijzigd, job=afgebroken
```

Test 3 is de belangrijkste. Als je die niet expliciet uitprobeert, weet je niet
of je vangnet werkt — en dan merk je het pas als je het nodig hebt.

## Niet doen

- Nooit `shell=True`
- Nooit het origineel verwijderen vóór het nieuwe bestand er staat
- Nooit `ffmpeg -i x.mkv ... x.mkv`
- Nooit verifiëren door het bronpad opnieuw te probén nadat je hebt vervangen

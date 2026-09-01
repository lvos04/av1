# Veiligheidsregels

Deze regels zijn niet onderhandelbaar. Ze komen allemaal uit een fout die
werkelijk is gemaakt, in dit project of in een van de pakketten ervoor.

Als Codex code voorstelt die een van deze regels schendt: afwijzen, ook
als het "maar tijdelijk" of "alleen voor de test" is.

---

## 1. Besluiten en uitvoeren zijn gescheiden

De scanner beslist. De worker voert uit. Daartussen zit **een tabel en een
afzonderlijke goedkeuringspoort**. Standaard is dat jouw goedkeuring. Na een
expliciet vrijgegeven canary mag de AI-controller alleen waarschuwingvrije,
deterministische besluiten bevestigen wanneer scanner, getypeerd AI-voorstel
en lokale Python-policy exact overeenkomen. Het model voert nooit zelf uit en
heeft geen tools, shell, filesystem, mounts of SQLite-toegang.

Dit is de belangrijkste architectuurkeuze van het project en de reden dat het
veiliger is dan wat je kant-en-klaar krijgt. Je ziet de volledige lijst met
beslissingen en redenen vóórdat er iets gebeurt, en je kunt per bestand of per
groep overrulen.

In een eerder pakket bepaalde een naamfilter rechtstreeks of een bestand
verwijderd werd. Die filter zag `Trailer.Park.Boys.S01E01.mkv` aan voor een
trailer. Er was geen moment waarop iemand die beslissing kon zien.

## 2. Het origineel wordt nooit overschreven vóór verificatie

De volgorde is altijd:

```
encode naar CACHE_ROOT  ->  verifieer  ->  plaats resultaat atomisch op zijn
                                         oorspronkelijke pad (bron verdwijnt)
```

Nooit `ffmpeg -i film.mkv ... film.mkv`. Nooit in-place. Nooit het origineel
verwijderen voordat het nieuwe bestand er staat en is gecontroleerd.

## 3. Verificatie vergelijkt met metadata van vóór de encode

Leg de probe van het origineel vast **voordat** je begint, en bewaar hem bij de
job. Vergelijk daarmee.

Ga het origineel niet opnieuw inspecteren nadat je het hebt vervangen — dan
vergelijk je het nieuwe bestand met zichzelf en gaat je controle nooit af. Dit
is precies de bug die in de Unmanic-plugin zat.

Faalt de verificatie, dan:

- job krijgt status `mislukt`
- origineel blijft staan, onaangeraakt
- resultaat blijft in de cache staan zodat je kunt kijken wat er misging
- de fout komt in de UI met de reden

## 4. Permanent vervangen gebeurt uitsluitend atomisch na verificatie

Er is geen prullenbak. Daarom mag de bron alleen verdwijnen als alle controles
geslaagd zijn én een volledig resultaat als verborgen tijdelijk bestand klaar
staat op hetzelfde filesystem als de bron. De enige verwijderende operatie is
de atomische naamwissel die het resultaat op het oorspronkelijke pad zet.

Faalt verificatie, metadata-behoud of het kopiëren naar het tijdelijke bestand,
dan blijft de bron onaangeraakt. Dit geldt ook voor opgeruimde trailers en
extra's: zonder een aparte, handmatige verwijderbevestiging worden die niet
gewist.

## 5. Er blijft altijd minstens één audiospoor over

Voldoet geen enkel spoor aan je talenlijst, houd dan het eerste originele
spoor. Een Koreaanse film mag er niet zonder audio uitkomen.

## 6. Vier categorieën, en maar één ervan mag weg

| Categorie | Betekenis | Mag verwijderd worden |
|---|---|---|
| `ok` | hoofdbestand | nee |
| `extra` | zekere treffer op achtervoegsel of extra-map | ja |
| `kort` | alleen op speelduur afgekeurd | **nooit** |
| `geen_video` | ander bestandstype | nee, niet aanraken |

`kort` mag nooit weg: dat kan een anime-aflevering van vier minuten of een OVA
zijn.

## 7. Naast de naamcontrole staat een tweede slot

Voordat er iets als extra wordt opgeruimd, kijkt een tweede controle naar de
**omgeving** in plaats van naar de naam:

- enige video in zijn map → blijft staan
- grootste video in zijn map → blijft staan
- groter dan `max_mb` (standaard 500) → blijft staan
- staat in een echte extra-map → mag weg, de map is het bewijs

`startcode/bestandsfilter.py:veilig_om_op_te_ruimen()` doet dit al.

## 8. Bestandsnaam en extensie blijven gelijk

Altijd MKV, altijd dezelfde stam. Zie `02-domeinkennis.md`, punt 14.

## 9. Hardlinks worden gedetecteerd en getoond

`os.stat(pad).st_nlink > 1` → badge in de UI, en standaard **niet** verwerken
tenzij je dat expliciet aanzet. Zie `02-domeinkennis.md`, punt 15.

Dit is de enige manier waarop dit project je netto schijfruimte kan kosten.

## 10. Nooit `shell=True`

Altijd een lijst met argumenten. Je bibliotheek bevat gegarandeerd een
bestandsnaam met een apostrof, een `&`, of een accent dat je quoting breekt.

## 11. Twee schrijvers op één bestand kan niet

Draait er nog een Unmanic naast, dan kunnen twee processen hetzelfde bestand
willen vervangen. Neem een claim in de database (`status = bezig`,
`worker_id`), en controleer vlak vóór de vervanging of het origineel nog
dezelfde grootte en mtime heeft als bij het scannen.

Klopt dat niet meer, dan is het bestand ondertussen veranderd: job afbreken,
niet vervangen.

## 12. Bij twijfel: overslaan

Kan de bitrate niet bepaald worden, is de speelduur onbekend, geeft ffprobe
niets terug — dan overslaan en loggen. Nooit "dan doen we het maar gewoon".

Een overgeslagen bestand kost je niets. Een verkeerd verwerkt bestand kost je
een re-download van 60 GB.

## 13. HDR10+ wordt nooit geforceerd

Een bron met HDR10+ wordt overgeslagen. De interface mag **nooit** een actie
"negeer", "forceer" of "toch verwerken" aanbieden. De foutweergave legt uit
dat de dynamische metadata niet betrouwbaar behouden kan worden, toont de
detectie en biedt alleen veilige vervolgstappen: profiel controleren, bestand
overslaan of de ongewijzigde bron behouden.

## 14. Een hardlink wordt niet automatisch hersteld

Een transcode levert bewust een nieuw bestand op. Dat kan daarom niet tegelijk
een herstel van de oude hardlink zijn: de torrent en de nieuwe encode hebben
andere inhoud. Hardlinks blijven standaard uitgesloten. Alleen na een
expliciete instelling én een bevestiging per bestand mag de pipeline het
bestand als zelfstandige kopie vervangen. De UI noemt dit eerlijk
"hardlink verbreken" en toont de extra tijdelijke en blijvende ruimte die dit
kost; zij belooft nooit dat een oorspronkelijke hardlink wordt hersteld.

## 15. Rechten en bestandsmetadata gaan mee

Vóór de encode worden eigenaar, groep, mode, POSIX-ACL's en relevante extended
attributes vastgelegd. Vóór de atomische wissel worden die op het tijdelijke
resultaat hersteld en gecontroleerd. Lukt dat niet, dan faalt de job vóórdat
het origineel wordt vervangen. Dit is geen voorkeur of schakelaar: een
geslaagde encode waar Jellyfin of Samba daarna niet bij kan is niet geslaagd.

## 16. Vervangen moet een stroomstoring overleven

Iedere vervanging heeft een rij in `vervang_transactie`. Bij opstarten handelt
de backend iedere onafgemaakte stap af: vóór de naamwissel blijft de bron op
haar pad staan; ná de naamwissel staat daar het geverifieerde resultaat. Is
geen van beide toestanden aantoonbaar, dan wordt het een conflict voor de
gebruiker. Nooit gissen, nooit een tijdelijk bestand als definitief resultaat
aanzien.

## 17. Schijven en mounts worden vóór elke job gecontroleerd

Controleer dat cache en library echt gemount, schrijfbaar en op het verwachte
bestandssysteem staan. Is een netwerkshare of USB-schijf weg,
dan start er geen job. De planner reserveert cache voor **alle** actieve jobs
samen; niet tweemaal optimistisch dezelfde vrije ruimte tellen.

Een nieuwe bibliotheek wordt pas opgeslagen na een eerste-gebruiktest met een
verborgen tijdelijk bestand: schrijven en `fsync`, zichtbare metadata
terugzetten, atomisch hernoemen, teruglezen en altijd opruimen. Lokale
mediamappen mogen overal staan behalve in beschermde systeem- of
pipelinepaden. Een SMB-URI bevat nooit accountgegevens; de vaste root-sockethelper
mount alleen privé-netwerkshares onder `/library/netwerk`. Bij iedere scan en
job moet `findmnt` opnieuw exact dezelfde CIFS `server/share` aantonen.

## 18. Veiligheidslagen zijn geen optie

Verificatie, metadata-behoud, mountcontrole, hersteltransacties en de
goedkeurings- of strengere begrensde policy-poort kunnen niet via de interface
worden uitgezet. HDR10+, Dolby Vision, hardlinks, permanent opruimen,
canary-vrijgave, encoderwissels en iedere verificatie-, metadata-, mount- of
herstelstoring blijven handmatig of geblokkeerd. Een uitvoerfout, inclusief
een OOM/process-kill, mag volgens de expliciete eigenaarskeuze exact één
automatische herstelpoging krijgen: tijdelijke joboutput wordt eerst
gecontroleerd verwijderd, de oorspronkelijke bron blijft onaangeraakt en een
tweede fout wordt nooit automatisch opnieuw gestart.
Optionele gemaksfuncties wel. Zie `08-opslag-herstel-en-schakelaars.md`.

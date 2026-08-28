# Figma-afstemming

Het visuele uitgangspunt voor de webinterface is het
[Figma-ontwerp](https://www.figma.com/design/9CQugincBWbZfyDj7xOtiB/Untitled?node-id=0-1&t=FT6CCCZHlwyxKpiI-1).

Gebruik het voor indeling, hiërarchie, donkere uitstraling, tabellen, kaarten,
statuschips en interactiepatronen. De inhoudelijke specificaties in dit pakket
zijn echter leidend. Bij een verschil gaan `04-veiligheidsregels.md`,
`07-bediening-en-beheer.md` en `08-opslag-herstel-en-schakelaars.md` vóór.

## Verplichte afwijkingen van het huidige ontwerp

- Geen pagina, instelling of knop voor een prullenbak. Een geverifieerd
  resultaat vervangt het origineel atomisch en permanent.
- HDR10+ is altijd **overslaan**. Geen “Negeer & Forceer” of vergelijkbare
  route.
- Een hardlink is standaard **overslaan**. De optionele actie heet “hardlink
  verbreken na bevestiging”; herstel van de oorspronkelijke hardlink wordt
  nooit beloofd.
- Navigatie: Dashboard, Beslissingen, Wachtrij, Workers, Bibliotheken,
  Profielen, Kwaliteit, Logs, Fouten en Instellingen.
- Instellingen bevat een onderdeel **Onderdelen** met de schakelbare opties uit
  `08-opslag-herstel-en-schakelaars.md`.
- Dashboard en systeemstatus tonen de project-SSD op
  `/srv/av1-pipeline`: vrije ruimte, gereserveerde ruimte, mountstatus en
  actieve cachejobs.
- Voeg statusweergave toe voor canary-limiet, hersteltransacties, back-ups,
  behoud van bestandsmetadata en QSV-diagnostiek.

## Ontwerpvolgorde

1. Werk eerst de vaste navigatie en foutieve/onveilige teksten bij.
2. Maak vervolgens Workers, Kwaliteit en Instellingen → Onderdelen.
3. Voeg SSD-, herstel-, canary- en back-upstatus toe aan Dashboard en Fouten.
4. Gebruik pas daarna microcopy en visuele verfijning.

De frontend mag nooit functionaliteit suggereren die de backend niet kan
uitvoeren. Elke gevaarlijke actie heeft een duidelijke bevestiging en is via
toetsenbord te bereiken.

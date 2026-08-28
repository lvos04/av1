# Fase 6 — Frontend: dashboard, wachtrij, workers

**Doel:** de drie schermen die je het meest openhebt.

**Duur:** 5-7 uur.

---

## Lees eerst

- `spec/05-ui.md` — schermen 1, 2 en 4
- `spec/07-bediening-en-beheer.md` — navigatie, status en toegankelijkheid
- `spec/09-figma-afstemming.md` — visueel vertrekpunt en verplichte correcties

## Opdracht

Next.js (App Router) + TypeScript + Tailwind + shadcn/ui. Recharts voor de
grafieken.

**Opzet:**

- `lib/api.ts` — getypeerde fetch-wrapper om de REST-API
- `lib/ws.ts` — één WebSocket-verbinding voor de hele app, met automatisch
  herverbinden en een backoff. Deel hem via React context; open er nooit één
  per component
- `types/` — TypeScript-types die overeenkomen met de pydantic-modellen.
  Genereren uit de OpenAPI-spec mag, met de hand overtypen mag ook, als ze maar
  kloppen
- donkere modus als standaard
- toetsenbordbediening en zichtbare focus vanaf de eerste componenten;
  bouw `j`, `k`, `/` en `?` als gedeelde bediening, niet per scherm opnieuw

**Scherm 1 — Dashboard** (`/`)

Vier tegels bovenin, de twee baankaarten in het midden, twee grafieken
onderaan. Precies zoals in `spec/05-ui.md` staat.

De baankaart is het belangrijkste component van de hele app. Toon `percent`,
`fps`, `speed`, ETA, bitrate, en de grootte tot nu toe tegenover de bron met
het percentage besparing. **Toon beide banen altijd**, ook als er één
leegstaat.

Live cijfers komen uit het `tick`-bericht. Zorg dat een getal dat niet
binnenkomt niet naar 0 springt maar zijn laatste waarde houdt en vergrijst —
anders staat je dashboard te knipperen.

**Scherm 2 — Wachtrij** (`/wachtrij`)

Tabel met filters op status, baan, library en encoder. Meervoudige selectie met
bulkacties. De reden is altijd zichtbaar, niet achter een tooltip. Virtualiseer
de lijst (`@tanstack/react-virtual`) — er kunnen tienduizend rijen in staan.

**Scherm 4 — Workers** (`/workers`)

Per baan de instellingen en de laatste 20 jobs met duur en besparing.

## Acceptatietest

- dashboard openen tijdens twee actieve encodes: beide kaarten lopen, fps en
  ETA veranderen elke seconde
- backend herstarten: de frontend verbindt vanzelf opnieuw, zonder verversen
  en zonder een muur van foutmeldingen
- wachtrij met 10.000 rijen scrollt vloeiend
- een job afbreken vanuit de UI stopt hem werkelijk

## Niet doen

Geen Redux, Zustand of andere globale state-bibliotheek. Serverstate hoort in
React Query (of `use` + fetch); live state komt uit de WebSocket-context.
Verder heb je niets nodig.

Bouw geen eigen componentbibliotheek. shadcn/ui geeft je tabellen, dialogen en
formulieren; besteed je tijd aan de baankaart en de beslissingenlijst.

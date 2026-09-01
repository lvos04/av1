"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import AiPage from "@/app/ai/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { bytes } from "@/lib/utils";

interface Settings {
  server_naam: string;
  tijdzone: string;
  log_niveau: "debug" | "info" | "warning" | "error";
  cpu_max_percent: number;
  cpu_max_temperatuur: number;
  max_gelijktijdige_encodes: number;
  cache_waarschuwing_gb: number;
  cache_pauze_gb: number;
  hardlink_modus: "overslaan" | "verbreek_na_bevestiging";
  meld_browser_fouten: boolean;
  meld_browser_batches: boolean;
  pauzeer_cpu_bij_kijken: boolean;
}

interface Feature {
  sleutel: string;
  ingeschakeld: boolean;
  uitleg: string;
  configuratie: Record<string, unknown>;
  laatste_fout: string | null;
}

interface Integration {
  naam: "jellyfin" | "radarr" | "sonarr";
  url: string | null;
  sleutel_ingesteld: boolean;
}

interface Backup {
  id: number;
  pad: string;
  grootte: number;
  sha256: string;
  gemaakt_op: string;
  gecontroleerd_op: string | null;
}

interface Diagnosis {
  dependencies: Record<string, unknown>;
  systeem: Record<string, unknown>;
  mounts: Record<string, unknown>;
}

interface Recovery {
  id: number;
  job_id: number;
  status: string;
  bron_pad: string;
  tijdelijk_pad: string | null;
  toelichting: string | null;
  gewijzigd_op: string;
}

interface StorageState {
  modus: "systeemschijf" | "aparte_schijf";
  pipeline_pad: string;
  werkruimte_pad: string;
  cache_pad: string;
  werk_pad: string;
  config_pad: string;
  backup_pad: string;
  backups_op: "systeemschijf" | "werkruimte";
  systeemschijf_reserve_gb: number;
  werkruimte_mountpoint: boolean;
  werkruimte_op_systeemschijf: boolean;
  vrij_bytes: number;
  totaal_bytes: number;
  cache_inhoud: { bestanden: number; mappen: number; bytes: number };
  werk_inhoud: { bestanden: number; mappen: number; bytes: number };
  kan_wisselen: boolean;
  actieve_jobs: number;
  achtergrondtaken: number;
  opslagfout: string | null;
  uitleg: string;
}

interface LanAccessState {
  ingeschakeld: boolean;
  gebruikersnaam: string;
  wachtwoord_ingesteld: boolean;
  adressen: string[];
  laatste_fout: string | null;
  gewijzigd_op: string;
  uitleg: string;
}

interface QueueRecoveryState {
  actieve_jobs: number[];
  weesjobs: number[];
  tijdelijke_jobs: number[];
  onbekende_paden: string[];
  onveilige_paden: string[];
  onveilige_workerinstellingen: Array<{ baan: string; aantal: number; aanbevolen: number }>;
  geheugen_totaal_bytes: number;
  geheugen_beschikbaar_bytes: number;
  kan_herstellen: boolean;
  uitleg: string;
}

interface QueueRecoveryResult {
  herstelde_jobs: number[];
  opgeruimde_jobs: number[];
  verwijderde_mappen: number;
  herstelde_workerbanen: string[];
  onbekende_paden_overgeslagen: string[];
  uitleg: string;
}

interface AiControllerState {
  ingeschakeld: boolean;
  modus: "schaduw" | "actief";
  provider: "openai";
  model: string;
  reasoning: string;
  maandbudget_usd: number;
  hdr10_vrijgegeven: boolean;
  api_sleutel_ingesteld: boolean;
  laatste_fout: string | null;
  gewijzigd_op: string;
  verbruik: {
    maand: string;
    input_tokens: number;
    output_tokens: number;
    kosten_usd: number;
    analyses: number;
  };
  hdr10_poort: {
    gereed: boolean;
    cpu: { geslaagd: number; afwijkingen: number };
    qsv: { geslaagd: number; afwijkingen: number };
    tv_test_vereist: boolean;
    uitleg: string;
  };
  bevoegdheden: string[];
  altijd_handmatig: string[];
}

const confirmations: Record<string, { enabled: boolean; text: string }> = {
  hardlink_verbreken: { enabled: true, text: "HARDLINKS VERBREKEN" },
  dv_experimenteel: { enabled: true, text: "EXPERIMENTEEL DOLBY VISION" },
  canary_limiet: { enabled: false, text: "CANARY-LIMIET UITZETTEN" },
};

export default function SettingsPage() {
  const [showAi, setShowAi] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [features, setFeatures] = useState<Feature[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [recovery, setRecovery] = useState<Recovery[]>([]);
  const [storage, setStorage] = useState<StorageState | null>(null);
  const [lanAccess, setLanAccess] = useState<LanAccessState | null>(null);
  const [queueRecovery, setQueueRecovery] = useState<QueueRecoveryState | null>(null);
  const [aiController, setAiController] = useState<AiControllerState | null>(null);
  const [storageMode, setStorageMode] = useState<StorageState["modus"]>("systeemschijf");
  const [message, setMessage] = useState("");
  const storageForm = useRef<HTMLFormElement>(null);

  const load = useCallback(async () => {
    const [loadedSettings, loadedFeatures, loadedIntegrations, loadedBackups, loadedRecovery, loadedStorage, loadedLanAccess, loadedQueueRecovery, loadedAiController] = await Promise.all([
      api<Settings>("/instellingen"),
      api<Feature[]>("/functies"),
      api<Integration[]>("/integraties"),
      api<Backup[]>("/backups"),
      api<Recovery[]>("/herstel"),
      api<StorageState>("/opslag"),
      api<LanAccessState>("/lan-toegang"),
      api<QueueRecoveryState>("/systeem/wachtrijherstel"),
      api<AiControllerState>("/ai-controller"),
    ]);
    setSettings(loadedSettings);
    setFeatures(loadedFeatures);
    setIntegrations(loadedIntegrations);
    setBackups(loadedBackups);
    setRecovery(loadedRecovery);
    setStorage(loadedStorage);
    setStorageMode(loadedStorage.modus);
    setLanAccess(loadedLanAccess);
    setQueueRecovery(loadedQueueRecovery);
    setAiController(loadedAiController);
  }, []);

  useEffect(() => { load().catch((error: unknown) => setMessage(String(error))); }, [load]);
  useEffect(() => {
    const update = () => setShowAi(window.location.search === "?tab=ai");
    update();
    window.addEventListener("popstate", update);
    window.addEventListener("pipeline:location", update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("pipeline:location", update);
    };
  }, []);

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const result = await mutation<Settings>("/instellingen", "PATCH", {
        server_naam: String(data.get("server_naam")),
        tijdzone: String(data.get("tijdzone")),
        log_niveau: String(data.get("log_niveau")),
        cpu_max_percent: Number(data.get("cpu_max_percent")),
        cpu_max_temperatuur: Number(data.get("cpu_max_temperatuur")),
        max_gelijktijdige_encodes: Number(data.get("max_gelijktijdige_encodes")),
        cache_waarschuwing_gb: Number(data.get("cache_waarschuwing_gb")),
        cache_pauze_gb: Number(data.get("cache_pauze_gb")),
        hardlink_modus: String(data.get("hardlink_modus")),
        meld_browser_fouten: data.get("meld_browser_fouten") === "on",
        meld_browser_batches: data.get("meld_browser_batches") === "on",
        pauzeer_cpu_bij_kijken: data.get("pauzeer_cpu_bij_kijken") === "on",
      });
      setSettings(result);
      setMessage("Instellingen server-side gevalideerd en opgeslagen.");
    } catch (error) { setMessage(String(error)); }
  }

  async function saveAiController(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await mutation<AiControllerState>("/ai-controller", "PATCH", {
        ingeschakeld: data.get("ingeschakeld") === "on",
        modus: String(data.get("modus")),
        maandbudget_usd: Number(data.get("maandbudget_usd")),
        api_sleutel: String(data.get("api_sleutel") ?? "") || undefined,
        hdr10_vrijgegeven: data.get("hdr10_vrijgegeven") === "on",
        bevestiging: String(data.get("bevestiging") ?? "") || undefined,
        hdr10_bevestiging: String(data.get("hdr10_bevestiging") ?? "") || undefined,
      });
      setAiController(result);
      form.reset();
      setMessage(
        result.modus === "actief" && result.ingeschakeld
          ? "Begrensde AI-controller actief; alleen policy-toegestane acties lopen door."
          : "AI-controllerconfiguratie opgeslagen in schaduwmodus of uitgeschakeld.",
      );
    } catch (error) {
      setMessage(String(error));
    }
  }

  function storagePayload(form: HTMLFormElement) {
    const data = new FormData(form);
    return {
      modus: storageMode,
      werkruimte_pad: storageMode === "systeemschijf"
        ? storage?.pipeline_pad ?? "/srv/av1-pipeline"
        : String(data.get("werkruimte_pad") ?? ""),
      backups_op: String(data.get("backups_op")),
      systeemschijf_reserve_gb: Number(data.get("systeemschijf_reserve_gb")),
    };
  }

  async function checkStorage() {
    if (!storageForm.current) return;
    try {
      const result = await mutation<Record<string, unknown>>(
        "/opslag/controle",
        "POST",
        storagePayload(storageForm.current),
      );
      setMessage(`Opslagcontrole geslaagd: ${JSON.stringify(result)}`);
    } catch (error) { setMessage(String(error)); }
  }

  async function applyStorage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const confirmation = window.prompt("Typ exact: OPSLAG WIJZIGEN");
    if (confirmation === null) return;
    try {
      await mutation<StorageState>("/opslag", "PATCH", {
        ...storagePayload(event.currentTarget),
        bevestiging: confirmation,
      });
      await load();
      setMessage("Opslag gewijzigd. Controleer de paden en hervat daarna beide workers.");
    } catch (error) { setMessage(String(error)); }
  }

  async function toggleFeature(feature: Feature) {
    const enabled = !feature.ingeschakeld;
    const risky = confirmations[feature.sleutel];
    let confirmation: string | null = null;
    if (risky && enabled === risky.enabled) {
      confirmation = window.prompt(`Typ exact: ${risky.text}`);
      if (confirmation === null) return;
    }
    try {
      await mutation(`/functies/${feature.sleutel}`, "PATCH", {
        ingeschakeld: enabled,
        configuratie: feature.configuratie,
        bevestiging: confirmation,
      });
      await load();
    } catch (error) { setMessage(String(error)); }
  }

  async function saveLanAccess(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await mutation<LanAccessState>("/lan-toegang", "PATCH", {
        ingeschakeld: true,
        gebruikersnaam: String(data.get("gebruikersnaam") ?? ""),
        wachtwoord: String(data.get("wachtwoord") ?? "") || undefined,
        bevestiging: String(data.get("bevestiging") ?? ""),
      });
      setLanAccess(result);
      form.reset();
      setMessage("Beveiligde LAN-toegang staat aan. Meld op een andere computer aan met dit account.");
    } catch (error) { setMessage(String(error)); }
  }

  async function disableLanAccess() {
    try {
      const result = await mutation<LanAccessState>("/lan-toegang", "PATCH", {
        ingeschakeld: false,
      });
      setLanAccess(result);
      setMessage("LAN-toegang staat uit; lokale bediening op deze machine blijft werken.");
    } catch (error) { setMessage(String(error)); }
  }

  async function saveIntegration(event: FormEvent<HTMLFormElement>, item: Integration) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await mutation(`/integraties/${item.naam}`, "PATCH", {
        url: String(data.get("url") ?? ""),
        api_sleutel: String(data.get("api_sleutel") ?? "") || undefined,
      });
      form.reset();
      await load();
      setMessage(`${item.naam} opgeslagen; de sleutel komt niet terug in dit antwoord.`);
    } catch (error) { setMessage(String(error)); }
  }

  async function testIntegration(item: Integration) {
    try {
      await mutation(`/integraties/${item.naam}/test`, "POST", {});
      setMessage(`${item.naam} is bereikbaar met het versleuteld opgeslagen geheim.`);
    } catch (error) { setMessage(String(error)); }
  }

  async function requestNotifications() {
    if (!("Notification" in window)) {
      setMessage("Deze browser ondersteunt geen systeemmeldingen; het dashboard blijft werken.");
      return;
    }
    const permission = await Notification.requestPermission();
    setMessage(permission === "granted" ? "Browsermeldingen toegestaan." : "Browsermeldingen geweigerd; het dashboard blijft volledig werken.");
  }

  async function createBackup() {
    await mutation("/backups/nu", "POST");
    await load();
    setMessage("Back-up met SHA-256-manifest gemaakt.");
  }

  async function restoreQueue() {
    const confirmation = window.prompt("Typ exact: WACHTRIJ VEILIG HERSTELLEN");
    if (confirmation === null) return;
    try {
      const result = await mutation<QueueRecoveryResult>("/systeem/wachtrijherstel", "POST", {
        bevestiging: confirmation,
      });
      await load();
      setMessage(
        `${result.herstelde_jobs.length} vastgelopen job(s) hersteld en ` +
        `${result.verwijderde_mappen} tijdelijke map(pen) veilig verwijderd. ` +
        `${result.herstelde_workerbanen.length} workerinstelling(en) begrensd. ` +
        "Bronbestanden en voltooide jobs zijn niet gewijzigd.",
      );
    } catch (error) { setMessage(String(error)); }
  }

  if (showAi) return <AiPage />;
  if (!settings) return <Card className="empty-state">Instellingen worden geladen…</Card>;
  return <div className="page-stack">
    {message ? <Card className="notice" role="status">{message}</Card> : null}
    {storage ? <form ref={storageForm} onSubmit={applyStorage}><Card className="settings-card"><div className="worker-header"><div><h2>Opslag kiezen</h2><span className="muted">Cache en werkruimte kunnen later samen naar een echte extra mount. De kleine database en configuratie blijven altijd op de systeemschijf.</span></div><Badge tone={storage.opslagfout ? "red" : "green"}>{storage.opslagfout ? "opslagfout" : "veilig actief"}</Badge></div><div className="settings-grid">
      <Field label="Werkopslag"><select name="modus" value={storageMode} onChange={(event) => setStorageMode(event.target.value as StorageState["modus"])}><option value="systeemschijf">1-TB-systeemschijf</option><option value="aparte_schijf">aparte gemounte werkschijf</option></select></Field>
      <Field label="Mountpad extra werkschijf"><input name="werkruimte_pad" defaultValue={storage.modus === "aparte_schijf" ? storage.werkruimte_pad : "/mnt/av1-werkruimte"} disabled={storageMode === "systeemschijf"} placeholder="/mnt/av1-werkruimte" /></Field>
      <Field label="Nieuwe back-ups"><select name="backups_op" defaultValue={storage.backups_op}><option value="systeemschijf">systeemschijf</option><option value="werkruimte">gekozen werkruimte</option></select></Field>
      <Field label="Vaste systeemreserve (GB)"><input name="systeemschijf_reserve_gb" type="number" min="150" max="500" defaultValue={storage.systeemschijf_reserve_gb} /></Field>
    </div><div className="decision-actions"><Button type="button" variant="secondary" onClick={checkStorage}>Controleer keuze</Button><Button type="submit">Pas opslag veilig toe</Button></div><p className="muted">{storage.uitleg}</p><p className="mono muted">Cache: {storage.cache_pad}<br />Werk: {storage.werk_pad}<br />Configuratie: {storage.config_pad}<br />Back-ups: {storage.backup_pad}</p><p className="muted">{bytes(storage.vrij_bytes)} vrij van {bytes(storage.totaal_bytes)} · cache {bytes(storage.cache_inhoud.bytes)} · werk {bytes(storage.werk_inhoud.bytes)} · {storage.actieve_jobs} actieve jobs. Wisselen kan alleen met lege cache/werkmap en zonder achtergrondtaak. De UI mount of formatteert nooit een schijf.</p>{storage.opslagfout ? <p className="danger">{storage.opslagfout}</p> : null}</Card></form> : null}
    <form onSubmit={saveSettings}><Card className="settings-card"><div className="worker-header"><div><h2>Algemeen en grenzen</h2><span className="muted">Bedieningswaarden in SQLite; opslagpaden beheer je hierboven als één gecontroleerde indeling.</span></div><Button type="submit">Opslaan</Button></div><div className="settings-grid">
      <Field label="Servernaam"><input name="server_naam" defaultValue={settings.server_naam} required /></Field>
      <Field label="Tijdzone"><input name="tijdzone" defaultValue={settings.tijdzone} required /></Field>
      <Field label="Logniveau"><select name="log_niveau" defaultValue={settings.log_niveau}><option>debug</option><option>info</option><option>warning</option><option>error</option></select></Field>
      <Field label="CPU-limiet (%)"><input name="cpu_max_percent" type="number" min="10" max="100" defaultValue={settings.cpu_max_percent} /></Field>
      <Field label="Temperatuurgrens (°C)"><input name="cpu_max_temperatuur" type="number" min="40" max="110" defaultValue={settings.cpu_max_temperatuur} /></Field>
      <Field label="Gelijktijdige encodes"><input name="max_gelijktijdige_encodes" type="number" min="1" defaultValue={settings.max_gelijktijdige_encodes} /><small>1 is aanbevolen met het huidige RAM; na een geheugenupgrade kun je dit verhogen.</small></Field>
      <Field label="Cachewaarschuwing (GB)"><input name="cache_waarschuwing_gb" type="number" min="2" defaultValue={settings.cache_waarschuwing_gb} /></Field>
      <Field label="Cachepauze (GB)"><input name="cache_pauze_gb" type="number" min={storage?.modus === "systeemschijf" ? storage.systeemschijf_reserve_gb : 1} defaultValue={settings.cache_pauze_gb} /></Field>
      <Field label="Hardlinkbeleid"><select name="hardlink_modus" defaultValue={settings.hardlink_modus}><option value="overslaan">altijd overslaan</option><option value="verbreek_na_bevestiging">verbreek na bevestiging</option></select></Field>
      <label className="toggle-row"><input name="pauzeer_cpu_bij_kijken" type="checkbox" defaultChecked={settings.pauzeer_cpu_bij_kijken} /><span>CPU pauzeren bij Jellyfin-afspelen</span></label>
      <label className="toggle-row"><input name="meld_browser_fouten" type="checkbox" defaultChecked={settings.meld_browser_fouten} /><span>Browsermelding bij fouten</span></label>
      <label className="toggle-row"><input name="meld_browser_batches" type="checkbox" defaultChecked={settings.meld_browser_batches} /><span>Browsermelding bij afgeronde batch</span></label>
    </div><Button type="button" variant="secondary" onClick={requestNotifications}>Vraag browserrechten</Button></Card></form>

    {aiController ? <form key={aiController.gewijzigd_op} onSubmit={saveAiController}><Card className="settings-card"><div className="worker-header"><div><h2>AI-controller</h2><span className="muted">OpenAI {aiController.model} · reasoning {aiController.reasoning} · geen tools · store:false · paden alleen als HMAC.</span></div><Badge tone={aiController.ingeschakeld ? aiController.modus === "actief" ? "green" : "amber" : "neutral"}>{aiController.ingeschakeld ? aiController.modus : "uit"}</Badge></div><div className="settings-grid">
      <label className="toggle-row"><input name="ingeschakeld" type="checkbox" defaultChecked={aiController.ingeschakeld} /><span>AI-analyses uitvoeren</span></label>
      <Field label="Modus"><select name="modus" defaultValue={aiController.modus}><option value="schaduw">Schaduw — alleen voorstellen</option><option value="actief">Actief — uitsluitend binnen policy</option></select></Field>
      <Field label="Maandbudget (USD)"><input name="maandbudget_usd" type="number" min="0.10" max="100" step="0.10" defaultValue={aiController.maandbudget_usd} /></Field>
      <Field label={aiController.api_sleutel_ingesteld ? "Nieuwe API-sleutel (leeg = behouden)" : "OpenAI API-sleutel"}><input name="api_sleutel" type="password" autoComplete="new-password" /></Field>
      <Field label="Actieve-modusbevestiging"><input name="bevestiging" placeholder="AI-BEPERKTE AUTONOMIE INSCHAKELEN" autoComplete="off" /></Field>
      <label className="toggle-row"><input name="hdr10_vrijgegeven" type="checkbox" defaultChecked={aiController.hdr10_vrijgegeven} disabled={!aiController.hdr10_poort.gereed} /><span>HDR10-autonomie afzonderlijk vrijgeven</span></label>
      <Field label="HDR10-bevestiging"><input name="hdr10_bevestiging" placeholder="HDR10-METADATA-AUTONOMIE" autoComplete="off" disabled={!aiController.hdr10_poort.gereed} /></Field>
    </div><p className="muted">Typ voor actieve modus exact <span className="mono">AI-BEPERKTE AUTONOMIE INSCHAKELEN</span>. Kosten deze maand: ${aiController.verbruik.kosten_usd.toFixed(4)} van ${aiController.maandbudget_usd.toFixed(2)} · {aiController.verbruik.analyses} analyses · {aiController.verbruik.input_tokens.toLocaleString("nl-NL")} inputtokens.</p><p className="muted">Automatisch toegestaan: {aiController.bevoegdheden.join("; ")}. Altijd handmatig/geblokkeerd: {aiController.altijd_handmatig.join("; ")}.</p><p className={aiController.hdr10_poort.gereed ? "muted" : "danger"}>HDR10-poort: {aiController.hdr10_poort.gereed ? "groen" : "geblokkeerd"} · CPU {aiController.hdr10_poort.cpu.geslaagd} goed/{aiController.hdr10_poort.cpu.afwijkingen} afwijkingen · QSV {aiController.hdr10_poort.qsv.geslaagd} goed/{aiController.hdr10_poort.qsv.afwijkingen} afwijkingen. {aiController.hdr10_poort.uitleg}</p>{aiController.laatste_fout ? <p className="danger">Laatste providerfout: {aiController.laatste_fout}</p> : null}<div className="decision-actions"><Button type="submit">Versleuteld opslaan</Button><Button type="button" variant="secondary" disabled={!aiController.ingeschakeld} onClick={async () => { try { const result = await mutation<Record<string, number>>("/ai-controller/analyseer", "POST"); await load(); setMessage(`AI-controle klaar: ${JSON.stringify(result)}`); } catch (error) { setMessage(String(error)); } }}>Nu in achtergrond controleren</Button></div><p className="muted">Prijsgrens in de controller: maximaal 8.000 input- en 800 outputtokens per gegroepeerde handtekening. Providerstoring, ongeldig JSON of budgetoverschrijding voert niets uit.</p></Card></form> : null}

    {lanAccess ? <form onSubmit={saveLanAccess}><Card className="settings-card"><div className="worker-header"><div><h2>Toegang vanaf andere computers</h2><span className="muted">De webinterface gebruikt poort 3000. De backend en poort 8000 blijven afgeschermd op deze machine.</span></div><Badge tone={lanAccess.ingeschakeld ? "green" : "neutral"}>{lanAccess.ingeschakeld ? "LAN actief" : "alleen lokaal"}</Badge></div><div className="settings-grid">
      <Field label="Gebruikersnaam"><input name="gebruikersnaam" defaultValue={lanAccess.gebruikersnaam} minLength={3} maxLength={64} pattern="[A-Za-z0-9._-]+" autoComplete="username" required /></Field>
      <Field label={lanAccess.wachtwoord_ingesteld ? "Nieuw wachtwoord (leeg = behouden)" : "Wachtwoord"}><input name="wachtwoord" type="password" minLength={12} maxLength={128} autoComplete="new-password" required={!lanAccess.wachtwoord_ingesteld} /></Field>
      <Field label="Bevestiging"><input name="bevestiging" placeholder="LAN-TOEGANG INSCHAKELEN" autoComplete="off" required /></Field>
    </div><p className="muted">Typ bij Bevestiging exact <span className="mono">LAN-TOEGANG INSCHAKELEN</span>. Het wachtwoord wordt direct geheugenhard gehasht en komt niet terug in de browser of logs.</p><div className="decision-actions"><Button type="submit">{lanAccess.ingeschakeld ? "Account veilig bijwerken" : "LAN-toegang inschakelen"}</Button>{lanAccess.ingeschakeld ? <Button type="button" variant="danger" onClick={disableLanAccess}>LAN-toegang uitschakelen</Button> : null}</div>{lanAccess.adressen.length ? <p className="mono muted">Open daarna op een andere computer:<br />{lanAccess.adressen.join("\n")}</p> : <p className="muted">Geen actief IPv4-netwerkadres gevonden.</p>}<p className="muted">Alleen privé-IP-adressen worden geaccepteerd. Na vijf foute aanmeldingen volgt tijdelijk een blokkade. Gebruik voor toegang vanaf internet eerst een VPN; stuur poort 3000 niet door in je router.</p>{lanAccess.laatste_fout ? <p className="danger">{lanAccess.laatste_fout}</p> : null}</Card></form> : null}

    <Card className="settings-card"><h2>Onderdelen</h2><p className="muted">Uitschakelen stopt alleen nieuw werk en verwijdert niets. Kernveiligheid staat niet in deze lijst.</p><div className="feature-list">{features.filter((feature) => !["externe_toegang", "ai_controller"].includes(feature.sleutel)).map((feature) => <div className="feature-row" key={feature.sleutel}><div><strong>{feature.sleutel.replaceAll("_", " ")}</strong><p>{feature.uitleg}</p>{feature.laatste_fout ? <small className="danger">{feature.laatste_fout}</small> : null}</div><Button variant={feature.ingeschakeld ? "secondary" : "default"} aria-pressed={feature.ingeschakeld} onClick={() => toggleFeature(feature)}>{feature.ingeschakeld ? "Aan" : "Uit"}</Button></div>)}</div></Card>

    <Card className="settings-card"><div className="worker-header"><div><h2>Hardwarediagnose</h2><span className="muted">QSV-test en GPU-meting veranderen of resetten geen kernel-driver.</span></div><div className="decision-actions"><Button variant="secondary" onClick={async () => { try { setMessage(JSON.stringify(await mutation("/systeem/qsv-test", "POST"))); } catch (error) { setMessage(String(error)); } }}>Test QSV-verbinding</Button><Button variant="secondary" onClick={async () => setMessage(JSON.stringify(await mutation("/systeem/gpu-herstart", "POST")))}>Herstart GPU-monitor</Button><Button variant="ghost" onClick={async () => setDiagnosis(await api<Diagnosis>("/systeem/diagnose"))}>Ververs diagnose</Button></div></div>{diagnosis ? <pre>{JSON.stringify(diagnosis, null, 2)}</pre> : null}</Card>

    {queueRecovery ? <Card className="settings-card"><div className="worker-header"><div><h2>Wachtrij herstellen</h2><span className="muted">Algemene veilige reset voor vastgelopen jobstatussen, te zware workerinstellingen en achtergebleven tijdelijke cache.</span></div><Badge tone={queueRecovery.actieve_jobs.length ? "blue" : queueRecovery.kan_herstellen ? "amber" : "green"}>{queueRecovery.actieve_jobs.length ? "job actief" : queueRecovery.kan_herstellen ? "herstel beschikbaar" : "geen herstel nodig"}</Badge></div><p>{queueRecovery.uitleg}</p><p className="muted">Werkelijk actief: {queueRecovery.actieve_jobs.length} · vastgelopen: {queueRecovery.weesjobs.length} · tijdelijke jobmappen: {queueRecovery.tijdelijke_jobs.length} · {bytes(queueRecovery.geheugen_beschikbaar_bytes)} van {bytes(queueRecovery.geheugen_totaal_bytes)} RAM beschikbaar</p>{queueRecovery.onveilige_workerinstellingen.map((item) => <p className="danger" key={item.baan}>{item.baan.toUpperCase()} gebruikt {item.aantal} workers; voor deze host is {item.aanbevolen} veilig om geheugentekort te voorkomen.</p>)}{queueRecovery.onbekende_paden.length ? <p className="danger">{queueRecovery.onbekende_paden.length} onbekende tijdelijke paden worden bewust niet verwijderd.</p> : null}{queueRecovery.onveilige_paden.length ? <p className="danger">Herstel geblokkeerd: {queueRecovery.onveilige_paden[0]}</p> : null}<div className="decision-actions"><Button variant="secondary" onClick={async () => { try { setQueueRecovery(await api<QueueRecoveryState>("/systeem/wachtrijherstel")); setMessage("Wachtrijstatus opnieuw gecontroleerd."); } catch (error) { setMessage(String(error)); } }}>Controleer opnieuw</Button><Button variant="danger" disabled={!queueRecovery.kan_herstellen || queueRecovery.actieve_jobs.length > 0} onClick={restoreQueue}>Herstel vastgelopen wachtrij</Button></div><p className="muted">De knop weigert te werken zolang een echte encoder of achtergrondtaak actief is. Media in de bibliotheek en geslaagde resultaten worden nooit verwijderd.</p></Card> : null}

    <Card className="settings-card"><h2>Jellyfin, Radarr en Sonarr</h2><div className="integration-grid">{integrations.map((item) => <form key={item.naam} className="integration-card" onSubmit={(event) => saveIntegration(event, item)}><div className="worker-header"><strong>{item.naam}</strong><Badge tone={item.sleutel_ingesteld ? "green" : "neutral"}>{item.sleutel_ingesteld ? "sleutel ingesteld" : "geen sleutel"}</Badge></div><Field label="URL"><input name="url" type="url" defaultValue={item.url ?? ""} placeholder={`http://${item.naam}:7878`} /></Field><Field label="Nieuwe API-sleutel"><input name="api_sleutel" type="password" autoComplete="new-password" /></Field><div className="decision-actions"><Button type="submit">Versleuteld opslaan</Button><Button type="button" variant="secondary" onClick={() => testIntegration(item)}>Test opgeslagen verbinding</Button></div></form>)}</div></Card>

    <Card className="settings-card"><div className="worker-header"><div><h2>Back-ups</h2><span className="muted">Herstelcontrole opent een tijdelijke, alleen-lezen kopie.</span></div><Button onClick={createBackup}>Maak back-up</Button></div>{backups.map((backup) => <div className="backup-row" key={backup.id}><span><strong>{new Date(backup.gemaakt_op).toLocaleString("nl-NL")}</strong><small>{bytes(backup.grootte)} · {backup.sha256.slice(0, 12)}…</small></span><Badge tone={backup.gecontroleerd_op ? "green" : "neutral"}>{backup.gecontroleerd_op ? "herstelbaar getest" : "niet getest"}</Badge><Button variant="secondary" onClick={async () => { await mutation(`/backups/${backup.id}/controleer-herstel`, "POST"); await load(); }}>Controleer herstel</Button></div>)}</Card>
    <Card className="settings-card"><h2>Hersteltransacties</h2><p className="muted">Bij een conflict wordt nooit automatisch gegist. Inspecteer eerst; alleen het verborgen tijdelijke resultaat kan hier expliciet worden verwijderd.</p>{recovery.map((item) => <div className="backup-row" key={item.id}><span><strong>Job #{item.job_id} · {item.status}</strong><small>{item.bron_pad}<br />{item.toelichting}</small></span><Badge tone={item.status === "conflict" ? "red" : "amber"}>{item.status}</Badge>{item.status === "conflict" ? <Button variant="danger" onClick={async () => { if (window.confirm("Verwijder uitsluitend het tijdelijke resultaat? De bron blijft onaangeraakt.")) { await mutation(`/herstel/${item.id}/tijdelijk-resultaat`, "DELETE"); await load(); } }}>Tijdelijk resultaat verwijderen</Button> : null}</div>)}{!recovery.length ? <p className="empty-state">Geen open hersteltransacties.</p> : null}</Card>
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

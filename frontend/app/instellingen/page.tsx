"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

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

const confirmations: Record<string, { enabled: boolean; text: string }> = {
  externe_toegang: { enabled: true, text: "LAN-TOEGANG INSCHAKELEN" },
  hardlink_verbreken: { enabled: true, text: "HARDLINKS VERBREKEN" },
  dv_experimenteel: { enabled: true, text: "EXPERIMENTEEL DOLBY VISION" },
  canary_limiet: { enabled: false, text: "CANARY-LIMIET UITZETTEN" },
};

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [features, setFeatures] = useState<Feature[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [recovery, setRecovery] = useState<Recovery[]>([]);
  const [storage, setStorage] = useState<StorageState | null>(null);
  const [storageMode, setStorageMode] = useState<StorageState["modus"]>("systeemschijf");
  const [message, setMessage] = useState("");
  const storageForm = useRef<HTMLFormElement>(null);

  const load = useCallback(async () => {
    const [loadedSettings, loadedFeatures, loadedIntegrations, loadedBackups, loadedRecovery, loadedStorage] = await Promise.all([
      api<Settings>("/instellingen"),
      api<Feature[]>("/functies"),
      api<Integration[]>("/integraties"),
      api<Backup[]>("/backups"),
      api<Recovery[]>("/herstel"),
      api<StorageState>("/opslag"),
    ]);
    setSettings(loadedSettings);
    setFeatures(loadedFeatures);
    setIntegrations(loadedIntegrations);
    setBackups(loadedBackups);
    setRecovery(loadedRecovery);
    setStorage(loadedStorage);
    setStorageMode(loadedStorage.modus);
  }, []);

  useEffect(() => { load().catch((error: unknown) => setMessage(String(error))); }, [load]);

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
      <Field label="Cachewaarschuwing (GB)"><input name="cache_waarschuwing_gb" type="number" min="2" defaultValue={settings.cache_waarschuwing_gb} /></Field>
      <Field label="Cachepauze (GB)"><input name="cache_pauze_gb" type="number" min={storage?.modus === "systeemschijf" ? storage.systeemschijf_reserve_gb : 1} defaultValue={settings.cache_pauze_gb} /></Field>
      <Field label="Hardlinkbeleid"><select name="hardlink_modus" defaultValue={settings.hardlink_modus}><option value="overslaan">altijd overslaan</option><option value="verbreek_na_bevestiging">verbreek na bevestiging</option></select></Field>
      <label className="toggle-row"><input name="pauzeer_cpu_bij_kijken" type="checkbox" defaultChecked={settings.pauzeer_cpu_bij_kijken} /><span>CPU pauzeren bij Jellyfin-afspelen</span></label>
      <label className="toggle-row"><input name="meld_browser_fouten" type="checkbox" defaultChecked={settings.meld_browser_fouten} /><span>Browsermelding bij fouten</span></label>
      <label className="toggle-row"><input name="meld_browser_batches" type="checkbox" defaultChecked={settings.meld_browser_batches} /><span>Browsermelding bij afgeronde batch</span></label>
    </div><Button type="button" variant="secondary" onClick={requestNotifications}>Vraag browserrechten</Button></Card></form>

    <Card className="settings-card"><h2>Onderdelen</h2><p className="muted">Uitschakelen stopt alleen nieuw werk en verwijdert niets. Kernveiligheid staat niet in deze lijst.</p><div className="feature-list">{features.map((feature) => <div className="feature-row" key={feature.sleutel}><div><strong>{feature.sleutel.replaceAll("_", " ")}</strong><p>{feature.uitleg}</p>{feature.laatste_fout ? <small className="danger">{feature.laatste_fout}</small> : null}</div><Button variant={feature.ingeschakeld ? "secondary" : "default"} aria-pressed={feature.ingeschakeld} onClick={() => toggleFeature(feature)}>{feature.ingeschakeld ? "Aan" : "Uit"}</Button></div>)}</div></Card>

    <Card className="settings-card"><div className="worker-header"><div><h2>Hardwarediagnose</h2><span className="muted">QSV-test en GPU-meting veranderen of resetten geen kernel-driver.</span></div><div className="decision-actions"><Button variant="secondary" onClick={async () => { try { setMessage(JSON.stringify(await mutation("/systeem/qsv-test", "POST"))); } catch (error) { setMessage(String(error)); } }}>Test QSV-verbinding</Button><Button variant="secondary" onClick={async () => setMessage(JSON.stringify(await mutation("/systeem/gpu-herstart", "POST")))}>Herstart GPU-monitor</Button><Button variant="ghost" onClick={async () => setDiagnosis(await api<Diagnosis>("/systeem/diagnose"))}>Ververs diagnose</Button></div></div>{diagnosis ? <pre>{JSON.stringify(diagnosis, null, 2)}</pre> : null}</Card>

    <Card className="settings-card"><h2>Jellyfin, Radarr en Sonarr</h2><div className="integration-grid">{integrations.map((item) => <form key={item.naam} className="integration-card" onSubmit={(event) => saveIntegration(event, item)}><div className="worker-header"><strong>{item.naam}</strong><Badge tone={item.sleutel_ingesteld ? "green" : "neutral"}>{item.sleutel_ingesteld ? "sleutel ingesteld" : "geen sleutel"}</Badge></div><Field label="URL"><input name="url" type="url" defaultValue={item.url ?? ""} placeholder={`http://${item.naam}:7878`} /></Field><Field label="Nieuwe API-sleutel"><input name="api_sleutel" type="password" autoComplete="new-password" /></Field><div className="decision-actions"><Button type="submit">Versleuteld opslaan</Button><Button type="button" variant="secondary" onClick={() => testIntegration(item)}>Test opgeslagen verbinding</Button></div></form>)}</div></Card>

    <Card className="settings-card"><div className="worker-header"><div><h2>Back-ups</h2><span className="muted">Herstelcontrole opent een tijdelijke, alleen-lezen kopie.</span></div><Button onClick={createBackup}>Maak back-up</Button></div>{backups.map((backup) => <div className="backup-row" key={backup.id}><span><strong>{new Date(backup.gemaakt_op).toLocaleString("nl-NL")}</strong><small>{bytes(backup.grootte)} · {backup.sha256.slice(0, 12)}…</small></span><Badge tone={backup.gecontroleerd_op ? "green" : "neutral"}>{backup.gecontroleerd_op ? "herstelbaar getest" : "niet getest"}</Badge><Button variant="secondary" onClick={async () => { await mutation(`/backups/${backup.id}/controleer-herstel`, "POST"); await load(); }}>Controleer herstel</Button></div>)}</Card>
    <Card className="settings-card"><h2>Hersteltransacties</h2><p className="muted">Bij een conflict wordt nooit automatisch gegist. Inspecteer eerst; alleen het verborgen tijdelijke resultaat kan hier expliciet worden verwijderd.</p>{recovery.map((item) => <div className="backup-row" key={item.id}><span><strong>Job #{item.job_id} · {item.status}</strong><small>{item.bron_pad}<br />{item.toelichting}</small></span><Badge tone={item.status === "conflict" ? "red" : "amber"}>{item.status}</Badge>{item.status === "conflict" ? <Button variant="danger" onClick={async () => { if (window.confirm("Verwijder uitsluitend het tijdelijke resultaat? De bron blijft onaangeraakt.")) { await mutation(`/herstel/${item.id}/tijdelijk-resultaat`, "DELETE"); await load(); } }}>Tijdelijk resultaat verwijderen</Button> : null}</div>)}{!recovery.length ? <p className="empty-state">Geen open hersteltransacties.</p> : null}</Card>
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

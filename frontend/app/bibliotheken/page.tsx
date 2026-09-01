"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { bytes } from "@/lib/utils";
import type { Library, Profile } from "@/types";

interface CanaryReport {
  library_id: number;
  status: "actief" | "vrijgegeven";
  ingeschakeld: boolean;
  limieten: { max_bestanden: number; max_gb: number; max_jobs_per_dag: number };
  limiet_bereikt: boolean;
  klaar: number;
  wacht: number;
  actief: number;
  mislukt: number;
  verificaties_ok: number;
  verificaties_mislukt: number;
  bron_bytes: number;
  resultaat_bytes: number;
  bespaard_bytes: number;
  vandaag: number;
  kan_vrijgeven: boolean;
}

type ScanInterval = 0 | 86400 | 604800;

function scanLabel(interval: number, scanTime: string): string {
  if (interval === 0) return "Automatisch: uit (alleen handmatig)";
  if (interval === 604800) return `Automatisch: wekelijks om ${scanTime}`;
  return `Automatisch: dagelijks om ${scanTime}`;
}

function localDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("nl-NL") : "niet gepland";
}

export default function LibrariesPage() {
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [canaryReports, setCanaryReports] = useState<Record<number, CanaryReport>>({});
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [pathMode, setPathMode] = useState<"lokaal" | "smb">("smb");
  const [smbGuest, setSmbGuest] = useState(false);
  const [profileId, setProfileId] = useState(0);
  const [scanInterval, setScanInterval] = useState<ScanInterval>(86400);
  const [scanTime, setScanTime] = useState("03:00");
  const [status, setStatus] = useState("");
  const load = useCallback(async () => {
    const [libraryData, profileData] = await Promise.all([
      api<Library[]>("/libraries"),
      api<Profile[]>("/profielen"),
    ]);
    setLibraries(libraryData);
    setProfiles(profileData);
    const reports = await Promise.all(
      libraryData.map((library) => api<CanaryReport>(`/libraries/${library.id}/canary`)),
    );
    setCanaryReports(Object.fromEntries(reports.map((report) => [report.library_id, report])));
    setProfileId((current) => current || profileData[0]?.id || 0);
  }, []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function start(libraryId: number, dryRun: boolean) {
    const result = await mutation<{ taak_id: string }>(`/libraries/${libraryId}/${dryRun ? "droogloop" : "scan"}`, "POST");
    setStatus(`${dryRun ? "Droogloop" : "Scan"} gestart als achtergrondtaak ${result.taak_id.slice(0, 8)}.`);
  }

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const location = path.trim();
    if (pathMode === "smb" && !location.toLowerCase().startsWith("smb://")) {
      setStatus("Gebruik een volledig SMB-adres, bijvoorbeeld smb://server/share/map.");
      return;
    }
    if (pathMode === "lokaal" && !location.startsWith("/")) {
      setStatus("Een lokale map moet een absoluut pad hebben, bijvoorbeeld /mnt/media/Films.");
      return;
    }
    try {
      setStatus("Eerste-gebruikcontrole draait; er wordt alleen een klein tijdelijk testbestand gemaakt en verwijderd…");
      await mutation("/libraries", "POST", {
        naam: name,
        locatie: location,
        profiel_id: profileId,
        scan_interval: scanInterval,
        scan_tijd: scanTime,
        smb_gebruikersnaam: pathMode === "smb" && !smbGuest ? String(data.get("smb_gebruikersnaam") ?? "") || undefined : undefined,
        smb_wachtwoord: pathMode === "smb" && !smbGuest ? String(data.get("smb_wachtwoord") ?? "") || undefined : undefined,
        smb_gast: pathMode === "smb" && smbGuest,
      });
      setName(""); setPath(""); form.reset(); setSmbGuest(false); await load();
      setStatus("Broncontrole geslaagd en bibliotheek toegevoegd. Voer nu eerst een droogloop uit; er is nog niets verwerkt.");
    } catch (error) {
      setStatus(`Opslaan mislukt: ${String(error)}`);
    }
  }

  async function updateSchedule(
    libraryId: number,
    interval: ScanInterval,
    currentTime: string,
  ) {
    try {
      await mutation(`/libraries/${libraryId}`, "PATCH", {
        scan_interval: interval,
        scan_tijd: currentTime,
      });
      await load();
      setStatus("Scanschema opgeslagen; gemiste scanmomenten worden niet ingehaald.");
    } catch (error) {
      setStatus(`Scanschema opslaan mislukt: ${String(error)}`);
    }
  }

  async function releaseCanary(libraryId: number) {
    const confirmation = window.prompt("Typ exact: CANARY-BATCH VRIJGEVEN");
    if (confirmation === null) return;
    try {
      await mutation(`/libraries/${libraryId}/canary-vrijgeven`, "POST", {
        bevestiging: confirmation,
      });
      await load();
      setStatus("Canary-rapport goedgekeurd. Deze bibliotheek mag de normale wachtrij verder afwerken.");
    } catch (error) { setStatus(`Vrijgeven mislukt: ${String(error)}`); }
  }

  async function removeLibrary(library: Library) {
    const confirmation = window.prompt(
      `Je verwijdert “${library.naam}” alleen uit de AV1-pipeline. ` +
      "De echte map, mediabestanden en eventuele SMB-mount blijven staan. " +
      "De catalogus, besluiten, jobs, scans en AI-historie van deze bibliotheek worden opgeruimd.\n\n" +
      "Typ exact: BIBLIOTHEEK VERWIJDEREN",
    );
    if (confirmation === null) return;
    try {
      const result = await mutation<{
        naam: string;
        bron_ongemoeid: boolean;
        lokale_pipeline_mappen_verwijderd: number;
      }>(`/libraries/${library.id}`, "DELETE", { bevestiging: confirmation });
      await load();
      setStatus(
        `Bibliotheek “${result.naam}” is uit de pipeline verwijderd. ` +
        `De bronmap en media zijn ongemoeid; ${result.lokale_pipeline_mappen_verwijderd} ` +
        "lokale pipeline-map(pen) zijn opgeruimd.",
      );
    } catch (error) {
      setStatus(`Bibliotheek verwijderen mislukt: ${String(error)}`);
    }
  }

  return (
    <div className="page-stack">
      {status ? <Card className="notice" role="status">{status}</Card> : null}
      <form onSubmit={add}><Card className="library-add"><h2>Bibliotheek toevoegen</h2><p className="muted">Kies iedere toegankelijke lokale mediamap of plak een volledig SMB-adres. Bij eerste gebruik moet de bron een veilige schrijf-, hernoem- en metadatatest doorstaan.</p><div className="toolbar"><label className="field">Naam<input value={name} onChange={(event) => setName(event.target.value)} required /></label><label className="field">Brontype<select value={pathMode} onChange={(event) => { setPathMode(event.target.value as "lokaal" | "smb"); setPath(""); }}><option value="smb">SMB-/servermap</option><option value="lokaal">lokale systeemmap</option></select></label><label className="field library-path">{pathMode === "smb" ? "SMB-adres" : "Absoluut lokaal pad"}<input placeholder={pathMode === "smb" ? "smb://truenas.local/jelly/Media/test" : "/mnt/media/Films"} value={path} onChange={(event) => setPath(event.target.value)} required /></label><label className="field">Profiel<select value={profileId} onChange={(event) => setProfileId(Number(event.target.value))}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.naam}</option>)}</select></label><label className="field">Automatisch scannen<select value={scanInterval} onChange={(event) => setScanInterval(Number(event.target.value) as ScanInterval)}><option value={0}>Handmatig</option><option value={86400}>Dagelijks</option><option value={604800}>Wekelijks</option></select></label><label className="field">Lokale scantijd<input type="time" value={scanTime} disabled={scanInterval === 0} onChange={(event) => setScanTime(event.target.value)} /></label></div>{pathMode === "smb" ? <div className="settings-grid"><Field label="SMB-gebruikersnaam"><input name="smb_gebruikersnaam" autoComplete="username" disabled={smbGuest} /></Field><Field label="SMB-wachtwoord"><input name="smb_wachtwoord" type="password" autoComplete="new-password" disabled={smbGuest} /></Field><label className="toggle-row"><input type="checkbox" checked={smbGuest} onChange={(event) => setSmbGuest(event.target.checked)} /><span>Deze share gebruikt gasttoegang</span></label></div> : null}<div className="decision-actions"><Button type="submit" disabled={!name || !path || !profileId}>Test bron en voeg toe</Button></div><p className="muted">Accountgegevens worden niet in de URL, browserstate of logs bewaard. Beschermde systeemmappen en pipelineopslag worden altijd geweigerd.</p></Card></form>
      <section className="library-grid" aria-label="Bibliotheken">
        {libraries.map((library) => {
          const processed = Number(library.verwerkt || 0);
          const total = Number(library.aantal_bestanden || 0);
          const percent = total ? Math.min(100, (processed / total) * 100) : 0;
          const canary = canaryReports[library.id];
          const saved = Number(library.bespaard_bytes ?? canary?.bespaard_bytes ?? 0);
          const processedSourceBytes = Number(
            library.verwerkte_bron_bytes ?? canary?.bron_bytes ?? 0,
          );
          const savedPercent = processedSourceBytes
            ? Math.max(0, Math.min(100, (saved / processedSourceBytes) * 100))
            : 0;
          return <Card className="library-card" key={library.id}><div className="worker-header"><div><h2>{library.naam}</h2><p className="mono muted">{library.bron_uri || library.pad}</p><p className="mono muted">Servicepad: {library.pad}</p></div><span className="badge badge-blue">{library.profiel_naam}</span></div><div className="notice"><strong>{scanLabel(library.scan_interval, library.scan_tijd)}</strong><span>Laatste: {localDate(library.laatste_scan)} · volgende: {localDate(library.volgende_scan)}</span><p className="muted">Status: {library.laatste_scan_status} · {library.laatste_scan_nieuw} nieuw · {library.laatste_scan_gewijzigd} gewijzigd · {library.laatste_scan_verdwenen} verdwenen · {library.stabiliteitswachtrij} wacht op stabiliteit.</p>{library.laatste_scan_fout ? <p className="danger">Laatste scanfout: {library.laatste_scan_fout}</p> : null}</div><div className="library-numbers"><span><strong>{total.toLocaleString("nl-NL")}</strong> aanwezig</span><span><strong>{bytes(library.totale_grootte)}</strong> totaal</span><span className="library-saving"><strong>{bytes(saved)}</strong> bespaard · {savedPercent.toFixed(1)}% van verwerkte brondata</span><span><strong>{library.ai_goedgekeurd}</strong> AI-goedgekeurd</span><span><strong>{library.handmatig_wachtend}</strong> handmatig wachtend</span><span><strong>{library.ai_geblokkeerd}</strong> geblokkeerd</span></div>{library.bron_controle ? <p className="muted">Eerste broncontrole: geslaagd · atomisch hernoemen getest · ACL {library.bron_controle.extended_acl ? "uitgebreid" : "vast"} · xattrs {library.bron_controle.xattrs ? "beschikbaar" : "beperkt"}</p> : null}{canary ? <div className="notice"><strong>Canary-batch: {canary.status === "vrijgegeven" ? "vrijgegeven" : canary.limiet_bereikt ? "limiet bereikt" : "actief"}</strong><span>{canary.klaar} klaar · {bytes(canary.bron_bytes)} brondata · {canary.verificaties_ok} verificaties goed · {canary.mislukt + canary.verificaties_mislukt} fouten · {canary.wacht} wachtend</span><p className="muted">Limieten: {canary.limieten.max_bestanden} bestanden, {canary.limieten.max_gb} GB of {canary.limieten.max_jobs_per_dag} jobs per dag.{!canary.ingeschakeld ? " De globale canary-limiet staat momenteel uit." : ""}</p>{canary.status === "actief" ? <Button variant="secondary" disabled={!canary.kan_vrijgeven} onClick={() => releaseCanary(library.id)}>Rapport goedkeuren en vrijgeven</Button> : null}</div> : null}<div><div className="progress-labels"><span>{processed.toLocaleString("nl-NL")} van {total.toLocaleString("nl-NL")} verwerkt</span><span>{percent.toFixed(1)}%</span></div><div className="progress"><span style={{ width: `${percent}%` }} /></div></div><div className="toolbar"><Button onClick={() => start(library.id, false)}>Nu scannen</Button><Button variant="secondary" onClick={() => start(library.id, true)}>Droogloop</Button><label className="field">Scanschema<select value={library.scan_interval} onChange={(event) => updateSchedule(library.id, Number(event.target.value) as ScanInterval, library.scan_tijd)}><option value={0}>Handmatig</option><option value={86400}>Dagelijks</option><option value={604800}>Wekelijks</option></select></label><label className="field">Scantijd<input type="time" value={library.scan_tijd} disabled={library.scan_interval === 0} onChange={(event) => updateSchedule(library.id, library.scan_interval as ScanInterval, event.target.value)} /></label><label className="field">Profiel<select value={library.profiel_id} onChange={async (event) => { await mutation(`/libraries/${library.id}`, "PATCH", { profiel_id: Number(event.target.value) }); await load(); setStatus("Profiel gewijzigd; canary-vrijgave is opnieuw vereist."); }}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.naam}</option>)}</select></label><Button variant="danger" onClick={() => removeLibrary(library)}>Uit pipeline verwijderen</Button></div></Card>;
        })}
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

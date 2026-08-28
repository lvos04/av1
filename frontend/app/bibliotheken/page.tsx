"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { bytes } from "@/lib/utils";
import type { Library, Profile } from "@/types";

export default function LibrariesPage() {
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [profileId, setProfileId] = useState(0);
  const [status, setStatus] = useState("");
  const load = useCallback(async () => {
    const [libraryData, profileData] = await Promise.all([
      api<Library[]>("/libraries"),
      api<Profile[]>("/profielen"),
    ]);
    setLibraries(libraryData);
    setProfiles(profileData);
    setProfileId((current) => current || profileData[0]?.id || 0);
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function start(libraryId: number, dryRun: boolean) {
    const result = await mutation<{ taak_id: string }>(`/libraries/${libraryId}/${dryRun ? "droogloop" : "scan"}`, "POST");
    setStatus(`${dryRun ? "Droogloop" : "Scan"} gestart als achtergrondtaak ${result.taak_id.slice(0, 8)}.`);
  }

  async function add() {
    await mutation("/libraries", "POST", { naam: name, pad: path, profiel_id: profileId, scan_interval: 86400 });
    setName(""); setPath(""); await load();
  }

  return (
    <div className="page-stack">
      {status ? <Card className="notice" role="status">{status}</Card> : null}
      <Card className="library-add"><h2>Bibliotheek toevoegen</h2><div className="toolbar"><label className="field">Naam<input value={name} onChange={(event) => setName(event.target.value)} /></label><label className="field library-path">Absoluut pad binnen /library<input placeholder="/library/Films" value={path} onChange={(event) => setPath(event.target.value)} /></label><label className="field">Profiel<select value={profileId} onChange={(event) => setProfileId(Number(event.target.value))}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.naam}</option>)}</select></label><Button disabled={!name || !path || !profileId} onClick={add}>Toevoegen</Button></div></Card>
      <section className="library-grid" aria-label="Bibliotheken">
        {libraries.map((library) => {
          const processed = Number(library.verwerkt || 0);
          const total = Number(library.aantal_bestanden || 0);
          const percent = total ? Math.min(100, (processed / total) * 100) : 0;
          return <Card className="library-card" key={library.id}><div className="worker-header"><div><h2>{library.naam}</h2><p className="mono muted">{library.pad}</p></div><span className="badge badge-blue">{library.profiel_naam}</span></div><div className="library-numbers"><span><strong>{total.toLocaleString("nl-NL")}</strong> bestanden</span><span><strong>{bytes(library.totale_grootte)}</strong> totaal</span><span><strong>{library.laatste_scan ? new Date(library.laatste_scan).toLocaleString("nl-NL") : "nooit"}</strong> laatste scan</span></div><div><div className="progress-labels"><span>{processed.toLocaleString("nl-NL")} van {total.toLocaleString("nl-NL")} verwerkt</span><span>{percent.toFixed(1)}%</span></div><div className="progress"><span style={{ width: `${percent}%` }} /></div></div><div className="toolbar"><Button onClick={() => start(library.id, false)}>Nu scannen</Button><Button variant="secondary" onClick={() => start(library.id, true)}>Droogloop</Button><label className="field">Profiel<select value={library.profiel_id} onChange={async (event) => { await mutation(`/libraries/${library.id}`, "PATCH", { profiel_id: Number(event.target.value) }); await load(); }}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.naam}</option>)}</select></label></div></Card>;
        })}
      </section>
    </div>
  );
}

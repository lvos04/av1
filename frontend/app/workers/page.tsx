"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { bytes, fileName } from "@/lib/utils";
import type { Lane, WorkerConfig } from "@/types";

export default function WorkersPage() {
  const [workers, setWorkers] = useState<WorkerConfig[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(() => api<WorkerConfig[]>("/workers").then(setWorkers), []);
  useEffect(() => { void load(); }, [load]);

  async function update(lane: Lane, body: Record<string, unknown>) {
    try {
      await mutation(`/workers/${lane}`, "PATCH", body);
      await load();
      setMessage("Workerinstelling veilig opgeslagen.");
    } catch (error) { setMessage(String(error)); }
  }

  async function pause(lane: Lane) {
    try {
      await mutation(`/workers/${lane}/pauzeer`, "POST");
      await load();
    } catch (error) { setMessage(String(error)); }
  }

  return (
    <div className="workers-settings">
      {message ? <Card className="notice" role="status">{message}</Card> : null}
      {workers.map((worker) => (
        <Card className="worker-settings" key={worker.baan}>
          <div className="worker-header"><h2>{worker.baan === "gpu" ? "GPU · Intel Arc" : "CPU · SVT-AV1"}</h2><Badge tone={worker.gepauzeerd ? "amber" : "green"}>{worker.gepauzeerd ? "gepauzeerd" : "beschikbaar"}</Badge></div>
          {worker.pauze_reden ? <p className="muted">{worker.pauze_reden}</p> : null}
          <div className="settings-grid">
            <label className="field">Aantal workers<input type="number" min={0} value={worker.aantal} onChange={(event) => update(worker.baan, { aantal: Number(event.target.value) })} /></label>
            <label className="field">Nice-waarde<input type="number" min={-20} max={19} value={worker.nice} onChange={(event) => update(worker.baan, { nice: Number(event.target.value) })} /></label>
            <label className="field">Stilte vanaf<input type="time" value={worker.stilte_start ?? ""} onChange={(event) => update(worker.baan, { stilte_start: event.target.value })} /></label>
            <label className="field">Stilte tot<input type="time" value={worker.stilte_einde ?? ""} onChange={(event) => update(worker.baan, { stilte_einde: event.target.value })} /></label>
          </div>
          <div className="notice"><strong>Totale encodegrens: {worker.totaal_max_actieve_jobs}</strong><span>{worker.veiligheidsuitleg}</span><p className="muted">{bytes(worker.geheugen_beschikbaar_bytes)} beschikbaar van {bytes(worker.geheugen_totaal_bytes)} RAM. Wijzig de totale grens onder Instellingen → Algemeen en grenzen.</p></div>
          <p><Button variant="secondary" onClick={() => pause(worker.baan)}>{worker.gepauzeerd ? "Baan hervatten" : "Baan pauzeren"}</Button></p>
          <h3>Nu actief</h3>
          <ul className="history-list">
            {worker.actieve_jobs.length ? worker.actieve_jobs.map((job) => <li key={job.job_id}><strong>{fileName(job.bestand)}</strong><Badge tone="blue">{job.percent?.toFixed(1) ?? "0.0"}%</Badge><span className="mono">{job.speed ? `${job.speed.toFixed(2)}×` : "start…"}</span></li>) : <li><span>Geen job actief op deze baan.</span></li>}
          </ul>
          <h3>Laatste 20 jobs</h3>
          <ul className="history-list">
            {worker.historie.length ? worker.historie.map((job) => {
              const saving = Math.max(0, job.bron_grootte - (job.resultaat_grootte ?? job.bron_grootte));
              return <li key={job.id}><strong>{fileName(job.pad)}</strong><Badge tone={job.status === "klaar" ? "green" : "red"}>{job.status}</Badge><span className="mono">−{bytes(saving)}</span></li>;
            }) : <li><span>Er is nog geen historie voor deze baan.</span></li>}
          </ul>
        </Card>
      ))}
    </div>
  );
}

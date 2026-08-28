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
  const load = useCallback(() => api<WorkerConfig[]>("/workers").then(setWorkers), []);
  useEffect(() => { void load(); }, [load]);

  async function update(lane: Lane, body: Record<string, unknown>) {
    await mutation(`/workers/${lane}`, "PATCH", body);
    await load();
  }

  async function pause(lane: Lane) {
    await mutation(`/workers/${lane}/pauzeer`, "POST");
    await load();
  }

  return (
    <div className="workers-settings">
      {workers.map((worker) => (
        <Card className="worker-settings" key={worker.baan}>
          <div className="worker-header"><h2>{worker.baan === "gpu" ? "GPU · Intel Arc" : "CPU · SVT-AV1"}</h2><Badge tone={worker.gepauzeerd ? "amber" : "green"}>{worker.gepauzeerd ? "gepauzeerd" : "beschikbaar"}</Badge></div>
          {worker.pauze_reden ? <p className="muted">{worker.pauze_reden}</p> : null}
          <div className="settings-grid">
            <label className="field">Aantal workers<input type="number" min={0} max={4} value={worker.aantal} onChange={(event) => update(worker.baan, { aantal: Number(event.target.value) })} /></label>
            <label className="field">Nice-waarde<input type="number" min={-20} max={19} value={worker.nice} onChange={(event) => update(worker.baan, { nice: Number(event.target.value) })} /></label>
            <label className="field">Stilte vanaf<input type="time" value={worker.stilte_start ?? ""} onChange={(event) => update(worker.baan, { stilte_start: event.target.value })} /></label>
            <label className="field">Stilte tot<input type="time" value={worker.stilte_einde ?? ""} onChange={(event) => update(worker.baan, { stilte_einde: event.target.value })} /></label>
          </div>
          <p><Button variant="secondary" onClick={() => pause(worker.baan)}>{worker.gepauzeerd ? "Baan hervatten" : "Baan pauzeren"}</Button></p>
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

"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { bytes, duration, fileName } from "@/lib/utils";
import { mutation } from "@/lib/api";
import type { Lane, LiveWorker } from "@/types";

function idle(lane: Lane): LiveWorker {
  return { baan: lane, job_id: null, gepauzeerd: false };
}

export function WorkerCard({ lane, worker }: { lane: Lane; worker?: LiveWorker }) {
  const value = worker ?? idle(lane);
  const [busy, setBusy] = useState(false);
  const active = value.job_id !== null;
  const source = value.grootte_bron ?? 0;
  const current = value.grootte_nu ?? 0;
  const saving = source > 0 ? Math.max(0, source - current) : 0;
  const savingPercent = source > 0 ? (saving / source) * 100 : 0;

  async function pause() {
    setBusy(true);
    try {
      await mutation(`/workers/${lane}/pauzeer`, "POST");
    } finally {
      setBusy(false);
    }
  }

  async function abort() {
    if (!value.job_id) return;
    setBusy(true);
    try {
      await mutation(`/jobs/${value.job_id}/afbreken`, "POST");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className={`worker-card ${lane}`} aria-label={`${lane.toUpperCase()}-baan`}>
      <div className="worker-header">
        <span className={`worker-title ${lane}`}>⌁ <span>{lane === "gpu" ? "GPU · Intel Arc · av1_qsv" : "CPU · SVT-AV1"}</span></span>
        <Badge tone={value.gepauzeerd ? "amber" : active ? lane === "gpu" ? "blue" : "purple" : "neutral"}>
          {value.gepauzeerd ? "gepauzeerd" : active ? "actief" : "vrij"}
        </Badge>
      </div>
      <div className="worker-file">
        <strong title={value.bestand}>{fileName(value.bestand)}</strong>
        <small>{active ? `Job #${value.job_id} · ${value.encoder ?? (lane === "gpu" ? "av1_qsv" : "libsvtav1")}` : value.pauze_reden ?? "Geen actieve job op deze baan."}</small>
      </div>
      <div>
        <div className={`progress-labels ${value.verouderd ? "stale" : ""}`}><span>{active ? `${(value.percent ?? 0).toFixed(1)}% voltooid` : "Wacht op werk"}</span><span>{active ? `${value.fps?.toFixed(1) ?? "—"} FPS · ${value.speed?.toFixed(1) ?? "—"}× · ETA ${duration(value.eta_seconden)}` : "—"}</span></div>
        <div className="progress" aria-label={`${value.percent ?? 0}% voltooid`}><span style={{ width: `${Math.max(0, Math.min(100, value.percent ?? 0))}%` }} /></div>
      </div>
      <div className="worker-details">
        <div className="detail-row"><span>BRON</span><strong>{bytes(value.grootte_bron)}</strong></div>
        <div className="detail-row"><span>RESULTAAT NU</span><strong className={value.verouderd ? "stale" : ""}>{bytes(value.grootte_nu)}</strong></div>
        <div className="detail-row"><span>GESCHATTE BESPARING</span><strong className="success">{active && source > 0 ? `${bytes(saving)} · ${savingPercent.toFixed(0)}%` : "—"}</strong></div>
        <div className="detail-row"><span>VERIFICATIE</span><strong>{active ? "na encode" : "—"}</strong></div>
      </div>
      <div className="worker-stats">
        <div><small>BITRATE OUT</small><strong className={value.verouderd ? "stale" : ""}>{value.bitrate_kbps ? `${(value.bitrate_kbps / 1000).toFixed(1)} Mbps` : "—"}</strong></div>
        <div><small>{lane === "gpu" ? "GPU-BAAN" : "CPU-BAAN"}</small><strong>{active ? "bezig" : "vrij"}</strong></div>
        <div><small>STATUS</small><strong>{value.gepauzeerd ? "pauze" : "gereed"}</strong></div>
      </div>
      <div className="worker-actions">
        <Button variant="secondary" disabled={busy} onClick={pause}>{value.gepauzeerd ? "Hervatten" : "Pauzeren"}</Button>
        <Button variant="danger" disabled={!active || busy} onClick={abort}>Afbreken</Button>
        <Link className="button button-secondary" aria-disabled={!active} href={active ? `/logs?job=${value.job_id}` : "/logs"}>Logs</Link>
      </div>
    </Card>
  );
}

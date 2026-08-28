"use client";

import Image from "next/image";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { bytes, fileName } from "@/lib/utils";
import type {
  DecisionItem,
  Profile,
  QualityTrial,
  Verification,
} from "@/types";

interface QualityStatus {
  vmaf_beschikbaar: boolean;
  vmaf_ingeschakeld: boolean;
  uitleg: string;
}

interface DecisionResponse {
  items: DecisionItem[];
}

export default function QualityPage() {
  const [status, setStatus] = useState<QualityStatus | null>(null);
  const [rows, setRows] = useState<Verification[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [files, setFiles] = useState<DecisionItem[]>([]);
  const [profileId, setProfileId] = useState(0);
  const [fileId, setFileId] = useState(0);
  const [trials, setTrials] = useState<QualityTrial[]>([]);
  const [message, setMessage] = useState("");
  const [slider, setSlider] = useState(50);

  const loadTrials = useCallback(async (selectedProfile: number) => {
    if (selectedProfile) {
      setTrials(await api<QualityTrial[]>(`/profielen/${selectedProfile}/proefdraaien`));
    }
  }, []);

  useEffect(() => {
    Promise.all([
      api<QualityStatus>("/kwaliteit/status"),
      api<Verification[]>("/kwaliteit/verificaties"),
      api<Profile[]>("/profielen"),
      api<DecisionResponse>("/besluiten/items?per_pagina=500"),
    ])
      .then(([quality, verifications, loadedProfiles, decisions]) => {
        setStatus(quality);
        setRows(verifications);
        setProfiles(loadedProfiles);
        setFiles(decisions.items);
        const firstProfile = loadedProfiles[0]?.id ?? 0;
        setProfileId(firstProfile);
        setFileId(decisions.items[0]?.bestand_id ?? 0);
        return loadTrials(firstProfile);
      })
      .catch((error: unknown) => setMessage(String(error)));
  }, [loadTrials]);

  async function startTrial() {
    setMessage("Proefdraai wordt gestart…");
    try {
      await mutation(`/profielen/${profileId}/proefdraai`, "POST", {
        bestand_id: fileId,
        crf_waarden: [27, 29, 31],
      });
      setMessage("De drie tijdelijke encodes draaien. De bron wordt niet gewijzigd.");
      window.setTimeout(() => loadTrials(profileId).catch(() => undefined), 4000);
    } catch (error) {
      setMessage(String(error));
    }
  }

  return (
    <div className="page-stack">
      <Card className="notice">
        <strong>VMAF en proefdraai</strong>
        {status?.uitleg ?? "Beschikbaarheid wordt gecontroleerd…"} {" "}
        {!status?.vmaf_beschikbaar
          ? "Er wordt geen score verzonnen; HDR-, audio- en attachmentverificatie blijft actief."
          : "VMAF meet een steekproef van bron en resultaat."}
      </Card>

      <Card className="quality-card">
        <div className="worker-header">
          <div>
            <h2>Profiel proefrijden</h2>
            <span className="muted">Tijdelijke SVT-AV1-encodes op CRF 27, 29 en 31.</span>
          </div>
          <Button disabled={!profileId || !fileId} onClick={startTrial}>Start proefdraai</Button>
        </div>
        <div className="profile-grid">
          <label className="field">Profiel
            <select value={profileId} onChange={(event) => {
              const selected = Number(event.target.value);
              setProfileId(selected);
              loadTrials(selected).catch(() => undefined);
            }}>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.naam}</option>)}
            </select>
          </label>
          <label className="field">Catalogusbestand
            <select value={fileId} onChange={(event) => setFileId(Number(event.target.value))}>
              {files.map((file) => <option key={file.id} value={file.bestand_id}>{file.pad}</option>)}
            </select>
          </label>
        </div>
        {message ? <p role="status" className="muted">{message}</p> : null}
        {trials.length ? <table className="data-table"><thead><tr><th>CRF</th><th>Grootte</th><th>Encode</th><th>VMAF</th><th>Status</th></tr></thead><tbody>{trials.map((trial) => <tr key={`${trial.taak_id}-${trial.crf}`}><td className="mono">{trial.crf}</td><td>{bytes(trial.grootte)}</td><td>{trial.duur_encode_seconden ? `${trial.duur_encode_seconden} s` : "—"}</td><td>{trial.vmaf_score?.toFixed(1) ?? "niet beschikbaar"}</td><td><Badge tone={trial.status === "klaar" ? "green" : "red"}>{trial.status}</Badge></td></tr>)}</tbody></table> : null}
      </Card>

      {rows.map((row) => <Card className="quality-card" key={row.job_id}>
        <div className="worker-header"><div><h2>{fileName(row.pad)}</h2><span className="muted mono">Job #{row.job_id}</span></div><Badge tone={row.geslaagd ? "green" : "red"}>{row.geslaagd ? "geverifieerd" : "afgekeurd"}</Badge></div>
        <div className="verification-grid">
          <Check ok={row.hdr_bron === row.hdr_resultaat} text={row.hdr_bron ? "HDR10-status behouden" : "SDR-status behouden"} />
          <Check ok={Boolean(row.md_gelijk)} text="HDR10 mastering display identiek" />
          <Check ok={row.dv_bron === row.dv_resultaat || row.dv_resultaat === 10} text={`Dolby Vision bron ${row.dv_bron || "geen"} · resultaat ${row.dv_resultaat || "geen"}`} />
          <Check ok={row.audio_resultaat > 0 && row.audio_resultaat <= row.audio_bron} text={`${row.audio_resultaat} audiosporen behouden (was ${row.audio_bron})`} />
          <Check ok={row.attachments_resultaat >= row.attachments_bron} text={`${row.attachments_resultaat} attachments behouden`} />
        </div>
        {row.bron_frame && row.resultaat_frame ? <div className="comparison">
          <div className="comparison-stage">
            <Image unoptimized width={900} height={506} src={`/api/kwaliteit/jobs/${row.job_id}/frame/bron`} alt="Bronframe" />
            <div className="comparison-result" style={{ width: `${slider}%` }}><Image unoptimized width={900} height={506} src={`/api/kwaliteit/jobs/${row.job_id}/frame/resultaat`} alt="Resultaatframe" /></div>
            <span className="comparison-line" style={{ left: `${slider}%` }} />
          </div>
          <label className="field">Vergelijk bron en resultaat<input aria-label="Vergelijkingsschuif" type="range" min={0} max={100} value={slider} onChange={(event) => setSlider(Number(event.target.value))} /></label>
        </div> : <p className="muted">Voor/na-frames zijn niet vastgelegd voor deze oudere job.</p>}
        <p>{row.vmaf_score !== null ? <Badge tone={row.vmaf_score >= 95 ? "green" : row.vmaf_score >= 90 ? "amber" : "red"}>VMAF {row.vmaf_score.toFixed(1)}</Badge> : <Badge>VMAF niet gemeten</Badge>} <span className="muted">{row.toelichting}</span></p>
      </Card>)}
      {!rows.length ? <Card className="empty-state">Nog geen voltooide verificaties. Resultaten verschijnen hier na een veilige encode.</Card> : null}
    </div>
  );
}

function Check({ ok, text }: { ok: boolean; text: string }) {
  return <div className={ok ? "quality-ok" : "quality-fail"}><strong>{ok ? "✓" : "✕"}</strong><span>{text}</span></div>;
}

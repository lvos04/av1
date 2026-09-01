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
  DolbyVisionOverview,
  Job,
  JobsResponse,
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
  totaal: number;
}

function buildDolbyVisionOverview(
  decisions: DecisionResponse,
  jobs: Job[],
  verifications: Verification[],
): DolbyVisionOverview {
  const detected = new Map<number, number>();
  const latestDecision = new Map<number, DecisionItem>();
  for (const decision of decisions.items) {
    latestDecision.set(decision.bestand_id, decision);
    const match = /DV profiel (\d+)/.exec(decision.reden);
    if (match) detected.set(decision.bestand_id, Number(match[1]));
  }
  const relevantJob = new Map<number, Job>();
  const priority = (job: Job) => ["wacht", "bezig", "verifieren"].includes(job.status)
    ? 0
    : job.status === "klaar" ? 1 : 2;
  for (const job of jobs) {
    const current = relevantJob.get(job.bestand_id);
    if (!current || priority(job) < priority(current) || (priority(job) === priority(current) && job.id > current.id)) {
      relevantJob.set(job.bestand_id, job);
    }
  }
  const verificationByJob = new Map(verifications.map((row) => [row.job_id, row]));
  const profiles = { "5": 0, "7": 0, "8": 0, overig: 0 };
  const items = [...detected.entries()].map(([fileId, profile]) => {
    const decision = latestDecision.get(fileId);
    const job = relevantJob.get(fileId);
    const verification = job ? verificationByJob.get(job.id) : undefined;
    const profileKey = String(profile) as "5" | "7" | "8";
    if (profileKey in profiles) profiles[profileKey] += 1;
    else profiles.overig += 1;
    const controleStatus = verification
      ? verification.geslaagd ? "geverifieerd" as const : "afgekeurd" as const
      : job && ["wacht", "bezig", "verifieren"].includes(job.status)
        ? "wacht_op_resultaat" as const
        : decision?.actie === "overslaan"
          ? "veilig_overgeslagen" as const
          : "niet_ingepland" as const;
    return {
      bestand_id: fileId,
      pad: decision?.pad ?? job?.pad ?? "Onbekend bestand",
      dv_profile: profile,
      dv_compat: 0,
      is_hdr10: profile === 5 ? 0 : 1,
      besluit_id: decision?.id ?? null,
      besluit_actie: decision?.actie ?? null,
      besluit_status: decision?.status ?? null,
      reden: decision?.reden ?? null,
      job_id: job?.id ?? null,
      job_status: job?.status ?? null,
      dv_bron: verification?.dv_bron ?? null,
      dv_resultaat: verification?.dv_resultaat ?? null,
      verificatie_geslaagd: verification?.geslaagd ?? null,
      verificatie_toelichting: verification?.toelichting ?? null,
      controle_status: controleStatus,
    };
  }).sort((left, right) => left.pad.localeCompare(right.pad, "nl"));
  return {
    totaal: items.length,
    profielen: profiles,
    items,
    afgekapt: decisions.totaal > decisions.items.length,
    uitleg: "Bronprofiel komt uit de catalogusscan; resultaatprofiel verschijnt pas na een volledige encode en verplichte verificatie.",
  };
}

export default function QualityPage() {
  const [status, setStatus] = useState<QualityStatus | null>(null);
  const [dolbyVision, setDolbyVision] = useState<DolbyVisionOverview | null>(null);
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
      api<JobsResponse>("/jobs?per_pagina=250"),
    ])
      .then(([quality, verifications, loadedProfiles, decisions, loadedJobs]) => {
        setStatus(quality);
        setDolbyVision(buildDolbyVisionOverview(decisions, loadedJobs.items, verifications));
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
            <h2>Dolby Vision-controle</h2>
            <span className="muted">Bronherkenning, geplande behandeling en resultaatcontrole staan hier los van elkaar.</span>
          </div>
          <Badge tone={dolbyVision?.totaal ? "blue" : "neutral"}>
            {dolbyVision ? `${dolbyVision.totaal} DV-bronnen herkend` : "controleren…"}
          </Badge>
        </div>
        {dolbyVision ? <>
          <p>{dolbyVision.uitleg}</p>
          <div className="library-numbers">
            <span><strong>{dolbyVision.profielen["5"]}</strong> profiel 5 · altijd overslaan</span>
            <span><strong>{dolbyVision.profielen["7"]}</strong> profiel 7 · dovi_tool vereist</span>
            <span><strong>{dolbyVision.profielen["8"]}</strong> profiel 8 · resultaat wordt profiel 10</span>
          </div>
          {dolbyVision.items.length ? <table className="data-table"><thead><tr><th>Bestand</th><th>Bron</th><th>Behandeling</th><th>DV-resultaatcontrole</th></tr></thead><tbody>{dolbyVision.items.map((item) => {
            const tone = item.controle_status === "geverifieerd" ? "green" : item.controle_status === "afgekeurd" ? "red" : item.controle_status === "wacht_op_resultaat" ? "blue" : "neutral";
            const resultText = item.controle_status === "geverifieerd"
              ? `Profiel ${item.dv_bron} → ${item.dv_resultaat} geverifieerd`
              : item.controle_status === "afgekeurd"
                ? item.verificatie_toelichting ?? "DV-verificatie afgekeurd"
                : item.controle_status === "wacht_op_resultaat"
                  ? "Nog geen resultaat; controle volgt na de encode"
                  : item.controle_status === "veilig_overgeslagen"
                    ? "Geen resultaat nodig; bron blijft onaangeraakt"
                    : "Nog niet ingepland";
            const sourceText = item.dv_profile === 5
              ? "Profiel 5 · geen HDR10-terugval"
              : item.dv_profile === 7
                ? "Profiel 7 · dual layer"
                : item.dv_compat
                  ? `Profiel ${item.dv_profile} · HDR-compat ${item.dv_compat}`
                  : `Profiel ${item.dv_profile}`;
            return <tr key={item.bestand_id}><td><strong>{fileName(item.pad)}</strong><br/><span className="muted mono">{item.pad}</span></td><td>{sourceText}</td><td>{item.job_id ? `Job #${item.job_id} · ${item.job_status}` : item.reden ?? "Nog geen besluit"}</td><td><Badge tone={tone}>{resultText}</Badge></td></tr>;
          })}</tbody></table> : <p className="empty-state">Geen Dolby Vision-bronnen in de catalogus gevonden.</p>}
          {dolbyVision.afgekapt ? <p className="muted">De eerste 500 DV-bronnen worden getoond.</p> : null}
        </> : <p className="muted">Dolby Vision-catalogus wordt geladen…</p>}
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
          <Check ok={row.dv_bron === row.dv_resultaat || row.dv_resultaat === 10} text={row.dv_bron ? `Dolby Vision bron ${row.dv_bron} · resultaat ${row.dv_resultaat || "ontbreekt"}` : "Deze bron bevat geen Dolby Vision"} />
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

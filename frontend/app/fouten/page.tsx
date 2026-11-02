"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { fileName } from "@/lib/utils";
import type { ErrorGroup, Job, JobsResponse } from "@/types";

interface ErrorResponse { groepen: ErrorGroup[] }
interface LogResponse { regels: string[] }

export default function ErrorsPage() {
  const [groups, setGroups] = useState<ErrorGroup[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<number, string[]>>({});
  const load = useCallback(async () => {
    const [errors, failed] = await Promise.all([api<ErrorResponse>("/fouten"), api<JobsResponse>("/jobs?status=mislukt&per_pagina=250")]);
    setGroups(errors.groepen); setJobs(failed.items);
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function open(group: ErrorGroup) {
    const key = `${group.foutsoort}:${group.fout}`;
    if (expanded === key) { setExpanded(null); return; }
    setExpanded(key);
    const affected = jobs.filter((job) => (job.foutsoort ?? "onbekend") === group.foutsoort && job.fout === group.fout);
    const values = await Promise.all(affected.map(async (job) => [job.id, (await api<LogResponse>(`/jobs/${job.id}/log?vanaf=0`)).regels.slice(-50)] as const));
    setLogs((old) => ({ ...old, ...Object.fromEntries(values) }));
  }

  async function retry(jobId: number) { await mutation(`/jobs/${jobId}/opnieuw`, "POST"); await load(); }

  return <div className="page-stack"><Card className="notice"><strong>Fouten houden de bron intact</strong>Er bestaat geen “negeer en forceer” voor HDR, HDR10+ of verificatie. Opnieuw proberen start alleen na een nieuwe veilige controle.</Card><section className="error-list">{groups.map((group) => { const key = `${group.foutsoort}:${group.fout}`; const affected = jobs.filter((job) => (job.foutsoort ?? "onbekend") === group.foutsoort && job.fout === group.fout); return <Card className="error-group" key={key}><button className="error-summary" onClick={() => open(group)} aria-expanded={expanded === key}><span>{expanded === key ? "▾" : "▸"}</span><Badge tone="red">{group.aantal}×</Badge><strong>{group.foutsoort}</strong><span>{group.fout}</span><small>{new Date(group.laatst).toLocaleString("nl-NL")}</small></button>{expanded === key ? <div className="error-items">{affected.map((job) => <article key={job.id}><div className="worker-header"><strong>{fileName(job.pad)}</strong><Button variant="secondary" onClick={() => retry(job.id)}>Opnieuw proberen</Button></div><p><strong>Impact:</strong> de encode/verificatie is gestopt; het origineel is niet vervangen.</p>{job.kwaliteitsadvies ? <p><strong>Advies:</strong> {job.kwaliteitsadvies}</p> : null}<p><strong>Veilige vervolgstap:</strong> controleer oorzaak en laatste logregels, herstel die oorzaak en probeer dan opnieuw.</p><pre>{job.commando ?? "Geen commando vastgelegd"}</pre><pre>{(logs[job.id] ?? []).join("\n") || "Geen logregels beschikbaar"}</pre></article>)}</div> : null}</Card>; })}{!groups.length ? <Card className="empty-state">Geen mislukte jobs.</Card> : null}</section></div>;
}

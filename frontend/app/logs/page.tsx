"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { fileName } from "@/lib/utils";
import { useLive } from "@/lib/ws";
import type { EventMessage, Job, JobsResponse } from "@/types";

interface LogResponse { regels: string[]; volgende: number }

export default function LogsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, setJobId] = useState<number | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const { events, volg, stopVolgen } = useLive();

  const loadLog = useCallback(async (id: number) => {
    const result = await api<LogResponse>(`/jobs/${id}/log?vanaf=0`);
    setLines(result.regels.slice(-2000));
  }, []);

  useEffect(() => {
    api<JobsResponse>("/jobs?per_pagina=250").then((result) => {
      setJobs(result.items);
      const requested = Number(new URLSearchParams(window.location.search).get("job"));
      const chosen = result.items.find((job) => job.id === requested)?.id ?? result.items[0]?.id;
      if (chosen) setJobId(chosen);
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!jobId) return;
    void loadLog(jobId);
    volg(jobId);
    return () => stopVolgen(jobId);
  }, [jobId, loadLog, stopVolgen, volg]);

  useEffect(() => {
    const incoming = events.filter(
      (event): event is Extract<EventMessage, { type: "log" }> =>
        event.type === "log" && event.job_id === jobId,
    );
    if (!incoming.length) return;
    setLines((old) => [...old, ...incoming.map((event) => event.tekst)].slice(-2000));
  }, [events, jobId]);

  const job = jobs.find((value) => value.id === jobId);
  const shown = useMemo(() => lines.filter((line) => line.toLowerCase().includes(query.toLowerCase())), [lines, query]);

  return <div className="page-stack"><div className="toolbar"><label className="field">Job<select value={jobId ?? ""} onChange={(event) => setJobId(Number(event.target.value))}><option value="">Kies een job</option>{jobs.map((value) => <option key={value.id} value={value.id}>#{value.id} · {fileName(value.pad)}</option>)}</select></label><label className="field library-path">Doorzoek log<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ffmpeg, warning, metadata…" /></label>{job?.commando ? <Button variant="secondary" onClick={() => navigator.clipboard.writeText(job.commando ?? "")}>Kopieer commando</Button> : null}</div>{job ? <Card className="log-meta"><strong>{fileName(job.pad)}</strong><span className="muted">Job #{job.id} · {job.status} · live abonnement actief zolang dit scherm open is</span></Card> : null}<Card className="log-view" aria-live="polite">{shown.length ? shown.map((line, index) => <div key={`${index}-${line.slice(0, 20)}`}>{line}</div>) : <div className="muted">Nog geen logregels voor deze job.</div>}</Card></div>;
}

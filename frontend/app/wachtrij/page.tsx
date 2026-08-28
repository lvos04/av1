"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { bytes, fileName } from "@/lib/utils";
import type { Job, JobsResponse, Lane } from "@/types";

const perPage = 250;

export default function QueuePage() {
  const [jobs, setJobs] = useState<Array<Job | undefined>>([]);
  const [total, setTotal] = useState(0);
  const [initialLoading, setInitialLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [lane, setLane] = useState("");
  const [encoder, setEncoder] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [cursor, setCursor] = useState(0);
  const parent = useRef<HTMLDivElement>(null);
  const loadingPages = useRef(new Set<number>());

  const load = useCallback(async (requestedPage: number, replace: boolean) => {
    if (loadingPages.current.has(requestedPage)) return;
    loadingPages.current.add(requestedPage);
    if (replace) setInitialLoading(true);
    try {
      const params = new URLSearchParams({ pagina: String(requestedPage), per_pagina: String(perPage) });
      if (status) params.set("status", status);
      if (lane) params.set("baan", lane);
      if (encoder) params.set("encoder", encoder);
      const result = await api<JobsResponse>(`/jobs?${params}`);
      setJobs((old) => {
        const next = replace ? new Array<Job | undefined>(result.totaal) : [...old];
        if (next.length !== result.totaal) next.length = result.totaal;
        const offset = (requestedPage - 1) * perPage;
        result.items.forEach((job, index) => { next[offset + index] = job; });
        return next;
      });
      setTotal(result.totaal);
    } finally {
      loadingPages.current.delete(requestedPage);
      if (replace) setInitialLoading(false);
    }
  }, [encoder, lane, status]);

  useEffect(() => {
    setSelected(new Set());
    setCursor(0);
    void load(1, true);
  }, [load]);

  // Deze bibliotheek is bewust gekozen voor tienduizenden rijen; de API
  // levert imperatieve meetfuncties die de React Compiler terecht overslaat.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: total,
    getScrollElement: () => parent.current,
    estimateSize: () => 64,
    overscan: 12,
  });
  const virtualRows = virtualizer.getVirtualItems();

  useEffect(() => {
    const missing = virtualRows.find((row) => jobs[row.index] === undefined);
    if (missing) void load(Math.floor(missing.index / perPage) + 1, false);
  }, [jobs, load, virtualRows]);

  useEffect(() => {
    const handler = (event: Event) => {
      const key = (event as CustomEvent<string>).detail;
      setCursor((old) => Math.max(0, Math.min(total - 1, old + (key === "j" ? 1 : -1))));
    };
    window.addEventListener("pipeline:navigate", handler);
    return () => window.removeEventListener("pipeline:navigate", handler);
  }, [total]);

  useEffect(() => {
    if (total) virtualizer.scrollToIndex(cursor, { align: "auto" });
  }, [cursor, total, virtualizer]);

  const selectedJobs = useMemo(
    () => jobs.filter((job): job is Job => job !== undefined && selected.has(job.id)),
    [jobs, selected],
  );

  function toggle(jobId: number) {
    setSelected((old) => {
      const value = new Set(old);
      if (value.has(jobId)) value.delete(jobId); else value.add(jobId);
      return value;
    });
  }

  async function bulkAbort() {
    await Promise.all(selectedJobs.map((job) => mutation(`/jobs/${job.id}/afbreken`, "POST")));
    setSelected(new Set());
    await load(1, true);
  }

  async function bulkLane(target: Lane) {
    await Promise.all(selectedJobs.filter((job) => job.status === "wacht").map((job) => mutation(`/jobs/${job.id}`, "PATCH", { baan: target })));
    setSelected(new Set());
    await load(1, true);
  }

  return (
    <div className="page-stack">
      <div className="toolbar">
        <label className="field">Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">Alle statussen</option><option value="wacht">Wacht</option><option value="bezig">Bezig</option><option value="verifieren">Verifiëren</option><option value="mislukt">Mislukt</option><option value="klaar">Klaar</option></select></label>
        <label className="field">Baan<select value={lane} onChange={(event) => setLane(event.target.value)}><option value="">CPU en GPU</option><option value="cpu">CPU</option><option value="gpu">GPU</option></select></label>
        <label className="field">Encoder<select value={encoder} onChange={(event) => setEncoder(event.target.value)}><option value="">Alle encoders</option><option value="libsvtav1">libsvtav1</option><option value="av1_qsv">av1_qsv</option></select></label>
        <span className="muted mono">{total.toLocaleString("nl-NL")} jobs</span>
        <div style={{ flex: 1 }} />
        <Button variant="secondary" disabled={!selectedJobs.length} onClick={() => bulkLane("cpu")}>Naar CPU</Button>
        <Button variant="secondary" disabled={!selectedJobs.length} onClick={() => bulkLane("gpu")}>Naar GPU</Button>
        <Button variant="danger" disabled={!selectedJobs.length} onClick={bulkAbort}>Afbreken ({selectedJobs.length})</Button>
      </div>
      <Card className="table-card">
        <div className="table-header"><span /><span>Bestand</span><span>Status</span><span>Baan</span><span>Encoder</span><span>Reden</span><span>Grootte</span></div>
        <div
          ref={parent}
          className="virtual-scroll"
          aria-label="Wachtrij met gevirtualiseerde rijen"
          tabIndex={0}
        >
          {total === 0 && !initialLoading ? <div className="empty-state">Geen jobs voor deze filters.</div> : null}
          <div className="virtual-inner" style={{ height: virtualizer.getTotalSize() }}>
            {virtualRows.map((virtualRow) => {
              const job = jobs[virtualRow.index];
              if (!job) {
                return <div key={`laden-${virtualRow.index}`} className="virtual-row muted" style={{ transform: `translateY(${virtualRow.start}px)` }}><span /><span>Rij laden…</span></div>;
              }
              return (
                <div key={job.id} className={`virtual-row ${selected.has(job.id) ? "selected" : ""}`} style={{ transform: `translateY(${virtualRow.start}px)` }} onClick={() => setCursor(virtualRow.index)}>
                  <input type="checkbox" aria-label={`Selecteer job ${job.id}`} checked={selected.has(job.id)} onChange={() => toggle(job.id)} />
                  <span className="file-cell"><strong title={job.pad}>{fileName(job.pad)}</strong><small>Job #{job.id} · prioriteit {job.prioriteit}</small></span>
                  <Badge tone={job.status === "mislukt" ? "red" : job.status === "klaar" ? "green" : job.status === "bezig" ? "blue" : "neutral"}>{job.status}</Badge>
                  <Badge tone={job.baan === "gpu" ? "blue" : "purple"}>{job.baan}</Badge>
                  <span className="mono">{job.encoder}</span>
                  <span className="reason-cell">{job.reden}</span><span className="mono">{bytes(job.bron_grootte)}</span>
                </div>
              );
            })}
          </div>
        </div>
      </Card>
    </div>
  );
}

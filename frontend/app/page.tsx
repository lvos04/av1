"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { WorkerCard } from "@/components/worker-card";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { bytes } from "@/lib/utils";
import { useLive } from "@/lib/ws";
import type { Statistics } from "@/types";

export default function DashboardPage() {
  const { tick, metrics } = useLive();
  const [statistics, setStatistics] = useState<Statistics | null>(null);

  useEffect(() => {
    api<Statistics>("/statistieken").then(setStatistics).catch(() => undefined);
  }, []);

  const workers = useMemo(() => ({
    gpu: tick?.workers.find((worker) => worker.baan === "gpu"),
    cpu: tick?.workers.find((worker) => worker.baan === "cpu"),
  }), [tick]);
  const q = tick?.wachtrij;
  const stats = statistics?.totaal;
  const processedToday = statistics?.per_dag.at(-1);

  return (
    <div className="page-stack">
      <section className="stats-grid" aria-label="Kerncijfers">
        <StatCard label="Wachtrij" value={`${q?.wachtend ?? "—"}`} detail={`${q?.bezig ?? 0} bezig · ${q?.klaar_vandaag ?? 0} klaar vandaag`} />
        <StatCard label="Besparing totaal" value={bytes(stats?.bespaard_bytes)} detail={`${(stats?.gemiddeld ?? 0).toFixed(1)}% gemiddeld per bestand`} />
        <StatCard label="Doorvoer" value={statistics ? `${bytes(statistics.doorvoer_bytes_per_dag_7d)}/dag` : "—"} detail={statistics?.prognose_klaar ? `Klaar rond ${new Date(statistics.prognose_klaar).toLocaleDateString("nl-NL")}` : "Nog onvoldoende historie"} />
        <StatCard label="Systeem" value={tick ? `CPU ${tick.systeem.cpu_percent.toFixed(0)}%` : "—"} detail={tick ? `${tick.systeem.gpu.beschikbaar ? `GPU ${tick.systeem.gpu.video_percent?.toFixed(0)}%` : "GPU-meting n/b"} · ${tick.systeem.schijf_vrij_gb.toFixed(0)} GB vrij` : "Live verbinding wordt opgebouwd"} error={Boolean(q?.fouten)} />
      </section>
      {tick && tick.systeem.cache_status !== "voldoende" ? <Card className="notice" role="status"><strong>Cache {tick.systeem.cache_status}</strong>{bytes(tick.systeem.schijf_beschikbaar_na_reservering)} blijft beschikbaar na actieve reserveringen. Waarschuwing bij {tick.systeem.cache_waarschuwing_gb} GB; beide banen pauzeren automatisch onder {tick.systeem.cache_pauze_gb} GB.</Card> : null}
      <section className="worker-grid" aria-label="Actieve workers"><WorkerCard lane="gpu" worker={workers.gpu} /><WorkerCard lane="cpu" worker={workers.cpu} /></section>
      <section className="chart-grid">
        <Card className="chart-card"><h2>CPU en GPU laatste uur</h2><div className="chart-area"><ResponsiveContainer width="100%" height="100%"><AreaChart data={metrics}><defs><linearGradient id="cpu" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#a855f7" stopOpacity={0.35}/><stop offset="95%" stopColor="#a855f7" stopOpacity={0}/></linearGradient><linearGradient id="gpu" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#3b82f6" stopOpacity={0.35}/><stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="tijd" stroke="#475569" tick={{ fontSize: 10 }} minTickGap={40}/><YAxis domain={[0,100]} stroke="#475569" tick={{ fontSize: 10 }} width={32}/><Tooltip contentStyle={{ background: "#12161f", border: "1px solid #222938" }}/><Area type="monotone" dataKey="cpu" name="CPU %" stroke="#a855f7" fill="url(#cpu)" isAnimationActive={false}/><Area type="monotone" dataKey="gpu" name="GPU Video %" stroke="#3b82f6" fill="url(#gpu)" isAnimationActive={false}/></AreaChart></ResponsiveContainer></div></Card>
        <Card className="chart-card"><h2>Besparing per dag (GB)</h2><div className="chart-area"><ResponsiveContainer width="100%" height="100%"><BarChart data={statistics?.per_dag ?? []}><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="dag" stroke="#475569" tick={{ fontSize: 10 }}/><YAxis stroke="#475569" tick={{ fontSize: 10 }} width={38} tickFormatter={(value: number) => (value / 1024 ** 3).toFixed(0)}/><Tooltip contentStyle={{ background: "#12161f", border: "1px solid #222938" }} formatter={(value) => bytes(Number(value))}/><Bar dataKey="bespaard_bytes" name="Bespaard" fill="#3b82f6" radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></div></Card>
      </section>
      <Card className="notice"><strong>Prognose</strong>{statistics?.prognose_klaar ? `Op basis van de laatste zeven dagen rondt de huidige wachtrij naar verwachting af op ${new Date(statistics.prognose_klaar).toLocaleDateString("nl-NL", { dateStyle: "long" })}.` : "Zodra er zeven dagen doorvoerdata is, verschijnt hier een betrouwbare prognose per baan."} {processedToday ? `Vandaag is ${bytes(processedToday.bespaard_bytes)} bespaard.` : ""}</Card>
    </div>
  );
}

function StatCard({ label, value, detail, error = false }: { label: string; value: string; detail: string; error?: boolean }) {
  return <Card className={`stat-card ${error ? "error" : ""}`}><p>{label}</p><strong>{value}</strong><small>{detail}</small></Card>;
}

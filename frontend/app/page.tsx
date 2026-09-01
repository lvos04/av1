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
  const system = tick?.systeem;
  const gpu = system?.gpu;
  const memory = system?.geheugen;

  return (
    <div className="page-stack">
      <section className="stats-grid" aria-label="Kerncijfers">
        <StatCard label="Wachtrij" value={`${q?.wachtend ?? "—"}`} detail={`${q?.bezig ?? 0} bezig · ${q?.klaar_vandaag ?? 0} klaar vandaag`} />
        <StatCard label="Besparing totaal" value={bytes(stats?.bespaard_bytes)} detail={`${(stats?.gemiddeld ?? 0).toFixed(1)}% gemiddeld per bestand`} />
        <StatCard label="Doorvoer" value={statistics ? `${bytes(statistics.doorvoer_bytes_per_dag_7d)}/dag` : "—"} detail={statistics?.prognose_klaar ? `Klaar rond ${new Date(statistics.prognose_klaar).toLocaleDateString("nl-NL")}` : "Nog onvoldoende historie"} />
        <StatCard label="Systeem" value={system ? `CPU ${system.cpu_percent.toFixed(0)}% · RAM ${memory?.percent.toFixed(0) ?? "—"}%` : "—"} detail={system ? `${gpu?.beschikbaar ? `GPU video ${gpu.video_percent?.toFixed(0) ?? "—"}% · render ${gpu.render_percent?.toFixed(0) ?? "—"}%` : "GPU-meting niet beschikbaar"} · totaal ${gpu?.platform_power_w?.toFixed(0) ?? "—"} W · ${system.schijf_vrij_gb.toFixed(0)} GB vrij` : "Live verbinding wordt opgebouwd"} error={Boolean(q?.fouten)} />
      </section>
      {tick?.systeem.opslagfout ? <Card className="notice error" role="alert"><strong>Werkopslag niet bereikbaar</strong>{tick.systeem.opslagfout}. Beide workers blijven veilig gepauzeerd; pas de keuze aan via Instellingen → Opslag kiezen.</Card> : null}
      {tick?.systeem.opslagmodus === "systeemschijf" ? <Card className="notice" role="status"><strong>Systeemschijf beschermd</strong>Cache en Ubuntu delen één schijf. De pipeline reserveert altijd minstens {tick.systeem.systeemschijf_reserve_gb} GB voor het systeem, boven op alle actieve jobreserveringen.</Card> : null}
      {tick && tick.systeem.cache_status !== "voldoende" ? <Card className="notice" role="status"><strong>Cache {tick.systeem.cache_status}</strong>{bytes(tick.systeem.schijf_beschikbaar_na_reservering)} blijft beschikbaar na actieve reserveringen. Waarschuwing bij {tick.systeem.effectieve_cache_waarschuwing_gb} GB; beide banen pauzeren automatisch onder {tick.systeem.effectieve_cache_pauze_gb} GB.</Card> : null}
      {system && !gpu?.beschikbaar ? <Card className="notice" role="status"><strong>GPU-meting niet beschikbaar</strong>{gpu?.fout?.split("\n")[0] ?? "De GPU-worker blijft onafhankelijk beschikbaar; controleer de hardwarediagnose bij Instellingen."}</Card> : null}
      <section className="worker-grid" aria-label="Actieve workers"><WorkerCard lane="gpu" worker={workers.gpu} /><WorkerCard lane="cpu" worker={workers.cpu} /></section>
      <section className="chart-grid">
        <Card className="chart-card"><h2>CPU en GPU laatste uur</h2><div className="chart-area"><ResponsiveContainer width="100%" height="100%"><AreaChart data={metrics}><defs><linearGradient id="cpu" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#a855f7" stopOpacity={0.35}/><stop offset="95%" stopColor="#a855f7" stopOpacity={0}/></linearGradient><linearGradient id="gpu-video" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/><stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="tijd" stroke="#475569" tick={{ fontSize: 10 }} minTickGap={40}/><YAxis domain={[0,100]} stroke="#475569" tick={{ fontSize: 10 }} width={32}/><Tooltip contentStyle={{ background: "#12161f", border: "1px solid #222938" }}/><Area type="monotone" dataKey="cpu" name="CPU %" stroke="#a855f7" fill="url(#cpu)" isAnimationActive={false}/><Area type="monotone" dataKey="gpuVideo" name="GPU Video %" stroke="#3b82f6" fill="url(#gpu-video)" connectNulls isAnimationActive={false}/><Area type="monotone" dataKey="gpuRender" name="GPU Render %" stroke="#22c55e" fill="none" connectNulls isAnimationActive={false}/></AreaChart></ResponsiveContainer></div></Card>
        <Card className="chart-card"><h2>Stroomverbruik laatste uur · systeemtotaal (PSys) {gpu?.platform_power_w?.toFixed(0) ?? "—"} W</h2><div className="chart-area"><ResponsiveContainer width="100%" height="100%"><AreaChart data={metrics}><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="tijd" stroke="#475569" tick={{ fontSize: 10 }} minTickGap={40}/><YAxis stroke="#475569" tick={{ fontSize: 10 }} width={40} tickFormatter={(value: number) => `${value.toFixed(0)} W`}/><Tooltip contentStyle={{ background: "#12161f", border: "1px solid #222938" }} formatter={(value) => `${Number(value).toFixed(1)} W`}/><Area type="monotone" dataKey="platformPower" name="Systeemtotaal (PSys)" stroke="#ef4444" fill="#ef444422" connectNulls isAnimationActive={false}/><Area type="monotone" dataKey="packagePower" name="Processorpakket" stroke="#f59e0b" fill="#f59e0b22" connectNulls isAnimationActive={false}/><Area type="monotone" dataKey="gpuPower" name="GPU-deel" stroke="#3b82f6" fill="none" connectNulls isAnimationActive={false}/></AreaChart></ResponsiveContainer></div></Card>
        <Card className="chart-card"><h2>Werkgeheugen laatste uur · {memory ? `${bytes(memory.gebruikt_bytes)} / ${bytes(memory.totaal_bytes)}` : "wachten op meting"}</h2><div className="chart-area"><ResponsiveContainer width="100%" height="100%"><AreaChart data={metrics}><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="tijd" stroke="#475569" tick={{ fontSize: 10 }} minTickGap={40}/><YAxis domain={[0,100]} stroke="#475569" tick={{ fontSize: 10 }} width={38} tickFormatter={(value: number) => `${value.toFixed(0)}%`}/><Tooltip contentStyle={{ background: "#12161f", border: "1px solid #222938" }} formatter={(value) => `${Number(value).toFixed(1)}%`}/><Area type="monotone" dataKey="memoryPercent" name="RAM gebruikt" stroke="#14b8a6" fill="#14b8a633" isAnimationActive={false}/></AreaChart></ResponsiveContainer></div></Card>
        <Card className="chart-card"><h2>Besparing per dag (GB)</h2><div className="chart-area"><ResponsiveContainer width="100%" height="100%"><BarChart data={statistics?.per_dag ?? []}><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="dag" stroke="#475569" tick={{ fontSize: 10 }}/><YAxis stroke="#475569" tick={{ fontSize: 10 }} width={38} tickFormatter={(value: number) => (value / 1024 ** 3).toFixed(0)}/><Tooltip contentStyle={{ background: "#12161f", border: "1px solid #222938" }} formatter={(value) => bytes(Number(value))}/><Bar dataKey="bespaard_bytes" name="Bespaard" fill="#3b82f6" radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></div></Card>
      </section>
      <Card className="notice"><strong>Prognose</strong>{statistics?.prognose_klaar ? `Op basis van de laatste zeven dagen rondt de huidige wachtrij naar verwachting af op ${new Date(statistics.prognose_klaar).toLocaleDateString("nl-NL", { dateStyle: "long" })}.` : "Zodra er zeven dagen doorvoerdata is, verschijnt hier een betrouwbare prognose per baan."} {processedToday ? `Vandaag is ${bytes(processedToday.bespaard_bytes)} bespaard.` : ""}</Card>
    </div>
  );
}

function StatCard({ label, value, detail, error = false }: { label: string; value: string; detail: string; error?: boolean }) {
  return <Card className={`stat-card ${error ? "error" : ""}`}><p>{label}</p><strong>{value}</strong><small>{detail}</small></Card>;
}

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import type { AiAnalysis, AiControllerStatus, AiStatistics } from "@/types";

type Period = 30 | 90;
type BadgeTone = "neutral" | "green" | "amber" | "red" | "purple";
type ApiAnalysis = Omit<
  AiAnalysis,
  "library_naam" | "redencodes" | "bewijsvelden" | "vertrouwen" | "kosten_usd"
> &
  Partial<
    Pick<
      AiAnalysis,
      "library_naam" | "redencodes" | "bewijsvelden" | "vertrouwen" | "kosten_usd"
    >
  > & { voorstel?: string };

const tooltipStyle = { background: "#12161f", border: "1px solid #222938" };

function money(value: number): string {
  return new Intl.NumberFormat("nl-NL", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value < 0.01 ? 6 : 2,
    maximumFractionDigits: 6,
  }).format(value);
}

function shortDay(value: string): string {
  const [, month, day] = value.split("-");
  return `${day}-${month}`;
}

function localDate(value: string): string {
  return new Date(value).toLocaleString("nl-NL", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function classificationTone(value: AiAnalysis["classificatie"]): BadgeTone {
  if (value === "overeenkomstig") return "green";
  if (value === "tijdelijk") return "amber";
  if (value === "mens_nodig") return "purple";
  if (value === "geblokkeerd") return "red";
  return "neutral";
}

function normalizeAnalysis(row: ApiAnalysis): AiAnalysis {
  let proposal: Record<string, unknown> = {};
  if (row.voorstel) {
    try {
      const parsed: unknown = JSON.parse(row.voorstel);
      if (typeof parsed === "object" && parsed !== null) proposal = parsed as Record<string, unknown>;
    } catch {
      proposal = {};
    }
  }
  const reasons = row.redencodes ?? proposal.redencodes;
  const evidence = row.bewijsvelden ?? proposal.bewijsvelden;
  const confidence = row.vertrouwen ?? proposal.vertrouwen;
  return {
    ...row,
    library_naam: row.library_naam ?? null,
    redencodes: Array.isArray(reasons) ? reasons.map(String) : [],
    bewijsvelden: Array.isArray(evidence) ? evidence.map(String) : [],
    vertrouwen: typeof confidence === "number" ? confidence : null,
    kosten_usd: row.kosten_usd ?? row.kosten_microdollar / 1_000_000,
  };
}

function browserDay(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function deriveStatistics(rows: AiAnalysis[], period: Period): AiStatistics {
  const today = new Date();
  const perDay = Array.from({ length: period }, (_, index) => {
    const value = new Date(today);
    value.setHours(12, 0, 0, 0);
    value.setDate(value.getDate() - (period - index - 1));
    return {
      dag: browserDay(value), analyses: 0, beslissingen: 0, overeenkomstig: 0,
      tijdelijk: 0, mens_nodig: 0, geblokkeerd: 0, provider_fouten: 0,
      uitgevoerd: 0, input_tokens: 0, output_tokens: 0,
      kosten_microdollar: 0, kosten_usd: 0,
    };
  });
  const buckets = new Map(perDay.map((day) => [day.dag, day]));
  for (const row of rows) {
    const bucket = buckets.get(browserDay(new Date(row.gemaakt_op)));
    if (!bucket) continue;
    bucket.analyses += 1;
    if (row.classificatie) {
      bucket.beslissingen += 1;
      bucket[row.classificatie] += 1;
    }
    if (row.fout) bucket.provider_fouten += 1;
    bucket.uitgevoerd += row.uitgevoerd;
    bucket.input_tokens += row.input_tokens;
    bucket.output_tokens += row.output_tokens;
    bucket.kosten_microdollar += row.kosten_microdollar;
    bucket.kosten_usd = bucket.kosten_microdollar / 1_000_000;
  }
  const keys = ["analyses", "beslissingen", "overeenkomstig", "tijdelijk", "mens_nodig", "geblokkeerd", "provider_fouten", "uitgevoerd", "input_tokens", "output_tokens", "kosten_microdollar"] as const;
  const total = Object.fromEntries(keys.map((key) => [key, perDay.reduce((sum, day) => sum + day[key], 0)])) as unknown as AiStatistics["totaal"];
  total.kosten_usd = total.kosten_microdollar / 1_000_000;
  return { dagen: period, tijdzone: Intl.DateTimeFormat().resolvedOptions().timeZone, totaal: total, per_dag: perDay };
}

export default function AiPage() {
  const [controller, setController] = useState<AiControllerStatus | null>(null);
  const [statistics, setStatistics] = useState<AiStatistics | null>(null);
  const [analyses, setAnalyses] = useState<AiAnalysis[]>([]);
  const [period, setPeriod] = useState<Period>(30);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [controllerResult, rawAnalyses, statisticsResult] = await Promise.all([
        api<AiControllerStatus>("/ai-controller"),
        api<ApiAnalysis[]>("/ai-controller/analyses?limit=500"),
        api<AiStatistics>(`/ai-controller/statistieken?dagen=${period}`).catch(() => null),
      ]);
      const analysesResult = rawAnalyses.map(normalizeAnalysis);
      setController(controllerResult);
      setStatistics(statisticsResult ?? deriveStatistics(analysesResult, period));
      setAnalyses(analysesResult);
      setError("");
    } catch (loadError) {
      setError(String(loadError));
    }
  }, [period]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function analyzeNow() {
    setBusy(true);
    setMessage("");
    try {
      const result = await mutation<Record<string, number>>("/ai-controller/analyseer", "POST");
      setMessage(
        `Controle klaar: ${result.geanalyseerd ?? 0} groepen beoordeeld, ${result.uitgevoerd ?? 0} acties door de policy toegestaan en ${result.opnieuw ?? 0} retries.`,
      );
      await load();
    } catch (analyzeError) {
      setError(String(analyzeError));
    } finally {
      setBusy(false);
    }
  }

  const total = statistics?.totaal;
  const usage = controller?.verbruik;
  return (
    <div className="page-stack">
      <div className="toolbar">
        <label className="field">Periode
          <select value={period} onChange={(event) => setPeriod(Number(event.target.value) as Period)}>
            <option value={30}>Laatste 30 dagen</option>
            <option value={90}>Laatste 90 dagen</option>
          </select>
        </label>
        <Button disabled={busy || !controller?.ingeschakeld} onClick={analyzeNow}>
          {busy ? "Controleren…" : "Nu controleren"}
        </Button>
        <span className="muted">Het model stelt alleen voor; de lokale Python-policy houdt altijd het laatste woord.</span>
      </div>

      {message ? <Card className="notice" role="status">{message}</Card> : null}
      {error ? <Card className="notice error" role="alert"><strong>AI-overzicht niet beschikbaar</strong>{error}</Card> : null}
      {controller?.laatste_fout ? <Card className="notice error" role="alert"><strong>Laatste providerfout</strong>{controller.laatste_fout}</Card> : null}
      {controller && (!controller.api_sleutel_ingesteld || !controller.ingeschakeld) ? (
        <Card className="notice"><strong>Controller niet volledig actief</strong>
          {!controller.api_sleutel_ingesteld ? "De versleutelde API-sleutel ontbreekt. " : ""}
          {!controller.ingeschakeld ? "AI-analyses staan uit. " : ""}
          Pas dit aan via Instellingen.
        </Card>
      ) : null}

      <section className="stats-grid" aria-label="AI-status">
        <Card className="stat-card"><p>Controller</p><strong>{controller?.ingeschakeld ? controller.modus : "uit"}</strong><small>{controller ? `${controller.provider} · ${controller.model} · reasoning ${controller.reasoning}` : "laden…"}</small></Card>
        <Card className="stat-card"><p>Kosten deze maand</p><strong>{money(usage?.kosten_usd ?? 0)}</strong><small>budget {money(controller?.maandbudget_usd ?? 0)} · {usage?.analyses ?? 0} analyses</small></Card>
        <Card className="stat-card"><p>Beslissingen in periode</p><strong>{total?.beslissingen ?? 0}</strong><small>{total?.uitgevoerd ?? 0} automatisch uitgevoerd · {total?.analyses ?? 0} pogingen</small></Card>
        <Card className="stat-card"><p>Veilig tegengehouden</p><strong>{(total?.geblokkeerd ?? 0) + (total?.mens_nodig ?? 0)}</strong><small>{total?.geblokkeerd ?? 0} geblokkeerd · {total?.mens_nodig ?? 0} mens nodig · {total?.provider_fouten ?? 0} providerfouten</small></Card>
      </section>

      <section className="chart-grid" aria-label="AI-grafieken">
        <Card className="chart-card"><h2>API-gebruik per dag</h2><div className="chart-area" role="img" aria-label="Inputtokens, outputtokens en kosten per dag"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={statistics?.per_dag ?? []}><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="dag" tickFormatter={shortDay} stroke="#475569" tick={{ fontSize: 10 }}/><YAxis yAxisId="tokens" stroke="#475569" tick={{ fontSize: 10 }} width={44}/><YAxis yAxisId="kosten" orientation="right" stroke="#475569" tick={{ fontSize: 10 }} width={52} tickFormatter={(value: number) => `$${value.toFixed(4)}`}/><Tooltip contentStyle={tooltipStyle} labelFormatter={(label) => `Dag ${label}`} formatter={(value, name) => String(name) === "Kosten" ? money(Number(value)) : `${Number(value).toLocaleString("nl-NL")} tokens`}/><Bar yAxisId="tokens" dataKey="input_tokens" name="Input" stackId="tokens" fill="#3b82f6"/><Bar yAxisId="tokens" dataKey="output_tokens" name="Output" stackId="tokens" fill="#a855f7" radius={[4,4,0,0]}/><Line yAxisId="kosten" type="monotone" dataKey="kosten_usd" name="Kosten" stroke="#10b981" strokeWidth={2} dot={false} isAnimationActive={false}/></ComposedChart></ResponsiveContainer></div></Card>
        <Card className="chart-card"><h2>AI-beslissingen per dag</h2><div className="chart-area" role="img" aria-label="Aantal AI-beslissingen per classificatie en dag"><ResponsiveContainer width="100%" height="100%"><BarChart data={statistics?.per_dag ?? []}><CartesianGrid stroke="#222938" vertical={false}/><XAxis dataKey="dag" tickFormatter={shortDay} stroke="#475569" tick={{ fontSize: 10 }}/><YAxis allowDecimals={false} stroke="#475569" tick={{ fontSize: 10 }} width={34}/><Tooltip contentStyle={tooltipStyle} labelFormatter={(label) => `Dag ${label}`}/><Bar dataKey="overeenkomstig" name="Overeenkomstig" stackId="besluiten" fill="#10b981"/><Bar dataKey="tijdelijk" name="Tijdelijk" stackId="besluiten" fill="#f59e0b"/><Bar dataKey="mens_nodig" name="Mens nodig" stackId="besluiten" fill="#a855f7"/><Bar dataKey="geblokkeerd" name="Geblokkeerd" stackId="besluiten" fill="#ef4444" radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></div></Card>
      </section>

      <Card className="ai-history-card">
        <div className="ai-history-heading"><div><h2>Laatste AI-beslissingen</h2><p className="muted">Getypeerd voorstel, technische onderbouwing en de onafhankelijke lokale policy-uitkomst.</p></div><Badge tone={controller?.modus === "actief" ? "green" : "amber"}>{controller?.modus ?? "laden"}</Badge></div>
        <div className="ai-decision-list">
          {analyses.length ? analyses.map((analysis) => (
            <article className="ai-decision" key={analysis.id}>
              <div className="ai-decision-title">
                <span><strong>Analyse #{analysis.id}</strong><small>{localDate(analysis.gemaakt_op)} · {analysis.library_naam ?? `Library ${analysis.library_id ?? "—"}`} · {analysis.soort === "besluitgroep" ? "besluitgroep" : "foutgroep"}</small></span>
                <span className="ai-badges"><Badge tone={classificationTone(analysis.classificatie)}>{analysis.classificatie ?? "providerfout"}</Badge><Badge tone={analysis.uitgevoerd ? "green" : "neutral"}>{analysis.uitgevoerd ? "uitgevoerd" : "geen actie"}</Badge></span>
              </div>
              {analysis.fout ? <p className="danger">{analysis.fout}</p> : null}
              <dl className="ai-decision-details">
                <div><dt>Voorstel</dt><dd>{analysis.voorgestelde_actie ?? "geen geldig voorstel"}</dd></div>
                <div><dt>Lokale policy</dt><dd>{analysis.policy_uitkomst ?? "geen actie"}</dd></div>
                <div><dt>Redencodes</dt><dd>{analysis.redencodes.length ? analysis.redencodes.join(", ") : "—"}</dd></div>
                <div><dt>Bewijsvelden</dt><dd>{analysis.bewijsvelden.length ? analysis.bewijsvelden.join(", ") : "—"}</dd></div>
              </dl>
              <p className="ai-usage-line">{analysis.input_tokens.toLocaleString("nl-NL")} input · {analysis.output_tokens.toLocaleString("nl-NL")} output · {money(analysis.kosten_usd)}{analysis.vertrouwen !== null ? ` · vertrouwen ${(analysis.vertrouwen * 100).toFixed(0)}% (alleen informatief)` : ""}</p>
            </article>
          )) : <div className="empty-state">Nog geen AI-beslissingen opgeslagen.</div>}
        </div>
      </Card>
    </div>
  );
}

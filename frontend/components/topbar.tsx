"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Icon } from "@/components/icon";
import { useLive } from "@/lib/ws";

const titles: Record<string, string> = {
  "/": "Dashboard",
  "/beslissingen": "Beslissingen",
  "/wachtrij": "Wachtrij",
  "/workers": "Workers",
  "/bibliotheken": "Bibliotheken",
  "/profielen": "Profielen",
  "/kwaliteit": "Kwaliteit",
  "/logs": "Logs",
  "/fouten": "Fouten",
  "/instellingen": "Instellingen",
};

export function Topbar() {
  const pathname = usePathname();
  const { tick } = useLive();
  const gpu = tick?.systeem.gpu;
  const [identity, setIdentity] = useState({ server_naam: "AV1 Pipeline", tijdzone: "Europe/Amsterdam" });
  useEffect(() => {
    fetch("/api/instellingen", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((value: unknown) => {
        if (typeof value === "object" && value !== null) {
          const row = value as Record<string, unknown>;
          setIdentity({ server_naam: String(row.server_naam ?? "AV1 Pipeline"), tijdzone: String(row.tijdzone ?? "Europe/Amsterdam") });
        }
      })
      .catch(() => undefined);
  }, []);
  return (
    <header className="topbar">
      <div className="topbar-left"><div><h1>{titles[pathname] ?? "AV1 Pipeline"}</h1><span className="topbar-identity">{identity.server_naam} · {identity.tijdzone}</span></div><label className="search"><Icon name="search" size={14} /><span className="sr-only">Zoeken</span><input data-global-search placeholder="Zoek bestand, serie of film…" /></label></div>
      <div className="topbar-stats"><span>CPU <strong>{tick ? `${tick.systeem.cpu_percent.toFixed(0)}%` : "—"}</strong></span><span>GPU Video <strong>{gpu?.beschikbaar && gpu.video_percent !== null ? `${gpu.video_percent.toFixed(0)}%` : "n/b"}</strong></span><span>Cache <strong>{tick ? `${tick.systeem.schijf_vrij_gb.toFixed(0)} GB vrij` : "—"}</strong></span></div>
    </header>
  );
}

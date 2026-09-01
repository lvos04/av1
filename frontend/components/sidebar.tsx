"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Icon } from "@/components/icon";
import { cn } from "@/lib/utils";
import { useLive } from "@/lib/ws";

const navigation = [
  ["Dashboard", "/", "home"],
  ["Beslissingen", "/beslissingen", "check"],
  ["AI", "/instellingen?tab=ai", "ai"],
  ["Wachtrij", "/wachtrij", "list"],
  ["Workers", "/workers", "workers"],
  ["Bibliotheken", "/bibliotheken", "folder"],
  ["Profielen", "/profielen", "settings"],
  ["Kwaliteit", "/kwaliteit", "activity"],
  ["Logs", "/logs", "file"],
  ["Fouten", "/fouten", "alert"],
  ["Instellingen", "/instellingen", "sliders"],
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const [search, setSearch] = useState("");
  const { tick, verbonden } = useLive();
  useEffect(() => {
    const update = () => setSearch(window.location.search);
    update();
    window.addEventListener("popstate", update);
    window.addEventListener("pipeline:location", update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("pipeline:location", update);
    };
  }, []);
  return (
    <aside className="sidebar">
      <div>
        <div className="brand">
          <span className="brand-logo"><Icon name="activity" size={20} /></span>
          <span><strong>AV1 Pipeline</strong><small>LOCAL ENGINE</small></span>
        </div>
        <nav aria-label="Hoofdnavigatie">
          {navigation.map(([label, href, icon]) => {
            const aiTab = search === "?tab=ai";
            const active = href === "/"
              ? pathname === "/"
              : href.includes("?tab=ai")
                ? pathname === "/instellingen" && aiTab
                : href === "/instellingen"
                  ? pathname === href && !aiTab
                  : pathname.startsWith(href);
            const count =
              href === "/wachtrij"
                ? tick?.wachtrij.wachtend
                : href === "/fouten"
                  ? tick?.wachtrij.fouten
                  : undefined;
            return (
              <Link key={href} href={href} onClick={() => window.setTimeout(() => window.dispatchEvent(new Event("pipeline:location")), 0)} className={cn("nav-item", active && "active")}>
                <Icon name={icon} />
                <span>{label}</span>
                {count ? <span className={cn("nav-count", href === "/fouten" && "error")}>{count}</span> : null}
              </Link>
            );
          })}
        </nav>
      </div>
      <footer>
        <p className="system-line"><span className={cn("status-dot", verbonden && "online")} />{verbonden ? "Systeem online" : "Opnieuw verbinden…"}</p>
        <p className="footer-meta"><span>v0.1.0</span><a href="/docs">Documentatie</a></p>
      </footer>
    </aside>
  );
}

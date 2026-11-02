"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import { bytes, fileName } from "@/lib/utils";
import type { DecisionGroup, DecisionItem, Lane } from "@/types";

interface GroupResponse { groepen: DecisionGroup[]; totaal: number }
interface ItemResponse { items: DecisionItem[]; totaal: number }

function groupFilter(group: DecisionGroup) {
  return {
    status: group.status,
    actie: group.actie,
    baan: group.baan,
    encoder: group.encoder,
    reden: group.reden,
    library_id: group.library_id,
  };
}

export default function DecisionsPage() {
  const [groups, setGroups] = useState<DecisionGroup[]>([]);
  const [status, setStatus] = useState("nieuw");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [items, setItems] = useState<DecisionItem[]>([]);
  const [confirm, setConfirm] = useState<DecisionGroup | null>(null);
  const [hardlinkItem, setHardlinkItem] = useState<DecisionItem | null>(null);
  const [hardlinkGroup, setHardlinkGroup] = useState<DecisionGroup | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [message, setMessage] = useState("");

  const pendingCount = groups.reduce((total, group) => total + group.aantal, 0);
  const pendingBytes = groups.reduce((total, group) => total + group.bytes, 0);
  const cleanupCount = groups
    .filter((group) => group.actie === "opruimen")
    .reduce((total, group) => total + group.aantal, 0);
  const cleanupBytes = groups
    .filter((group) => group.actie === "opruimen")
    .reduce((total, group) => total + group.bytes, 0);
  const hardlinkCount = groups
    .filter((group) => group.nlink > 1)
    .reduce((total, group) => total + group.aantal, 0);

  const load = useCallback(async () => {
    const result = await api<GroupResponse>(`/besluiten?status=${encodeURIComponent(status)}`);
    setGroups(result.groepen);
  }, [status]);
  useEffect(() => { void load(); }, [load]);

  async function open(group: DecisionGroup) {
    if (expanded === group.eerste_id) {
      setExpanded(null);
      return;
    }
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(groupFilter(group))) {
      if (value !== null) params.set(key === "status" ? "status" : key, String(value));
    }
    const result = await api<ItemResponse>(`/besluiten/items?${params}&per_pagina=500`);
    setItems(result.items);
    setExpanded(group.eerste_id);
  }

  async function approve(group: DecisionGroup) {
    if (group.actie === "opruimen") {
      setConfirm(group);
      return;
    }
    if (group.nlink > 1) {
      setHardlinkGroup(group);
      return;
    }
    const result = await mutation<{ goedgekeurd: number }>("/besluiten/goedkeuren", "POST", { filter: groupFilter(group) });
    setMessage(`${result.goedgekeurd} besluiten goedgekeurd en veilig ingepland.`);
    await load();
  }

  async function approveHardlinks() {
    if (!hardlinkGroup) return;
    try {
      const result = await mutation<{ goedgekeurd: number }>("/besluiten/goedkeuren", "POST", {
        filter: groupFilter(hardlinkGroup),
        hardlinks_bevestigd: true,
      });
      setMessage(`${result.goedgekeurd} hardlinkjobs definitief bevestigd en ingepland.`);
      setHardlinkGroup(null);
      await load();
    } catch (error) { setMessage(String(error)); }
  }

  async function approveCleanup() {
    if (!confirm) return;
    const result = await mutation<{ goedgekeurd: number }>("/besluiten/goedkeuren", "POST", {
      filter: groupFilter(confirm),
      bevestig_aantal: confirm.aantal,
      bevestig_bytes: confirm.bytes,
    });
    setMessage(`${result.goedgekeurd} permanente opruimbesluiten expliciet goedgekeurd.`);
    setConfirm(null);
    await load();
  }

  async function approveAll() {
    if (hardlinkCount > 0) {
      setConfirmAll(false);
      setMessage(`${hardlinkCount} hardlinkbesluiten vereisen eerst individuele bevestiging; de rest kan wel gezamenlijk worden goedgekeurd.`);
      return;
    }
    try {
      const result = await mutation<{ goedgekeurd: number }>("/besluiten/goedkeuren", "POST", {
        filter: { status: "nieuw" },
        bevestig_aantal: cleanupCount,
        bevestig_bytes: cleanupBytes,
      });
      setMessage(`${result.goedgekeurd} openstaande besluiten goedgekeurd en veilig ingepland.`);
      setConfirmAll(false);
      await load();
    } catch (error) { setMessage(String(error)); }
  }

  async function reject(group: DecisionGroup) {
    const result = await mutation<{ afgewezen: number }>("/besluiten/afwijzen", "POST", { filter: groupFilter(group) });
    setMessage(`${result.afgewezen} besluiten afgewezen.`);
    await load();
  }

  async function override(item: DecisionItem, lane: Lane, hardlink = false) {
    try {
      await mutation(`/besluiten/${item.id}`, "PATCH", {
        baan: lane,
        encoder: lane === "gpu" ? "av1_qsv" : "libsvtav1",
        hardlink_bevestigd: hardlink,
      });
      setMessage(hardlink ? `Hardlink voor ${fileName(item.pad)} per bestand bevestigd. Keur de nieuwe groep nog afzonderlijk goed.` : `Baan voor ${fileName(item.pad)} aangepast.`);
      setHardlinkItem(null);
      setExpanded(null);
      await load();
    } catch (error) { setMessage(String(error)); }
  }

  return (
    <div className="page-stack">
      <div className="toolbar"><label className="field">Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="nieuw">Nieuw</option><option value="goedgekeurd">Goedgekeurd</option><option value="afgewezen">Afgewezen</option><option value="ingepland">Ingepland</option></select></label><span className="muted">De scanner beslist; pas jouw expliciete goedkeuring maakt encodewerk uitvoerbaar.</span>{status === "nieuw" ? <Button variant="danger" disabled={pendingCount === 0} onClick={() => setConfirmAll(true)}>Alles goedkeuren ({pendingCount.toLocaleString("nl-NL")})</Button> : null}</div>
      {message ? <Card className="notice" role="status">{message}</Card> : null}
      <section className="decision-list" aria-label="Gegroepeerde besluiten">
        {groups.map((group) => {
          const destructive = group.actie === "opruimen";
          const isOpen = expanded === group.eerste_id;
          return (
            <Card className={`decision-group ${destructive ? "destructive" : ""}`} key={`${group.eerste_id}-${group.reden}`}>
              <div className="decision-summary">
                <button className="disclosure" aria-expanded={isOpen} onClick={() => open(group)}>{isOpen ? "▾" : "▸"}<span className="sr-only">Details</span></button>
                <strong>{group.aantal.toLocaleString("nl-NL")} bestanden</strong>
                <Badge tone={destructive ? "red" : group.actie === "overslaan" ? "neutral" : "blue"}>{group.actie}</Badge>
                {group.baan ? <Badge tone={group.baan === "gpu" ? "blue" : "purple"}>{group.baan} · {group.encoder}</Badge> : null}
                {group.nlink > 1 ? <Badge tone="amber">hardlink × {group.nlink}</Badge> : null}
                <span className="decision-reason">{group.reden}</span>
                <span className="mono">{bytes(group.bytes)}</span>
                {group.status === "nieuw" ? <div className="decision-actions"><Button variant={destructive ? "danger" : "default"} onClick={() => approve(group)}>Goedkeuren</Button><Button variant="secondary" onClick={() => reject(group)}>Afwijzen</Button></div> : <Badge>{group.status}</Badge>}
              </div>
              {isOpen ? <div className="decision-items">{items.map((item) => <div className="decision-item" key={item.id}><span className="file-cell"><strong>{fileName(item.pad)}</strong><small>{item.library_naam} · {bytes(item.grootte)}</small></span>{item.reden.includes("leek een extra") ? <Badge tone="amber">leek extra, blijft staan</Badge> : null}{item.nlink > 1 ? <span className="hardlink-note"><Badge tone="amber">HARDLINK GEDETECTEERD</Badge> Torrentbestand blijft oud; blijvende extra ruimte kan {bytes(item.grootte)} zijn. De hardlink wordt niet hersteld.</span> : null}{item.status === "nieuw" && item.nlink > 1 ? <Button variant="secondary" onClick={() => setHardlinkItem(item)}>Per bestand verbreken…</Button> : null}{item.status === "nieuw" && item.actie !== "opruimen" && item.actie !== "overslaan" && item.nlink <= 1 ? <span className="override-actions"><Button variant="ghost" onClick={() => override(item, "cpu")}>CPU</Button><Button variant="ghost" onClick={() => override(item, "gpu")}>GPU</Button></span> : null}</div>)}</div> : null}
            </Card>
          );
        })}
        {!groups.length ? <Card className="empty-state">Geen besluiten met deze status.</Card> : null}
      </section>
      {confirm ? <div className="dialog-backdrop" role="presentation"><section className="dialog destructive-dialog" role="alertdialog" aria-modal="true" aria-labelledby="cleanup-title"><h2 id="cleanup-title">Permanent opruimen bevestigen</h2><p><strong>{confirm.aantal.toLocaleString("nl-NL")} bestanden, {bytes(confirm.bytes)}</strong> worden permanent verwijderd. Dit is geen cache-opruiming en er is geen prullenbak.</p><p className="danger">Controleer de groep en grootte. De pipeline voert dit alleen uit na deze expliciete bevestiging.</p><div className="dialog-actions"><Button variant="secondary" onClick={() => setConfirm(null)}>Annuleren</Button><Button variant="danger" onClick={approveCleanup}>Bevestig {confirm.aantal} bestanden</Button></div></section></div> : null}
      {confirmAll ? <div className="dialog-backdrop" role="presentation"><section className="dialog destructive-dialog" role="alertdialog" aria-modal="true" aria-labelledby="approve-all-title"><h2 id="approve-all-title">Alle openstaande besluiten goedkeuren?</h2><p><strong>{pendingCount.toLocaleString("nl-NL")} besluiten ({bytes(pendingBytes)})</strong> worden goedgekeurd. Daarvan worden {pendingCount - cleanupCount} encodeer-/oversla-besluiten verwerkt.</p>{cleanupCount > 0 ? <p className="danger"><strong>{cleanupCount.toLocaleString("nl-NL")} opruimbesluiten ({bytes(cleanupBytes)})</strong> verwijderen na geslaagde verificatie permanent bestanden. Er is geen prullenbak.</p> : null}<p className="muted">De bronbestanden worden niet overschreven vóór verificatie. Harde links worden automatisch tegengehouden voor individuele bevestiging.</p><div className="dialog-actions"><Button variant="secondary" onClick={() => setConfirmAll(false)}>Annuleren</Button><Button variant="danger" onClick={approveAll}>Alles goedkeuren</Button></div></section></div> : null}
      {hardlinkItem ? <div className="dialog-backdrop" role="presentation"><section className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="hardlink-item-title"><h2 id="hardlink-item-title">Hardlink per bestand verbreken</h2><p><strong>{fileName(hardlinkItem.pad)}</strong> wordt na verificatie een zelfstandige AV1-kopie.</p><p className="danger">Het torrentbestand blijft de oude inhoud houden. De hardlink wordt niet hersteld en de blijvende extra ruimte kan {bytes(hardlinkItem.grootte)} zijn.</p><div className="dialog-actions"><Button variant="secondary" onClick={() => setHardlinkItem(null)}>Annuleren</Button><Button onClick={() => override(hardlinkItem, "cpu", true)}>Ik begrijp dit, bevestig bestand</Button></div></section></div> : null}
      {hardlinkGroup ? <div className="dialog-backdrop" role="presentation"><section className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="hardlink-group-title"><h2 id="hardlink-group-title">Hardlinkjobs definitief inplannen</h2><p><strong>{hardlinkGroup.aantal.toLocaleString("nl-NL")} bestanden, {bytes(hardlinkGroup.bytes)}</strong> zijn eerder per bestand bevestigd.</p><p className="danger">Dit is de tweede bevestiging. Torrent-hardlinks blijven oud en worden niet hersteld.</p><div className="dialog-actions"><Button variant="secondary" onClick={() => setHardlinkGroup(null)}>Annuleren</Button><Button onClick={approveHardlinks}>Definitief inplannen</Button></div></section></div> : null}
    </div>
  );
}

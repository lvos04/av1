"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, mutation } from "@/lib/api";
import type { Profile } from "@/types";

const help: Record<string, string> = {
  drempel_2160: "Alleen 4K-video boven deze kbps-drempel wordt hercodeerd.",
  drempel_1080: "Alleen 1080p-video boven deze kbps-drempel wordt hercodeerd.",
  drempel_720: "Alleen 720p-video boven deze kbps-drempel wordt hercodeerd.",
  crf_2160: "Kwaliteitsdoel voor 4K; lager is groter en behoudender.",
  crf_1080: "Kwaliteitsdoel voor 1080p; lager is groter en behoudender.",
  preset_2160: "SVT-AV1-snelheid. Preset 6 op 4K kan vijf tot acht uur per film kosten.",
  preset_1080: "SVT-AV1-snelheid; lagere waarden zijn veel trager.",
  gpu_quality: "Kwaliteitsdoel van av1_qsv op Intel Arc.",
  gpu_voor_sdr_1080: "Gebruik de snelle GPU-baan voor 1080p SDR.",
  gpu_voor_sdr_4k: "Gebruik de GPU-baan voor 4K SDR.",
  gpu_voor_hdr10: "av1_qsv draagt mastering display niet mee; de pipeline muxt deze metadata daarom na verificatie terug.",
  dv_modus: "Standaard skip. AV1 met Dolby Vision is experimenteel en beperkt afspeelbaar.",
  film_grain: "SVT-AV1 film-grain synthese; 0 schakelt dit uit.",
  audio_talen: "Talen om te behouden; er blijft altijd minstens één audiospoor over.",
  ondertitel_talen: "Ondertiteltalen om te behouden; ASS-lettertypen blijven gekoppeld.",
  commentaar_weg: "Verwijder herkenbare commentaarsporen.",
  beste_per_taal: "Bewaar per taal het beste audiospoor.",
};

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [saved, setSaved] = useState("");
  const load = useCallback(() => api<Profile[]>("/profielen").then(setProfiles), []);
  useEffect(() => { void load(); }, [load]);

  function updateLocal(profileId: number, key: string, raw: string | boolean) {
    setProfiles((old) => old.map((profile) => {
      if (profile.id !== profileId) return profile;
      const current = profile.instellingen[key];
      let value: unknown = raw;
      if (typeof current === "number") value = Number(raw);
      if (Array.isArray(current)) value = String(raw).split(",").map((item) => item.trim()).filter(Boolean);
      return { ...profile, instellingen: { ...profile.instellingen, [key]: value } };
    }));
  }

  async function save(profile: Profile) {
    await mutation(`/profielen/${profile.id}`, "PATCH", { naam: profile.naam, instellingen: profile.instellingen });
    setSaved(`Profiel ‘${profile.naam}’ opgeslagen. Nieuwe scans gebruiken deze waarden; bestaande besluiten veranderen niet stil.`);
  }

  return <div className="page-stack">{saved ? <Card className="notice" role="status">{saved}</Card> : null}{profiles.map((profile) => <Card className="profile-card" key={profile.id}><div className="worker-header"><h2>{profile.naam}</h2><Button onClick={() => save(profile)}>Profiel opslaan</Button></div><div className="profile-grid">{Object.entries(profile.instellingen).map(([key, value]) => <label className="profile-field" key={key}><span><strong>{key}</strong><small>{help[key] ?? "Profielinstelling voor de beslisboom."}</small></span>{typeof value === "boolean" ? <input type="checkbox" checked={value} onChange={(event) => updateLocal(profile.id, key, event.target.checked)} /> : key === "dv_modus" ? <select value={String(value)} onChange={(event) => updateLocal(profile.id, key, event.target.value)}><option value="skip">skip — veilig</option><option value="av1">av1 — experimenteel</option></select> : <input value={Array.isArray(value) ? value.join(", ") : String(value)} onChange={(event) => updateLocal(profile.id, key, event.target.value)} />}</label>)}</div><div className="profile-footer"><p><strong>Proefdraaien</strong><br/><span className="muted">Schakel de optionele functie in en kies op Kwaliteit één catalogusbestand. Er wordt nooit automatisch een heel bestand gestart.</span></p><a className="button button-secondary" href="/kwaliteit">Naar Kwaliteit</a></div></Card>)}</div>;
}

export type Lane = "cpu" | "gpu";
export type JobStatus =
  | "wacht"
  | "bezig"
  | "verifieren"
  | "klaar"
  | "mislukt"
  | "afgebroken"
  | "overgeslagen";

export interface LiveWorker {
  baan: Lane;
  job_id: number | null;
  bestand?: string;
  encoder?: string;
  percent?: number | null;
  fps?: number | null;
  speed?: number | null;
  eta_seconden?: number | null;
  bitrate_kbps?: number | null;
  grootte_nu?: number;
  grootte_bron?: number;
  gepauzeerd?: boolean;
  pauze_reden?: string | null;
  verouderd?: boolean;
}

export interface SystemMetrics {
  cpu_percent: number;
  cpu_temp: number | null;
  geheugen: {
    totaal_bytes: number;
    gebruikt_bytes: number;
    beschikbaar_bytes: number;
    percent: number;
  };
  gpu: {
    video_percent: number | null;
    render_percent: number | null;
    freq_mhz: number | null;
    power_w: number | null;
    package_power_w: number | null;
    platform_power_w: number | null;
    platform_power_beschikbaar: boolean;
    platform_power_fout?: string;
    beschikbaar: boolean;
    fout?: string;
  };
  schijf_vrij_gb: number;
  schijf_vrij_bytes: number;
  schijf_gereserveerd_bytes: number;
  schijf_beschikbaar_na_reservering: number;
  cache_status: "voldoende" | "waarschuwing" | "pauzegrens";
  cache_waarschuwing_gb: number;
  cache_pauze_gb: number;
  effectieve_cache_waarschuwing_gb: number;
  effectieve_cache_pauze_gb: number;
  opslagmodus: "systeemschijf" | "aparte_schijf";
  systeemschijf_reserve_gb: number;
  opslagfout: string | null;
}

export interface QueueCounts {
  wachtend: number;
  bezig: number;
  klaar_vandaag: number;
  fouten: number;
}

export interface TickMessage {
  type: "tick";
  tijd: string;
  workers: LiveWorker[];
  systeem: SystemMetrics;
  wachtrij: QueueCounts;
}

export type EventMessage =
  | { type: "job_klaar"; job_id: number; besparing_bytes: number }
  | { type: "job_mislukt"; job_id: number; reden: string }
  | { type: "scan_klaar"; library_id: number; nieuw: number; gewijzigd: number }
  | { type: "log"; job_id: number; niveau: string; tekst: string; tijd: string }
  | { type: "melding"; soort: string; titel: string; actie?: string };

export interface Job {
  id: number;
  bestand_id: number;
  status: JobStatus;
  baan: Lane;
  prioriteit: number;
  percent: number;
  fps: number | null;
  speed: number | null;
  eta_seconden: number | null;
  fout: string | null;
  foutsoort: string | null;
  commando: string | null;
  pad: string;
  bron_grootte: number;
  library_id: number;
  actie: string;
  encoder: string;
  reden: string;
  gestart_op: string | null;
  klaar_op: string | null;
  resultaat_grootte: number | null;
  verificatie_geslaagd: number | null;
  verificatie_toelichting: string | null;
  kwaliteitsadvies: string | null;
  kwaliteitsadvies_status: string | null;
}

export interface JobsResponse {
  items: Job[];
  totaal: number;
  pagina: number;
  per_pagina: number;
}

export interface WorkerConfig {
  baan: Lane;
  aantal: number;
  nice: number;
  stilte_start: string | null;
  stilte_einde: string | null;
  gepauzeerd: boolean;
  pauze_reden: string | null;
  veilige_max_workers: number;
  totaal_max_actieve_jobs: number;
  geheugen_totaal_bytes: number;
  geheugen_beschikbaar_bytes: number;
  veiligheidsuitleg: string;
  actieve_jobs: Array<{
    job_id: number;
    percent: number | null;
    fps: number | null;
    speed: number | null;
    eta_seconden: number | null;
    bitrate_kbps: number | null;
    resultaat_pad: string | null;
    bestand: string;
    grootte_bron: number;
    encoder: string | null;
  }>;
  historie: Job[];
}

export interface Statistics {
  totaal: { bespaard_bytes: number; gemiddeld: number; jobs: number };
  per_dag: Array<{ dag: string; bespaard_bytes: number; jobs: number }>;
  doorvoer_bytes_per_dag_7d: number;
  resterend_bytes: number;
  prognose_klaar: string | null;
}

export interface AiControllerStatus {
  ingeschakeld: boolean;
  modus: "schaduw" | "actief";
  provider: "openai";
  model: string;
  reasoning: string;
  maandbudget_usd: number;
  api_sleutel_ingesteld: boolean;
  laatste_fout: string | null;
  verbruik: {
    maand: string;
    input_tokens: number;
    output_tokens: number;
    kosten_usd: number;
    analyses: number;
  };
}

export interface AiAnalysis {
  id: number;
  soort: "besluitgroep" | "foutgroep";
  library_id: number | null;
  library_naam: string | null;
  besluit_id: number | null;
  job_id: number | null;
  provider: string;
  model: string;
  modus: "schaduw" | "actief";
  classificatie: "overeenkomstig" | "tijdelijk" | "mens_nodig" | "geblokkeerd" | null;
  voorgestelde_actie: "overslaan_bevestigen" | "besluit_goedkeuren" | "eenmaal_opnieuw" | "niets" | null;
  policy_uitkomst: string | null;
  uitgevoerd: number;
  input_tokens: number;
  output_tokens: number;
  kosten_microdollar: number;
  kosten_usd: number;
  fout: string | null;
  gemaakt_op: string;
  redencodes: string[];
  bewijsvelden: string[];
  vertrouwen: number | null;
  retry_aanpassing: "geen" | "cpu_geheugen_veilig" | "gpu_geheugen_veilig" | "doelbitrate_verlagen";
}

export interface AiDayStatistics {
  dag: string;
  analyses: number;
  beslissingen: number;
  overeenkomstig: number;
  tijdelijk: number;
  mens_nodig: number;
  geblokkeerd: number;
  provider_fouten: number;
  uitgevoerd: number;
  input_tokens: number;
  output_tokens: number;
  kosten_microdollar: number;
  kosten_usd: number;
}

export interface AiStatistics {
  dagen: number;
  tijdzone: string;
  totaal: Omit<AiDayStatistics, "dag" | "kosten_usd"> & { kosten_usd: number };
  per_dag: AiDayStatistics[];
}

export interface DecisionGroup {
  actie: string;
  baan: Lane | null;
  encoder: string | null;
  reden: string;
  status: string;
  library_id: number;
  nlink: number;
  aantal: number;
  bytes: number;
  eerste_id: number;
}

export interface DecisionItem {
  id: number;
  bestand_id: number;
  actie: string;
  baan: Lane | null;
  encoder: string | null;
  reden: string;
  status: string;
  pad: string;
  grootte: number;
  nlink: number;
  library_id: number;
  library_naam: string;
}

export interface Library {
  id: number;
  naam: string;
  pad: string;
  profiel_id: number;
  profiel_naam: string;
  scan_interval: number;
  scan_tijd: string;
  laatste_scan: string | null;
  volgende_scan: string | null;
  laatste_scan_status: "nooit" | "bezig" | "klaar" | "mislukt";
  laatste_scan_fout: string | null;
  actief: boolean;
  canary_status: string;
  aantal_bestanden: number;
  totale_grootte: number;
  bespaard_bytes: number;
  verwerkte_bron_bytes: number;
  verwerkt: number;
  stabiliteitswachtrij: number;
  laatste_scan_nieuw: number;
  laatste_scan_gewijzigd: number;
  laatste_scan_verdwenen: number;
  ai_goedgekeurd: number;
  handmatig_wachtend: number;
  ai_geblokkeerd: number;
  bron_type: "lokaal" | "smb";
  bron_uri: string | null;
  bron_controle: {
    geslaagd: boolean;
    atomisch_hernoemen: boolean;
    extended_acl: boolean;
    xattrs: boolean;
    waarschuwing: string | null;
  } | null;
  bron_gecontroleerd_op: string | null;
}

export interface Profile {
  id: number;
  naam: string;
  instellingen: Record<string, unknown>;
}

export interface ErrorGroup {
  foutsoort: string;
  fout: string;
  aantal: number;
  laatst: string;
}

export interface Verification {
  job_id: number;
  status: string;
  pad: string;
  hdr_bron: number;
  hdr_resultaat: number;
  dv_bron: number;
  dv_resultaat: number;
  md_gelijk: number;
  audio_bron: number;
  audio_resultaat: number;
  attachments_bron: number;
  attachments_resultaat: number;
  geslaagd: number;
  toelichting: string;
  vmaf_score: number | null;
  bron_frame: string | null;
  resultaat_frame: string | null;
}

export interface DolbyVisionItem {
  bestand_id: number;
  pad: string;
  dv_profile: number;
  dv_compat: number;
  is_hdr10: number;
  besluit_id: number | null;
  besluit_actie: string | null;
  besluit_status: string | null;
  reden: string | null;
  job_id: number | null;
  job_status: string | null;
  dv_bron: number | null;
  dv_resultaat: number | null;
  verificatie_geslaagd: number | null;
  verificatie_toelichting: string | null;
  controle_status:
    | "geverifieerd"
    | "afgekeurd"
    | "wacht_op_resultaat"
    | "veilig_overgeslagen"
    | "niet_ingepland";
}

export interface DolbyVisionOverview {
  totaal: number;
  profielen: Record<"5" | "7" | "8" | "overig", number>;
  items: DolbyVisionItem[];
  afgekapt: boolean;
  uitleg: string;
}

export interface QualityTrial {
  taak_id: string;
  status: "klaar" | "mislukt";
  crf: number;
  vmaf_score: number | null;
  grootte: number | null;
  duur_encode_seconden: number | null;
  toelichting: string;
  gemaakt_op: string;
  pad: string;
}

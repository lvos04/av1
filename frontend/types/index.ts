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
  gpu: {
    video_percent: number | null;
    render_percent: number | null;
    freq_mhz: number | null;
    power_w: number | null;
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
  actieve_jobs: Job[];
  historie: Job[];
}

export interface Statistics {
  totaal: { bespaard_bytes: number; gemiddeld: number; jobs: number };
  per_dag: Array<{ dag: string; bespaard_bytes: number; jobs: number }>;
  doorvoer_bytes_per_dag_7d: number;
  resterend_bytes: number;
  prognose_klaar: string | null;
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
  laatste_scan: string | null;
  actief: boolean;
  canary_status: string;
  aantal_bestanden: number;
  totale_grootte: number;
  verwerkt: number;
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

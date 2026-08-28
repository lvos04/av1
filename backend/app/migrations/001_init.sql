CREATE TABLE instelling (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    server_naam TEXT NOT NULL DEFAULT 'AV1 Pipeline',
    tijdzone TEXT NOT NULL DEFAULT 'Europe/Amsterdam',
    log_niveau TEXT NOT NULL DEFAULT 'info',
    cpu_max_percent INTEGER NOT NULL DEFAULT 90,
    cpu_max_temperatuur INTEGER NOT NULL DEFAULT 85,
    cache_waarschuwing_gb INTEGER NOT NULL DEFAULT 50,
    cache_pauze_gb INTEGER NOT NULL DEFAULT 10,
    hardlink_modus TEXT NOT NULL DEFAULT 'overslaan',
    meld_browser_fouten INTEGER NOT NULL DEFAULT 1,
    meld_browser_batches INTEGER NOT NULL DEFAULT 1,
    jellyfin_url TEXT,
    jellyfin_api_sleutel TEXT,
    pauzeer_cpu_bij_kijken INTEGER NOT NULL DEFAULT 0,
    radarr_url TEXT,
    radarr_api_sleutel TEXT,
    sonarr_url TEXT,
    sonarr_api_sleutel TEXT
);

INSERT INTO instelling(id) VALUES (1);

CREATE TABLE functie (
    sleutel TEXT PRIMARY KEY,
    ingeschakeld INTEGER NOT NULL DEFAULT 0 CHECK (ingeschakeld IN (0, 1)),
    configuratie TEXT NOT NULL DEFAULT '{}',
    laatste_fout TEXT,
    gewijzigd_op TEXT NOT NULL
);

INSERT INTO functie(sleutel, ingeschakeld, gewijzigd_op) VALUES
 ('gpu_monitor', 0, CURRENT_TIMESTAMP),
 ('vmaf', 0, CURRENT_TIMESTAMP),
 ('proefdraai', 0, CURRENT_TIMESTAMP),
 ('browsermeldingen', 0, CURRENT_TIMESTAMP),
 ('jellyfin_pauze', 0, CURRENT_TIMESTAMP),
 ('radarr', 0, CURRENT_TIMESTAMP),
 ('sonarr', 0, CURRENT_TIMESTAMP),
 ('externe_toegang', 0, CURRENT_TIMESTAMP),
 ('hardlink_verbreken', 0, CURRENT_TIMESTAMP),
 ('dv_experimenteel', 0, CURRENT_TIMESTAMP),
 ('automatische_backups', 1, CURRENT_TIMESTAMP),
 ('canary_limiet', 1, CURRENT_TIMESTAMP);

CREATE TABLE profiel (
    id INTEGER PRIMARY KEY,
    naam TEXT NOT NULL UNIQUE,
    inst TEXT NOT NULL
);

CREATE TABLE library (
    id INTEGER PRIMARY KEY,
    naam TEXT NOT NULL,
    pad TEXT NOT NULL UNIQUE,
    profiel_id INTEGER NOT NULL REFERENCES profiel(id),
    scan_interval INTEGER DEFAULT 86400,
    laatste_scan TEXT,
    scan_cursor TEXT,
    actief INTEGER DEFAULT 1,
    verwacht_device INTEGER,
    canary_status TEXT NOT NULL DEFAULT 'actief'
);

CREATE TABLE bestand (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES library(id),
    pad TEXT NOT NULL UNIQUE,
    grootte INTEGER NOT NULL,
    mtime REAL NOT NULL,
    nlink INTEGER DEFAULT 1,
    categorie TEXT NOT NULL,
    probe TEXT,
    gescand_op TEXT NOT NULL
);
CREATE INDEX idx_bestand_library ON bestand(library_id);
CREATE INDEX idx_bestand_cat ON bestand(categorie);

CREATE TABLE besluit (
    id INTEGER PRIMARY KEY,
    bestand_id INTEGER NOT NULL REFERENCES bestand(id) ON DELETE CASCADE,
    actie TEXT NOT NULL,
    baan TEXT,
    encoder TEXT,
    reden TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'nieuw',
    door_mens INTEGER DEFAULT 0,
    hardlink_bevestigd INTEGER NOT NULL DEFAULT 0,
    gemaakt_op TEXT NOT NULL
);
CREATE INDEX idx_besluit_status ON besluit(status);

CREATE TABLE job (
    id INTEGER PRIMARY KEY,
    besluit_id INTEGER NOT NULL REFERENCES besluit(id),
    bestand_id INTEGER NOT NULL REFERENCES bestand(id),
    baan TEXT NOT NULL,
    status TEXT NOT NULL,
    prioriteit INTEGER DEFAULT 0,
    worker_id TEXT,
    proces_pid INTEGER,
    commando TEXT,
    bron_probe TEXT,
    bron_grootte INTEGER,
    bron_mtime REAL,
    bron_bestandsmetadata TEXT,
    bron_device INTEGER,
    bron_inode INTEGER,
    resultaat_pad TEXT,
    resultaat_grootte INTEGER,
    cache_reservering INTEGER NOT NULL DEFAULT 0,
    percent REAL DEFAULT 0,
    fps REAL,
    speed REAL,
    bitrate_kbps REAL,
    eta_seconden INTEGER,
    fout TEXT,
    foutsoort TEXT,
    pogingen INTEGER NOT NULL DEFAULT 0,
    afbreken_gevraagd INTEGER NOT NULL DEFAULT 0,
    gestart_op TEXT,
    klaar_op TEXT
);
CREATE INDEX idx_job_status ON job(status, baan, prioriteit DESC);

CREATE TABLE verificatie (
    job_id INTEGER PRIMARY KEY REFERENCES job(id),
    hdr_bron INTEGER,
    hdr_resultaat INTEGER,
    dv_bron INTEGER,
    dv_resultaat INTEGER,
    md_gelijk INTEGER,
    audio_bron INTEGER,
    audio_resultaat INTEGER,
    attachments_bron INTEGER,
    attachments_resultaat INTEGER,
    geslaagd INTEGER NOT NULL,
    toelichting TEXT
);

CREATE TABLE kwaliteitsmeting (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES job(id),
    profiel_id INTEGER REFERENCES profiel(id),
    soort TEXT NOT NULL,
    start_seconde REAL,
    duur_seconden INTEGER,
    crf INTEGER,
    vmaf_score REAL,
    grootte INTEGER,
    duur_encode_seconden INTEGER,
    bron_frame TEXT,
    resultaat_frame TEXT,
    gemaakt_op TEXT NOT NULL
);

CREATE TABLE vervang_transactie (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES job(id),
    status TEXT NOT NULL,
    bron_pad TEXT NOT NULL,
    tijdelijk_pad TEXT,
    bron_device INTEGER NOT NULL,
    bron_inode INTEGER,
    bron_grootte INTEGER,
    bron_mtime REAL,
    resultaat_sha256 TEXT,
    gemaakt_op TEXT NOT NULL,
    gewijzigd_op TEXT NOT NULL,
    toelichting TEXT
);

CREATE TABLE auditlog (
    id INTEGER PRIMARY KEY,
    soort TEXT NOT NULL,
    onderwerp TEXT NOT NULL,
    oud TEXT,
    nieuw TEXT,
    tijd TEXT NOT NULL
);

CREATE TABLE backup_status (
    id INTEGER PRIMARY KEY,
    pad TEXT NOT NULL,
    grootte INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    gemaakt_op TEXT NOT NULL,
    gecontroleerd_op TEXT
);

CREATE TABLE logregel (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES job(id) ON DELETE CASCADE,
    niveau TEXT NOT NULL,
    tekst TEXT NOT NULL,
    tijd TEXT NOT NULL
);
CREATE INDEX idx_log_job ON logregel(job_id, id DESC);

CREATE TABLE besparing (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES job(id),
    library_id INTEGER REFERENCES library(id),
    bytes_voor INTEGER NOT NULL,
    bytes_na INTEGER NOT NULL,
    dag TEXT NOT NULL
);
CREATE INDEX idx_besparing_dag ON besparing(dag);

CREATE TABLE worker_config (
    baan TEXT PRIMARY KEY CHECK (baan IN ('cpu', 'gpu')),
    aantal INTEGER NOT NULL DEFAULT 1,
    nice INTEGER NOT NULL DEFAULT 0,
    stilte_start TEXT,
    stilte_einde TEXT,
    gepauzeerd INTEGER NOT NULL DEFAULT 0,
    pauze_reden TEXT
);
INSERT INTO worker_config(baan) VALUES ('cpu'), ('gpu');

CREATE TABLE scan_run (
    id INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES library(id),
    status TEXT NOT NULL,
    cursor TEXT,
    gevonden INTEGER NOT NULL DEFAULT 0,
    fout TEXT,
    gestart_op TEXT NOT NULL,
    klaar_op TEXT
);

# Datamodel

SQLite met WAL. Eén schrijver (de backend), meerdere lezers.

```sql
-- ── Libraries: je mediamappen ────────────────────────────────────────
CREATE TABLE library (
    id            INTEGER PRIMARY KEY,
    naam          TEXT NOT NULL,              -- "Anime", "Films"
    pad           TEXT NOT NULL UNIQUE,       -- "/library/anime"
    profiel_id    INTEGER NOT NULL REFERENCES profiel(id),
    scan_interval INTEGER DEFAULT 86400,      -- seconden, 0 = alleen handmatig
    laatste_scan  TIMESTAMP,
    actief        BOOLEAN DEFAULT 1
);

-- ── Algemene instellingen: één rij met id = 1 ───────────────────────
-- Paden blijven in /etc/av1-pipeline.env; deze tabel bevat bediening en
-- gedrag, geen geheimen.
CREATE TABLE instelling (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    server_naam           TEXT NOT NULL DEFAULT 'AV1 Pipeline',
    tijdzone              TEXT NOT NULL DEFAULT 'Europe/Amsterdam',
    log_niveau            TEXT NOT NULL DEFAULT 'info',
    cpu_max_percent       INTEGER NOT NULL DEFAULT 90,
    cpu_max_temperatuur   INTEGER NOT NULL DEFAULT 85,
    cache_waarschuwing_gb INTEGER NOT NULL DEFAULT 50,
    cache_pauze_gb        INTEGER NOT NULL DEFAULT 10,
    hardlink_modus        TEXT NOT NULL DEFAULT 'overslaan',
    meld_browser_fouten   BOOLEAN NOT NULL DEFAULT 1,
    meld_browser_batches  BOOLEAN NOT NULL DEFAULT 1,
    jellyfin_url          TEXT,
    jellyfin_api_sleutel  TEXT,               -- versleuteld opslaan
    pauzeer_cpu_bij_kijken BOOLEAN NOT NULL DEFAULT 0,
    radarr_url            TEXT,
    radarr_api_sleutel    TEXT,               -- versleuteld opslaan
    sonarr_url            TEXT,
    sonarr_api_sleutel    TEXT                -- versleuteld opslaan
);

-- ── Schakelaars: optionele onderdelen zijn hier direct aan/uit te zetten ──
-- Kernveiligheid (mountcontrole, verificatie, hersteltransactie en behoud van
-- bestandsmetadata) staat bewust niet in deze tabel en is nooit uitschakelbaar.
CREATE TABLE functie (
    sleutel        TEXT PRIMARY KEY,
    ingeschakeld   BOOLEAN NOT NULL DEFAULT 0,
    configuratie   TEXT NOT NULL DEFAULT '{}', -- JSON, gevalideerd per sleutel
    gewijzigd_op   TIMESTAMP NOT NULL
);
-- Initiële waarden: gpu_monitor=0, vmaf=0, proefdraai=0, browsermeldingen=0,
-- jellyfin_pauze=0, radarr=0, sonarr=0, externe_toegang=0,
-- hardlink_verbreken=0, dv_experimenteel=0, automatische_backups=1,
-- canary_limiet=1.

-- ── Profielen: de instellingen per library ───────────────────────────
-- Bewust een eigen tabel: anime wil CRF 32 en drempel 3000, films willen
-- CRF 27 en drempel 20000. Zie prompts/05 voor de UI.
CREATE TABLE profiel (
    id     INTEGER PRIMARY KEY,
    naam   TEXT NOT NULL UNIQUE,              -- "Anime", "Films 4K", "Series"
    inst   TEXT NOT NULL                      -- JSON, zie beslissen.STANDAARD
);

-- ── Bestanden: wat de scanner vindt ──────────────────────────────────
CREATE TABLE bestand (
    id           INTEGER PRIMARY KEY,
    library_id   INTEGER NOT NULL REFERENCES library(id),
    pad          TEXT NOT NULL UNIQUE,
    grootte      INTEGER NOT NULL,
    mtime        REAL NOT NULL,
    nlink        INTEGER DEFAULT 1,           -- >1 = hardlink, zie regel 9
    categorie    TEXT NOT NULL,               -- ok | extra | kort | geen_video
    probe        TEXT,                        -- JSON van probe.probe()
    gescand_op   TIMESTAMP NOT NULL
);
CREATE INDEX idx_bestand_library ON bestand(library_id);
CREATE INDEX idx_bestand_cat     ON bestand(categorie);

-- ── Besluiten: wat er zou moeten gebeuren ────────────────────────────
-- Losgekoppeld van de uitvoering. Zie veiligheidsregel 1.
CREATE TABLE besluit (
    id          INTEGER PRIMARY KEY,
    bestand_id  INTEGER NOT NULL REFERENCES bestand(id) ON DELETE CASCADE,
    actie       TEXT NOT NULL,                -- overslaan|opschonen|hercoderen|opruimen
    baan        TEXT,                         -- cpu | gpu
    encoder     TEXT,                         -- libsvtav1 | av1_qsv | handbrake
    reden       TEXT NOT NULL,
    payload     TEXT NOT NULL,                -- JSON van Besluit
    status      TEXT NOT NULL DEFAULT 'nieuw',-- nieuw|goedgekeurd|afgewezen|ingepland
    door_mens   BOOLEAN DEFAULT 0,            -- handmatig overruled?
    gemaakt_op  TIMESTAMP NOT NULL
);
CREATE INDEX idx_besluit_status ON besluit(status);

-- ── Jobs: wat er daadwerkelijk draait ────────────────────────────────
CREATE TABLE job (
    id             INTEGER PRIMARY KEY,
    besluit_id     INTEGER NOT NULL REFERENCES besluit(id),
    bestand_id     INTEGER NOT NULL REFERENCES bestand(id),
    baan           TEXT NOT NULL,
    status         TEXT NOT NULL,             -- wacht|bezig|verifieren|klaar|mislukt|afgebroken
    prioriteit     INTEGER DEFAULT 0,
    worker_id      TEXT,
    commando       TEXT,                      -- JSON-lijst, exact wat er draaide
    -- Metadata van het ORIGINEEL, vastgelegd voor de encode. Hiermee wordt
    -- geverifieerd. Zie veiligheidsregel 3.
    bron_probe     TEXT,
    bron_grootte   INTEGER,
    bron_bestandsmetadata TEXT,             -- JSON: uid, gid, mode, ACL, xattrs
    bron_device    INTEGER,
    bron_inode     INTEGER,
    resultaat_grootte INTEGER,
    percent        REAL DEFAULT 0,
    fps            REAL,
    speed          REAL,
    eta_seconden   INTEGER,
    fout           TEXT,
    pogingen       INTEGER NOT NULL DEFAULT 0,
    gestart_op     TIMESTAMP,
    klaar_op       TIMESTAMP
);
CREATE INDEX idx_job_status ON job(status, baan, prioriteit DESC);

-- ── Verificatie: is de metadata heel gebleven ────────────────────────
CREATE TABLE verificatie (
    job_id        INTEGER PRIMARY KEY REFERENCES job(id),
    hdr_bron      BOOLEAN, hdr_resultaat      BOOLEAN,
    dv_bron       INTEGER, dv_resultaat       INTEGER,
    md_gelijk     BOOLEAN,
    geslaagd      BOOLEAN NOT NULL,
    toelichting   TEXT
);

-- ── Beheer, herstel en kwaliteitsmetingen ───────────────────────────
CREATE TABLE kwaliteitsmeting (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER REFERENCES job(id),
    profiel_id   INTEGER REFERENCES profiel(id),
    soort        TEXT NOT NULL,              -- vmaf | proefdraai
    start_seconde REAL,
    duur_seconden INTEGER,
    crf          INTEGER,
    vmaf_score   REAL,
    grootte      INTEGER,
    duur_encode_seconden INTEGER,
    bron_frame   TEXT,
    resultaat_frame TEXT,
    gemaakt_op   TIMESTAMP NOT NULL
);

-- ── Herstel en audit ─────────────────────────────────────────────────
CREATE TABLE vervang_transactie (
    id                INTEGER PRIMARY KEY,
    job_id            INTEGER NOT NULL UNIQUE REFERENCES job(id),
    status            TEXT NOT NULL, -- gestart|resultaat_gekopieerd|bron_verplaatst|vervangen|hersteld
    bron_pad          TEXT NOT NULL,
    tijdelijk_pad     TEXT,
    bron_device       INTEGER NOT NULL,
    gemaakt_op        TIMESTAMP NOT NULL,
    gewijzigd_op      TIMESTAMP NOT NULL,
    toelichting       TEXT
);

CREATE TABLE auditlog (
    id          INTEGER PRIMARY KEY,
    soort       TEXT NOT NULL,              -- besluit|instelling|herstel|integratie
    onderwerp   TEXT NOT NULL,
    oud         TEXT,
    nieuw       TEXT,
    tijd        TIMESTAMP NOT NULL
);

CREATE TABLE backup_status (
    id          INTEGER PRIMARY KEY,
    pad         TEXT NOT NULL,
    grootte     INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    gemaakt_op  TIMESTAMP NOT NULL,
    gecontroleerd_op TIMESTAMP
);

-- ── Logs ─────────────────────────────────────────────────────────────
-- Alleen de laatste N regels per job in de database; de volledige uitvoer
-- gaat naar CONFIG_ROOT/logs/job-<id>.log
CREATE TABLE logregel (
    id        INTEGER PRIMARY KEY,
    job_id    INTEGER REFERENCES job(id) ON DELETE CASCADE,
    niveau    TEXT NOT NULL,                  -- info | waarschuwing | fout
    tekst     TEXT NOT NULL,
    tijd      TIMESTAMP NOT NULL
);
CREATE INDEX idx_log_job ON logregel(job_id, id DESC);

-- ── Geschiedenis: voor de besparingsteller ───────────────────────────
CREATE TABLE besparing (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER REFERENCES job(id),
    library_id  INTEGER REFERENCES library(id),
    bytes_voor  INTEGER NOT NULL,
    bytes_na    INTEGER NOT NULL,
    dag         DATE NOT NULL
);
CREATE INDEX idx_besparing_dag ON besparing(dag);
```

## Twee dingen die je makkelijk fout doet

**`bron_probe` op de job, niet op het bestand.** Het bestand verandert; de job
moet weten hoe het eruitzag toen hij begon. Zonder dit kun je niet verifiëren.

**`mtime` en `grootte` op het bestand bijhouden.** Vlak vóór de vervanging
controleer je of ze nog kloppen. Zo niet, dan heeft iets anders het bestand
aangeraakt en breek je af (veiligheidsregel 11).

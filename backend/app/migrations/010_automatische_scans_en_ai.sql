ALTER TABLE library ADD COLUMN scan_tijd TEXT NOT NULL DEFAULT '03:00';
ALTER TABLE library ADD COLUMN volgende_scan TEXT;
ALTER TABLE library ADD COLUMN laatste_scan_status TEXT NOT NULL DEFAULT 'nooit';
ALTER TABLE library ADD COLUMN laatste_scan_fout TEXT;

UPDATE library SET scan_interval=86400 WHERE scan_interval IS NULL;

ALTER TABLE bestand ADD COLUMN device INTEGER;
ALTER TABLE bestand ADD COLUMN inode INTEGER;
ALTER TABLE bestand ADD COLUMN aanwezig INTEGER NOT NULL DEFAULT 1
    CHECK (aanwezig IN (0, 1));
ALTER TABLE bestand ADD COLUMN eerste_gezien TEXT;
ALTER TABLE bestand ADD COLUMN stabiel_vanaf TEXT;
ALTER TABLE bestand ADD COLUMN scan_status TEXT NOT NULL DEFAULT 'gereed';
ALTER TABLE bestand ADD COLUMN profiel_fingerprint TEXT;

UPDATE bestand
SET eerste_gezien=gescand_op,
    stabiel_vanaf=gescand_op,
    scan_status='gereed'
WHERE eerste_gezien IS NULL;

-- Een crash of een oude dubbele API-aanroep mag de nieuwe unieke claim niet
-- blokkeren. Alleen de nieuwste hervatbare scan blijft actief.
UPDATE scan_run
SET status='mislukt',
    fout='dubbele oude scanclaim bij migratie afgesloten',
    klaar_op=CURRENT_TIMESTAMP
WHERE status='bezig'
  AND id NOT IN (
      SELECT MAX(nieuwste.id)
      FROM scan_run AS nieuwste
      WHERE nieuwste.status='bezig'
      GROUP BY nieuwste.library_id
  );

CREATE UNIQUE INDEX idx_scan_een_actief_per_library
ON scan_run(library_id)
WHERE status='bezig';

CREATE TABLE ai_analyse (
    id INTEGER PRIMARY KEY,
    soort TEXT NOT NULL CHECK (soort IN ('besluitgroep', 'foutgroep')),
    fingerprint TEXT NOT NULL,
    library_id INTEGER REFERENCES library(id),
    besluit_id INTEGER REFERENCES besluit(id),
    job_id INTEGER REFERENCES job(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_versie TEXT NOT NULL,
    modus TEXT NOT NULL CHECK (modus IN ('schaduw', 'actief')),
    invoer_hash TEXT NOT NULL,
    classificatie TEXT,
    voorgestelde_actie TEXT,
    voorstel TEXT,
    policy_uitkomst TEXT,
    uitgevoerd INTEGER NOT NULL DEFAULT 0 CHECK (uitgevoerd IN (0, 1)),
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    kosten_microdollar INTEGER NOT NULL DEFAULT 0,
    fout TEXT,
    gemaakt_op TEXT NOT NULL
);
CREATE INDEX idx_ai_analyse_fingerprint
ON ai_analyse(soort, fingerprint, prompt_versie, id DESC);
CREATE INDEX idx_ai_analyse_maand
ON ai_analyse(gemaakt_op, kosten_microdollar);

ALTER TABLE besluit ADD COLUMN goedkeuringsbron TEXT;
ALTER TABLE besluit ADD COLUMN ai_analyse_id INTEGER REFERENCES ai_analyse(id);
ALTER TABLE job ADD COLUMN automatische_retry_pogingen INTEGER NOT NULL DEFAULT 0;

CREATE TABLE job_poging (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    poging INTEGER NOT NULL,
    status TEXT NOT NULL,
    fout TEXT,
    foutsoort TEXT,
    fout_handtekening TEXT,
    gestart_op TEXT,
    klaar_op TEXT,
    ai_analyse_id INTEGER REFERENCES ai_analyse(id),
    UNIQUE(job_id, poging)
);
CREATE INDEX idx_job_poging_fout
ON job_poging(fout_handtekening, status, id DESC);

INSERT INTO functie(sleutel, ingeschakeld, configuratie, gewijzigd_op)
VALUES (
    'ai_controller',
    0,
    '{"modus":"schaduw","provider":"openai","model":"gpt-5.6-luna","reasoning":"medium","maandbudget_usd":10,"hdr10_vrijgegeven":false,"prompt_versie":"1"}',
    CURRENT_TIMESTAMP
);

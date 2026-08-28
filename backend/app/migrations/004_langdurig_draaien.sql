ALTER TABLE job ADD COLUMN laatste_fout_handtekening TEXT;
ALTER TABLE job ADD COLUMN zelfde_fout_pogingen INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_job_foutreeks ON job(status, laatste_fout_handtekening, zelfde_fout_pogingen);

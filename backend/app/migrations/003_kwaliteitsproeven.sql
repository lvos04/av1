ALTER TABLE kwaliteitsmeting ADD COLUMN bestand_id INTEGER REFERENCES bestand(id);
ALTER TABLE kwaliteitsmeting ADD COLUMN taak_id TEXT;
ALTER TABLE kwaliteitsmeting ADD COLUMN status TEXT NOT NULL DEFAULT 'klaar';
ALTER TABLE kwaliteitsmeting ADD COLUMN toelichting TEXT;

CREATE INDEX idx_kwaliteit_taak ON kwaliteitsmeting(taak_id, crf);

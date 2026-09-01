ALTER TABLE library ADD COLUMN bron_type TEXT NOT NULL DEFAULT 'lokaal';
ALTER TABLE library ADD COLUMN bron_uri TEXT;
ALTER TABLE library ADD COLUMN bron_controle TEXT;
ALTER TABLE library ADD COLUMN bron_gecontroleerd_op TEXT;

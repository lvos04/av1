-- Deze velden stonden kort in een al toegepaste migratie 010. Een nieuwe
-- migratie is nodig: eenmaal geregistreerde migraties worden bewust nooit
-- stil opnieuw uitgevoerd op een bestaande mediacatalogus.
ALTER TABLE scan_run ADD COLUMN nieuw INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scan_run ADD COLUMN gewijzigd INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scan_run ADD COLUMN verdwenen INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scan_run ADD COLUMN uitgesteld INTEGER NOT NULL DEFAULT 0;

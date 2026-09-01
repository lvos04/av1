-- De eigenaar kiest bewust hoeveel media-encodes totaal tegelijk mogen lopen.
-- Eén blijft de veilige standaard op de huidige 15-GiB-host. De eigenaar kan
-- de waarde later zonder herinstallatie verhogen wanneer er meer RAM is.
ALTER TABLE instelling ADD COLUMN max_gelijktijdige_encodes INTEGER NOT NULL DEFAULT 1
    CHECK (max_gelijktijdige_encodes >= 1);

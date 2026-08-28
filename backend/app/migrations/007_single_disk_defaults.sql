-- Alleen de oude ongewijzigde pakketstandaard wordt veiliger gemaakt. Een
-- bewust eerder aangepaste grens blijft van de gebruiker.
UPDATE instelling
SET cache_waarschuwing_gb = 250,
    cache_pauze_gb = 150
WHERE cache_waarschuwing_gb = 50
  AND cache_pauze_gb = 10;

-- Een eerdere scan kon voor een onveranderd bestand een tweede besluit/job maken.
-- Bewaar een geslaagde job altijd; anders blijft alleen de oudste wachtende of
-- actieve job uitvoerbaar. Historie blijft bestaan voor diagnose.
UPDATE job
SET status='overgeslagen',
    fout='dubbele job veilig overgeslagen; bestand is al door een eerdere job voltooid',
    foutsoort='DuplicateJob',
    proces_pid=NULL,
    worker_id=NULL,
    cache_reservering=0,
    klaar_op=CURRENT_TIMESTAMP
WHERE status IN ('wacht','bezig','verifieren')
  AND EXISTS (
      SELECT 1 FROM job AS voltooid
      WHERE voltooid.bestand_id=job.bestand_id
        AND voltooid.status='klaar'
  );

UPDATE job
SET status='overgeslagen',
    fout='dubbele job veilig overgeslagen; oudste wachtrij-item blijft behouden',
    foutsoort='DuplicateJob',
    proces_pid=NULL,
    worker_id=NULL,
    cache_reservering=0,
    klaar_op=CURRENT_TIMESTAMP
WHERE status IN ('wacht','bezig','verifieren')
  AND id NOT IN (
      SELECT MIN(oudste.id)
      FROM job AS oudste
      WHERE oudste.status IN ('wacht','bezig','verifieren')
      GROUP BY oudste.bestand_id
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_job_een_actief_per_bestand
ON job(bestand_id)
WHERE status IN ('wacht','bezig','verifieren');

-- Twee gelijktijdige 4K-SVT-processen overschreden op de 15-GB-host het
-- werkgeheugen. Eén CPU-job plus één QSV-job blijft de veilige indeling.
UPDATE worker_config SET aantal=1 WHERE baan='cpu' AND aantal>1;

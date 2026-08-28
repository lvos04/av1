UPDATE functie
SET configuratie='{"max_bestanden":50,"max_gb":100,"max_jobs_per_dag":10}'
WHERE sleutel='canary_limiet' AND configuratie='{}';

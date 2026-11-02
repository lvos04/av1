-- AI-herstel mag uitsluitend instellingen van de ene mislukte job aanpassen.
-- Het gedeelde profiel en eerder goedgekeurde mediabesluit blijven ongewijzigd.
ALTER TABLE job ADD COLUMN ai_retry_aanpassing TEXT NOT NULL DEFAULT 'geen'
    CHECK (ai_retry_aanpassing IN (
        'geen','cpu_geheugen_veilig','gpu_geheugen_veilig','doelbitrate_verlagen'
    ));
ALTER TABLE job ADD COLUMN ai_encode_override TEXT NOT NULL DEFAULT '{}';

UPDATE functie
SET configuratie=json_set(
        configuratie,
        '$.model','gpt-6-luna',
        '$.prompt_versie','2',
        '$.geheugenherstel_ingeschakeld',json('true')
    ),
    laatste_fout=NULL,
    gewijzigd_op=CURRENT_TIMESTAMP
WHERE sleutel='ai_controller';

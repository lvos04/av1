CREATE TABLE geheim (
    sleutel TEXT PRIMARY KEY,
    ciphertext TEXT NOT NULL,
    gewijzigd_op TEXT NOT NULL
);

CREATE TABLE onderhoud_status (
    sleutel TEXT PRIMARY KEY,
    laatst_op TEXT,
    resultaat TEXT
);

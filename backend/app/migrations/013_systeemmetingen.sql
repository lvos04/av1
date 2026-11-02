CREATE TABLE systeem_meting (
    id INTEGER PRIMARY KEY,
    gemeten_op TEXT NOT NULL,
    cpu_percent REAL NOT NULL,
    geheugen_percent REAL NOT NULL,
    geheugen_gebruikt_bytes INTEGER NOT NULL,
    gpu_video_percent REAL,
    gpu_render_percent REAL,
    gpu_power_w REAL,
    package_power_w REAL,
    platform_power_w REAL
);

CREATE INDEX idx_systeem_meting_tijd ON systeem_meting(gemeten_op);

-- Skema generik Fase 7 §2.2: host tidak tahu arti metrik/event, semuanya dari manifest modul.
PRAGMA journal_mode = WAL;          -- tulis (writer) & baca (dashboard) bersamaan
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS metric_minute (
    camera_id   TEXT NOT NULL,
    module_id   TEXT NOT NULL,
    metric_key  TEXT NOT NULL,
    region_id   TEXT NOT NULL DEFAULT '',   -- '' = metrik tingkat kamera
    minute_ts   INTEGER NOT NULL,           -- epoch dibulatkan ke menit
    avg REAL, min REAL, max REAL, last REAL,
    n           INTEGER NOT NULL,
    PRIMARY KEY (camera_id, module_id, metric_key, region_id, minute_ts)
);
CREATE INDEX IF NOT EXISTS ix_metric_time ON metric_minute(minute_ts);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    camera_id   TEXT NOT NULL,
    module_id   TEXT NOT NULL,
    type        TEXT NOT NULL,
    region_id   TEXT,
    track_id    INTEGER,
    ts          REAL NOT NULL,              -- epoch detik
    data        TEXT                        -- JSON, maks 2 KB
);
CREATE INDEX IF NOT EXISTS ix_events_cam_type_time ON events(camera_id, type, ts);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(ts);

CREATE TABLE IF NOT EXISTS heat_hour (
    camera_id   TEXT NOT NULL,
    module_id   TEXT NOT NULL,
    hour_ts     INTEGER NOT NULL,
    rows INTEGER NOT NULL, cols INTEGER NOT NULL,
    grid        BLOB NOT NULL,              -- float32 zlib
    PRIMARY KEY (camera_id, module_id, hour_ts)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, camera_id TEXT NOT NULL, rule_id TEXT NOT NULL,
    ts REAL NOT NULL, severity TEXT NOT NULL DEFAULT 'warning',
    message TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_alerts_time ON alerts(ts);

CREATE TABLE IF NOT EXISTS camera_health (       -- juga sumber data Fase 9–10
    camera_id   TEXT NOT NULL,
    minute_ts   INTEGER NOT NULL,
    state       TEXT NOT NULL,
    recv_fps REAL, proc_fps REAL, dropped_rate REAL,
    detect_ms REAL, module_ms REAL, latency_ms REAL,
    reconnects  INTEGER,
    PRIMARY KEY (camera_id, minute_ts)
);

CREATE TABLE IF NOT EXISTS system_minute (       -- pemakaian sumber daya PC
    minute_ts INTEGER PRIMARY KEY,
    cpu_pct REAL, ram_mb REAL, gpu_pct REAL, vram_mb REAL, cams_active INTEGER
);

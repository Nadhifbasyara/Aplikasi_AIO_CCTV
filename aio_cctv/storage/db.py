"""Koneksi SQLite, inisialisasi skema, dan retensi data (Fase 7 §2, §3.7)."""
import sqlite3
import time
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
SCHEMA_VERSION = 1


def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    """Writer memakai satu koneksi tulis; dashboard/laporan membuka koneksi baca sendiri."""
    if readonly:
        con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, timeout=5)
    else:
        con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def init_db(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = connect(path)
    try:
        con.executescript(SCHEMA)
        if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
    finally:
        con.close()


def purge(path: str | Path, days: int, now: float | None = None) -> dict[str, int]:
    """Hapus data mentah lebih tua dari `days` hari; 0 = simpan selamanya."""
    if days <= 0 or not Path(path).exists():
        return {}
    cutoff = (now or time.time()) - days * 86400
    deleted = {}
    con = connect(path)
    try:
        with con:
            for table, col in (("events", "ts"), ("alerts", "ts"),
                               ("metric_minute", "minute_ts"), ("heat_hour", "hour_ts"),
                               ("camera_health", "minute_ts"), ("system_minute", "minute_ts")):
                deleted[table] = con.execute(f"DELETE FROM {table} WHERE {col} < ?",
                                             (cutoff,)).rowcount
    finally:
        con.close()
    return deleted

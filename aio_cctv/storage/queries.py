"""Kueri baca untuk dashboard & laporan (Fase 7 §3.4). Setiap fungsi membuka koneksi
read-only sendiri, jadi aman dipanggil dari thread pool sementara writer menulis."""
from __future__ import annotations

import json
import time
import zlib
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path

import numpy as np

from aio_cctv.storage.db import connect


def _q(db: str | Path, sql: str, params: tuple = ()) -> list:
    if not Path(db).exists():
        return []
    with closing(connect(db, readonly=True)) as con:
        return con.execute(sql, params).fetchall()


def _combine(rows: list, agg: str) -> float:
    """Gabungkan baris metric_minute (urut waktu) sesuai `agg` manifest."""
    if agg == "max":
        return max(r["max"] for r in rows)
    if agg == "last":
        return rows[-1]["last"]
    if agg == "sum":
        return sum(r["avg"] * r["n"] for r in rows)
    total = sum(r["n"] for r in rows)                     # avg tertimbang jumlah sampel
    return sum(r["avg"] * r["n"] for r in rows) / total if total else 0.0


def _metric_rows(db, cam: str, module: str, key: str, start: float, end: float) -> list:
    return _q(db, "SELECT region_id, minute_ts, avg, min, max, last, n FROM metric_minute "
                  "WHERE camera_id=? AND module_id=? AND metric_key=? AND minute_ts BETWEEN ? AND ? "
                  "ORDER BY minute_ts", (cam, module, key, int(start), int(end)))


def metric_summary(db, cam, module, key, start, end, agg="avg") -> dict[str, float]:
    """{region_id: nilai} untuk seluruh rentang; '' = metrik tingkat kamera."""
    by_region = defaultdict(list)
    for r in _metric_rows(db, cam, module, key, start, end):
        by_region[r["region_id"]].append(r)
    return {reg: _combine(rows, agg) for reg, rows in by_region.items()}


def metric_series(db, cam, module, key, start, end, agg="avg",
                  bucket_s: int = 60) -> dict[str, tuple[list[float], list[float]]]:
    """{region_id: (epoch[], nilai[])} dengan bucket per menit (60) atau per jam (3600)."""
    buckets: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for r in _metric_rows(db, cam, module, key, start, end):
        buckets[r["region_id"]][r["minute_ts"] // bucket_s * bucket_s].append(r)
    out = {}
    for reg, b in buckets.items():
        xs = sorted(b)
        out[reg] = ([float(x) for x in xs], [_combine(b[x], agg) for x in xs])
    return out


def metric_by_hour_of_day(db, cam, module, key, start, end, agg="avg") -> list[float]:
    """24 nilai (jam 00–23 waktu lokal), semua zona digabung — bahan "jam sibuk"."""
    hours: dict[int, list] = defaultdict(list)
    for r in _metric_rows(db, cam, module, key, start, end):
        hours[datetime.fromtimestamp(r["minute_ts"]).hour].append(r)
    return [_combine(hours[h], agg) if hours.get(h) else 0.0 for h in range(24)]


def events_by_hour_of_day(db, cam, etype, start, end) -> list[int]:
    counts = [0] * 24
    for (ts,) in _q(db, "SELECT ts FROM events WHERE camera_id=? AND type=? AND ts BETWEEN ? AND ?",
                    (cam, etype, start, end)):
        counts[datetime.fromtimestamp(ts).hour] += 1
    return counts


def event_counts(db, cam, start, end) -> dict[str, int]:
    return {r["type"]: r["n"] for r in _q(
        db, "SELECT type, COUNT(*) AS n FROM events WHERE camera_id=? AND ts BETWEEN ? AND ? "
            "GROUP BY type ORDER BY type", (cam, start, end))}


def recent_events(db, cam, etype, start, end, limit=200) -> list[dict]:
    rows = _q(db, "SELECT ts, type, region_id, track_id, data FROM events WHERE camera_id=? "
                  "AND (? IS NULL OR type=?) AND ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT ?",
              (cam, etype, etype, start, end, limit))
    return [{**dict(r), "data": json.loads(r["data"] or "{}")} for r in rows]


def heat_sum(db, cam, module, start, end) -> np.ndarray | None:
    total = None
    for r in _q(db, "SELECT rows, cols, grid FROM heat_hour WHERE camera_id=? AND module_id=? "
                    "AND hour_ts BETWEEN ? AND ?", (cam, module, int(start) // 3600 * 3600, int(end))):
        g = np.frombuffer(zlib.decompress(r["grid"]), dtype=np.float32).reshape(r["rows"], r["cols"])
        if total is None:
            total = g.copy()
        elif total.shape == g.shape:
            total += g
    return total


def alerts(db, start, end, cam: str | None = None, unacked_only=False, limit=500) -> list[dict]:
    rows = _q(db, "SELECT id, camera_id, rule_id, ts, severity, message, acknowledged FROM alerts "
                  "WHERE ts BETWEEN ? AND ? AND (? IS NULL OR camera_id=?) AND (?=0 OR acknowledged=0) "
                  "ORDER BY ts DESC LIMIT ?", (start, end, cam, cam, int(unacked_only), limit))
    return [dict(r) for r in rows]


def ack_alerts(db, ids: list[int]) -> None:
    with closing(connect(db)) as con, con:
        con.executemany("UPDATE alerts SET acknowledged=1 WHERE id=?", [(i,) for i in ids])


def health(db, cam, start, end) -> list[dict]:
    return [dict(r) for r in _q(
        db, "SELECT * FROM camera_health WHERE camera_id=? AND minute_ts BETWEEN ? AND ? "
            "ORDER BY minute_ts", (cam, int(start), int(end)))]


def uptime(db, cam, start, end) -> tuple[int, int]:
    """(menit streaming, menit tercatat) — kamera berhenti tidak dihitung sebagai downtime."""
    rows = health(db, cam, start, end)
    return sum(r["state"] in ("streaming", "file") for r in rows), len(rows)


def system(db, start, end) -> list[dict]:
    return [dict(r) for r in _q(db, "SELECT * FROM system_minute WHERE minute_ts BETWEEN ? AND ? "
                                    "ORDER BY minute_ts", (int(start), int(end)))]


def cameras_with_data(db, start, end) -> list[tuple[str, str]]:
    """[(camera_id, module_id)] yang punya metrik pada rentang itu."""
    return [(r[0], r[1]) for r in _q(
        db, "SELECT DISTINCT camera_id, module_id FROM metric_minute WHERE minute_ts BETWEEN ? AND ?",
        (int(start), int(end)))]


def day_range(days: int, now: float | None = None) -> tuple[float, float]:
    """(awal hari lokal `days-1` hari lalu, akhir hari ini). Akhirnya bukan "sekarang": file
    video yang diputar lebih cepat dari waktu nyata memberi timestamp sedikit di depan jam."""
    now = now or time.time()
    today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today.timestamp() - (days - 1) * 86400
    return start, today.timestamp() + 86400 - 1e-3

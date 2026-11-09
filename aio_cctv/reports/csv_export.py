"""Ekspor tabel mentah ke CSV (Fase 7 §3.6)."""
import csv
from contextlib import closing
from datetime import datetime
from pathlib import Path

from aio_cctv.storage.db import connect

TABLES = {   # nama file: (SQL, kolom waktu epoch)
    "metrik_per_menit": (("SELECT camera_id, module_id, metric_key, region_id, minute_ts, avg, "
                          "min, max, last, n FROM metric_minute WHERE minute_ts BETWEEN ? AND ? "
                          "AND camera_id IN ({cams}) ORDER BY camera_id, minute_ts"), "minute_ts"),
    "event": (("SELECT camera_id, module_id, type, region_id, track_id, ts, data FROM events "
               "WHERE ts BETWEEN ? AND ? AND camera_id IN ({cams}) ORDER BY ts"), "ts"),
    "alert": (("SELECT camera_id, rule_id, ts, severity, message, acknowledged FROM alerts "
               "WHERE ts BETWEEN ? AND ? AND camera_id IN ({cams}) ORDER BY ts"), "ts"),
    "kesehatan_kamera": (("SELECT * FROM camera_health WHERE minute_ts BETWEEN ? AND ? "
                          "AND camera_id IN ({cams}) ORDER BY camera_id, minute_ts"), "minute_ts"),
}


def export_csv(db: str | Path, out_dir: str | Path, cameras: list[str], start: float,
               end: float) -> list[Path]:
    """Satu file per tabel; kolom waktu epoch ditambah kolom waktu lokal yang terbaca."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(start).strftime("%Y%m%d") + "-" + \
        datetime.fromtimestamp(end).strftime("%Y%m%d")
    marks = ",".join("?" * len(cameras))
    files = []
    with closing(connect(db, readonly=True)) as con:
        for name, (sql, time_col) in TABLES.items():
            cur = con.execute(sql.format(cams=marks), (start, end, *cameras))
            cols = [c[0] for c in cur.description]
            ti = cols.index(time_col)
            path = out_dir / f"aio-cctv_{name}_{stamp}.csv"
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow([*cols, "waktu_lokal"])
                for row in cur:
                    w.writerow([*row, datetime.fromtimestamp(row[ti]).strftime("%Y-%m-%d %H:%M:%S")])
            files.append(path)
    return files

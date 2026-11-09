"""Data dashboard per kamera (Fase 7 §3.4), dipakai bersama halaman Analitik desktop dan
dashboard web (Fase 8 §8) — angka keduanya berasal dari fungsi yang sama."""
from aio_cctv.storage import queries as q

AGG_LABELS = {"avg": "rata-rata", "max": "maks", "last": "nilai akhir", "sum": "total"}
DURATION_BINS = [(10, "<10 dtk"), (30, "10–30 dtk"), (60, "30 dtk–1 mnt"), (300, "1–5 mnt"),
                 (900, "5–15 mnt"), (float("inf"), ">15 mnt")]


def is_seconds(field: str) -> bool:
    """Konvensi kontrak: nama field berakhiran _s = detik (mis. duration_s)."""
    return field.endswith("_s")


def fmt_seconds(sec: float) -> str:
    sec = int(round(sec))
    if sec < 60:
        return f"{sec} dtk"
    if sec < 3600:
        return f"{sec // 60} mnt {sec % 60:02d} dtk"
    return f"{sec // 3600} jam {sec % 3600 // 60:02d} mnt"


def field_stats(values_by_region: dict[str, list[float]], field: str) -> dict:
    """Ringkasan generik satu field angka event: per zona + sebaran (histogram) gabungan."""
    import statistics

    regions = {r: {"n": len(v), "avg": sum(v) / len(v), "median": statistics.median(v),
                   "max": max(v), "min": min(v)} for r, v in values_by_region.items() if v}
    allv = [x for v in values_by_region.values() for x in v]
    if is_seconds(field):
        edges = DURATION_BINS
    elif allv:                                       # field bukan detik: 6 kelas sama lebar
        lo, hi = min(allv), max(allv)
        step = (hi - lo) / 6 or 1
        edges = [(lo + step * (i + 1) if i < 5 else float("inf"),
                  f"{lo + step * i:.3g}–{lo + step * (i + 1):.3g}") for i in range(6)]
    else:
        edges = []
    counts = [0] * len(edges)
    for x in allv:
        counts[next(i for i, (hi, _) in enumerate(edges) if x < hi)] += 1
    return {"regions": regions, "bins": [label for _, label in edges], "counts": counts,
            "seconds": is_seconds(field)}


def with_gaps(xs: list[float], ys: list[float], bucket_s: float) -> tuple[list, list]:
    """Sisipkan NaN di antara titik yang berjarak > 2 bucket, supaya grafik garis TERPUTUS
    saat tidak ada data (kamera/aplikasi mati), bukan ditarik lurus melintasi celah."""
    out_x, out_y = [], []
    for i, (x, y) in enumerate(zip(xs, ys)):
        if i and x - xs[i - 1] > 2 * bucket_s:
            out_x.append((x + xs[i - 1]) / 2)
            out_y.append(float("nan"))
        out_x.append(x)
        out_y.append(y)
    return out_x, out_y


def default_widgets(manifest) -> list[dict]:
    """Bila modul tidak mengisi `dashboard`: KPI metrik kamera, grafik metrik per zona,
    jam sibuk per event."""
    out = [{"type": "kpi", "metric": m.key} for m in manifest.metrics if not m.per_region][:3]
    out += [{"type": "timeseries", "metric": m.key} for m in manifest.metrics if m.per_region][:2]
    out += [{"type": "hourly", "event": e.type} for e in manifest.events][:2]
    out += [{"type": "event_stats", "event": e.type, "field": f} for e in manifest.events
            for f, kind in e.fields.items() if kind in ("float", "int") and is_seconds(f)][:2]
    return out


def load_dashboard(db, cam_id: str, manifest, widgets: list[dict], start: float,
                   end: float, live: list | None = None) -> list[dict]:
    """Dijalankan di thread pool. Mengembalikan data siap gambar untuk tiap widget.

    live: [[key, region_id, nilai]] dari statistik worker yang sedang berjalan (KPI "sekarang").
    """
    bucket = 60 if end - start <= 86400 + 1 else 3600
    now = {(k, r or ""): v for k, r, v in (live or [])}
    out = []
    for w in widgets:
        kind, d = w.get("type"), {"spec": w}
        spec = manifest.metric(w["metric"]) if w.get("metric") else None
        agg = spec.agg if spec else "avg"
        if kind == "kpi" and spec:
            d["values"] = q.metric_summary(db, cam_id, manifest.id, spec.key, start, end, agg)
            d["max"] = q.metric_summary(db, cam_id, manifest.id, spec.key, start, end, "max")
            d["latest"] = q.metric_latest(db, cam_id, manifest.id, spec.key, start, end)
            d["now"] = {r: v for (k, r), v in now.items() if k == spec.key}
        elif kind == "timeseries" and spec:
            d["series"] = q.metric_series(db, cam_id, manifest.id, spec.key, start, end, agg, bucket)
            d["bucket_s"] = bucket
        elif kind == "hourly":
            d["bars"] = (q.events_by_hour_of_day(db, cam_id, w["event"], start, end)
                         if w.get("event") else
                         q.metric_by_hour_of_day(db, cam_id, manifest.id, spec.key, start, end, agg)
                         if spec else [0] * 24)
        elif kind == "event_stats" and w.get("event") and w.get("field"):
            d.update(field_stats(q.event_field_values(db, cam_id, w["event"], w["field"],
                                                      start, end), w["field"]))
        elif kind == "events":
            d["rows"] = q.recent_events(db, cam_id, w.get("event"), start, end, limit=200)
        elif kind == "heatmap":
            d["heat"] = q.heat_sum(db, cam_id, manifest.id, start, end)
        out.append(d)
    on, total = q.uptime(db, cam_id, start, end)
    out.append({"spec": {"type": "health"}, "health": q.health(db, cam_id, start, end),
                "uptime": (on, total), "counts": q.event_counts(db, cam_id, start, end)})
    return out


class LiveTail:
    """Titik "menit yang sedang berjalan" untuk grafik per menit.

    Nilai live (sesaat) dikumpulkan per menit lalu diringkas dengan `agg` yang sama seperti
    database, sehingga ujung grafik bergerak tiap detik tetapi tetap bermakna "rata-rata menit
    ini" — setelah menit selesai, nilainya sama dengan yang ditulis Aggregator.
    """

    def __init__(self, agg: str = "avg", keep_s: float = 300):
        self.agg, self.keep_s = agg, keep_s
        self._samples: dict[str, dict[int, list[float]]] = {}

    def add(self, region: str, value: float, wall_ts: float) -> None:
        minute = int(wall_ts // 60 * 60)
        per = self._samples.setdefault(region, {})
        per.setdefault(minute, []).append(float(value))
        for m in [m for m in per if m < wall_ts - self.keep_s]:
            del per[m]

    def points(self, region: str, after: float = float("-inf")) -> tuple[list[float], list[float]]:
        """Titik menit (x = awal menit) yang belum ada di data tersimpan (x > after)."""
        xs, ys = [], []
        for m, vals in sorted(self._samples.get(region, {}).items()):
            if m > after:
                xs.append(float(m))
                ys.append({"max": max, "last": lambda v: v[-1], "sum": sum}.get(
                    self.agg, lambda v: sum(v) / len(v))(vals))
        return xs, ys


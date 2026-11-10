"""Laporan ringkas per periode (Fase 7 §3.6). `build_report()` murni Python (bisa dites tanpa Qt);
`html_to_pdf()` memakai QTextDocument + QPdfWriter, jadi tanpa dependensi tambahan."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape

import numpy as np

from aio_cctv.annotate import fmt_value, render_heat
from aio_cctv.modules.manifest import ModuleManifest
from aio_cctv.storage import queries as q
from aio_cctv.storage.dashboard_data import field_stats, fmt_seconds


@dataclass
class CameraInfo:
    id: str
    name: str
    manifest: ModuleManifest | None
    region_names: dict[str, str]


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d-%m-%Y %H:%M")


def _table(head: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>"
                   for r in rows)
    return f"<table><tr>{th}</tr>{body}</table>" if rows else "<p class='muted'>Tidak ada data.</p>"


# field cap waktu (posisi detik video / epoch): rata-ratanya tidak bermakna
TIME_FIELDS = {"start", "end", "ts", "t", "time", "timestamp"}


@dataclass
class CamReport:
    """Isi laporan satu kamera; dipakai bersama oleh HTML/PDF dan pratinjau di aplikasi."""
    info: CameraInfo
    perf: list[tuple[str, str]] = field(default_factory=list)        # (ukuran, nilai)
    metrics: list[list[str]] = field(default_factory=list)           # metrik, zona, nilai, agg, maks
    events: list[tuple[str, int]] = field(default_factory=list)      # (label, jumlah)
    per_hour: dict[str, list[int]] = field(default_factory=dict)     # label -> 24 jumlah per jam
    stats: list[list] = field(default_factory=list)                  # durasi & nilai event
    heat: np.ndarray | None = None                                   # citra BGR heatmap
    alerts: list[list[str]] = field(default_factory=list)            # waktu, aturan, tingkat, pesan


def collect(db, cameras: list[CameraInfo], start: float, end: float) -> list[CamReport]:
    out = []
    for cam in cameras:
        r = CamReport(cam)
        out.append(r)
        m = cam.manifest
        if m is None:
            continue
        on, total = q.uptime(db, cam.id, start, end)
        hl = q.health(db, cam.id, start, end)

        def avg(key):
            return f"{np.mean([x[key] or 0 for x in hl]):.1f}" if hl else "-"

        r.perf = [("Menit tercatat", str(total)),
                  ("Uptime", f"{100 * on / total:.1f}%" if total else "-"),
                  ("FPS proses rata-rata", avg("proc_fps")),
                  ("Detect", f"{avg('detect_ms')} ms" if hl else "-"),
                  ("Modul", f"{avg('module_ms')} ms" if hl else "-"),
                  ("Reconnect", str(max(x["reconnects"] or 0 for x in hl)) if hl else "-")]

        for spec in m.metrics:
            for reg, v in sorted(q.metric_summary(db, cam.id, m.id, spec.key, start, end,
                                                  spec.agg).items()):
                mx = q.metric_summary(db, cam.id, m.id, spec.key, start, end, "max").get(reg, v)
                r.metrics.append([spec.label or spec.key,
                                  cam.region_names.get(reg, reg) or "(kamera)",
                                  f"{fmt_value(v)} {spec.unit}".strip(), spec.agg,
                                  f"{fmt_value(mx)} {spec.unit}".strip()])

        labels = {e.type: e.label or e.type for e in m.events}
        counts = q.event_counts(db, cam.id, start, end)
        r.events = [(labels.get(t, t), n) for t, n in counts.items()]
        r.per_hour = {labels.get(t, t): q.events_by_hour_of_day(db, cam.id, t, start, end)
                      for t in counts}

        # field angka event yang dideklarasikan manifest (mis. lama tinggal = duration_s)
        for e in m.events:
            for fname, kind in e.fields.items():
                if kind not in ("float", "int") or fname in TIME_FIELDS:
                    continue
                st = field_stats(q.event_field_values(db, cam.id, e.type, fname, start, end), fname)
                fv = fmt_seconds if st["seconds"] else fmt_value
                r.stats += [[f"{labels.get(e.type, e.type)} · {fname}",
                             cam.region_names.get(reg, reg) or "(kamera)", s["n"], fv(s["avg"]),
                             fv(s["median"]), fv(s["max"])]
                            for reg, s in sorted(st["regions"].items())]

        if (heat := q.heat_sum(db, cam.id, m.id, start, end)) is not None:
            w = 480
            r.heat = render_heat(heat, (w, max(1, round(w * heat.shape[0] / heat.shape[1]))))
        r.alerts = [[_fmt_ts(a["ts"]), a["rule_id"], a["severity"], a["message"]]
                    for a in q.alerts(db, start, end, cam.id)[:50]]
    return out


def header(start: float, end: float) -> str:
    return f"Periode {_fmt_ts(start)} – {_fmt_ts(end)}"


def build_report(db, cameras: list[CameraInfo], start: float, end: float,
                 app_version: str = "") -> tuple[str, dict[str, np.ndarray]]:
    """(html, {nama_gambar: citra BGR}) — gambar dirujuk <img src="nama_gambar">."""
    return render_html(collect(db, cameras, start, end), start, end, app_version)


def render_html(reports: list[CamReport], start: float, end: float,
                app_version: str = "") -> tuple[str, dict[str, np.ndarray]]:
    images: dict[str, np.ndarray] = {}
    parts = [(f"<h1>Laporan Analitik CCTV</h1><p>{header(start, end)} · "
              f"dibuat {_fmt_ts(datetime.now().timestamp())}"
              f"{' · AIO-CCTV ' + escape(app_version) if app_version else ''}</p>")]
    for r in reports:
        m = r.info.manifest
        parts.append(f"<h2>{escape(r.info.name)}</h2>")
        if m is None:
            parts.append("<p class='muted'>Modul kamera ini tidak terpasang; metrik tidak bisa "
                         "diberi label.</p>")
            continue
        parts.append(f"<p>Modul: <b>{escape(m.name)}</b> ({escape(m.id)} {escape(m.version)}) · "
                     f"penulis: {escape(', '.join(m.authors) or '-')}</p>")
        # kinerja sebagai deretan kotak (satu baris tabel)
        cells = "".join(f"<td class='kpi'><span class='muted'>{escape(k)}</span><br>"
                        f"<b class='big'>{escape(v)}</b></td>" for k, v in r.perf)
        parts.append(f"<h3>Kinerja kamera</h3><table class='kpis'><tr>{cells}</tr></table>")
        parts.append("<h3>Metrik</h3>" + _table(["Metrik", "Zona", "Nilai", "Agregasi", "Maks"],
                                                r.metrics))
        parts.append("<h3>Event</h3>" + _table(["Event", "Jumlah"], [list(e) for e in r.events]))
        hour_rows = [[label, f"{h:02d}:00", n] for label, hours in r.per_hour.items()
                     for h, n in enumerate(hours) if n]
        if hour_rows:
            parts.append("<h3>Event per jam</h3>" + _table(["Event", "Jam", "Jumlah"], hour_rows))
        if r.stats:
            parts.append("<h3>Durasi & nilai event</h3>" + _table(
                ["Event · field", "Zona", "Jumlah", "Rata-rata", "Median", "Maks"], r.stats))
        if r.heat is not None:
            name = f"heat_{r.info.id}.png"
            images[name] = r.heat
            parts.append(f"<h3>Heatmap</h3><p><img src='{name}' width='{r.heat.shape[1]}'></p>")
        parts.append("<h3>Alert</h3>" + _table(["Waktu", "Aturan", "Tingkat", "Pesan"], r.alerts))

    style = ("<style>body{font-family:sans-serif;font-size:9pt} h1{font-size:16pt} "
             "h2{font-size:13pt;margin-top:14px} h3{font-size:10pt} table{border-collapse:collapse} "
             "th,td{border:1px solid #999;padding:2px 6px} th{background:#eee} .muted{color:#777}"
             " table.kpis td{border:1px solid #ccc;background:#f6f6f6;padding:4px 10px}"
             " .big{font-size:12pt}</style>")
    return f"<html><head>{style}</head><body>{''.join(parts)}</body></html>", images


def html_to_pdf(html: str, path: str, images: dict[str, np.ndarray] | None = None) -> None:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QPageSize, QPdfWriter, QTextDocument

    from aio_cctv.gui.utils import to_qimage

    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(150)
    doc = QTextDocument()
    for name, img in (images or {}).items():
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), to_qimage(img))
    doc.setHtml(html)
    doc.print(writer)

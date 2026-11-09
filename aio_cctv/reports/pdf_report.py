"""Laporan ringkas per periode (Fase 7 §3.6). `build_report()` murni Python (bisa dites tanpa Qt);
`html_to_pdf()` memakai QTextDocument + QPdfWriter, jadi tanpa dependensi tambahan."""
from __future__ import annotations

from dataclasses import dataclass
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


def build_report(db, cameras: list[CameraInfo], start: float, end: float,
                 app_version: str = "") -> tuple[str, dict[str, np.ndarray]]:
    """(html, {nama_gambar: citra BGR}) — gambar dirujuk <img src="nama_gambar">."""
    images: dict[str, np.ndarray] = {}
    parts = [(f"<h1>Laporan Analitik CCTV</h1><p>Periode {_fmt_ts(start)} – {_fmt_ts(end)} · "
              f"dibuat {_fmt_ts(datetime.now().timestamp())}"
              f"{' · AIO-CCTV ' + escape(app_version) if app_version else ''}</p>")]
    for cam in cameras:
        m = cam.manifest
        parts.append(f"<h2>{escape(cam.name)}</h2>")
        if m is None:
            parts.append("<p class='muted'>Modul kamera ini tidak terpasang; metrik tidak bisa "
                         "diberi label.</p>")
            continue
        parts.append(f"<p>Modul: <b>{escape(m.name)}</b> ({escape(m.id)} {escape(m.version)}) · "
                     f"penulis: {escape(', '.join(m.authors) or '-')}</p>")

        on, total = q.uptime(db, cam.id, start, end)
        hl = q.health(db, cam.id, start, end)
        perf = [["Menit tercatat", total],
                ["Uptime", f"{100 * on / total:.1f}%" if total else "-"]]
        if hl:
            for label, key in (("FPS proses rata-rata", "proc_fps"), ("Detect (ms)", "detect_ms"),
                               ("Modul (ms)", "module_ms")):
                perf.append([label, f"{np.mean([r[key] or 0 for r in hl]):.1f}"])
            perf.append(["Reconnect", max(r["reconnects"] or 0 for r in hl)])
        parts.append("<h3>Kinerja kamera</h3>" + _table(["Ukuran", "Nilai"], perf))

        rows = []
        for spec in m.metrics:
            for reg, v in sorted(q.metric_summary(db, cam.id, m.id, spec.key, start, end,
                                                  spec.agg).items()):
                mx = q.metric_summary(db, cam.id, m.id, spec.key, start, end, "max").get(reg, v)
                rows.append([spec.label or spec.key, cam.region_names.get(reg, reg) or "(kamera)",
                             f"{fmt_value(v)} {spec.unit}".strip(), spec.agg,
                             f"{fmt_value(mx)} {spec.unit}".strip()])
        parts.append("<h3>Metrik</h3>" + _table(["Metrik", "Zona", "Nilai", "Agregasi", "Maks"],
                                                rows))

        counts = q.event_counts(db, cam.id, start, end)
        labels = {e.type: e.label or e.type for e in m.events}
        parts.append("<h3>Event</h3>" + _table(["Event", "Jumlah"],
                                               [[labels.get(t, t), n] for t, n in counts.items()]))
        if counts:
            hour_rows = []
            for t in counts:
                per_hour = q.events_by_hour_of_day(db, cam.id, t, start, end)
                hour_rows += [[labels.get(t, t), f"{h:02d}:00", n]
                              for h, n in enumerate(per_hour) if n]
            parts.append("<h3>Event per jam</h3>" + _table(["Event", "Jam", "Jumlah"], hour_rows))

        # field angka event yang dideklarasikan manifest (mis. lama tinggal = duration_s)
        stat_rows = []
        for e in m.events:
            for field, kind in e.fields.items():
                if kind not in ("float", "int"):
                    continue
                st = field_stats(q.event_field_values(db, cam.id, e.type, field, start, end), field)
                fv = fmt_seconds if st["seconds"] else fmt_value
                stat_rows += [[f"{labels.get(e.type, e.type)} · {field}",
                               cam.region_names.get(reg, reg) or "(kamera)", s["n"], fv(s["avg"]),
                               fv(s["median"]), fv(s["max"])]
                              for reg, s in sorted(st["regions"].items())]
        if stat_rows:
            parts.append("<h3>Durasi & nilai event</h3>" + _table(
                ["Event · field", "Zona", "Jumlah", "Rata-rata", "Median", "Maks"], stat_rows))

        if (heat := q.heat_sum(db, cam.id, m.id, start, end)) is not None:
            name = f"heat_{cam.id}.png"
            w = 480
            images[name] = render_heat(heat, (w, max(1, round(w * heat.shape[0] / heat.shape[1]))))
            parts.append(f"<h3>Heatmap</h3><p><img src='{name}' width='{w}'></p>")

        al = q.alerts(db, start, end, cam.id)
        parts.append("<h3>Alert</h3>" + _table(
            ["Waktu", "Aturan", "Tingkat", "Pesan"],
            [[_fmt_ts(a["ts"]), a["rule_id"], a["severity"], a["message"]] for a in al[:50]]))

    style = ("<style>body{font-family:sans-serif;font-size:9pt} h1{font-size:16pt} "
             "h2{font-size:13pt;margin-top:14px} h3{font-size:10pt} table{border-collapse:collapse} "
             "th,td{border:1px solid #999;padding:2px 6px} th{background:#eee} .muted{color:#777}"
             "</style>")
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

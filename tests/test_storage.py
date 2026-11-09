"""Fase 7: writer, agregator, kueri, retensi, ekspor laporan. Tanpa GPU/video/Qt."""
import csv
import json
import time
from datetime import datetime

import numpy as np
import pytest
import supervision as sv

from aio_cctv.core.paths import BUNDLED_MODULES_DIR
from aio_cctv.modules.api import Event, Metric, ModuleResult
from aio_cctv.modules.manifest import load_manifest
from aio_cctv.reports.csv_export import export_csv
from aio_cctv.reports.pdf_report import CameraInfo, build_report
from aio_cctv.storage import queries as q
from aio_cctv.storage.aggregator import Aggregator, HealthRecorder, record_alert
from aio_cctv.storage.db import connect, purge
from aio_cctv.storage.writer import StorageWriter

T0 = datetime(2026, 11, 9, 10, 0, 0).timestamp()        # 10:00 waktu lokal, awal menit & jam


@pytest.fixture
def writer(tmp_path):
    w = StorageWriter(tmp_path / "a.db", flush_interval=0.05)
    w.start()
    yield w
    w.stop()


def res(metrics=(), events=(), heat=None):
    return ModuleResult(sv.Detections.empty(), list(metrics), list(events), heat=heat)


def rows(db, sql, params=()):
    con = connect(db, readonly=True)
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def test_minute_aggregation_and_flush_on_stop(writer):
    ag = Aggregator("c1", "m", writer)
    for i in range(90):                                   # 90 detik: menit 10:00 & 10:01
        ag.consume(res([Metric("n", i % 3, "z1")]), ts=float(i), wall_ts=T0 + i)
    ag.flush()
    writer.flush()
    r = rows(writer.db_path, "SELECT * FROM metric_minute ORDER BY minute_ts")
    assert [x["minute_ts"] for x in r] == [T0, T0 + 60]
    assert (r[0]["n"], r[0]["min"], r[0]["max"], r[0]["avg"]) == (60, 0, 2, 1.0)
    assert r[1]["n"] == 30 and r[1]["last"] == 89 % 3


def test_event_time_converted_from_source_domain(writer):
    """ts video 5,0 s pada frame ber-epoch T0+100: event ts 4,5 s -> epoch T0+99,5."""
    ag = Aggregator("c1", "m", writer)
    ag.consume(res(events=[Event("x", 4.5, "z1", 7, {"d": 1.5})]), ts=5.0, wall_ts=T0 + 100)
    writer.flush()
    (e,) = rows(writer.db_path, "SELECT * FROM events")
    assert e["ts"] == pytest.approx(T0 + 99.5) and e["track_id"] == 7
    assert json.loads(e["data"]) == {"d": 1.5}


def test_event_data_violating_contract_is_not_stored(writer):
    ag = Aggregator("c1", "m", writer)
    ag.consume(res(events=[Event("img", 0, data={"crop": np.zeros(3)}),
                           Event("big", 0, data={"s": "x" * 5000})]), 0, T0)
    writer.flush()
    data = [json.loads(r["data"]) for r in rows(writer.db_path, "SELECT data FROM events")]
    assert all("_ditolak" in d for d in data) and len(data) == 2


def test_heat_stored_as_delta_per_hour(writer):
    ag = Aggregator("c1", "m", writer)
    grid = np.zeros((2, 2), np.float32)
    for i in range(3):                                    # 09:59:58, 09:59:59, 10:00:00
        grid[0, 0] += 1
        ag.consume(res(heat=grid), ts=float(i), wall_ts=T0 - 2 + i)
    ag.flush()
    writer.flush()
    jam9 = q.heat_sum(writer.db_path, "c1", "m", T0 - 3600, T0 - 1)
    jam10 = q.heat_sum(writer.db_path, "c1", "m", T0, T0 + 10)
    assert jam9[0, 0] == 2 and jam10[0, 0] == 1           # tidak dihitung dobel


def test_health_recorder_averages_per_minute(writer):
    h = HealthRecorder("c1", writer)
    for i, fps in enumerate((10, 12, 14)):
        h.add({"state": "streaming", "proc_fps": fps, "detect_ms": 5, "reconnects": i}, T0 + i)
    h.add({"state": "reconnecting", "proc_fps": 0, "reconnects": 3}, T0 + 61)
    h.flush()
    writer.flush()
    r = q.health(writer.db_path, "c1", T0, T0 + 120)
    assert [(x["state"], x["proc_fps"], x["reconnects"]) for x in r] == \
        [("streaming", 12.0, 2), ("reconnecting", 0.0, 3)]
    assert q.uptime(writer.db_path, "c1", T0, T0 + 120) == (1, 2)


def test_writer_queue_full_drops_instead_of_blocking(tmp_path):
    w = StorageWriter(tmp_path / "b.db", max_queue=2)       # tidak di-start: antrean menumpuk
    for _ in range(5):
        w.put("SELECT 1", ())
    assert w.dropped == 3


def test_queries_agg_kinds_and_hour_of_day(writer):
    ag = Aggregator("c1", "m", writer)
    for i in range(120):                                  # menit 1: nilai 0..59, menit 2: 60..119
        ag.consume(res([Metric("v", i)]), float(i), T0 + i)
    for i in range(3):
        ag.consume(res(events=[Event("e", 0.0)]), 0.0, T0 + 3600 * i)   # jam 10, 11, 12
    ag.flush()
    writer.flush()
    db = writer.db_path
    s = {agg: q.metric_summary(db, "c1", "m", "v", T0, T0 + 600, agg)[""]
         for agg in ("avg", "max", "last", "sum")}
    assert s == {"avg": 59.5, "max": 119, "last": 119, "sum": sum(range(120))}
    per_hour = q.events_by_hour_of_day(db, "c1", "e", T0, T0 + 4 * 3600)
    assert per_hour[10:13] == [1, 1, 1] and sum(per_hour) == 3
    assert q.metric_series(db, "c1", "m", "v", T0, T0 + 600, "max", 3600)[""] == ([T0], [119])


def test_alerts_record_and_ack(writer):
    from aio_cctv.alerts.rules import Alert
    record_alert(writer, "c1", Alert("r1", "antrean", 0, "critical"), T0)
    record_alert(writer, "c2", Alert("r2", "offline", 0), T0 + 5)
    writer.flush()
    db = writer.db_path
    assert [a["rule_id"] for a in q.alerts(db, T0 - 1, T0 + 10)] == ["r2", "r1"]
    q.ack_alerts(db, [a["id"] for a in q.alerts(db, T0 - 1, T0 + 10, "c1")])
    assert [a["camera_id"] for a in q.alerts(db, T0 - 1, T0 + 10, unacked_only=True)] == ["c2"]


def test_purge_removes_only_old_rows(writer):
    ag = Aggregator("c1", "m", writer)
    now = time.time()
    ag.events([Event("lama", 0.0)], 0.0, now - 100 * 86400)
    ag.events([Event("baru", 0.0)], 0.0, now)
    writer.flush()
    assert purge(writer.db_path, 90, now)["events"] == 1
    assert purge(writer.db_path, 0, now) == {}             # 0 = simpan selamanya
    assert [r["type"] for r in rows(writer.db_path, "SELECT type FROM events")] == ["baru"]


def test_reports_use_manifest_labels(writer, tmp_path):
    m = load_manifest(BUNDLED_MODULES_DIR / "baseline")
    ag = Aggregator("c1", m.id, writer)
    for i in range(30):
        ag.consume(res([Metric("zone_count", 2, "z1")],
                       [Event("line_cross", float(i), "l1", i, {"direction": "in"})],
                       heat=np.ones((4, 4), np.float32) * i), float(i), T0 + i)
    ag.flush()
    writer.flush()
    html, images = build_report(writer.db_path,
                                [CameraInfo("c1", "Kasir", m, {"z1": "Area Kasir"})],
                                T0 - 60, T0 + 3600)
    assert "Orang" in html and "Area Kasir" in html and "Melintas garis" in html
    assert "heat_c1.png" in images and images["heat_c1.png"].ndim == 3
    files = export_csv(writer.db_path, tmp_path / "csv", ["c1"], T0 - 60, T0 + 3600)
    with open(next(f for f in files if "event" in f.name)) as fh:
        data = list(csv.DictReader(fh))
    assert len(data) == 30 and data[0]["waktu_lokal"].startswith("2026-11-09 10:00")


def test_heat_of_running_hour_visible_before_hour_ends(writer):
    ag = Aggregator("c1", "m", writer)
    grid = np.zeros((2, 2), np.float32)
    for i in range(70):                                   # 10:00:00–10:01:09, jam belum selesai
        grid[0, 0] += 1
        ag.consume(res(heat=grid), float(i), T0 + i)
    writer.flush()                                        # tanpa ag.flush(): kamera masih jalan
    assert q.heat_sum(writer.db_path, "c1", "m", T0, T0 + 3599)[0, 0] == 60
    ag.flush()
    writer.flush()
    assert q.heat_sum(writer.db_path, "c1", "m", T0, T0 + 3599)[0, 0] == 70   # REPLACE, bukan +


def test_with_gaps_breaks_line_only_across_missing_data():
    import math

    from aio_cctv.storage.dashboard_data import with_gaps
    xs, ys = with_gaps([0, 60, 120, 2000, 2060], [1, 2, 3, 4, 5], bucket_s=60)
    assert len(xs) == 6 and math.isnan(ys[3])             # satu pemutus di celah 120→2000
    assert [y for y in ys if not math.isnan(y)] == [1, 2, 3, 4, 5]
    assert with_gaps([0, 60, 120], [1, 1, 1], 60) == ([0, 60, 120], [1, 1, 1])


def test_kpi_has_now_latest_avg_and_max(writer):
    from aio_cctv.storage.dashboard_data import load_dashboard

    class M:                                              # manifest minimal
        id = "m"
        def metric(self, key):
            from aio_cctv.modules.manifest import MetricSpec
            return MetricSpec(key=key, agg="avg")
        metrics, events = [], []

    ag = Aggregator("c1", "m", writer)
    for i, v in enumerate([0] * 60 + [1] * 60 + [2] * 60):   # 3 menit: 0, 1, 2 orang
        ag.consume(res([Metric("n", v)]), float(i), T0 + i)
    ag.flush()
    writer.flush()
    (kpi, _health) = load_dashboard(writer.db_path, "c1", M(), [{"type": "kpi", "metric": "n"}],
                                    T0, T0 + 3600, live=[["n", None, 3.0]])
    assert kpi["values"] == {"": 1.0} and kpi["max"] == {"": 2.0}
    assert kpi["latest"] == {"": (T0 + 120, 2.0)} and kpi["now"] == {"": 3.0}
    (kpi_off, _) = load_dashboard(writer.db_path, "c1", M(), [{"type": "kpi", "metric": "n"}],
                                  T0, T0 + 3600)
    assert kpi_off["now"] == {}                           # kamera mati: pakai "terakhir"


def test_live_tail_summarises_running_minute_like_the_database():
    from aio_cctv.storage.dashboard_data import LiveTail
    t = LiveTail("avg")
    for i, v in enumerate([0, 2, 4]):                     # menit 10:00
        t.add("z1", v, T0 + i)
    t.add("z1", 9, T0 + 61)                               # menit 10:01
    assert t.points("z1") == ([T0, T0 + 60], [2.0, 9.0])
    assert t.points("z1", after=T0) == ([T0 + 60], [9.0])  # menit yang sudah tersimpan dilewati
    assert LiveTail("max").points("x") == ([], [])
    m = LiveTail("max")
    for v in (1, 5, 3):
        m.add("", v, T0)
    assert m.points("")[1] == [5.0]
    m.add("", 1, T0 + 1000)                               # sampel > 5 menit lalu dibuang
    assert m.points("")[0] == [T0 + 960]


def test_health_recorder_stores_stream_fps_as_recv_fps(writer):
    h = HealthRecorder("c1", writer)
    h.add({"state": "streaming", "stream_fps": 25.0, "proc_fps": 24.0}, T0)
    h.flush()
    writer.flush()
    assert q.health(writer.db_path, "c1", T0, T0 + 60)[0]["recv_fps"] == 25.0

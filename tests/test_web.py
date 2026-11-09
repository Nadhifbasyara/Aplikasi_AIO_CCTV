"""Fase 8: dashboard web lokal (API, auth, snapshot/MJPEG). Tanpa Qt & GPU."""
import io
import zipfile
from datetime import datetime

import numpy as np
import pytest
import supervision as sv
from fastapi.testclient import TestClient

from aio_cctv.core.framehub import FrameHub
from aio_cctv.modules.api import Event, Metric, ModuleResult
from aio_cctv.modules.registry import discover
from aio_cctv.storage.aggregator import Aggregator, record_alert
from aio_cctv.storage.dashboard_data import load_dashboard
from aio_cctv.storage.queries import day_range
from aio_cctv.storage.writer import StorageWriter
from aio_cctv.web.auth import Sessions, hash_password, verify_password
from aio_cctv.web.state import CameraView, WebState
from aio_cctv.zones.models import Line, Profile, Zone


@pytest.fixture
def state(tmp_path):
    db = tmp_path / "a.db"
    w = StorageWriter(db, flush_interval=0.05)
    w.start()
    ag = Aggregator("cam1", "baseline", w)
    now = datetime.now().replace(second=0, microsecond=0).timestamp()
    for i in range(30):
        ag.consume(ModuleResult(sv.Detections.empty(), [Metric("people_count", 3),
                                                         Metric("zone_count", 2, "z1")],
                                [Event("line_cross", float(i), "l1", i, {"direction": "in"})],
                                heat=np.ones((4, 8), np.float32) * i), float(i), now + i)
    ag.flush()
    from aio_cctv.alerts.rules import Alert
    record_alert(w, "cam1", Alert("r1", "Area ramai", 0, "warning"), now)
    w.flush()
    w.stop()
    prof = tmp_path / "cam1.json"
    Profile(profile_id="cam1", frame_size=(640, 360),
            zones=[Zone(id="z1", name="Kasir", polygon=[(0, 0), (1, 0), (1, 1)])],
            lines=[Line(id="l1", name="Pintu", p1=(0, 0.5), p2=(1, 0.5))]).save(prof)
    frames = FrameHub()
    s = WebState(db, frames)
    s.set_modules(discover())
    s.set_cameras([CameraView("cam1", "Kasir depan", "baseline", prof),
                   CameraView("cam2", "Gudang", "tidak-ada", tmp_path / "x.json")])
    s.set_running("cam1", True)
    s.set_stats("cam1", {"state": "streaming", "proc_fps": 12.5, "detect_ms": 6.5,
                         "metrics": {"Orang terlacak": "3 orang"},
                         "live_metrics": [["people_count", None, 4.0]]})
    frames.put("cam1", np.zeros((360, 640, 3), np.uint8), np.zeros((360, 640, 3), np.uint8), False)
    return s


@pytest.fixture
def client(state):
    from aio_cctv.web.api import create_app
    return TestClient(create_app(state))


def test_index_and_health(client):
    assert "AIO-CCTV" in client.get("/").text
    h = client.get("/api/health").json()
    assert h["auth_required"] is False and h["logged_in"] is True


def test_cameras_list_carries_status_and_unread(client):
    cams = {c["id"]: c for c in client.get("/api/cameras").json()}
    assert cams["cam1"]["running"] and cams["cam1"]["proc_fps"] == 12.5
    assert cams["cam1"]["module_name"].startswith("Baseline") and cams["cam1"]["unread_alerts"] == 1
    assert cams["cam1"]["has_frame"] and not cams["cam2"]["has_frame"]
    assert "tidak terpasang" in cams["cam2"]["module_name"]


def test_snapshot_and_404(client):
    r = client.get("/api/cameras/cam1/snapshot.jpg?w=320")
    assert r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert client.get("/api/cameras/cam2/snapshot.jpg").status_code == 404
    assert client.get("/api/cameras/nope/snapshot.jpg").status_code == 404


def test_mjpeg_streams_jpeg_parts(state):
    """Generator MJPEG diuji langsung (TestClient menunggu respons lengkap untuk stream tak
    berujung)."""
    from aio_cctv.web.api import create_app
    app = create_app(state)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/cameras/{cam_id}/live.mjpg")
    resp = route.endpoint("cam1", fps=15, w=320)
    assert resp.media_type.startswith("multipart/x-mixed-replace")
    gen = resp.body_iterator
    import asyncio
    first = asyncio.run(gen.__anext__())
    assert first.startswith(b"--frame\r\nContent-Type: image/jpeg") and b"\xff\xd8" in first


def test_dashboard_numbers_equal_desktop_loader(client, state):
    d = client.get("/api/cameras/cam1/dashboard?days=1").json()
    m = state.manifest("baseline")
    desktop = load_dashboard(state.db_path, "cam1", m, m.dashboard, *day_range(1))
    kpi_web = next(w for w in d["widgets"] if w["spec"]["type"] == "kpi")
    kpi_desk = next(w for w in desktop if w["spec"]["type"] == "kpi")
    assert kpi_web["values"] == kpi_desk["values"] == {"": 3.0}
    assert kpi_web["now"] == {"": 4.0} and kpi_web["max"] == {"": 3.0}   # live dari worker
    ts = next(w for w in d["widgets"] if w["spec"]["type"] == "timeseries")
    assert ts["bucket_s"] == 60
    assert d["regions"] == {"z1": "Kasir", "l1": "Pintu"}
    hourly = next(w for w in d["widgets"] if w["spec"]["type"] == "hourly")
    assert sum(hourly["bars"]) == 30
    assert next(w for w in d["widgets"] if w["spec"]["type"] == "heatmap")["has_heat"]
    assert client.get("/api/cameras/cam1/heatmap.png").content[:4] == b"\x89PNG"
    assert client.get("/api/cameras/cam2/dashboard").status_code == 409


def test_alerts_and_ack(client):
    d = client.get("/api/alerts?days=1").json()
    assert d["unread"] == 1 and d["alerts"][0]["camera_name"] == "Kasir depan"
    assert client.post("/api/alerts/ack", json={"all": True}).json() == {"acknowledged": 1}
    assert client.get("/api/alerts?days=1").json()["unread"] == 0


def test_reports_csv_zip_and_html(client):
    today = datetime.now().date().isoformat()
    r = client.get(f"/api/reports/csv?start={today}&end={today}&cams=cam1")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 4 and any("event" in n for n in names)
    html = client.get(f"/api/reports/html?start={today}&end={today}&cams=cam1").text
    assert "Kasir depan" in html and "data:image/png;base64," in html
    assert client.get("/api/reports/csv?cams=tidak-ada").status_code == 400


def test_login_required_when_lan_enabled(client, state):
    state.auth_required = True
    state.password_hash = hash_password("rahasia-123")
    assert client.get("/api/cameras").status_code == 401
    assert client.get("/").status_code == 200                       # halaman login tetap tampil
    assert client.get("/api/health").json()["logged_in"] is False
    assert client.post("/api/login", json={"password": "salah"}).status_code == 401
    assert client.post("/api/login", json={"password": "rahasia-123"}).status_code == 200
    assert client.get("/api/cameras").status_code == 200            # cookie sesi terpasang
    client.post("/api/logout")
    assert client.get("/api/cameras").status_code == 401


def test_password_hash_and_sessions():
    h = hash_password("abc12345")
    assert verify_password("abc12345", h) and not verify_password("x", h)
    assert not verify_password("abc12345", "rusak")
    s = Sessions(ttl_s=0.05)
    t = s.new()
    assert s.valid(t) and not s.valid("lain")
    import time
    time.sleep(0.06)
    assert not s.valid(t)


def test_live_endpoint_for_kpi_polling(client, state):
    assert client.get("/api/cameras/cam1/live").json()["live_metrics"] == [["people_count", None, 4.0]]
    state.set_stats("cam1", {"state": "streaming", "live_metrics": [["people_count", None, 5.0]]})
    assert client.get("/api/cameras/cam1/live").json()["live_metrics"][0][2] == 5.0
    state.set_running("cam1", False)
    off = client.get("/api/cameras/cam1/live").json()
    assert off["running"] is False and off["live_metrics"] == [] and off["ts"] > 0   # ts = jam server

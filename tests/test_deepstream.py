"""Jalur B (DeepStream, Fase 9 §5.8) — bagian yang bisa diuji tanpa Docker/GPU:
protokol shared memory, DeepStreamSource, config nvinfer, dan Pipeline mode "deepstream"."""
import threading
import time

import numpy as np
import pytest
import supervision as sv

from aio_cctv.core.paths import BUNDLED_MODULES_DIR
from aio_cctv.inference.deepstream import shm
from aio_cctv.inference.deepstream.service import infer_config
from aio_cctv.modules.manifest import load_manifest
from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.deepstream_source import DeepStreamSource
from aio_cctv.zones.models import Profile, SourceConfig


class FakeGroup:
    names = ("person", "bicycle", "car")


class FakePool:
    """Pengganti GroupPool: membuat slot di tmp_path tanpa container."""

    def __init__(self, path):
        self.path, self.released = path, []

    def acquire(self, key, manifest, cam_id, uri, w, h):
        self.slot = shm.Slot(self.path, w, h, create=True)
        self.slot.set("conn", shm.STREAMING)
        return FakeGroup(), self.slot

    def release(self, cam_id):
        self.released.append(cam_id)


def fake_runner(path, stop: threading.Event, frames: int = 5):
    """Meniru Sink.consume di runner: tulis frame + deteksi hanya bila host meminta."""
    slot = shm.Slot(path)
    sent = 0
    while not stop.is_set() and sent < frames:
        if slot.get("state") == shm.WANT:
            sent += 1
            slot.frame[:] = sent
            slot.dets[0] = (10, 20, 30, 40, 0.9, 0)
            slot.dets[1] = (50, 60, 70, 80, 0.5, 2)
            slot.set("n_det", 2)
            slot.set("ts", time.monotonic())
            slot.set("wall", time.time())
            slot.set("seq", sent)
            slot.set("frames_total", sent * 3)            # 2 dari 3 frame tidak diminta
            slot.set("state", shm.READY)
        time.sleep(0.001)
    slot.close()


def test_slot_layout_roundtrip(tmp_path):
    a = shm.Slot(tmp_path / "s", 64, 32, create=True)
    b = shm.Slot(tmp_path / "s")                       # sisi lain (runner) membuka file sama
    assert b.frame.shape == (32, 64, 3) and b.get("state") == shm.IDLE
    a.frame[5, 7] = (1, 2, 3)
    a.set("seq", 9)
    assert tuple(b.frame[5, 7]) == (1, 2, 3) and b.get("seq") == 9
    b.close()
    a.unlink()
    assert not (tmp_path / "s").exists()


def test_slot_rejects_foreign_file(tmp_path):
    (tmp_path / "x").write_bytes(b"\0" * shm.slot_size(8, 8))
    with pytest.raises(ValueError):
        shm.Slot(tmp_path / "x")


def test_source_requests_frames_and_returns_detections(tmp_path):
    pool = FakePool(tmp_path / "cam")
    src = DeepStreamSource("rtsp://x/1", "cam1", manifest=None, key=("k",), width=64,
                           height=32, pool=pool, start_timeout_s=1)
    assert src.start()
    assert src.state == "streaming" and src.resolution == (64, 32)
    stop = threading.Event()
    th = threading.Thread(target=fake_runner, args=(tmp_path / "cam", stop))
    th.start()
    got = []
    t_end = time.monotonic() + 5
    while len(got) < 3 and time.monotonic() < t_end:
        if (f := src.read()) is not None:
            got.append(f)
        time.sleep(0.001)
    stop.set()
    th.join()
    assert [f.seq for f in got] == [1, 2, 3]
    assert int(got[1].image[0, 0, 0]) == 2              # frame disalin, bukan view slot
    det = got[0].detections
    assert isinstance(det, sv.Detections) and len(det) == 2
    assert det.xyxy[1].tolist() == [50, 60, 70, 80]
    assert list(det["class_name"]) == ["person", "car"]
    assert src.stats.frames_consumed == 3 and src.stats.frames_received == 9
    src.stop()
    src.stop()                                           # aman dua kali
    assert pool.released == ["cam1"] and src.finished


def test_infer_config_matches_manifest():
    m = load_manifest(BUNDLED_MODULES_DIR / "baseline")
    names = ("person", "bicycle", "car")
    cfg = infer_config(m, names)
    assert "network-mode=2" in cfg                       # FP16
    assert "filter-out-class-ids=1;2" in cfg             # baseline: hanya person
    assert f"pre-cluster-threshold={m.model.conf}" in cfg
    assert f"nms-iou-threshold={m.model.iou}" in cfg
    assert "num-detected-classes=3" in cfg


def test_infer_config_rejects_unknown_class():
    from aio_cctv.inference.deepstream.service import DeepStreamUnavailable
    m = load_manifest(BUNDLED_MODULES_DIR / "baseline")
    with pytest.raises(DeepStreamUnavailable):
        infer_config(m, ["car", "truck"])


def test_pipeline_deepstream_mode_uses_given_detections(monkeypatch):
    import aio_cctv.pipeline as pl

    def no_model(*a, **k):
        raise AssertionError("mode deepstream tidak boleh memuat model YOLO di aplikasi")

    monkeypatch.setattr(pl.Detector, "from_manifest", no_model)
    m = load_manifest(BUNDLED_MODULES_DIR / "baseline")
    profile = Profile(profile_id="p", source=SourceConfig(uri=""))
    pipe = Pipeline(m, profile, (64, 48), 10, camera_id="c", inference="deepstream")
    det = sv.Detections(xyxy=np.array([[1, 2, 20, 40]], np.float32),
                        confidence=np.array([0.9], np.float32), class_id=np.array([0]))
    img = np.zeros((48, 64, 3), np.uint8)
    for i in range(3):                                   # tracker butuh beberapa frame
        res = pipe.step(img, i * 0.1, det)
    assert len(res.tracks) == 1 and res.tracks.tracker_id is not None
    assert len(pipe.step(img, 0.4, None).tracks) == 0    # tanpa deteksi: kosong, tidak error
    pipe.close(0.5)


def test_frame_flow_splits_camera_frames():
    from aio_cctv.gui.workers.pipeline_worker import frame_flow
    # kamera 25 fps, target 10, tercapai: 60% dilewati sengaja, tidak ada yang hilang
    assert frame_flow(25, 10, 10) == {"skip_pct": 60.0, "lost_pct": 0.0}
    # target tidak tercapai (7 fps): 3 frame/detik dari 25 hilang karena beban
    assert frame_flow(25, 7, 10)["lost_pct"] == pytest.approx(12.0)
    assert frame_flow(15, 15, 0) == {"skip_pct": 0.0, "lost_pct": 0.0}
    assert frame_flow(0, 0, 10) == {"skip_pct": 0.0, "lost_pct": 0.0}


def test_tile_stats_show_camera_values():
    from aio_cctv.gui.widgets.video_tile import fmt_bitrate, stats_text
    assert fmt_bitrate(2048) == "2.05 Mbps" and fmt_bitrate(512) == "512 Kbps"
    assert "tidak terukur" in fmt_bitrate(None)
    text = stats_text({"stream_type": "RTSP/TCP · DeepStream", "source_resolution": (2560, 1440),
                       "resolution": (1280, 720), "stream_fps": 25.0, "bitrate_kbps": None,
                       "proc_fps": 10.0, "target_fps": 10, "skip_pct": 60.0, "lost_pct": 0.0})
    assert "Kamera: 2560x1440 @ 25.0 fps" in text          # resolusi asli, bukan mux
    assert "Analisis: 1280x720 @ 10.0 fps (target 10)" in text
    assert "Dilewati: 60% (sengaja) · Hilang: 0.0%" in text

"""Runner Jalur B — dijalankan DI DALAM container DeepStream (Python 3.12), bukan di venv aplikasi.

    python3 /aio/ds/runner.py /aio/run/<kunci>/cameras.json

Pipeline (satu per model; semua kamera yang memakai model itu dibatch bersama):

    nvurisrcbin ×N (RTSP/TCP, NVDEC, reconnect otomatis) ─┐
                                                          ├─ nvstreammux (batch N) ─ nvinfer
    probe "stamp": waktu frame selesai di-decode ─────────┘   (TensorRT FP16) ─ nvvideoconvert
                                                              (RGB, GPU) ─ appsink ─ Sink.consume

Sink.consume menulis deteksi (selalu kecil) dan frame (hanya bila host memintanya, lihat
shm.py) ke slot shared memory masing-masing kamera. Tracker & analitik TIDAK di sini: tetap
dijalankan modul rekan di aplikasi (kontrak modul; nvtracker sengaja tidak dipakai).
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time

import cupy as cp
import shm  # satu folder dengan berkas ini (container menjalankan runner.py langsung)
from pyservicemaker import BatchMetadataOperator, BufferRetriever, Pipeline, Probe, Receiver

NO_FRAME_RECONNECT_S = 3.0


def log(msg: str) -> None:
    print(f"[aio-ds] {msg}", flush=True)


class Camera:
    def __init__(self, index: int, cam_id: str, slot: shm.Slot):
        self.index, self.id, self.slot = index, cam_id, slot
        self.last_frame = 0.0
        self.window: list[float] = []
        self.ever_streamed = False


class MuxStamp(BatchMetadataOperator):
    """Di keluaran nvstreammux (sesudah decode, sebelum inferensi): catat waktu tiap frame,
    setara `ts` Jalur A (saat frame selesai di-decode), dan hitung frame/FPS kamera."""

    def __init__(self, cams: list[Camera], stamps: dict):
        super().__init__()
        self.cams, self.stamps = cams, stamps

    def handle_metadata(self, batch_meta):
        now, wall = time.monotonic(), time.time()
        for fm in batch_meta.frame_items:
            self.stamps[(fm.pad_index, fm.frame_number)] = (now, wall)
            if fm.pad_index >= len(self.cams):
                continue
            cam = self.cams[fm.pad_index]
            cam.last_frame = now
            cam.ever_streamed = True
            cam.window.append(now)
            while cam.window and now - cam.window[0] > 2.0:
                cam.window.pop(0)
            h = cam.slot.header
            h["frames_total"] = int(h["frames_total"]) + 1   # uint64 + int → float64 di numpy
            h["fps"] = len(cam.window) / 2.0
            h["conn"] = shm.STREAMING
            h["src_width"], h["src_height"] = fm.source_width, fm.source_height
        if len(self.stamps) > 4096:                  # frame yang dibuang sebelum sampai sink
            for k in list(self.stamps)[:2048]:
                self.stamps.pop(k, None)


class Sink(BufferRetriever):
    def __init__(self, cams: list[Camera], stamps: dict):
        super().__init__()
        self.cams, self.stamps = cams, stamps

    def consume(self, buffer):
        now = time.monotonic()
        for fm in buffer.batch_meta.frame_items:
            if fm.pad_index >= len(self.cams):
                continue
            cam = self.cams[fm.pad_index]
            slot = cam.slot
            t_mux, wall = self.stamps.pop((fm.pad_index, fm.frame_number), (now, time.time()))
            h = slot.header
            h["frames_sink"] = int(h["frames_sink"]) + 1
            h["infer_ms"] += 0.1 * ((now - t_mux) * 1e3 - float(h["infer_ms"]))
            if int(h["state"]) != shm.WANT:
                continue                              # host belum minta: jangan salin frame
            n = 0
            for om in fm.object_items:
                if n >= shm.MAX_DET:
                    break
                r = om.rect_params
                slot.dets[n] = (r.left, r.top, r.left + r.width, r.top + r.height,
                                om.confidence, om.class_id)
                n += 1
            t0 = time.perf_counter()
            img = cp.from_dlpack(buffer.extract(fm.batch_id))      # (H, W, 3|4) RGB di GPU
            cp.ascontiguousarray(img[:, :, 2::-1]).get(out=slot.frame)   # → BGR di shm
            h["copy_ms"] = (time.perf_counter() - t0) * 1e3
            h["n_det"] = n
            h["ts"], h["wall"] = t_mux, wall
            h["seq"] = int(h["seq"]) + 1
            h["state"] = shm.READY                    # terakhir: host boleh membaca
        return 1


def watchdog(cams: list[Camera], stop: threading.Event) -> None:
    while not stop.wait(0.5):
        now = time.monotonic()
        for cam in cams:
            h = cam.slot.header
            h["heartbeat"] = now
            if cam.ever_streamed and now - cam.last_frame > NO_FRAME_RECONNECT_S:
                if int(h["conn"]) == shm.STREAMING:
                    h["reconnects"] = int(h["reconnects"]) + 1
                h["conn"] = shm.RECONNECTING
                h["fps"] = 0.0


def main(cfg_path: str) -> None:
    with open(cfg_path) as fh:
        cfg = json.load(fh)
    w, h = int(cfg["width"]), int(cfg["height"])
    cams = [Camera(i, c["id"], shm.Slot(c["slot"])) for i, c in enumerate(cfg["cams"])]
    for cam in cams:
        cam.slot.header["conn"] = shm.CONNECTING
    stamps: dict = {}
    p = Pipeline("aio-cctv-deepstream")
    p.add("nvstreammux", "mux", {"batch-size": len(cams), "width": w, "height": h,
                                 "batched-push-timeout": int(cfg.get("batched_push_timeout_us",
                                                                     40000)),
                                 "live-source": 1})
    for i, c in enumerate(cfg["cams"]):
        props = {"uri": c["uri"]}
        if c["uri"].startswith("rtsp://"):
            props.update({"select-rtp-protocol": 4, "latency": 200,
                          "rtsp-reconnect-interval": 5, "rtsp-reconnect-attempts": -1})
        else:
            props.update({"file-loop": True})
        p.add("nvurisrcbin", f"src_{i}", props)
        p.link((f"src_{i}", "mux"), ("", f"sink_{i}"))
    p.add("nvinfer", "infer", {"config-file-path": cfg["infer_config"]})
    p.add("nvvideoconvert", "conv", {})
    p.add("capsfilter", "caps", {"caps": "video/x-raw(memory:NVMM), format=RGB"})
    p.add("appsink", "sink", {"emit-signals": True, "sync": False, "max-buffers": 2,
                              "drop": True})
    p.link("mux", "infer", "conv", "caps", "sink")
    p.attach("mux", Probe("stamp", MuxStamp(cams, stamps)))
    p.attach("sink", Receiver("receiver", Sink(cams, stamps)), tips="new-sample")

    stop = threading.Event()
    threading.Thread(target=watchdog, args=(cams, stop), daemon=True).start()

    def bye(*_):
        stop.set()
        for cam in cams:
            cam.slot.header["conn"] = shm.STOPPED
        log("berhenti")
        os._exit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    log(f"mulai: {len(cams)} kamera, mux {w}x{h}, infer {cfg['infer_config']}")
    p.start()
    p.wait()
    bye()


if __name__ == "__main__":
    main(sys.argv[1])

"""InferenceServer bersama + batching dinamis lintas kamera (Fase 9 §5.1).

Satu server per *bobot model* (beserta kelas/conf/iou/imgsz/device). Semua kamera yang memakai
modul yang sama berbagi satu salinan model; frame dari beberapa kamera digabung menjadi satu
batch. Tracker & analitik tetap per kamera di thread worker masing-masing — server hanya
mengerjakan deteksi.

Alur per frame di worker:   det = shared(frame)   # memblok sampai batch berisi frame ini selesai
Karena tiap worker menunggu hasilnya sendiri, satu kamera paling banyak punya satu frame di
antrean — pembagian giliran antarkamera (fairness) terjadi dengan sendirinya.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass

import numpy as np
import supervision as sv

log = logging.getLogger(__name__)


@dataclass
class _Job:
    frame: np.ndarray
    fut: Future
    t_submit: float


class InferenceServer(threading.Thread):
    def __init__(self, detector, max_batch: int = 8, max_wait_ms: float = 5.0,
                 name: str = "inference"):
        super().__init__(name=name, daemon=True)
        self.detector = detector
        self.max_batch = max(1, int(max_batch))
        self.max_wait_s = max(0.0, float(max_wait_ms)) / 1000
        self.q: queue.Queue[_Job] = queue.Queue()
        self._stop_event = threading.Event()      # bukan `_stop`: menimpa metode Thread
        # statistik (rata-rata bergerak), dibaca worker untuk tile & benchmark
        self.batch_avg = 0.0
        self.infer_ms = 0.0                       # waktu satu batch
        self.batches = 0

    def submit(self, frame: np.ndarray) -> Future:
        fut: Future = Future()
        if self._stop_event.is_set():
            fut.set_exception(RuntimeError("InferenceServer sudah berhenti"))
        else:
            self.q.put(_Job(frame, fut, time.perf_counter()))
        return fut

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                jobs = [self.q.get(timeout=0.1)]
            except queue.Empty:
                continue
            deadline = time.perf_counter() + self.max_wait_s
            while len(jobs) < self.max_batch:
                remaining = deadline - time.perf_counter()
                try:      # ambil yang sudah menunggu tanpa jeda; tunggu sisa batas waktu bila perlu
                    jobs.append(self.q.get_nowait() if remaining <= 0 else
                                self.q.get(timeout=remaining))
                except queue.Empty:
                    break
            t0 = time.perf_counter()
            try:
                results = self.detector.detect_batch([j.frame for j in jobs])
            except Exception as e:                # error model: semua kamera di batch ini tahu
                for j in jobs:
                    j.fut.set_exception(e)
                continue
            dt = (time.perf_counter() - t0) * 1e3
            self.batches += 1
            self.batch_avg += 0.05 * (len(jobs) - self.batch_avg)
            self.infer_ms += 0.05 * (dt - self.infer_ms)
            for j, det in zip(jobs, results):
                j.fut.set_result((det, (t0 - j.t_submit) * 1e3, dt / len(jobs)))
        while not self.q.empty():                 # jangan biarkan worker menunggu selamanya
            self.q.get_nowait().fut.set_exception(RuntimeError("InferenceServer berhenti"))

    def stop(self) -> None:
        self._stop_event.set()


class SharedDetector:
    """Pengganti `Detector` di Pipeline: memanggil server bersama, tampak seperti detektor biasa."""

    def __init__(self, key, server: InferenceServer, pool: ServerPool):
        self.key, self.server, self.pool = key, server, pool
        self.queue_ms = 0.0                       # menunggu giliran di antrean
        self.detect_ms = 0.0                      # bagian frame ini dari waktu batch
        self._released = False

    def __call__(self, frame: np.ndarray) -> sv.Detections:
        det, self.queue_ms, self.detect_ms = self.server.submit(frame).result(timeout=30)
        return det

    def release(self) -> None:
        if not self._released:
            self._released = True
            self.pool.release(self.key)


class ServerPool:
    """Server dibuat saat kamera pertama yang memakai model itu mulai, dihentikan saat kamera
    terakhir berhenti (reference counting)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._servers: dict[tuple, tuple[InferenceServer, int]] = {}

    def acquire(self, key: tuple, make_detector, max_batch: int, max_wait_ms: float
                ) -> SharedDetector:
        with self._lock:
            if key in self._servers:
                server, refs = self._servers[key]
                self._servers[key] = (server, refs + 1)
            else:
                det = make_detector()             # memuat model di bawah lock: sekali saja
                det.warmup()
                server = InferenceServer(det, max_batch, max_wait_ms,
                                         name=f"inference-{len(self._servers) + 1}")
                server.start()
                self._servers[key] = (server, 1)
                log.info("InferenceServer baru: %s (batch ≤ %d, tunggu ≤ %.0f ms)",
                         key[0], max_batch, max_wait_ms)
        return SharedDetector(key, server, self)

    def release(self, key: tuple) -> None:
        with self._lock:
            if key not in self._servers:
                return
            server, refs = self._servers[key]
            if refs > 1:
                self._servers[key] = (server, refs - 1)
            else:
                del self._servers[key]
                server.stop()
                log.info("InferenceServer dihentikan: %s", key[0])

    def active(self) -> int:
        with self._lock:
            return len(self._servers)


POOL = ServerPool()                               # satu per proses aplikasi

"""Modul dari run_trackebotsort.py (Galang) — BoT-SORT + hitung pengunjung & durasi per ID.

Perbedaan dengan skrip asli (karena kontrak modul):
- Deteksi YOLO dijalankan HOST (bobot/conf/iou/kelas sama, lihat module.yaml); modul hanya
  menjalankan tracker BoT-SORT Ultralytics di atas deteksi tersebut. Hasil tracking setara
  `model.track(persist=True, tracker="custom_botsort.yaml")`.
- Waktu memakai `ts` dari sumber video, bukan time.time(), supaya hasil bisa diulang.
- Kotak & label digambar host dari `labels` (frame tidak boleh ditulis).
- Laporan akhir (durasi tiap ID) dikirim sebagai event `kunjungan_selesai`.
"""
from __future__ import annotations

import os
import threading

import numpy as np
import supervision as sv
import yaml
from ultralytics.engine.results import Boxes
from ultralytics.trackers.basetrack import BaseTrack
from ultralytics.trackers.bot_sort import BOTSORT
from ultralytics.utils import IterableSimpleNamespace

from aio_cctv.modules.api import AnalyticsModule, CameraContext, Event, Metric, ModuleResult

_ID_LOCK = threading.Lock()


class _CameraBotSort(BOTSORT):
    """BOTSORT dengan penghitung ID sendiri per kamera.

    Ultralytics menyimpan penghitung ID di variabel kelas `BaseTrack._count` (satu untuk seluruh
    proses), dan tracker baru me-reset-nya ke 0. Di aplikasi multi-kamera itu membuat ID kamera
    lain bertabrakan saat sebuah kamera dinyalakan. Di sini penghitung ditukar masuk/keluar
    di bawah lock, sehingga penomoran tiap kamera tetap 1, 2, 3, … persis seperti skrip asli.
    """

    def __init__(self, args):
        self._count = 0
        with _ID_LOCK:
            saved = BaseTrack._count
            super().__init__(args)                # memanggil reset_id() → BaseTrack._count = 0
            BaseTrack._count = saved

    def update(self, results, img=None, feats=None, **kw):
        with _ID_LOCK:
            saved, BaseTrack._count = BaseTrack._count, self._count
            try:
                return super().update(results, img, feats, **kw)
            finally:
                self._count, BaseTrack._count = BaseTrack._count, saved

    def reset_id(self):
        self._count = 0


def _heat(w: int, h: int, cell: int = 8) -> np.ndarray:
    return np.zeros((h // cell + 1, w // cell + 1), dtype=np.float32)


class BotSortVisitorModule(AnalyticsModule):
    CELL = 8

    def __init__(self, ctx: CameraContext):
        super().__init__(ctx)
        p = ctx.params
        with open(os.path.join(ctx.module_dir, "custom_botsort.yaml")) as fh:
            cfg = yaml.safe_load(fh)
        # track_buffer dalam FRAME → hitung dari FPS efektif (aturan kontrak §5.4)
        cfg["track_buffer"] = max(1, round((ctx.fps or 10) * p["lost_track_seconds"]))
        cfg["with_reid"] = False                  # Re-ID butuh fitur model; tidak dipakai skrip asli
        self.tracker = _CameraBotSort(IterableSimpleNamespace(**cfg))
        self.grace_s, self.min_visit_s = p["grace_s"], p["min_visit_s"]
        self.wh = ctx.frame_wh
        self.unique: set[int] = set()             # id_unik di skrip asli
        self.visits: dict[int, list[float]] = {}  # tid -> [masuk, terakhir terlihat]
        self.heat = _heat(*ctx.frame_wh, self.CELL)

    def _track(self, frame, det: sv.Detections) -> sv.Detections:
        w, h = self.wh
        if len(det):
            cls = det.class_id if det.class_id is not None else np.zeros(len(det))
            data = np.column_stack([det.xyxy, det.confidence, cls]).astype(np.float32)
        else:
            data = np.zeros((0, 6), np.float32)
        out = self.tracker.update(Boxes(data, (h, w)), frame)
        if not len(out):
            return sv.Detections.empty()
        idx = out[:, 7].astype(int)               # indeks deteksi asal
        tracks = det[idx]
        tracks.xyxy = out[:, :4].astype(np.float32)
        tracks.tracker_id = out[:, 4].astype(int)
        return tracks

    def _visit_event(self, tid: int, start: float, end: float) -> Event:
        return Event("kunjungan_selesai", end, None, tid,
                     {"start": start, "end": end, "duration_s": end - start})

    def process(self, frame, detections, ts: float) -> ModuleResult:
        tracks = self._track(frame, detections)
        events: list[Event] = []
        labels: dict[int, str] = {}
        for tid in (tracks.tracker_id if len(tracks) else []):
            tid = int(tid)
            if tid not in self.unique:
                self.unique.add(tid)
                events.append(Event("pengunjung_baru", ts, None, tid, {}))
            if tid in self.visits:
                self.visits[tid][1] = ts
            else:
                self.visits[tid] = [ts, ts]
            labels[tid] = f"ID: {tid} | {ts - self.visits[tid][0]:.1f}s"

        for tid, (start, last) in list(self.visits.items()):
            if ts - last > self.grace_s:          # ID hilang lebih lama dari jeda → selesai
                del self.visits[tid]
                if last - start >= self.min_visit_s:
                    events.append(self._visit_event(tid, start, last))

        if len(tracks):
            pts = tracks.get_anchors_coordinates(sv.Position.BOTTOM_CENTER)
            ys = np.clip((pts[:, 1] // self.CELL).astype(int), 0, self.heat.shape[0] - 1)
            xs = np.clip((pts[:, 0] // self.CELL).astype(int), 0, self.heat.shape[1] - 1)
            np.add.at(self.heat, (ys, xs), 1.0)

        metrics = [Metric("people_count", float(len(tracks))),
                   Metric("total_pengunjung", float(len(self.unique)))]
        return ModuleResult(tracks, metrics, events, labels, heat=self.heat)

    def close(self, ts: float) -> list[Event]:
        out = [self._visit_event(t, s, e) for t, (s, e) in self.visits.items()
               if e - s >= self.min_visit_s]
        self.visits.clear()
        return out

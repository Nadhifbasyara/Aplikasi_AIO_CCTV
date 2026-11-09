"""ModuleResult -> baris DB generik (Fase 7 §3.3). Tidak tahu arti metrik/event apa pun."""
from __future__ import annotations

import json
import logging
import zlib
from collections import defaultdict

import numpy as np

from aio_cctv.modules.api import Event, ModuleResult
from aio_cctv.modules.host import MAX_EVENT_DATA

log = logging.getLogger(__name__)

INS_METRIC = ("INSERT OR REPLACE INTO metric_minute(camera_id,module_id,metric_key,region_id,"
              "minute_ts,avg,min,max,last,n) VALUES (?,?,?,?,?,?,?,?,?,?)")
INS_EVENT = ("INSERT INTO events(camera_id,module_id,type,region_id,track_id,ts,data) "
             "VALUES (?,?,?,?,?,?,?)")
INS_HEAT = ("INSERT OR REPLACE INTO heat_hour(camera_id,module_id,hour_ts,rows,cols,grid) "
            "VALUES (?,?,?,?,?,?)")
INS_ALERT = "INSERT INTO alerts(camera_id,rule_id,ts,severity,message) VALUES (?,?,?,?,?)"
INS_HEALTH = ("INSERT OR REPLACE INTO camera_health(camera_id,minute_ts,state,recv_fps,proc_fps,"
              "dropped_rate,detect_ms,module_ms,latency_ms,reconnects) VALUES (?,?,?,?,?,?,?,?,?,?)")


def record_alert(writer, camera_id: str, alert, epoch: float) -> dict:
    """Tulis alert ke DB dan kembalikan bentuk dict untuk sinyal GUI."""
    writer.put(INS_ALERT, (camera_id, alert.rule_id, epoch, alert.severity, alert.message))
    return {"camera_id": camera_id, "rule_id": alert.rule_id, "ts": epoch,
            "severity": alert.severity, "message": alert.message}


def event_data_json(e: Event) -> str:
    """Event.data -> JSON ≤ 2 KB. Data yang melanggar kontrak (citra, objek) tidak disimpan."""
    try:
        s = json.dumps(e.data, ensure_ascii=False)
    except (TypeError, ValueError):
        log.warning("Event.data '%s' tidak bisa diserialisasi JSON; dibuang", e.type)
        return json.dumps({"_ditolak": "bukan JSON sederhana"})
    if len(s.encode()) > MAX_EVENT_DATA:
        log.warning("Event.data '%s' %d byte > %d; dibuang", e.type, len(s), MAX_EVENT_DATA)
        return json.dumps({"_ditolak": f"lebih dari {MAX_EVENT_DATA} byte"})
    return s


class Aggregator:
    """Satu per kamera, dijalankan di thread worker. Hasilnya dititipkan ke StorageWriter."""

    def __init__(self, camera_id: str, module_id: str, writer):
        self.cam, self.mod, self.writer = camera_id, module_id, writer
        self._minute: int | None = None
        self._samples: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._hour: int | None = None
        self._heat_base: np.ndarray | None = None  # grid kumulatif saat jam berjalan dimulai
        self._heat_last: np.ndarray | None = None

    def consume(self, res: ModuleResult, ts: float, wall_ts: float) -> None:
        """ts = waktu sumber (monotonic / posisi video), wall_ts = epoch frame yang sama."""
        self.events(res.events, ts, wall_ts)
        minute = int(wall_ts // 60 * 60)
        if self._minute is not None and minute != self._minute:
            self._flush_minute()
        self._minute = minute
        for m in res.metrics:
            self._samples[(m.key, m.region_id or "")].append(float(m.value))
        if res.heat is not None:
            self._heat(res.heat, int(wall_ts // 3600 * 3600))

    def events(self, events: list[Event], ts: float, wall_ts: float) -> None:
        offset = wall_ts - ts                      # ts event berada di domain waktu sumber
        for e in events:
            self.writer.put(INS_EVENT, (self.cam, self.mod, e.type, e.region_id,
                                        None if e.track_id is None else int(e.track_id),
                                        float(e.ts) + offset, event_data_json(e)))

    def _flush_minute(self) -> None:
        for (key, region), xs in self._samples.items():
            self.writer.put(INS_METRIC, (self.cam, self.mod, key, region, self._minute,
                                         sum(xs) / len(xs), min(xs), max(xs), xs[-1], len(xs)))
        self._samples.clear()

    def _heat(self, grid: np.ndarray, hour: int) -> None:
        """ModuleResult.heat = akumulasi sejak modul mulai; per jam disimpan selisihnya."""
        grid = np.asarray(grid, dtype=np.float32)
        if self._heat_last is not None and self._heat_last.shape != grid.shape:
            self._heat_base = None                 # modul mengganti ukuran grid: mulai ulang
        if self._hour is not None and hour != self._hour:
            self._flush_heat()
            self._heat_base = self._heat_last.copy() if self._heat_last is not None else None
        if self._heat_base is None:
            self._heat_base = np.zeros_like(grid)
        self._hour = hour
        self._heat_last = grid.copy()

    def _flush_heat(self) -> None:
        if self._hour is None or self._heat_last is None or self._heat_base is None:
            return
        delta = np.clip(self._heat_last - self._heat_base, 0, None).astype(np.float32)
        if delta.any():
            self.writer.put(INS_HEAT, (self.cam, self.mod, self._hour, delta.shape[0],
                                       delta.shape[1], zlib.compress(delta.tobytes())))

    def flush(self) -> None:
        """Panggil saat kamera berhenti: menit & jam yang belum lengkap tetap tersimpan."""
        if self._minute is not None:
            self._flush_minute()
        self._flush_heat()
        self._heat_base = self._heat_last.copy() if self._heat_last is not None else None


class HealthRecorder:
    """Rata-rata statistik worker per menit -> camera_health (bahan Fase 9–10)."""

    FIELDS = ("recv_fps", "proc_fps", "dropped_rate", "detect_ms", "module_ms", "latency_ms")

    def __init__(self, camera_id: str, writer):
        self.cam, self.writer = camera_id, writer
        self._minute: int | None = None
        self._rows: list[dict] = []

    def add(self, stats: dict, wall_ts: float) -> None:
        minute = int(wall_ts // 60 * 60)
        if self._minute is not None and minute != self._minute:
            self.flush()
        self._minute = minute
        self._rows.append(stats)

    def flush(self) -> None:
        if not self._rows:
            return
        rows = self._rows
        avg = {f: float(np.mean([r.get(f, 0.0) or 0.0 for r in rows])) for f in self.FIELDS}
        self.writer.put(INS_HEALTH, (self.cam, self._minute, rows[-1].get("state", "?"),
                                     avg["recv_fps"], avg["proc_fps"], avg["dropped_rate"],
                                     avg["detect_ms"], avg["module_ms"], avg["latency_ms"],
                                     int(rows[-1].get("reconnects", 0))))
        self._rows = []

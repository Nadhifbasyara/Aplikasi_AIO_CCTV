# FASE 6 — Penyimpanan Data, Dashboard Analitik, Alert & Laporan

> Sebelumnya: [FASE-05](FASE-05-aplikasi-desktop-pyqt6.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-07 Profil & Template](FASE-07-profil-konfigurasi-template.md)
> Referensi Proposal: **§1** (traffic masuk/keluar, jam sibuk, kepadatan), **§5 Tujuan** (“dashboard metrik dwell time, heatmap, traffic”), **§6** (“Database metrik… kirim METRIK AGREGAT saja bukan video”), **§7** (Time-series), **§8.5** (grafik jam sibuk, rata-rata dwell, ekspor laporan, **sistem alert**), **§11** (privasi: simpan metrik agregat, bukan wajah)
> Estimasi: **3 minggu** · Milestone: **M4** (bagian 2)

---

## 1. Tujuan

1. Menyimpan hasil analitik secara **persisten & efisien** di SQLite lokal (setara peran “Database metrik” Proposal §6, versi edge).
2. Menyediakan **dashboard** di aplikasi: traffic per jam, jam sibuk, rata-rata/median dwell per zona, tren okupansi, heatmap per periode.
3. **Rule engine & alert**: antrean > N orang, okupansi penuh, dwell melebihi batas, kamera offline.
4. **Laporan** harian/mingguan: ekspor CSV & PDF.
5. **Fitur privasi** sesuai Proposal §11.

## 2. Desain Data

### 2.1 Apa yang disimpan (dan yang TIDAK)

| Disimpan | Tidak disimpan (default) |
|---|---|
| Sesi dwell (zona, track sementara, mulai, selesai, durasi) | Video mentah |
| Event crossing (garis, arah, waktu) | Crop wajah / gambar orang |
| Agregat okupansi per menit (rata-rata, maks) | Identitas apa pun |
| Heatmap agregat per jam (grid kecil) | Kredensial kamera (ada di keyring) |
| Alert & status kamera (uptime, reconnect) | |

> `track_id` hanya bermakna selama sesi tracking dan di-*reset* saat aplikasi restart → bukan identitas pribadi (analitik anonim, Proposal §11).

### 2.2 Skema SQLite — `aio_cctv/storage/schema.sql`

```sql
PRAGMA journal_mode = WAL;          -- tulis & baca bersamaan (worker + dashboard)
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS dwell_sessions (
    id          INTEGER PRIMARY KEY,
    camera_id   TEXT NOT NULL,
    zone_id     TEXT NOT NULL,
    track_id    INTEGER NOT NULL,
    start_ts    REAL NOT NULL,       -- epoch detik
    end_ts      REAL NOT NULL,
    duration_s  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_dwell_cam_zone_time ON dwell_sessions(camera_id, zone_id, start_ts);

CREATE TABLE IF NOT EXISTS line_events (
    id          INTEGER PRIMARY KEY,
    camera_id   TEXT NOT NULL,
    line_id     TEXT NOT NULL,
    track_id    INTEGER NOT NULL,
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_line_cam_time ON line_events(camera_id, line_id, ts);

CREATE TABLE IF NOT EXISTS occupancy_minute (
    camera_id   TEXT NOT NULL,
    zone_id     TEXT NOT NULL,
    minute_ts   INTEGER NOT NULL,    -- epoch dibulatkan ke menit
    avg_count   REAL NOT NULL,
    max_count   INTEGER NOT NULL,
    PRIMARY KEY (camera_id, zone_id, minute_ts)
);

CREATE TABLE IF NOT EXISTS heatmap_hour (
    camera_id   TEXT NOT NULL,
    hour_ts     INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    cols        INTEGER NOT NULL,
    grid        BLOB NOT NULL,       -- float32 terkompresi (zlib)
    PRIMARY KEY (camera_id, hour_ts)
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    camera_id   TEXT NOT NULL,
    rule_id     TEXT NOT NULL,
    ts          REAL NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'warning',
    message     TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS camera_health (
    camera_id   TEXT NOT NULL,
    minute_ts   INTEGER NOT NULL,
    state       TEXT NOT NULL,
    recv_fps    REAL,
    proc_fps    REAL,
    reconnects  INTEGER,
    PRIMARY KEY (camera_id, minute_ts)
);
```

> **Kenapa SQLite, bukan TimescaleDB/InfluxDB (Proposal §7)?** Aplikasi desktop all-in-one harus *zero-install* di PC pelanggan. SQLite + indeks waktu + agregasi per menit sudah cukup untuk 1–16 kamera. Skema ini **mudah dipetakan** ke TimescaleDB bila sinkronisasi cloud (Fase 7, opsional) diaktifkan.

## 3. Implementasi

### 3.1 Struktur file

```
aio_cctv/storage/
├── schema.sql
├── db.py                 # koneksi, migrasi sederhana (user_version)
├── writer.py             # StorageWriter: thread + antrean, batch insert
├── aggregator.py         # FrameResult -> agregat per menit & heatmap per jam
└── queries.py            # query dashboard & laporan
aio_cctv/analytics/
└── rules.py              # RuleEngine + definisi aturan
aio_cctv/reports/
├── csv_export.py
└── pdf_report.py         # QTextDocument -> PDF (tanpa dependensi tambahan)
aio_cctv/gui/pages/
├── analytics.py          # dashboard (pyqtgraph)
├── reports.py
└── alerts.py
```

### 3.2 Writer non-blocking — `aio_cctv/storage/writer.py`

Worker kamera **tidak boleh menunggu disk**. Worker hanya memasukkan item ke antrean; satu thread writer melakukan *batch insert* tiap 1 detik.

```python
import queue
import sqlite3
import threading
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class StorageWriter(threading.Thread):
    def __init__(self, db_path: Path, flush_interval: float = 1.0):
        super().__init__(name="storage-writer", daemon=True)
        self.db_path, self.flush_interval = db_path, flush_interval
        self.q: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=100_000)
        self._stop = threading.Event()

    def put(self, sql: str, params: tuple) -> None:
        try:
            self.q.put_nowait((sql, params))
        except queue.Full:
            pass                               # lebih baik kehilangan 1 event daripada GUI macet

    def run(self) -> None:
        con = sqlite3.connect(self.db_path)
        con.executescript(SCHEMA)
        while not self._stop.is_set() or not self.q.empty():
            self._stop.wait(self.flush_interval)
            batch: dict[str, list[tuple]] = {}
            while True:
                try:
                    sql, params = self.q.get_nowait()
                except queue.Empty:
                    break
                batch.setdefault(sql, []).append(params)
            if batch:
                with con:                      # satu transaksi per flush
                    for sql, rows in batch.items():
                        con.executemany(sql, rows)
        con.close()

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)
```

### 3.3 Agregator — `aio_cctv/storage/aggregator.py`

```python
import time
import zlib
from collections import defaultdict

import numpy as np

from aio_cctv.analytics.events import FrameResult

INS_DWELL = "INSERT INTO dwell_sessions(camera_id,zone_id,track_id,start_ts,end_ts,duration_s) VALUES (?,?,?,?,?,?)"
INS_LINE = "INSERT INTO line_events(camera_id,line_id,track_id,direction,ts) VALUES (?,?,?,?,?)"
INS_OCC = "INSERT OR REPLACE INTO occupancy_minute VALUES (?,?,?,?,?)"
INS_HEAT = "INSERT OR REPLACE INTO heatmap_hour VALUES (?,?,?,?,?)"


class Aggregator:
    """Mengubah FrameResult (monotonic ts) menjadi baris DB (epoch ts)."""

    def __init__(self, camera_id: str, writer, heatmap):
        self.cam, self.writer, self.heatmap = camera_id, writer, heatmap
        self.offset = time.time() - time.monotonic()      # konversi monotonic -> epoch
        self._minute, self._samples = None, defaultdict(list)
        self._hour = None

    def consume(self, res: FrameResult, wall_ts: float) -> None:
        for s in res.closed_sessions:
            self.writer.put(INS_DWELL, (self.cam, s.zone_id, s.track_id,
                                        s.start + self.offset, s.end + self.offset, s.duration))
        for c in res.crossings:
            self.writer.put(INS_LINE, (self.cam, c.line_id, c.track_id, c.direction, c.ts + self.offset))

        minute = int(wall_ts // 60 * 60)
        if self._minute is not None and minute != self._minute:
            for zone_id, xs in self._samples.items():
                self.writer.put(INS_OCC, (self.cam, zone_id, self._minute, float(np.mean(xs)), int(max(xs))))
            self._samples.clear()
        self._minute = minute
        for zone_id, n in res.zone_counts.items():
            self._samples[zone_id].append(n)

        hour = int(wall_ts // 3600 * 3600)
        if self._hour is not None and hour != self._hour:
            g = self.heatmap.acc
            self.writer.put(INS_HEAT, (self.cam, self._hour, g.shape[0], g.shape[1],
                                       zlib.compress(g.astype(np.float32).tobytes())))
            g[:] = 0                                        # heatmap per jam
        self._hour = hour
```

> Untuk **FileSource** (demo YouTube) `offset` membuat data seolah terjadi “sekarang”. Tambahkan opsi *start time* manual agar video rekaman lama dapat dipetakan ke jam aslinya.

Integrasi: di `PipelineWorker` (Fase 5), setelah `pipe.step()` panggil `aggregator.consume(res, f.wall_ts)`; `StorageWriter` tunggal dibuat oleh `MainWindow` dan dibagikan ke semua worker.

### 3.4 Query dashboard — `aio_cctv/storage/queries.py`

```python
TRAFFIC_PER_HOUR = """
SELECT strftime('%H', ts, 'unixepoch', 'localtime') AS jam,
       SUM(direction = 'in')  AS masuk,
       SUM(direction = 'out') AS keluar
FROM line_events
WHERE camera_id = ? AND line_id = ? AND ts BETWEEN ? AND ?
GROUP BY jam ORDER BY jam;
"""

DWELL_STATS = """
SELECT zone_id, COUNT(*) AS sesi, AVG(duration_s) AS rata2, MAX(duration_s) AS maks
FROM dwell_sessions
WHERE camera_id = ? AND start_ts BETWEEN ? AND ?
GROUP BY zone_id;
"""

OCCUPANCY_TREND = """
SELECT minute_ts, avg_count, max_count FROM occupancy_minute
WHERE camera_id = ? AND zone_id = ? AND minute_ts BETWEEN ? AND ?
ORDER BY minute_ts;
"""

PEAK_HOURS = """
SELECT strftime('%H', minute_ts, 'unixepoch', 'localtime') AS jam, AVG(avg_count) AS rata2_orang
FROM occupancy_minute WHERE camera_id = ? AND minute_ts BETWEEN ? AND ?
GROUP BY jam ORDER BY rata2_orang DESC LIMIT 3;
"""
```

Median dwell dihitung di Python (SQLite tidak punya fungsi median bawaan).

### 3.5 Halaman Analitik (dashboard)

Komponen (pyqtgraph — cepat & native Qt):

| Widget | Sumber query | Proposal |
|---|---|---|
| Kartu KPI: total pengunjung hari ini, rata-rata dwell, okupansi saat ini, jam tersibuk | gabungan | §1, §8.5 |
| Grafik batang **traffic masuk/keluar per jam** | `TRAFFIC_PER_HOUR` | §1 “jam sibuk” |
| Grafik garis **okupansi per menit** per zona | `OCCUPANCY_TREND` | §1 “kepadatan” |
| Tabel/boxplot **dwell per zona** | `DWELL_STATS` | §1 “dwell time” |
| **Heatmap** periode terpilih di atas snapshot | `heatmap_hour` (jumlahkan grid) | §1 “heatmap” |
| Filter: kamera, zona, rentang tanggal (hari ini / 7 hari / custom) | — | |

Muat data di `QThreadPool` dan refresh otomatis tiap 30–60 detik.

### 3.6 Rule engine & alert — `aio_cctv/analytics/rules.py`

Aturan disimpan **di profil** (bagian `rules`, formal di skema v2 Fase 7):

```json
"rules": [
  { "id": "r1", "type": "occupancy_gt", "zone_id": "z2", "threshold": 5,    "for_s": 30,  "cooldown_s": 300,
    "message": "Antrean kasir lebih dari 5 orang" },
  { "id": "r2", "type": "dwell_gt",     "zone_id": "z1", "threshold": 5400, "cooldown_s": 600,
    "message": "Pelanggan duduk lebih dari 90 menit" },
  { "id": "r3", "type": "camera_offline", "for_s": 60 }
]
```

```python
from dataclasses import dataclass


@dataclass
class Alert:
    rule_id: str
    message: str
    ts: float
    severity: str = "warning"


class RuleEngine:
    def __init__(self, rules: list[dict]):
        self.rules = rules
        self._since: dict[str, float] = {}       # kondisi mulai terpenuhi
        self._last_fired: dict[str, float] = {}

    def _gate(self, rule: dict, condition: bool, ts: float) -> bool:
        rid = rule["id"]
        if not condition:
            self._since.pop(rid, None)
            return False
        start = self._since.setdefault(rid, ts)
        if ts - start < rule.get("for_s", 0):
            return False
        if ts - self._last_fired.get(rid, -1e18) < rule.get("cooldown_s", 300):
            return False
        self._last_fired[rid] = ts
        return True

    def evaluate(self, res, ts: float, camera_state: str = "streaming") -> list[Alert]:
        alerts = []
        for r in self.rules:
            if r["type"] == "occupancy_gt":
                cond = res.zone_counts.get(r["zone_id"], 0) > r["threshold"]
            elif r["type"] == "dwell_gt":
                cond = any(d.get(r["zone_id"], 0) > r["threshold"] for d in res.dwell_now.values())
            elif r["type"] == "camera_offline":
                cond = camera_state != "streaming"
            else:
                continue
            if self._gate(r, cond, ts):
                alerts.append(Alert(r["id"], r.get("message", r["type"]), ts))
        return alerts
```

> `camera_offline` tidak bisa dievaluasi dari worker saat tidak ada frame — evaluasi aturan ini dipindah ke timer di `CameraManager` yang memeriksa `source.state`.

Kanal notifikasi:
1. **Tray icon** (`QSystemTrayIcon.showMessage`) + suara.
2. Halaman **Alert** (riwayat, tombol *acknowledge*).
3. (Opsional) **Telegram Bot** — `POST https://api.telegram.org/bot<token>/sendMessage` berisi **teks saja** (tanpa gambar, sesuai privasi). Token disimpan di keyring.

### 3.7 Laporan — `aio_cctv/reports/`

- **CSV**: ekspor tabel mentah (dwell_sessions, line_events, occupancy_minute) sesuai filter.
- **PDF** harian/mingguan tanpa library tambahan: susun HTML (KPI, tabel, gambar grafik yang di-*export* dari pyqtgraph dan heatmap PNG) → `QTextDocument.setHtml()` → `QPdfWriter`.

```python
from PyQt6.QtGui import QPageSize, QPdfWriter, QTextDocument


def html_to_pdf(html: str, path: str) -> None:
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(150)
    doc = QTextDocument()
    doc.setHtml(html)
    doc.print(writer)
```

Isi laporan: periode, kamera, total masuk/keluar, jam sibuk, dwell rata-rata/median per zona, okupansi maksimum, grafik, heatmap, daftar alert, uptime kamera.

### 3.8 Fitur privasi (Proposal §11)

| Fitur | Implementasi |
|---|---|
| **Mode anonim tampilan** | Toggle “Blur orang” → `sv.BlurAnnotator` / `sv.PixelateAnnotator` diterapkan sebelum frame dikirim ke GUI & snapshot laporan |
| **Tanpa rekaman video** default | Tidak ada fitur rekam kontinu; (opsional) klip bukti alert 10 detik **harus diaktifkan eksplisit** + otomatis terhapus setelah N hari |
| **Retensi data** | Pengaturan “hapus event mentah > 90 hari” (agregat per menit/jam tetap) — dijalankan saat startup |
| **Pemberitahuan area terpantau** | Template stiker/poster PDF di menu Bantuan |
| **Kredensial** | Keyring (Fase 5) |

---

## 4. Pengujian Fase 6

- [ ] Uji beban: 4 kamera × 8 jam → ukuran DB, waktu query dashboard (< 1 s untuk rentang 7 hari).
- [ ] Konsistensi: total `line_events` di DB = total in/out yang tertampil di overlay.
- [ ] Alert: skenario uji dengan video YouTube kerumunan → alert okupansi muncul sekali per cooldown, tidak *spam*.
- [ ] Crash-safety: matikan paksa aplikasi → DB tidak korup (WAL), data sampai flush terakhir tersimpan.
- [ ] Laporan PDF terbuka benar & angka cocok dengan dashboard.

## 5. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| Modul storage, rules, reports | `aio_cctv/storage/`, `aio_cctv/analytics/rules.py`, `aio_cctv/reports/` |
| Halaman Analitik, Laporan, Alert | `aio_cctv/gui/pages/` |
| ERD database + diagram alur data | `docs/diagram/` (Bab III) |
| Contoh laporan PDF dari data uji | `docs/logbook/` |

## 6. Definition of Done

- [ ] Dashboard menampilkan traffic per jam, dwell per zona, okupansi, heatmap dari data nyata.
- [ ] ≥3 tipe aturan alert berfungsi dengan notifikasi tray.
- [ ] Ekspor CSV & PDF berfungsi.
- [ ] Mode blur & retensi data berfungsi.
- [ ] Tag `v0.6-data-dashboard`.

## 7. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| `database is locked` | WAL + satu writer thread; dashboard hanya membaca dengan koneksi terpisah |
| Jam sistem PC salah → data bergeser | Tampilkan peringatan bila selisih NTP besar; catat zona waktu |
| Track ID reset setelah restart → dwell sesi terpotong | Flush sesi aktif saat stop (`engine.flush()`), tandai sesi “terpotong” |

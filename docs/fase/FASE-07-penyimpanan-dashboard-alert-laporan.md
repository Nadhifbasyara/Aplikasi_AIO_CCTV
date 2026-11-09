# FASE 7 — Penyimpanan Data, Dashboard, Alert & Laporan (Generik)

> Sebelumnya: [FASE-06](FASE-06-integrasi-model-modul-tim.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-08 Profil & Preset](FASE-08-profil-konfigurasi-preset.md)
> Referensi Proposal: **§1** (traffic, jam sibuk, kepadatan), **§6** (“kirim METRIK AGREGAT saja bukan video”), **§8.5** (grafik, ekspor laporan, **sistem alert**), **§11** (privasi)
> Estimasi: **3 minggu** · Milestone: **M4c** · Menjawab: **RM-1** (alert & laporan)
> *Revisi 2026-11-09: dahulu FASE-06. Skema data diubah dari tabel khas analitik (dwell, line) menjadi **generik**, karena analitik kini milik modul TA-1/TA-2.*

---

## 1. Tujuan

1. Menyimpan **metrik dan event apa pun yang dideklarasikan modul** (Fase 6) secara persisten di SQLite, tanpa skema khusus per modul.
2. **Dashboard** yang dibangkitkan dari `manifest.dashboard` dan dapat disusun ulang pengguna.
3. **Rule engine & alert generik**: ambang metrik, kemunculan event, kamera offline, modul error.
4. **Laporan** harian/mingguan: CSV & PDF.
5. Mencatat **kesehatan kamera dan sumber daya** per menit, sebagai data mentah untuk Fase 9–10.
6. **Fitur privasi** sesuai Proposal §11.

Prinsip: host **tidak pernah** menulis `if module_id == "ta1"`. Semua perbedaan antarmodul datang dari manifest.

## 2. Desain Data

### 2.1 Apa yang disimpan (dan yang tidak)

| Disimpan | Tidak disimpan (default) |
|---|---|
| Agregat **metrik** per menit (avg/min/max/last, jumlah sampel) | Video mentah |
| **Event** modul (tipe, waktu, zona, track sementara, `data` JSON kecil) | Crop/gambar orang, embedding Re-ID |
| Grid heatmap per jam (bila modul mengisi `ModuleResult.heat`) | Identitas apa pun |
| Alert, status kamera, pemakaian sumber daya | Kredensial kamera (ada di keyring) |

> `track_id` hanya bermakna selama sesi tracking (analitik anonim). Bila modul TA-2 memakai Re-ID, **embedding tetap di memori modul** dan tidak dikirim ke host (kontrak Fase 6 §4.3).

### 2.2 Skema SQLite — `aio_cctv/storage/schema.sql`

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS metric_minute (
    camera_id   TEXT NOT NULL,
    module_id   TEXT NOT NULL,
    metric_key  TEXT NOT NULL,
    region_id   TEXT NOT NULL DEFAULT '',   -- '' = metrik tingkat kamera
    minute_ts   INTEGER NOT NULL,           -- epoch dibulatkan ke menit
    avg REAL, min REAL, max REAL, last REAL,
    n           INTEGER NOT NULL,
    PRIMARY KEY (camera_id, module_id, metric_key, region_id, minute_ts)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    camera_id   TEXT NOT NULL,
    module_id   TEXT NOT NULL,
    type        TEXT NOT NULL,
    region_id   TEXT,
    track_id    INTEGER,
    ts          REAL NOT NULL,              -- epoch detik
    data        TEXT                        -- JSON, maks 2 KB
);
CREATE INDEX IF NOT EXISTS ix_events_cam_type_time ON events(camera_id, type, ts);

CREATE TABLE IF NOT EXISTS heat_hour (
    camera_id   TEXT NOT NULL,
    module_id   TEXT NOT NULL,
    hour_ts     INTEGER NOT NULL,
    rows INTEGER NOT NULL, cols INTEGER NOT NULL,
    grid        BLOB NOT NULL,              -- float32 zlib
    PRIMARY KEY (camera_id, module_id, hour_ts)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, camera_id TEXT NOT NULL, rule_id TEXT NOT NULL,
    ts REAL NOT NULL, severity TEXT NOT NULL DEFAULT 'warning',
    message TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS camera_health (       -- juga sumber data Fase 9–10
    camera_id   TEXT NOT NULL,
    minute_ts   INTEGER NOT NULL,
    state       TEXT NOT NULL,
    recv_fps REAL, proc_fps REAL, dropped_rate REAL,
    detect_ms REAL, module_ms REAL, latency_ms REAL,
    reconnects  INTEGER,
    PRIMARY KEY (camera_id, minute_ts)
);

CREATE TABLE IF NOT EXISTS system_minute (       -- pemakaian sumber daya PC
    minute_ts INTEGER PRIMARY KEY,
    cpu_pct REAL, ram_mb REAL, gpu_pct REAL, vram_mb REAL, cams_active INTEGER
);
```

> **Mengapa generik (EAV-ringan), bukan tabel per metrik?** Modul baru atau metrik baru tidak memerlukan migrasi skema. Agregasi per menit menjaga ukuran DB tetap kecil: 16 kamera × 10 metrik × 1440 menit ≈ 230 ribu baris/hari.

## 3. Implementasi

### 3.1 Struktur file

```
aio_cctv/storage/
├── schema.sql
├── db.py              # koneksi, migrasi (PRAGMA user_version)
├── writer.py          # StorageWriter: satu thread, batch insert
├── aggregator.py      # ModuleResult -> metric_minute / events / heat_hour
└── queries.py
aio_cctv/alerts/
└── rules.py           # RuleEngine generik
aio_cctv/reports/
├── csv_export.py
└── pdf_report.py
aio_cctv/gui/pages/
├── dashboard.py       # widget dibangkitkan dari manifest
├── reports.py
└── alerts.py
```

### 3.2 Writer non-blocking — `aio_cctv/storage/writer.py`

Worker kamera **tidak boleh menunggu disk**. Worker hanya memasukkan item ke antrean, lalu satu thread writer melakukan *batch insert* tiap 1 detik.

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
            pass                               # lebih baik kehilangan 1 baris daripada worker macet

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
                with con:
                    for sql, rows in batch.items():
                        con.executemany(sql, rows)
        con.close()

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)
```

### 3.3 Agregator generik — `aio_cctv/storage/aggregator.py`

```python
import json
import time
from collections import defaultdict

INS_METRIC = "INSERT OR REPLACE INTO metric_minute VALUES (?,?,?,?,?,?,?,?,?,?)"
INS_EVENT = "INSERT INTO events(camera_id,module_id,type,region_id,track_id,ts,data) VALUES (?,?,?,?,?,?,?)"


class Aggregator:
    """ModuleResult (ts monotonic) -> baris DB (ts epoch). Tidak tahu arti metrik."""

    def __init__(self, camera_id: str, module_id: str, writer):
        self.cam, self.mod, self.writer = camera_id, module_id, writer
        self.offset = time.time() - time.monotonic()
        self._minute = None
        self._samples: dict[tuple[str, str], list[float]] = defaultdict(list)

    def consume(self, res, ts: float) -> None:
        for e in res.events:
            data = json.dumps(e.data, ensure_ascii=False)[:2048]
            self.writer.put(INS_EVENT, (self.cam, self.mod, e.type, e.region_id, e.track_id,
                                        e.ts + self.offset, data))
        minute = int((ts + self.offset) // 60 * 60)
        if self._minute is not None and minute != self._minute:
            self._flush()
        self._minute = minute
        for m in res.metrics:
            self._samples[(m.key, m.region_id or "")].append(m.value)

    def _flush(self) -> None:
        for (key, region), xs in self._samples.items():
            self.writer.put(INS_METRIC, (self.cam, self.mod, key, region, self._minute,
                                         sum(xs) / len(xs), min(xs), max(xs), xs[-1], len(xs)))
        self._samples.clear()
```

`manifest.metrics[].agg` (`avg`/`max`/`last`/`sum`) menentukan kolom mana yang dipakai dashboard dan laporan saat menggabungkan per jam/hari.

### 3.4 Dashboard dari manifest

| `type` widget | Sumber | Tampilan |
|---|---|---|
| `kpi` | `metric_minute` (nilai `agg` hari ini) | Kartu angka + satuan |
| `timeseries` | `metric_minute` per zona | Grafik garis (pyqtgraph) |
| `hourly` | `metric_minute` dikelompokkan per jam | Grafik batang (jam sibuk) |
| `events` | `events` | Tabel + jumlah per jam |
| `heatmap` | `heat_hour` dijumlah | Overlay di atas snapshot |
| `health` *(bawaan host)* | `camera_health`, `system_minute` | FPS, latensi, CPU/GPU per kamera |

Label, satuan, dan judul diambil dari manifest. Filter tersedia untuk kamera, modul, zona, dan rentang tanggal. Data dimuat di `QThreadPool` dan di-refresh tiap 30–60 detik.

### 3.5 Rule engine generik — `aio_cctv/alerts/rules.py`

```json
"rules": [
  { "id": "r1", "type": "metric_gt",  "metric": "people_count", "region_role": "kasir",
    "threshold": 5, "for_s": 30, "cooldown_s": 300, "message": "Antrean kasir > 5 orang" },
  { "id": "r2", "type": "event",      "event": "idle_long", "min_count": 1, "window_s": 60,
    "cooldown_s": 600, "message": "Ada pegawai idle terlalu lama" },
  { "id": "r3", "type": "camera_offline", "for_s": 60 },
  { "id": "r4", "type": "module_error" }
]
```

| Tipe | Kondisi |
|---|---|
| `metric_gt` / `metric_lt` | Nilai metrik (per zona atau peran zona) melewati ambang selama `for_s` |
| `event` | Event tipe tertentu muncul ≥ `min_count` kali dalam `window_s` |
| `camera_offline` | `source.state != "streaming"` selama `for_s` (dievaluasi timer `CameraManager`) |
| `module_error` | `process()` melempar exception |
| `overload` *(host)* | `proc_fps` < target selama `for_s` (lihat Fase 9) |

```python
class RuleEngine:
    def __init__(self, rules: list[dict]):
        self.rules = [r for r in rules if r.get("enabled", True)]
        self._since: dict[str, float] = {}
        self._last_fired: dict[str, float] = {}
        self._recent: dict[str, list[float]] = {}

    def _gate(self, r: dict, cond: bool, ts: float) -> bool:
        rid = r["id"]
        if not cond:
            self._since.pop(rid, None)
            return False
        if ts - self._since.setdefault(rid, ts) < r.get("for_s", 0):
            return False
        if ts - self._last_fired.get(rid, -1e18) < r.get("cooldown_s", 300):
            return False
        self._last_fired[rid] = ts
        return True

    def evaluate(self, res, ts: float, region_of_role: dict[str, set[str]]) -> list[Alert]:
        out = []
        for r in self.rules:
            if r["type"] in ("metric_gt", "metric_lt"):
                role = r.get("region_role")
                regions = region_of_role.get(role, set()) if role else None   # None = semua zona
                vals = [m.value for m in res.metrics if m.key == r["metric"]
                        and (regions is None or m.region_id in regions)]
                if not vals:
                    cond = False
                elif r["type"] == "metric_gt":
                    cond = max(vals) > r["threshold"]
                else:
                    cond = min(vals) < r["threshold"]
            elif r["type"] == "event":
                hist = self._recent.setdefault(r["id"], [])
                hist += [e.ts for e in res.events if e.type == r["event"]]
                hist[:] = [t for t in hist if ts - t <= r.get("window_s", 60)]
                cond = len(hist) >= r.get("min_count", 1)
            else:
                continue                                  # tipe host dievaluasi di CameraManager
            if self._gate(r, cond, ts):
                out.append(Alert(r["id"], r.get("message", r["type"]), ts))
        return out
```

Editor aturan di GUI menampilkan **dropdown metrik/event dari manifest** modul kamera tersebut, sehingga pengguna tidak perlu mengetik key.

Kanal notifikasi:
1. **Tray icon** + suara.
2. Halaman **Alert** (riwayat, *acknowledge*).
3. (Opsional) **Telegram Bot**: **teks saja**, token di keyring.

### 3.6 Laporan — `aio_cctv/reports/`

- **CSV**: ekspor `metric_minute`, `events`, `alerts` sesuai filter.
- **PDF** (`QTextDocument` → `QPdfWriter`, tanpa dependensi tambahan). Isi laporan disusun dari manifest: ringkasan tiap metrik (agregat sesuai `agg`), jumlah event per tipe per jam, grafik widget dashboard, heatmap, daftar alert, **uptime & kinerja kamera**, serta atribusi modul (nama, versi, penulis).

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

### 3.7 Fitur privasi (Proposal §11)

| Fitur | Implementasi |
|---|---|
| Mode anonim tampilan | Toggle "Blur orang" → `sv.BlurAnnotator` pada kotak `res.tracks` sebelum frame dikirim ke GUI/snapshot |
| Tanpa rekaman video | Tidak ada rekam kontinu; klip bukti alert opsional, harus diaktifkan eksplisit, dan terhapus otomatis |
| Retensi data | Hapus `events` > N hari (default 90); `metric_minute` dirangkum ke per jam setelah 30 hari |
| Validasi data modul | `Event.data` ditolak bila bukan JSON sederhana atau > 2 KB (mencegah modul menyelipkan citra/embedding) |
| Kredensial | Keyring (Fase 5) |

---

## 4. Pengujian Fase 7

- [ ] Uji beban: 8 kamera × 8 jam (simulasi MediaMTX) → ukuran DB, waktu query dashboard < 1 s untuk 7 hari.
- [ ] Konsistensi: jumlah event di DB = jumlah event yang dikeluarkan modul (dihitung terpisah di uji).
- [ ] Modul baru dengan metrik baru tampil di dashboard **tanpa** perubahan skema/kode.
- [ ] Alert `metric_gt` muncul sekali per cooldown, tidak *spam*; `camera_offline` & `module_error` berfungsi.
- [ ] Crash-safety: matikan paksa aplikasi → DB tidak korup (WAL).
- [ ] Laporan PDF terbuka benar & angka cocok dengan dashboard.

## 5. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| Modul storage, alerts, reports | `aio_cctv/storage/`, `aio_cctv/alerts/`, `aio_cctv/reports/` |
| Halaman Dashboard, Laporan, Alert | `aio_cctv/gui/pages/` |
| ERD + diagram alur data (Bab III) | `docs/diagram/` |
| Contoh laporan PDF (baseline + modul rekan) | `docs/logbook/` |

## 6. Definition of Done

- [ ] Dashboard menampilkan metrik dari **≥2 modul berbeda** berdasarkan manifest saja.
- [ ] ≥4 tipe aturan alert berfungsi dengan notifikasi tray.
- [ ] Ekspor CSV & PDF berfungsi.
- [ ] `camera_health` & `system_minute` terisi (dipakai Fase 9–10).
- [ ] Mode blur & retensi data berfungsi.
- [ ] Tag `v0.7-data-dashboard`.

## 7. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| `database is locked` | WAL + satu writer thread; dashboard memakai koneksi baca terpisah |
| Modul mengeluarkan metrik sangat sering (tiap frame × banyak zona) | Agregasi per menit di memori; hanya hasil agregat yang ditulis |
| Jam sistem PC salah | Peringatan bila selisih NTP besar; simpan zona waktu |
| Sesi terbuka terpotong saat berhenti | `module.close(ts)` dipanggil saat stop; event penutup ikut disimpan |

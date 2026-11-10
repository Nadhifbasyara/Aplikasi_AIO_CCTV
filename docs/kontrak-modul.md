# Kontrak Modul Analitik AIO-CCTV — v1.0

Panduan untuk pembuat modul (TA-1: perilaku konsumen, TA-2: produktivitas pegawai).
Dengan mengikuti kontrak ini, model YOLO11, tracker, dan analitik kalian bisa dijalankan aplikasi desktop AIO-CCTV pada banyak kamera, **tanpa mengubah kode aplikasi**.

> Status: **draf v1.0, belum disepakati tim** (lihat [Fase 6 §7](fase/FASE-06-integrasi-model-modul-tim.md#7-kesepakatan-dengan-ta-1--ta-2-lakukan-di-minggu-pertama)).
> Contoh lengkap yang berjalan: [`modules/baseline/`](../modules/baseline/).

---

## 1. Pembagian kerja

| Dikerjakan aplikasi (host) | Dikerjakan modul (kalian) |
|---|---|
| Mengambil frame dari kamera RTSP/DVR/file | — |
| **Memuat dan menjalankan bobot YOLO11** kalian (nanti di-batch lintas kamera dan dipercepat TensorRT/OpenVINO) | Menyediakan file bobot + `imgsz`/`conf`/kelas |
| — | **Tracker** (ByteTrack/BoT-SORT/OC-SORT/Re-ID, dll.) |
| — | **Logika analitik** dan definisi metrik/event |
| Menggambar zona/garis, menyimpan profil | Mendeklarasikan **peran zona** yang dibutuhkan |
| Overlay video, dashboard, alert, laporan, database | Mendeklarasikan label/satuan metrik di manifest |

Jadi modul **tidak** memanggil YOLO sendiri. Modul menerima hasil deteksi yang sudah jadi dari host.

> Host bisa menjalankan deteksi lewat dua jalur: Python (Ultralytics/TensorRT/ONNX/OpenVINO) atau **NVIDIA DeepStream** (Linux + GPU NVIDIA). Bagi modul, keduanya sama: `process()` tetap menerima `frame` BGR `numpy` dan `sv.Detections`. Karena itu, modul **jangan** bergantung pada atribut khusus Ultralytics (mis. objek `Results`) dan jangan mengasumsikan resolusi frame tertentu. Pakai `CameraContext.frame_wh` untuk ukuran frame.

## 2. Struktur paket

```
ta1-perilaku-konsumen/          # nama folder bebas; id diambil dari module.yaml
├── module.yaml                 # manifest (wajib)
├── analyzer.py                 # entrypoint (nama bebas, ditulis di manifest)
├── tracker.py, ...             # file lain; boleh import relatif: from .tracker import X
├── weights/
│   ├── best.pt                 # bobot YOLO11 → dimuat HOST
│   └── osnet.pt                # bobot lain (mis. Re-ID) → dimuat MODUL sendiri
├── samples/
│   ├── demo.mp4                # klip pendek untuk uji (opsional tapi sangat disarankan)
│   └── expected.jsonl          # keluaran acuan untuk uji paritas (lihat §6)
└── README.md
```

Folder paket dimuat sebagai paket Python bernama unik (`aio_mod_<id>`). Karena itu **import antar-file di dalam modul harus relatif** (`from .tracker import ...`). Dua modul yang sama-sama punya `analyzer.py` tidak akan bentrok.

## 3. `module.yaml`

```yaml
id: ta1-perilaku-konsumen          # unik: huruf kecil, angka, strip
name: Analitik Perilaku Konsumen
version: 0.1.0                     # versi modul kalian (SemVer)
api_version: "1.0"                 # versi kontrak ini; mayor harus sama dengan host
authors: ["Nama TA-1"]
license: "AGPL-3.0"
entrypoint: analyzer:ConsumerModule   # <file tanpa .py>:<NamaKelas>

model:                             # dimuat host: YOLO(<folder modul>/<weights>)
  weights: weights/best.pt         # .pt / .onnx / .engine / folder OpenVINO
  task: detect                     # detect | pose
  imgsz: 640
  conf: 0.35
  iou: 0.5
  classes: [person]                # nama dari model.names bobot kalian; [] = semua kelas

requirements:                      # dicek saat modul dimuat
  - boxmot==11.0.2

regions:                           # peran zona/garis yang dipahami modul
  - {role: rak,   kind: zone, label: "Rak produk", hint: "Area depan rak", multiple: true}
  - {role: kasir, kind: zone, label: "Antrean kasir"}
  - {role: pintu, kind: line, label: "Pintu masuk"}

params:                            # diatur pengguna per kamera (formulir dibuat otomatis)
  min_dwell_s: {type: float, default: 5, min: 0, label: "Dwell minimum (detik)"}
  # type: float | int | bool | str

metrics:                           # angka yang dikirim modul
  - {key: visitors_in, label: "Pengunjung masuk", unit: "orang", per_region: true, agg: last}
  - {key: zone_count,  label: "Orang di zona",    unit: "orang", per_region: true, agg: avg}
  # agg: avg | max | last | sum  (cara merangkum per menit/jam di dashboard & laporan)

events:                            # kejadian diskrit yang dikirim modul
  - {type: dwell_closed, label: "Kunjungan rak selesai", fields: {duration_s: float}}

dashboard:                         # widget bawaan (diformalkan di Fase 7)
  - {type: kpi, metric: visitors_in}
```

Host menolak modul (status **TIDAK VALID** di halaman Modul) jika ada field wajib yang hilang, `api_version` mayornya beda, file bobot/entrypoint tidak ada, requirement tidak terpenuhi, atau `key`/`type`/`role` duplikat.

## 3a. Preset (opsional, disarankan)

Preset adalah "template tempat" yang dipakai **wizard** di aplikasi (halaman Kamera → *Wizard preset…*). Pengguna memilih preset, lalu wizard memintanya menggambar setiap area satu per satu. Aturan alert dan dashboard terpasang otomatis. Preset ditulis sebagai JSON di `presets/<nama>.json`, **tanpa kode**.

```json
{
  "preset_id": "minimarket",
  "label": "Minimarket",
  "description": "Pengunjung per jam, antrean kasir, area rak promo",
  "slots": [
    {"key": "pintu", "kind": "line", "role": "pintu", "label": "Pintu masuk",
     "hint": "Tarik garis melintang pintu; panah = arah masuk"},
    {"key": "kasir", "kind": "zone", "role": "kasir", "label": "Antrean kasir"},
    {"key": "rak",   "kind": "zone", "role": "rak",   "label": "Rak promo",
     "multiple": true, "optional": true}
  ],
  "params": {"min_dwell_s": 5},
  "rules": [
    {"id": "antrean", "type": "metric_gt", "metric": "zone_count", "region_role": "kasir",
     "threshold": 4, "for_s": 30, "message": "Antrean kasir > 4 orang"}
  ],
  "dashboard": [
    {"type": "kpi", "metric": "visitors_in"},
    {"type": "hourly", "event": "masuk", "title": "Pengunjung per jam"},
    {"type": "heatmap"}
  ]
}
```

| Bagian | Aturan |
|---|---|
| `slots[].role` | Harus ada di `regions` manifest dengan `kind` yang sama. Modul membacanya lewat `Region.role` |
| `slots[].multiple` / `optional` | Boleh digambar lebih dari satu / boleh dilewati |
| `params` | Kunci harus ada di `params` manifest |
| `rules` | Format aturan alert aplikasi. Jenisnya: `metric_gt`, `metric_lt`, `event`, `camera_offline`, `module_error`, `overload`. `metric`/`event` harus terdaftar di manifest; `region_role` menyaring zona berdasarkan peran |
| `dashboard` | Jenis widget: `kpi`, `timeseries`, `hourly`, `events`, `heatmap`, dan `event_stats` (ringkasan satu field angka event: jumlah, rata-rata, median, maks, sebaran; mis. `{"type": "event_stats", "event": "dwell_closed", "field": "duration_s"}`). Merujuk `metric`/`event`/`field` manifest. Field berakhiran `_s` ditampilkan sebagai durasi (detik) |

Preset yang salah **tidak** membuat modul tidak valid. Preset itu hanya disembunyikan dari wizard, dan peringatannya tampil di halaman Modul. Contoh lengkap: [`modules/baseline/presets/toko-kecil.json`](../modules/baseline/presets/toko-kecil.json).

## 4. Antarmuka Python

Kelas entrypoint menurunkan `AnalyticsModule` dari [`aio_cctv/modules/api.py`](../aio_cctv/modules/api.py). File itu hanya bergantung pada `numpy` dan `supervision`. Saat dijalankan di dalam aplikasi, `from aio_cctv.modules.api import ...` langsung tersedia. Untuk pengembangan di repo kalian sendiri, salin saja file tersebut.

```python
import supervision as sv
from aio_cctv.modules.api import AnalyticsModule, CameraContext, Event, Metric, ModuleResult

from .tracker import MyTracker


class ConsumerModule(AnalyticsModule):
    def __init__(self, ctx: CameraContext):
        super().__init__(ctx)
        # ctx.fps = FPS yang BENAR-BENAR diproses (bisa lebih kecil dari FPS kamera)
        self.tracker = MyTracker(fps=ctx.fps)
        self.kasir = [r for r in ctx.zones() if r.role == "kasir"]
        self.kasir_px = [ctx.to_pixels(r.points) for r in self.kasir]   # 0..1 -> piksel

    def process(self, frame, detections: sv.Detections, ts: float) -> ModuleResult:
        tracks = self.tracker.update(detections, frame)    # frame untuk Re-ID (read-only!)
        metrics = [Metric("zone_count", float(n), r.id) for r, n in self._hitung(tracks)]
        events = []                                         # Event("dwell_closed", ts, ...)
        labels = {int(t): f"ID {t}" for t in tracks.tracker_id} if len(tracks) else {}
        return ModuleResult(tracks, metrics, events, labels)

    def close(self, ts: float) -> list[Event]:
        return []                                           # tutup sesi yang masih terbuka
```

| Objek | Isi |
|---|---|
| `CameraContext` | `camera_id`, `frame_wh`, `fps` (efektif), `regions` (zona/garis + `role`, koordinat 0..1), `params` (sudah digabung default manifest), `module_dir`; helper `zones()`, `lines()`, `to_pixels()` |
| `detections` | `sv.Detections` hasil YOLO host: sudah difilter `classes` & `conf`, **belum** punya `tracker_id` |
| `ts` | detik monoton dari sumber video. Untuk file video = posisi waktu video (deterministik) |
| `ModuleResult.tracks` | `sv.Detections` + `tracker_id` (digambar host) |
| `ModuleResult.metrics` | `Metric(key, value, region_id=None)`; `key` harus ada di manifest |
| `ModuleResult.events` | `Event(type, ts, region_id, track_id, data)`; `type` harus ada di manifest |
| `ModuleResult.labels` | `{track_id: "teks"}` untuk label kotak (default `ID n`) |
| `ModuleResult.heat` | opsional: grid akumulasi ≥ 0 (mis. heatmap); host yang menormalisasi & mewarnai |

## 5. Aturan

1. **`frame` read-only.** Jangan menggambar di atasnya, karena overlay digambar host dari `tracks`/`labels`. Menulis ke `frame` memicu error.
2. **`Event.data` hanya JSON sederhana, maksimal 2 KB.** Dilarang mengirim citra, crop, atau embedding Re-ID (privasi, UU PDP).
3. **Jangan** membuat thread, mengakses jaringan, atau menulis file. File yang perlu dibaca (bobot Re-ID, konfigurasi) diambil dari `ctx.module_dir`.
4. **Parameter yang bergantung FPS** (mis. `lost_track_buffer`) dihitung dari `ctx.fps`, bukan dari FPS kamera.
5. `process()` harus cepat. Waktunya diukur terpisah sebagai `module_ms` dan ikut menentukan berapa kamera yang sanggup dilayani satu PC.
6. Pengecualian dari modul tidak menjatuhkan aplikasi: hanya kamera yang memakai modul itu yang berhenti, dengan pesan "Modul error".
7. **Hitungan sebaiknya dikirim sebagai event**, bukan hanya angka kumulatif. Host menyimpan metrik sebagai agregat per menit, dan penghitung kumulatif (mis. total masuk) kembali ke 0 setiap kali kamera dijalankan ulang. Kirim `Event("masuk", ...)` per kejadian, sehingga dashboard dan laporan bisa menghitung per jam dengan benar. Metrik kumulatif boleh tetap dikirim untuk tampilan live.

## 6. Menguji sebelum serah terima

```bash
# kepatuhan: manifest, bobot, entrypoint, tipe hasil, waktu per frame
python scripts/check_module.py path/ke/modul --video samples/demo.mp4

# membuat keluaran acuan (jalankan di versi modul yang kalian anggap benar)
python scripts/check_module.py path/ke/modul --video samples/demo.mp4 --frames 0 \
    --record samples/expected.jsonl

# uji paritas: hasil di host harus identik dengan acuan
python scripts/check_module.py path/ke/modul --video samples/demo.mp4 --frames 0 \
    --expected samples/expected.jsonl
```

Contoh keluaran yang lulus (modul baseline, 1452 frame):

```
[OK] manifest valid (baseline 1.0.0, api 1.0)
[OK] file lengkap
[OK] requirements terpenuhi
[OK] bobot dimuat: kelas = {0: 'person'}, imgsz 640
[OK] entrypoint analyzer:BaselineModule dapat diinstansiasi
[OK] 1452 frame ref1_360p.mp4: tidak ada exception
[OK] semua Metric.key, Event.type & Event.data sesuai kontrak
[INFO] detect_ms p50 6.3 / p95 6.8 · module_ms p50 1.3 / p95 1.5
[OK] paritas vs baseline_ref.jsonl: identik
[INFO] event per tipe (hasil, acuan): {'dwell_closed': (38, 38), 'line_cross': (9, 9)}
```

Format `expected.jsonl` (satu baris per frame; baris terakhir `frame: -1` berisi event dari `close()`):

```json
{"frame": 12, "ts": 0.916, "tracks": [[3, 101.2, 40.5, 150.0, 210.7, 0]],
 "metrics": [["zone_count", "z1", 1.0]],
 "events": [{"type": "line_cross", "ts": 0.916, "region_id": "l1", "track_id": 3,
             "data": {"direction": "in"}}]}
```

Jika `expected.jsonl` dibuat dari skrip kalian sendiri (di luar aplikasi), pastikan format dan `ts`-nya sama. Paling mudah, buat acuan dengan perintah `--record` di atas.

Setelah lulus, kirim folder atau ZIP-nya. Pemasangan di aplikasi dilakukan lewat **Modul → Pasang dari folder…/ZIP…**.

## 7. Versi kontrak

- `api_version` mengikuti SemVer. Host v1.x menerima modul `1.*`.
- Perubahan yang tidak merusak (field opsional baru) → versi minor.
- Perubahan yang merusak → versi mayor, hanya lewat kesepakatan tertulis ketiga TA.

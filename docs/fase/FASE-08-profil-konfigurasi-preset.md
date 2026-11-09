# FASE 8 — Profil Kamera, Preset Modul & Import/Export

> Sebelumnya: [FASE-07](FASE-07-penyimpanan-dashboard-alert-laporan.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-09 Optimasi & DVR](FASE-09-optimasi-sumber-daya-packaging.md)
> Referensi Proposal: **§4 RM-3 lama** (konfigurasi tanpa ubah kode), **§6** (perbedaan antar-bisnis ditentukan konfigurasi), **§8.4** (konfigurasi JSON)
> Estimasi: **1–2 minggu** · Milestone: **M4 — Aplikasi lengkap** · Menjawab: **RM-1** (manajemen zona, tanpa ubah kode)
> *Revisi 2026-11-09: dahulu FASE-07. "Template vertikal" (F&B/ritel/klinik) yang dibuat host **diganti** dengan **preset yang dikirim modul**, karena pengetahuan domain kini milik TA-1/TA-2. Sinkronisasi cloud diperkecil menjadi catatan opsional.*

---

## 1. Tujuan

1. **Skema profil v2**: satu profil per kamera = sumber + **modul terpilih** + parameter modul + zona/garis **bertag peran** + aturan alert + tata letak dashboard. Profil v1 (Fase 1) dimigrasi otomatis.
2. **Preset dari modul**: paket modul membawa `presets/*.json` (mis. TA-1: "Minimarket", "Kafe"; TA-2: "Kantor terbuka"). Wizard host memandu pengguna menggambar setiap slot.
3. **Import/export profil** (`.aioprofile`).
4. **Bukti RM-1**: memasang modul/preset baru dan mengganti modul pada kamera = **0 baris kode host berubah**.

## 2. Konsep: Modul → Preset → Profil

| | **Modul** (Fase 6) | **Preset** (di dalam paket modul) | **Profil** (milik pengguna) |
|---|---|---|---|
| Pembuat | TA-1 / TA-2 | TA-1 / TA-2 | Pengguna, lewat wizard host |
| Isi | Model, tracker, analitik, deklarasi metrik/event/peran zona | Slot zona per peran + hint, aturan default, dashboard default, nilai parameter | Koordinat zona nyata di kamera tertentu + aturan yang disesuaikan |
| Contoh | `ta1-perilaku-konsumen` | "Minimarket: rak promo, antrean kasir, pintu" | "Toko Sumber Rejeki – Kamera Kasir" |

## 3. Skema Profil v2 — `aio_cctv/core/profile_v2.py`

```python
from typing import Literal

from pydantic import BaseModel, Field

from aio_cctv.zones.models import Line, SourceConfig, Zone


class ZoneV2(Zone):
    role: str | None = None                 # dari manifest.regions[].role


class LineV2(Line):
    role: str | None = None


class ModuleBinding(BaseModel):
    module_id: str
    module_version: str                     # versi saat profil dibuat; peringatan bila berbeda
    preset_id: str | None = None
    params: dict = Field(default_factory=dict)


class Rule(BaseModel):                      # tipe & field dari Fase 7 §3.5
    id: str
    type: Literal["metric_gt", "metric_lt", "event", "camera_offline", "module_error", "overload"]
    metric: str | None = None
    event: str | None = None
    region_role: str | None = None
    threshold: float | None = None
    for_s: float = 0
    window_s: float = 60
    min_count: int = 1
    cooldown_s: float = 300
    message: str = ""
    enabled: bool = True


class DashboardWidget(BaseModel):
    type: Literal["kpi", "timeseries", "hourly", "events", "heatmap", "health"]
    metric: str | None = None
    event: str | None = None
    title: str = ""


class PerfSettings(BaseModel):              # diisi Fase 9
    stream: Literal["main", "sub"] = "sub"
    target_fps: float = 10.0
    priority: Literal["normal", "low"] = "normal"


class ProfileV2(BaseModel):
    schema_version: Literal[2] = 2
    profile_id: str
    name: str = ""
    source: SourceConfig | None = None
    module: ModuleBinding
    zones: list[ZoneV2] = Field(default_factory=list)
    lines: list[LineV2] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    dashboard: list[DashboardWidget] = Field(default_factory=list)
    perf: PerfSettings = Field(default_factory=PerfSettings)


def migrate(data: dict) -> ProfileV2:
    """v1 (Fase 1) -> v2: zona/garis tetap, modul diisi baseline."""
    if data.get("schema_version", 1) == 1:
        data = {**data, "schema_version": 2,
                "module": {"module_id": "baseline", "module_version": "1.0.0"}}
        data.pop("target_classes", None)    # kini ditentukan manifest modul
    return ProfileV2.model_validate(data)
```

`params` divalidasi terhadap `manifest.params` (tipe, min/max, default). Kalau modul diperbarui dan sebuah parameter hilang atau berubah tipe, profil tetap dimuat dengan nilai default, lalu pengguna diberi peringatan.

## 4. Format Preset — `modules/<id>/presets/<preset>.json`

```json
{
  "preset_id": "minimarket",
  "label": "Minimarket",
  "description": "Pengunjung per jam, antrean kasir, area rak yang paling sering didatangi",
  "slots": [
    { "key": "pintu", "kind": "line", "role": "pintu", "label": "Pintu masuk",
      "hint": "Tarik garis melintang pintu, arah panah = masuk" },
    { "key": "kasir", "kind": "zone", "role": "kasir", "label": "Antrean kasir",
      "hint": "Gambar lantai tempat orang mengantre" },
    { "key": "rak",   "kind": "zone", "role": "rak",   "label": "Rak promo",
      "multiple": true, "optional": true }
  ],
  "params": { "min_dwell_s": 5 },
  "rules": [
    { "id": "antrean", "type": "metric_gt", "metric": "people_count", "region_role": "kasir",
      "threshold": 4, "for_s": 30, "message": "Antrean kasir > 4 orang" }
  ],
  "dashboard": [
    { "type": "kpi", "metric": "visitors_in" },
    { "type": "hourly", "metric": "visitors_in", "title": "Pengunjung per jam" },
    { "type": "heatmap" }
  ]
}
```

Isi preset (peran, metrik, aturan) adalah keputusan TA-1/TA-2. Host hanya memvalidasi bahwa `role`, `metric`, dan `event` yang dirujuk memang ada di manifest modulnya.

## 5. Wizard (GUI)

```
Langkah 1  Pilih kamera ................ [Kasir (Hikvision DVR ch 3) ▼]
Langkah 2  Pilih modul ................. ( ) Perilaku Konsumen (TA-1)  ( ) Produktivitas (TA-2)  ( ) Baseline
Langkah 3  Pilih preset ................ ( ) Minimarket  ( ) Kafe  ( ) Kosong
Langkah 4  Gambar area per slot ........ "Gambar lantai tempat orang mengantre"   [Lewati — opsional]
Langkah 5  Parameter & aturan .......... dibangkitkan dari manifest; nilai default dari preset
Langkah 6  Kinerja ..................... Stream: sub ▼  Target FPS: 10   (Fase 9)
Langkah 7  Ringkasan & simpan
```

`QWizard` dengan halaman dinamis per slot. Editor zona Fase 5 dipakai ulang sebagai widget.

## 6. Import / Export — `.aioprofile`

ZIP berisi `profile.json`, `snapshot.jpg` (acuan visual), dan `meta.json` (versi aplikasi, `module_id@version`, resolusi). Kredensial dan URL kamera **tidak** disertakan. Saat import, bila modul belum terpasang atau versinya berbeda, aplikasi memberi tahu pengguna.

## 7. Protokol Uji "Tanpa Ubah Kode" (bukti RM-1)

1. Bekukan kode host: `git tag v0.8-freeze`.
2. Pasang modul TA-1 dan TA-2 **dari ZIP** lewat GUI.
3. Untuk setiap modul × ≥1 preset: penguji menjalankan wizard dari nol pada video/kamera yang sesuai. Catat waktu (menit), jumlah langkah, dan `git diff --stat v0.8-freeze` (harus kosong).
4. Ganti modul kamera A dari TA-1 ke TA-2 dan sebaliknya. Pastikan dashboard, alert, dan laporan menyesuaikan.
5. Pasang **versi baru** sebuah modul (mis. v0 → v1). Pastikan profil lama tetap termuat.

| Modul | Preset | Sumber video | Waktu konfigurasi | Langkah | Baris kode host diubah | Dashboard/alert sesuai? |
|---|---|---|---|---|---|---|
| TA-1 | Minimarket | … | … | … | **0** | ✓/✗ |
| TA-2 | Kantor | … | … | … | **0** | ✓/✗ |
| Baseline | — | … | … | … | **0** | ✓/✗ |

## 8. (Opsional) Sinkronisasi Metrik ke Cloud

Hanya dikerjakan bila M5 tercapai lebih awal. Thread `CloudSync` mengirim baris `metric_minute` yang belum terkirim ke endpoint FastAPI minimal (`POST /v1/metrics`, API key per lokasi). Isinya hanya angka agregat, tanpa video. Toggle **nonaktif secara default**. Jika tidak sempat, cukup dijelaskan sebagai rancangan di Bab III/V.

## 9. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| `ProfileV2` + migrasi + JSON Schema | `aio_cctv/core/profile_v2.py`, `docs/profile.schema.json` |
| Format preset (bagian dari kontrak modul) | `docs/kontrak-modul.md` |
| Wizard, import/export | `aio_cctv/gui/` |
| Tabel uji "tanpa ubah kode" | `docs/logbook/` |

## 10. Definition of Done

- [ ] Profil v1 lama termuat otomatis sebagai v2 dengan modul baseline.
- [ ] Wizard berjalan untuk preset dari ≥2 modul.
- [ ] Export → import di PC lain berfungsi.
- [ ] Uji §7 lulus dengan **0 baris kode host diubah**.
- [ ] Tag `v0.8-profiles` → **Milestone M4**.

## 11. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Rekan belum membuat preset | Profil tetap bisa dibuat manual (preset "Kosong" + peran dari manifest) |
| Versi modul berubah dan merusak profil | `module_version` di profil + validasi params dengan nilai default + peringatan |
| Skema profil berubah lagi | `schema_version` + `migrate()` berantai + unit test profil lama |

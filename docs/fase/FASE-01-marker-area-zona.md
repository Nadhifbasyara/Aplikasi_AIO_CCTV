# FASE 1 — Marker Area: Editor Zona & Garis di Atas Video

> Sebelumnya: [FASE-00](FASE-00-persiapan-lingkungan.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-02 Deteksi Sederhana](FASE-02-deteksi-sederhana-video-youtube.md)
> Referensi Proposal: **§8.4** (Konfigurasi per tenant — JSON), **§8.5** (“Editor zona visual… hasilkan JSON otomatis”), **§4 RM-3** (konfigurasi tanpa ubah kode), **§1** (konfigurasi zona & aturan)
> Estimasi: **1–2 minggu** · Milestone: bagian pertama **M1 (MVP awal)**

---

## 1. Tujuan

1. Membuat **skema data zona & garis** (model Pydantic) yang menjadi “bahasa bersama” seluruh fase — evolusi dari JSON `tenant_kafe.json` Proposal §8.4.
2. Membuat **editor marker area** sederhana: buka video (hasil unduhan YouTube), ambil satu frame, klik untuk menggambar **poligon zona** dan **garis hitung**, lalu simpan ke JSON.
3. Membuat **viewer** yang memutar video dengan overlay zona dari JSON — bukti bahwa zona tersimpan benar.

> Fase ini sengaja memakai jendela **OpenCV** (cepat dibuat). Editor visual versi GUI (drag titik, rename, warna) dibangun ulang di Fase 5 dengan PyQt6, **memakai model data yang sama**.

## 2. Keputusan Desain

| Keputusan | Alasan |
|---|---|
| **Koordinat ternormalisasi 0..1** (bukan piksel seperti Proposal §8.4) | Kamera IP punya *main-stream* (mis. 2560×1440) dan *sub-stream* (640×360). Zona yang digambar di satu resolusi tetap valid di resolusi lain. Konversi ke piksel dilakukan saat runtime. |
| **Pydantic v2** untuk validasi | Konfigurasi rusak (poligon < 3 titik, koordinat di luar frame) ditolak saat dimuat, bukan menyebabkan crash di tengah analitik. Skema JSON bisa diekspor otomatis (Fase 7). |
| Field `schema_version` | Memungkinkan migrasi skema v1 → v2 di Fase 7 tanpa merusak profil lama. |
| Tipe zona: `dwell`, `occupancy`, `presence` | Mengikuti Proposal §8.4 (`dwell`, `occupancy`) + `presence` untuk sekadar hitung orang. |
| Anchor titik = **bawah-tengah bbox** (kaki) | Sama seperti Proposal §8.2 (heatmap di titik kaki). Orang “berada di zona lantai” ditentukan oleh posisi kaki, bukan kepala. |

## 3. Struktur File Fase Ini

```
aio_cctv/
├── core/
│   └── colors.py            # helper warna
├── zones/
│   ├── models.py            # Profile, Zone, Line, SourceConfig, to_pixels
│   ├── editor_cv.py         # editor marker area (OpenCV)
│   └── viewer_cv.py         # putar video + overlay zona
configs/profiles/
└── demo_ref1.json           # hasil editor
tests/
└── test_zone_models.py
```

---

## 4. Implementasi

### 4.1 Model data — `aio_cctv/zones/models.py`

```python
"""Model konfigurasi zona/garis (evolusi JSON Proposal §8.4, skema v1)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator

Point = tuple[float, float]          # (x, y) ternormalisasi 0..1


def _check_normalized(points: list[Point]) -> list[Point]:
    for x, y in points:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"titik {x, y} harus ternormalisasi 0..1")
    return points


class Zone(BaseModel):
    id: str
    name: str
    type: Literal["dwell", "occupancy", "presence"] = "dwell"
    polygon: list[Point]
    color: str = "#E53935"

    @field_validator("polygon")
    @classmethod
    def _valid_polygon(cls, v: list[Point]) -> list[Point]:
        if len(v) < 3:
            raise ValueError("poligon minimal 3 titik")
        return _check_normalized(v)


class Line(BaseModel):
    id: str
    name: str
    type: Literal["counting"] = "counting"
    p1: Point
    p2: Point
    color: str = "#FDD835"

    @field_validator("p1", "p2")
    @classmethod
    def _valid_point(cls, v: Point) -> Point:
        return _check_normalized([v])[0]


class SourceConfig(BaseModel):
    type: Literal["file", "rtsp", "webcam"] = "file"
    uri: str


class Profile(BaseModel):
    schema_version: int = 1
    profile_id: str
    vertical: str = "generic"                     # fnb, retail, clinic, ... (Proposal §1)
    source: SourceConfig
    frame_size: tuple[int, int] | None = None     # resolusi saat zona digambar (info saja)
    target_classes: list[str] = Field(default_factory=lambda: ["person"])
    zones: list[Zone] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")


def to_pixels(points: list[Point], w: int, h: int) -> np.ndarray:
    """Ubah titik ternormalisasi ke piksel (int32, siap untuk OpenCV/Supervision)."""
    return np.array([[round(x * (w - 1)), round(y * (h - 1))] for x, y in points], dtype=np.int32)


def to_normalized(points: list[tuple[int, int]], w: int, h: int) -> list[Point]:
    return [(round(x / (w - 1), 4), round(y / (h - 1), 4)) for x, y in points]
```

### 4.2 Helper warna — `aio_cctv/core/colors.py`

```python
def hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


PALETTE = ["#E53935", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA", "#00ACC1"]
```

### 4.3 Editor marker area — `aio_cctv/zones/editor_cv.py`

Fitur: mode **zona** (klik ≥3 titik, Enter untuk menutup) dan mode **garis** (klik 2 titik), undo, hapus zona terakhir, pilih frame dengan argumen `--time`, simpan JSON.

```python
"""Editor marker area berbasis OpenCV (Fase 1).

Kontrol:
  Klik kiri  : tambah titik
  Enter      : tutup poligon (mode zona)
  L          : ganti mode ZONA <-> GARIS
  U          : undo titik terakhir
  D          : hapus zona/garis terakhir
  S          : simpan JSON
  Q / Esc    : keluar
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

from aio_cctv.core.colors import PALETTE, hex_to_bgr
from aio_cctv.zones.models import Line, Profile, SourceConfig, Zone, to_normalized, to_pixels

WIN = "AIO-CCTV | Editor Zona"
HELP = "Klik=titik  Enter=tutup zona  L=mode  U=undo  D=hapus  S=simpan  Q=keluar"


def _pt(a) -> tuple[int, int]:
    return int(a[0]), int(a[1])


class ZoneEditor:
    def __init__(self, frame: np.ndarray, profile: Profile):
        self.base = frame
        self.h, self.w = frame.shape[:2]
        self.profile = profile
        self.points: list[tuple[int, int]] = []
        self.mode = "zona"
        self.dirty = False

    # ---------- interaksi ----------
    def on_mouse(self, event, x, y, flags, param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((x, y))
            if self.mode == "garis" and len(self.points) == 2:
                self.commit()

    def commit(self) -> None:
        if self.mode == "zona" and len(self.points) >= 3:
            n = len(self.profile.zones) + 1
            self.profile.zones.append(Zone(
                id=f"z{n}", name=f"Zona {n}",
                polygon=to_normalized(self.points, self.w, self.h),
                color=PALETTE[(n - 1) % len(PALETTE)],
            ))
            self.dirty = True
        elif self.mode == "garis" and len(self.points) == 2:
            n = len(self.profile.lines) + 1
            p1, p2 = to_normalized(self.points, self.w, self.h)
            self.profile.lines.append(Line(id=f"l{n}", name=f"Garis {n}", p1=p1, p2=p2))
            self.dirty = True
        self.points = []

    def undo(self) -> None:
        if self.points:
            self.points.pop()

    def delete_last(self) -> None:
        items = self.profile.zones if self.mode == "zona" else self.profile.lines
        if items:
            items.pop()
            self.dirty = True

    # ---------- tampilan ----------
    def render(self) -> np.ndarray:
        img = self.base.copy()
        overlay = img.copy()
        for z in self.profile.zones:
            pts = to_pixels(z.polygon, self.w, self.h)
            cv2.fillPoly(overlay, [pts], hex_to_bgr(z.color))
        img = cv2.addWeighted(overlay, 0.25, img, 0.75, 0)

        for z in self.profile.zones:
            pts = to_pixels(z.polygon, self.w, self.h)
            cv2.polylines(img, [pts], True, hex_to_bgr(z.color), 2)
            cv2.putText(img, f"{z.name} [{z.type}]", _pt(pts[0]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        for ln in self.profile.lines:
            p1, p2 = to_pixels([ln.p1, ln.p2], self.w, self.h)
            cv2.line(img, _pt(p1), _pt(p2), hex_to_bgr(ln.color), 3)
            cv2.putText(img, ln.name, _pt(p1), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        for p in self.points:                              # bentuk yang sedang digambar
            cv2.circle(img, p, 5, (0, 255, 0), -1)
        if len(self.points) > 1:
            cv2.polylines(img, [np.array(self.points, np.int32)], False, (0, 255, 0), 1)

        status = f"MODE: {self.mode.upper()} | zona={len(self.profile.zones)} garis={len(self.profile.lines)}"
        status += " *belum disimpan*" if self.dirty else ""
        for i, text in enumerate([status, HELP]):
            cv2.putText(img, text, (10, 25 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
            cv2.putText(img, text, (10, 25 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        return img


def grab_frame(uri: str, at_sec: float) -> np.ndarray:
    cap = cv2.VideoCapture(uri)
    cap.set(cv2.CAP_PROP_POS_MSEC, at_sec * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"Gagal membaca frame dari {uri} pada detik {at_sec}")
    return frame


def main() -> None:
    ap = argparse.ArgumentParser(description="Editor marker area (zona & garis)")
    ap.add_argument("video", help="path video (atau URL RTSP, dipakai mulai Fase 4)")
    ap.add_argument("--profile", required=True, help="path JSON profil (dibuat jika belum ada)")
    ap.add_argument("--time", type=float, default=0.0, help="detik frame acuan")
    ap.add_argument("--vertical", default="generic")
    args = ap.parse_args()

    frame = grab_frame(args.video, args.time)
    h, w = frame.shape[:2]
    path = Path(args.profile)
    if path.exists():
        profile = Profile.load(path)
    else:
        src_type = "rtsp" if args.video.startswith("rtsp://") else "file"
        profile = Profile(profile_id=path.stem, vertical=args.vertical,
                          source=SourceConfig(type=src_type, uri=args.video))
    profile.frame_size = (w, h)

    editor = ZoneEditor(frame, profile)
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WIN, editor.on_mouse)

    while True:
        cv2.imshow(WIN, editor.render())
        key = cv2.waitKey(20) & 0xFF
        if key in (13, 10):
            editor.commit()
        elif key == ord("l"):
            editor.points, editor.mode = [], ("garis" if editor.mode == "zona" else "zona")
        elif key == ord("u"):
            editor.undo()
        elif key == ord("d"):
            editor.delete_last()
        elif key == ord("s"):
            profile.save(path)
            editor.dirty = False
            print(f"[simpan] {path}")
        elif key in (ord("q"), 27):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
```

Pemakaian:

```bash
python -m aio_cctv.zones.editor_cv data/videos/ref1.mp4 --profile configs/profiles/demo_ref1.json --time 5 --vertical fnb
```

Nama & tipe zona di fase ini diubah langsung di JSON (mis. `"Zona 1"` → `"Meja Pelanggan"`, `"type": "dwell"`). Di Fase 5 hal ini dilakukan lewat GUI.

Contoh hasil (bandingkan dengan Proposal §8.4 — kini ternormalisasi):

```json
{
  "schema_version": 1,
  "profile_id": "demo_ref1",
  "vertical": "fnb",
  "source": { "type": "file", "uri": "data/videos/ref1.mp4" },
  "frame_size": [1280, 720],
  "target_classes": ["person"],
  "zones": [
    { "id": "z1", "name": "Meja Pelanggan", "type": "dwell",
      "polygon": [[0.078,0.278],[0.391,0.278],[0.391,0.764],[0.078,0.764]], "color": "#E53935" },
    { "id": "z2", "name": "Area Kasir", "type": "occupancy",
      "polygon": [[0.406,0.25],[0.547,0.25],[0.547,0.583],[0.406,0.583]], "color": "#1E88E5" }
  ],
  "lines": [
    { "id": "l1", "name": "Pintu Masuk", "type": "counting",
      "p1": [0.039,0.167], "p2": [0.234,0.167], "color": "#FDD835" }
  ]
}
```

### 4.4 Viewer overlay — `aio_cctv/zones/viewer_cv.py`

Memutar video dan menggambar zona dari JSON, **pada resolusi berapa pun**. Uji penting: jalankan dengan video yang sama tapi di-*resize* (mis. 640×360) untuk membuktikan normalisasi bekerja.

```python
import argparse

import cv2

from aio_cctv.core.colors import hex_to_bgr
from aio_cctv.zones.models import Profile, to_pixels


def draw_profile(frame, profile: Profile):
    h, w = frame.shape[:2]
    for z in profile.zones:
        cv2.polylines(frame, [to_pixels(z.polygon, w, h)], True, hex_to_bgr(z.color), 2)
    for ln in profile.lines:
        p1, p2 = to_pixels([ln.p1, ln.p2], w, h)
        cv2.line(frame, tuple(map(int, p1)), tuple(map(int, p2)), hex_to_bgr(ln.color), 3)
    return frame


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--video", help="override sumber di profil")
    args = ap.parse_args()
    profile = Profile.load(args.profile)
    cap = cv2.VideoCapture(args.video or profile.source.uri)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        cv2.imshow("AIO-CCTV | Viewer", draw_profile(frame, profile))
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
```

```bash
ffmpeg -i data/videos/ref1.mp4 -vf scale=640:360 -an data/videos/ref1_360p.mp4
python -m aio_cctv.zones.viewer_cv --profile configs/profiles/demo_ref1.json --video data/videos/ref1_360p.mp4
```

### 4.5 Unit test — `tests/test_zone_models.py`

```python
import pytest
from pydantic import ValidationError

from aio_cctv.zones.models import Profile, SourceConfig, Zone, to_normalized, to_pixels


def test_roundtrip(tmp_path):
    p = Profile(profile_id="t", source=SourceConfig(uri="x.mp4"),
                zones=[Zone(id="z1", name="A", polygon=[(0, 0), (1, 0), (1, 1)])])
    f = tmp_path / "p.json"
    p.save(f)
    assert Profile.load(f) == p


def test_polygon_min_points():
    with pytest.raises(ValidationError):
        Zone(id="z", name="x", polygon=[(0, 0), (1, 1)])


def test_out_of_range():
    with pytest.raises(ValidationError):
        Zone(id="z", name="x", polygon=[(0, 0), (1.2, 0), (1, 1)])


def test_pixel_conversion_is_resolution_independent():
    norm = to_normalized([(0, 0), (1279, 719)], 1280, 720)
    assert to_pixels(norm, 640, 360).tolist() == [[0, 0], [639, 359]]
```

```bash
pytest -q
```

---

## 5. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| Skema profil v1 (Pydantic) | `aio_cctv/zones/models.py` |
| Editor marker area OpenCV | `aio_cctv/zones/editor_cv.py` |
| Viewer overlay | `aio_cctv/zones/viewer_cv.py` |
| ≥3 profil contoh untuk video YouTube uji (mis. 1 kafe, 1 ritel, 1 koridor) | `configs/profiles/*.json` |
| Unit test | `tests/test_zone_models.py` |
| Screenshot editor & overlay (bahan laporan Bab IV) | `docs/logbook/img/` |

## 6. Definition of Done

- [ ] Dapat menggambar ≥2 zona dan ≥1 garis lalu menyimpannya ke JSON.
- [ ] JSON yang rusak (poligon 2 titik / koordinat > 1) ditolak dengan pesan jelas.
- [ ] Overlay tepat posisinya pada video resolusi asli **dan** hasil resize (bukti normalisasi).
- [ ] `pytest` hijau.
- [ ] Tag `v0.1-zone-editor`.

## 7. Catatan untuk Laporan TA

- Jelaskan alasan normalisasi koordinat sebagai **kontribusi rekayasa** kecil terhadap Proposal §8.4 (relevan dengan kamera multi-stream di Fase 4).
- Diagram kelas `Profile – Zone – Line – SourceConfig` → Bab III (perancangan).
- Hubungkan dengan **RM-3 (Proposal §4)**: perilaku analitik ditentukan oleh berkas konfigurasi, bukan kode.

## 8. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Koordinat mouse salah pada jendela `WINDOW_NORMAL` yang di-scale | OpenCV memetakan balik ke koordinat gambar; verifikasi dengan viewer. Jika bermasalah, pakai `WINDOW_AUTOSIZE` |
| Frame acuan gelap/blur | Gunakan `--time` untuk memilih frame yang jelas |
| Poligon self-intersecting (bentuk “kupu-kupu”) | Didiamkan dulu; validasi `shapely.Polygon(...).is_valid` ditambahkan di editor GUI Fase 5 |

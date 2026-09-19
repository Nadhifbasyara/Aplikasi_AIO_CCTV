# FASE 2 — Deteksi Sederhana pada Video YouTube (MVP Awal)

> Sebelumnya: [FASE-01](FASE-01-marker-area-zona.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-03 Tracking & Analitik](FASE-03-tracking-analitik-inti.md)
> Referensi Proposal: **§8.2** (skrip inti — bagian deteksi), **§7** (YOLOv11, “Apakah YOLO cukup?”), **§2** (video referensi), **§5** (objek utama: *person*), **§13** (“YOLO pretrained COCO sudah cukup”)
> Estimasi: **1–2 minggu** · Milestone: **M1 — MVP Awal selesai**

---

## 1. Tujuan

1. Menjalankan **YOLOv11 pretrained COCO** untuk mendeteksi **orang** pada video YouTube hasil Fase 0.
2. Menggabungkan hasil deteksi dengan **zona dari Fase 1** → menampilkan **jumlah orang per zona** secara langsung.
3. Menghasilkan **video beranotasi** + **log CSV jumlah per zona per detik** + ringkasan (FPS rata-rata, jumlah maksimum per zona).
4. Membuat **perbandingan awal model** (`yolo11n` vs `yolo11s` vs `yolo11m`) pada perangkat uji — dasar pemilihan model di fase selanjutnya.

**Hasil fase ini = target awal proyek:** *aplikasi yang bisa menandai area dari video dan melakukan deteksi sederhana pada video dari YouTube.*

## 2. Ruang Lingkup

| Termasuk | Belum termasuk (fase berikutnya) |
|---|---|
| Deteksi orang per frame | ID tracking (Fase 3) |
| Hitung orang di dalam zona per frame | Dwell time, line-crossing, heatmap (Fase 3) |
| Anotasi kotak + confidence | Sumber RTSP (Fase 4) |
| Ekspor video & CSV | GUI (Fase 5) |

## 3. Struktur File

```
aio_cctv/inference/
└── detector.py              # kelas Detector (dipakai ulang sampai akhir)
scripts/
├── detect_zones.py          # CLI MVP: video + profil -> video beranotasi + CSV
└── compare_models.py        # benchmark n/s/m
outputs/                     # (gitignore) hasil run
```

---

## 4. Implementasi

### 4.1 Kelas Detector — `aio_cctv/inference/detector.py`

Dibungkus dalam kelas agar backend dapat diganti (ONNX/TensorRT) di Fase 8 tanpa mengubah kode pemanggil.

```python
"""Pembungkus detektor objek. Backend default: Ultralytics YOLOv11 (Proposal §7)."""
from __future__ import annotations

import numpy as np
import supervision as sv
from ultralytics import YOLO

COCO_NAME_TO_ID = {"person": 0}   # diperluas bila target_classes bertambah (Proposal §5)


class Detector:
    def __init__(self, weights: str = "models/yolo11n.pt", classes: list[str] | None = None,
                 conf: float = 0.35, iou: float = 0.5, imgsz: int = 640, device: str | None = None):
        self.model = YOLO(weights)
        self.class_ids = [COCO_NAME_TO_ID[c] for c in (classes or ["person"])]
        self.conf, self.iou, self.imgsz, self.device = conf, iou, imgsz, device

    def __call__(self, frame: np.ndarray) -> sv.Detections:
        result = self.model(frame, classes=self.class_ids, conf=self.conf, iou=self.iou,
                            imgsz=self.imgsz, device=self.device, verbose=False)[0]
        return sv.Detections.from_ultralytics(result)

    def warmup(self) -> None:
        self(np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8))
```

> Perbedaan dengan Proposal §8.2: filter kelas dilakukan **di dalam model** (`classes=[0]`) bukan setelahnya — NMS lebih efisien dan tidak membuang waktu memproses kelas lain.

### 4.2 CLI MVP — `scripts/detect_zones.py`

```python
"""MVP Fase 2: deteksi orang + hitung per zona pada video (mis. unduhan YouTube).

Contoh:
  python scripts/detect_zones.py --profile configs/profiles/demo_ref1.json --show
"""
import argparse
import csv
import json
import time
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

from aio_cctv.inference.detector import Detector
from aio_cctv.zones.models import Profile, to_pixels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--video", help="override sumber di profil")
    ap.add_argument("--model", default="models/yolo11n.pt")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    profile = Profile.load(args.profile)
    video = args.video or profile.source.uri
    info = sv.VideoInfo.from_video_path(video)
    w, h = info.resolution_wh
    out_dir = Path(args.out) / profile.profile_id
    out_dir.mkdir(parents=True, exist_ok=True)

    detector = Detector(args.model, profile.target_classes, args.conf, imgsz=args.imgsz)
    detector.warmup()

    # Zona -> PolygonZone (Supervision, Proposal §7 "Analitik zona")
    zones = []
    for z in profile.zones:
        pz = sv.PolygonZone(polygon=to_pixels(z.polygon, w, h),
                            triggering_anchors=(sv.Position.BOTTOM_CENTER,))
        ann = sv.PolygonZoneAnnotator(zone=pz, color=sv.Color.from_hex(z.color),
                                      thickness=2, text_scale=0.7)
        zones.append((z, pz, ann))

    box_ann = sv.BoxAnnotator(thickness=2)
    label_ann = sv.LabelAnnotator(text_scale=0.5)

    csv_f = open(out_dir / "counts_per_second.csv", "w", newline="")
    writer = csv.writer(csv_f)
    writer.writerow(["second", "total_person"] + [z.name for z in profile.zones])

    max_count = {z.name: 0 for z in profile.zones}
    infer_times: list[float] = []
    fps = info.fps or 25

    with sv.VideoSink(str(out_dir / "annotated.mp4"), info) as sink:
        for idx, frame in enumerate(sv.get_video_frames_generator(video)):
            t0 = time.perf_counter()
            det = detector(frame)
            infer_times.append(time.perf_counter() - t0)

            counts = {}
            for z, pz, _ in zones:
                counts[z.name] = int(pz.trigger(det).sum())
                max_count[z.name] = max(max_count[z.name], counts[z.name])

            labels = [f"person {c:.2f}" for c in det.confidence]
            vis = box_ann.annotate(frame.copy(), det)
            vis = label_ann.annotate(vis, det, labels=labels)
            for _, _, ann in zones:
                vis = ann.annotate(vis)          # menampilkan current_count di tengah zona
            cv2.putText(vis, f"Total orang: {len(det)}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            sink.write_frame(vis)

            if idx % int(round(fps)) == 0:     # sampel 1x per detik video
                writer.writerow([idx // int(round(fps)), len(det)] + list(counts.values()))

            if args.show:
                cv2.imshow("AIO-CCTV | Deteksi", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    csv_f.close()
    cv2.destroyAllWindows()
    summary = {
        "video": video, "model": args.model, "imgsz": args.imgsz, "frames": len(infer_times),
        "mean_infer_ms": round(1000 * float(np.mean(infer_times)), 2),
        "fps_infer": round(1 / float(np.mean(infer_times)), 1),
        "max_person_per_zone": max_count,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
```

Keluaran di `outputs/<profile_id>/`:
- `annotated.mp4` — kotak orang + angka jumlah di setiap zona,
- `counts_per_second.csv` — bahan grafik awal & validasi manual,
- `summary.json` — FPS inferensi & jumlah maksimum per zona.

### 4.3 Perbandingan model — `scripts/compare_models.py`

```python
"""Benchmark sederhana yolo11n/s/m pada satu video (bahan pemilihan model, Proposal §7 & §10)."""
import sys
import time

import numpy as np
import supervision as sv

from aio_cctv.inference.detector import Detector

video = sys.argv[1]
frames = [f for _, f in zip(range(300), sv.get_video_frames_generator(video))]  # 300 frame pertama

print(f"{'model':<12}{'imgsz':>6}{'ms/frame':>10}{'FPS':>7}{'avg_det':>9}")
for weights in ["models/yolo11n.pt", "models/yolo11s.pt", "models/yolo11m.pt"]:
    for imgsz in (640, 480):
        d = Detector(weights, imgsz=imgsz)
        d.warmup()
        times, counts = [], []
        for f in frames:
            t0 = time.perf_counter()
            counts.append(len(d(f)))
            times.append(time.perf_counter() - t0)
        ms = 1000 * np.mean(times)
        print(f"{weights.split('/')[-1]:<12}{imgsz:>6}{ms:>10.1f}{1000 / ms:>7.1f}{np.mean(counts):>9.2f}")
```

Hasilnya dicatat di logbook sebagai **Tabel Benchmark Awal** (akan dibandingkan kembali setelah optimasi Fase 8).

---

## 5. Validasi Sederhana (pra-evaluasi)

Evaluasi formal ada di Fase 9, tetapi di sini lakukan **sanity check** agar masalah terlihat sejak awal:

1. Ambil **20 frame acak** dari tiap video uji (`ffmpeg -vf "select='not(mod(n\,150))'" -vsync vfr frame_%03d.jpg`).
2. Hitung manual jumlah orang per zona di setiap frame.
3. Bandingkan dengan `counts_per_second.csv` → hitung **MAE jumlah orang per zona**.
4. Catat pola kegagalan: orang terpotong tepi frame, duduk terhalang meja, pantulan kaca, kerumunan rapat, sudut kamera atas (*top-down*).

| Video | Kondisi | MAE per zona | Kegagalan dominan |
|---|---|---|---|
| ref1 | … | … | … |
| ref2 | … | … | … |

> Temuan kegagalan ini menjadi dasar pembahasan **RM-2 (Proposal §4)** dan keputusan apakah perlu model lebih besar, `conf` lebih rendah, atau fine-tuning dengan CrowdHuman (Proposal §13).

## 6. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| Kelas `Detector` | `aio_cctv/inference/detector.py` |
| CLI MVP deteksi + zona | `scripts/detect_zones.py` |
| Video beranotasi untuk ≥3 video YouTube | `outputs/*/annotated.mp4` |
| Tabel benchmark model & tabel sanity-check | `docs/logbook/` |
| **Video demo M1** (layar: editor zona → deteksi berjalan) | `docs/demo/m1.mp4` |

## 7. Definition of Done

- [ ] Satu perintah menghasilkan video beranotasi dengan jumlah orang per zona yang terlihat benar.
- [ ] CSV & `summary.json` terbentuk.
- [ ] Benchmark n/s/m tercatat; model default fase berikut dipilih dengan alasan.
- [ ] MAE sanity-check tercatat untuk ≥3 video.
- [ ] Demo M1 direkam & ditunjukkan ke pembimbing.
- [ ] Tag `v0.2-mvp-detection`.

## 8. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Video YouTube memiliki overlay teks/logo yang terdeteksi | Abaikan (kelas dibatasi *person*); jika mengganggu, buat zona hanya di area lantai |
| Orang duduk hanya terlihat setengah badan → anchor bawah-tengah jatuh di meja | Catat; di Fase 3 opsi anchor `CENTER` per zona (mis. zona meja duduk) |
| FPS rendah di CPU | `imgsz=480`, `yolo11n`; proses offline tetap bisa (video, bukan live) |
| `VideoSink` codec tidak tersedia | Gunakan ekstensi `.mp4` dengan codec `mp4v` (default Supervision) atau `.avi` |

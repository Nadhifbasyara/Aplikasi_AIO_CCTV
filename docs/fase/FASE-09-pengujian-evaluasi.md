# FASE 9 — Pengujian & Evaluasi

> Sebelumnya: [FASE-08](FASE-08-optimasi-packaging.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-10 Laporan TA](FASE-10-dokumentasi-laporan-ta.md)
> Referensi Proposal: **§10** (Rencana Pengujian & Metrik — acuan utama), **§13** (Dataset publik + “Strategi tugas akhir”, `scripts/eval_tracking.py`), **§4** (RM-1 s.d. RM-4), **§9** (Metodologi langkah 5), **§5** (evaluasi pada rekaman/video sampel + uji lapangan terbatas)
> Estimasi: **3 minggu** · Milestone: **M5 — Siap Rilis & Terukur**

---

## 1. Tujuan

Menghasilkan **bukti kuantitatif** yang menjawab setiap Rumusan Masalah (Proposal §4) dengan metrik yang direncanakan di Proposal §10, ditambah pengujian khusus fokus akhir (RTSP pada kamera konsumer & kualitas aplikasi desktop).

## 2. Peta Evaluasi

| RM (Proposal §4) | Aspek (Proposal §10) | Metrik | Data | Skrip |
|---|---|---|---|---|
| RM-1 RTSP → data real-time | Integrasi RTSP *(tambahan fokus akhir)* | Kompatibilitas merek, latensi, waktu pulih, uptime 24 jam | Kamera fisik + MediaMTX | `rtsp_probe.py`, `rtsp_latency.py`, `camera_health` |
| RM-2 MOT & occlusion | Akurasi deteksi | Precision, Recall, mAP@0.5 | Frame berlabel dari rekaman sendiri / video YouTube (+ CrowdHuman subset) | `eval_detection.py` |
| RM-2 | Akurasi tracking | **MOTA, IDF1, ID switches** | **MOT17 / MOT20** (Proposal §13) | `eval_tracking.py` |
| RM-2 | Akurasi dwell time | MAE (s), MAPE (%) — target **< 10%** | Rekaman sendiri + stopwatch manual | `eval_dwell.py` |
| RM-2 | Akurasi counting | % error in/out; MAE jumlah orang | Rekaman sendiri; **Mall Dataset** (counting) | `eval_counting.py` |
| RM-3 konfigurabilitas | **Kegenerikan** | Waktu & langkah adaptasi, **0 baris kode** | ≥2 vertikal | protokol Fase 7 §7 |
| RM-4 akurasi & FPS | Kinerja | FPS, latensi, CPU/GPU/RAM — target **≥ 20 FPS** RTX 3060 | Semua | `benchmark.py` (Fase 8) |
| RM-4 | Skalabilitas | Kamera maks per node | 1→N kamera | `benchmark.py --cams N` |
| — | Fungsional aplikasi | Black-box test | — | tabel uji |
| — | Usability | **SUS** (System Usability Scale) | ≥ 10 responden | kuesioner |

## 3. Persiapan Data

### 3.1 Dataset publik (Proposal §13)

| Dataset | Dipakai untuk | Catatan |
|---|---|---|
| MOT17 (train, 7 sekuens) | MOTA/IDF1 baseline standar | GT publik tersedia hanya untuk *train* — cukup untuk evaluasi (tanpa training) |
| MOT20 (train, 4 sekuens) | Kerumunan padat (mirip kafe/ritel ramai) | Sangat berat → cukup jalankan pada potongan / resolusi lebih kecil bila perlu |
| Mall Dataset (CUHK) | Counting per frame (anotasi titik kepala) | Bukan tracking — bandingkan **jumlah orang per frame** |
| CrowdHuman (val, subset) | (Opsional) deteksi pada occlusion berat | Lisensi riset |

> Cek lisensi setiap dataset (Proposal §13: sebagian besar non-komersial). DukeMTMC **tidak** dipakai.

### 3.2 Data sendiri (bukti di dunia nyata)

- Rekam **≥ 2 lokasi berbeda** (mis. kafe + minimarket, sesuai Proposal §13 “Strategi tugas akhir”) dengan kamera konsumer yang sama dengan Fase 4, **dengan izin pemilik** dan pemberitahuan area terpantau (Proposal §11).
- Durasi total ≥ 60 menit, pilih 3–5 klip 5 menit dengan kondisi berbeda (sepi, ramai, cahaya redup).
- Video YouTube referensi (Proposal §2) dipakai sebagai **data pendahuluan**; laporan tetap memprioritaskan rekaman sendiri untuk klaim akurasi.

### 3.3 Anotasi ground truth

| GT | Alat | Isi |
|---|---|---|
| Bounding box orang (deteksi) | CVAT / Label Studio / Roboflow (ekspor YOLO) | 200–500 frame sampel |
| Dwell | Spreadsheet + stopwatch / pemutar video dengan timestamp | per orang: zona, waktu masuk, waktu keluar |
| Counting | Spreadsheet | waktu tiap orang melintas garis + arah |

Idealnya 2 anotator untuk sebagian data → laporkan kesepakatan antar-anotator.

## 4. Skrip Evaluasi

### 4.1 Tracking — `scripts/eval_tracking.py` (disebut di Proposal §13)

```python
"""Evaluasi MOT (MOTA, IDF1, ID switch) pada sekuens MOT17/MOT20 format MOTChallenge.

Contoh: python scripts/eval_tracking.py data/datasets/MOT17/train --tracker bytetrack
"""
import argparse
import configparser
from pathlib import Path

import cv2
import motmetrics as mm
import numpy as np
import pandas as pd

from aio_cctv.inference.detector import Detector
from aio_cctv.tracking.tracker import ByteTrackTracker

COLS = ["frame", "id", "x", "y", "w", "h", "conf", "cls", "vis"]


def load_gt(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=None, names=COLS)
    return df[(df.conf == 1) & (df.cls == 1)]           # hanya pejalan kaki yang dinilai


def eval_sequence(seq: Path, detector: Detector) -> mm.MOTAccumulator:
    ini = configparser.ConfigParser()
    ini.read(seq / "seqinfo.ini")
    fps = float(ini["Sequence"]["frameRate"])
    tracker = ByteTrackTracker(fps)
    gt = load_gt(seq / "gt" / "gt.txt")
    acc = mm.MOTAccumulator(auto_id=False)

    for i, img_path in enumerate(sorted((seq / "img1").glob("*.jpg")), start=1):
        det = tracker.update(detector(cv2.imread(str(img_path))))
        g = gt[gt.frame == i]
        if len(det):
            hyp_xywh = np.c_[det.xyxy[:, :2], det.xyxy[:, 2:] - det.xyxy[:, :2]]
            hyp_ids = det.tracker_id.tolist()
        else:
            hyp_xywh, hyp_ids = np.empty((0, 4)), []
        dist = mm.distances.iou_matrix(g[["x", "y", "w", "h"]].values, hyp_xywh, max_iou=0.5)
        acc.update(g.id.tolist(), hyp_ids, dist, frameid=i)
    return acc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="folder berisi sekuens, mis. MOT17/train")
    ap.add_argument("--model", default="models/yolo11n.pt")
    ap.add_argument("--filter", default="FRCNN", help="MOT17: pakai satu varian detektor saja")
    args = ap.parse_args()

    detector = Detector(args.model, conf=0.25)
    seqs = [p for p in sorted(Path(args.root).iterdir())
            if p.is_dir() and (args.filter in p.name or p.name.startswith("MOT20"))]
    accs, names = [], []
    for seq in seqs:
        print("evaluasi", seq.name)
        accs.append(eval_sequence(seq, detector))
        names.append(seq.name)

    mh = mm.metrics.create()
    summary = mh.compute_many(accs, names=names, generate_overall=True,
                              metrics=["mota", "motp", "idf1", "num_switches",
                                       "precision", "recall", "num_false_positives", "num_misses"])
    print(mm.io.render_summary(summary, formatters=mh.formatters,
                               namemap=mm.io.motchallenge_metric_names))
    summary.to_csv("outputs/eval_tracking.csv")


if __name__ == "__main__":
    main()
```

> Sekuens MOT17 hadir dalam 3 salinan (DPM/FRCNN/SDP — hanya berbeda deteksi publik). Karena kita memakai detektor sendiri, cukup evaluasi satu salinan (`--filter FRCNN`) agar tidak terhitung 3×.

Variasi yang dilaporkan: ByteTrack vs BoT-SORT (Fase 3), yolo11n vs m, imgsz 640 vs 480, parameter `lost_track_seconds`.

### 4.2 Dwell — `scripts/eval_dwell.py`

Pencocokan sesi sistem ↔ GT: untuk setiap sesi GT (zona, masuk, keluar), cari sesi sistem di zona sama dengan **overlap waktu terbesar** (IoU temporal ≥ 0.5). Laporkan:

- **MAE** = rata-rata |durasi_sistem − durasi_GT| (detik)
- **MAPE** = rata-rata |selisih| / durasi_GT × 100% → target Proposal §10: **< 10%**
- Jumlah sesi **terpecah** (1 GT → >1 sesi sistem, biasanya akibat ID switch) dan **terlewat**.

Bila satu GT dicocokkan dengan beberapa sesi sistem (terpecah), **jumlahkan** durasinya dan laporkan juga versi “tanpa penggabungan” agar dampak ID switch terlihat.

### 4.3 Counting — `scripts/eval_counting.py`

- Garis masuk/keluar: `error% = |sistem − GT| / GT × 100` per arah, per klip; juga per interval 15 menit.
- Mall Dataset: jumlah orang per frame (seluruh frame sebagai satu zona) → MAE & RMSE; bandingkan dengan angka baseline di literatur counting.

### 4.4 Deteksi — `scripts/eval_detection.py`

Gunakan validator Ultralytics pada data berlabel format YOLO:

```python
from ultralytics import YOLO

metrics = YOLO("models/yolo11n.pt").val(data="data/datasets/own_person/data.yaml",
                                         classes=[0], imgsz=640, conf=0.001, iou=0.6)
print(metrics.box.map50, metrics.box.map, metrics.box.mp, metrics.box.mr)
```

(`data.yaml` memetakan kelas 0 = person agar kompatibel dengan COCO.)

## 5. Pengujian Sistem (fokus akhir: desktop + RTSP)

### 5.1 RTSP & kamera konsumer

Gunakan data Fase 4 (matriks kompatibilitas, latensi, uji `tc netem`, uji cabut kabel) dan ulangi dengan **build rilis** (Fase 8). Tambahkan ringkasan statistik:

| Kamera | Latensi rata-rata ± SD (ms) | Waktu pulih rata-rata (s) | Uptime 24 jam (%) | Reconnect / 24 jam | FPS diterima |
|---|---|---|---|---|---|

### 5.2 Kinerja & skalabilitas

| Perangkat | Backend | Kamera | FPS/kamera | Latensi (ms) | CPU % | GPU % | RAM/VRAM |
|---|---|---|---|---|---|---|---|
| Laptop i5 tanpa GPU | ONNX CPU / OpenVINO | 1, 2 | | | | | |
| PC RTX 3060 | TensorRT FP16 | 1, 4, 8 | | | | | |

Kesimpulan yang dicari: **jumlah kamera maksimum** per perangkat dengan FPS ≥ ambang (mis. 10 FPS per kamera cukup untuk analitik orang indoor).

### 5.3 Black-box testing (fungsional)

| ID | Fitur | Skenario | Hasil yang diharapkan | Hasil | Status |
|---|---|---|---|---|---|
| BB-01 | Tambah kamera | Isi merek Tapo, IP, akun benar → Tes Koneksi | Info codec/resolusi + snapshot tampil | | |
| BB-02 | Tambah kamera | Password salah | Pesan “Username/password salah” | | |
| BB-03 | Discovery | Klik “Cari di jaringan” | Kamera ONVIF di LAN terdaftar | | |
| BB-04 | Editor zona | Gambar zona 4 titik, simpan | Zona tersimpan & tampil di live view | | |
| BB-05 | Editor zona | Simpan poligon 2 titik | Ditolak dengan pesan | | |
| BB-06 | Live | Cabut kabel kamera 30 s | Status reconnecting → streaming tanpa restart | | |
| BB-07 | Dwell | Orang duduk di zona meja | Label durasi bertambah | | |
| BB-08 | Counting | Orang melewati garis | IN bertambah 1 | | |
| BB-09 | Alert | Antrean > ambang selama for_s | Notifikasi tray muncul sekali | | |
| BB-10 | Dashboard | Pilih rentang 7 hari | Grafik tampil < 1 s | | |
| BB-11 | Laporan | Ekspor PDF harian | File PDF benar | | |
| BB-12 | Template | Wizard F&B | Profil + aturan + dashboard terbentuk | | |
| BB-13 | Import/Export | Export lalu import profil | Zona identik | | |
| BB-14 | Privasi | Aktifkan blur | Semua orang ter-blur di tampilan & snapshot | | |
| BB-15 | Instalasi | Pasang di PC bersih | Aplikasi berjalan tanpa Python | | |

### 5.4 Usability — SUS

- Responden ≥ 10 (target: pemilik/staf UMKM, mahasiswa non-IT).
- Tugas: (1) tambah kamera, (2) pasang template kafe & gambar zona, (3) baca jam tersibuk di dashboard, (4) ekspor laporan.
- Catat **tingkat keberhasilan tugas** & **waktu penyelesaian**, lalu isi 10 butir SUS.
- Skor SUS = ((Σ(butir ganjil − 1) + Σ(5 − butir genap)) × 2.5). > 68 = di atas rata-rata.

### 5.5 Uji lapangan terbatas (Proposal §5)

Pasang aplikasi di **1 lokasi nyata** selama ≥ 3 hari (dengan izin). Bandingkan jam sibuk/traffic dari sistem dengan persepsi/catatan pemilik usaha (mis. data transaksi kasir per jam sebagai proksi) — bahan diskusi nilai bisnis di Bab IV.

## 6. Analisis

Untuk setiap metrik: tabel hasil → grafik → **analisis penyebab** (contoh: “MAPE dwell 14% di klip ramai disebabkan 3 ID switch saat pelanggan berpapasan di depan kasir; dengan BoT-SORT turun menjadi 8%”). Hubungkan balik ke setiap RM dan ke tujuan Proposal §5.

## 7. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| Skrip evaluasi | `scripts/eval_*.py` |
| Ground truth anotasi | `data/datasets/own_*` (tidak di-commit; deskripsi di laporan) |
| Tabel & grafik hasil semua metrik | `docs/hasil-evaluasi/` |
| Data mentah SUS & black-box | `docs/hasil-evaluasi/` |

## 8. Definition of Done

- [ ] Semua baris tabel Proposal §10 memiliki angka hasil.
- [ ] Setiap RM (Proposal §4) punya jawaban berbasis data.
- [ ] Black-box 15/15 kasus terisi; bug yang ditemukan diperbaiki atau dicatat sebagai batasan.
- [ ] SUS ≥ 10 responden terkumpul.
- [ ] Tag `v1.0.0` → **Milestone M5**.

## 9. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Anotasi dwell memakan waktu | Batasi pada klip 5 menit; gunakan pemutar dengan timestamp frame (mis. VLC + ekstensi, atau skrip bantu yang mencatat tombol) |
| MOT20 terlalu berat | Jalankan di PC GPU, atau subset sekuens |
| Target < 10% error dwell tidak tercapai | Laporkan jujur + analisis penyebab + perbaikan yang dicoba; itu tetap kontribusi ilmiah |
| Sulit mendapat izin rekam lokasi | Ajukan surat dari kampus; alternatif: lingkungan kampus (kantin, perpustakaan) |

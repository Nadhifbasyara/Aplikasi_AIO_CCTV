# FASE 0 — Persiapan Lingkungan & Struktur Proyek

> Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-01 Marker Area](FASE-01-marker-area-zona.md)
> Referensi Proposal: **§7** (Pemilihan Teknologi), **§8.1** (Instalasi), **§2** (Video Referensi), **§13** (Dataset)
> Estimasi: **1 minggu**

---

## 1. Tujuan

1. Menyiapkan lingkungan pengembangan yang **bisa direproduksi** (Linux & Windows) untuk seluruh fase.
2. Membuat **struktur repository final** sejak awal (lihat Roadmap §5) supaya kode fase awal tidak perlu dibongkar ulang.
3. Mengunduh **video referensi YouTube** (Proposal §2) sebagai bahan uji Fase 1–3.
4. Menyiapkan **simulator kamera RTSP** (MediaMTX) agar pengembangan RTSP bisa dimulai tanpa bergantung pada kamera fisik.
5. Memverifikasi GPU/CPU dan mencatat **spesifikasi perangkat uji** (dibutuhkan untuk Proposal §10 “Kinerja”).

## 2. Prasyarat

- PC/laptop dengan Python 3.11 (3.10–3.12 juga bisa; hindari versi terbaru yang belum didukung PyTorch).
- (Disarankan) GPU NVIDIA + driver terbaru. Target Proposal §10: RTX 3060 ≥ 20 FPS.
- Git, FFmpeg, koneksi internet untuk unduh model & video.

---

## 3. Langkah Kerja

### 3.1 Perangkat lunak dasar

| Komponen | Linux (Ubuntu/Debian) | Windows |
|---|---|---|
| Python 3.11 | `sudo apt install python3.11 python3.11-venv` atau `uv python install 3.11` | installer python.org / `winget install Python.Python.3.11` |
| Git | `sudo apt install git` | `winget install Git.Git` |
| FFmpeg (+ffprobe) | `sudo apt install ffmpeg` | `winget install Gyan.FFmpeg` |
| VLC (cek stream manual) | `sudo apt install vlc` | `winget install VideoLAN.VLC` |
| MediaMTX (simulator RTSP) | unduh rilis `mediamtx_*_linux_amd64.tar.gz` dari GitHub bluenviron/mediamtx | unduh `mediamtx_*_windows_amd64.zip` |
| Editor | VS Code + ekstensi Python, Pylance, Ruff | sama |

Cek driver GPU: `nvidia-smi` (catat versi driver & CUDA yang ditampilkan).

### 3.2 Inisialisasi repository

```bash
cd ~/Aplikasi_AIO_CCTV
git init
mkdir -p aio_cctv/{core,sources,zones,inference,tracking,analytics,storage,reports,gui} \
         configs/{profiles,templates} data/{videos,recordings,datasets} models scripts tests docs/logbook
touch aio_cctv/__init__.py aio_cctv/{core,sources,zones,inference,tracking,analytics,storage,reports,gui}/__init__.py
```

`.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
data/
models/
*.pt
*.onnx
*.engine
runs/
build/
dist/
*.spec
.env
```

> Video, dataset, dan bobot model **tidak** di-commit (ukuran besar & lisensi — Proposal §13).

### 3.3 Virtual environment & dependensi

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -U pip
```

Instal PyTorch **sesuai GPU** terlebih dahulu (ambil perintah dari pytorch.org → “Get Started”), contoh CUDA 12.x:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

Lalu `pyproject.toml` (kembangan dari Proposal §8.1):

```toml
[project]
name = "aio-cctv"
version = "0.0.1"
requires-python = ">=3.10,<3.13"
dependencies = [
  "ultralytics>=8.3",        # YOLOv11 (Proposal §7)
  "supervision>=0.25",       # PolygonZone, LineZone, ByteTrack (Proposal §7)
  "opencv-python>=4.10",
  "numpy",
  "pydantic>=2.7",           # validasi skema profil (Fase 1)
  "yt-dlp",                  # unduh video YouTube (Fase 0)
]

[project.optional-dependencies]
gui   = ["PyQt6>=6.7", "pyqtgraph>=0.13", "keyring", "platformdirs"]   # Fase 5-6
rtsp  = ["av>=12", "onvif-zeep", "WSDiscovery"]                        # Fase 4
eval  = ["motmetrics", "pandas", "matplotlib"]                         # Fase 9
dev   = ["pytest", "ruff"]

[tool.ruff]
line-length = 100
```

```bash
pip install -e ".[dev]"
```

### 3.4 Unduh video referensi dari YouTube (Proposal §2)

`scripts/download_youtube.py`:

```python
"""Unduh video referensi YouTube (Proposal §2) sebagai bahan uji lokal.

Catatan: hanya untuk riset/pengujian lokal. Jangan didistribusikan ulang.
"""
import argparse
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import download_range_func

REFERENSI = {
    "ref1": "https://www.youtube.com/watch?v=dHcxTmU6atk",
    "ref2": "https://www.youtube.com/watch?v=sFc1ZhDjvYI",
    "ref3": "https://www.youtube.com/watch?v=ng1onQ42seE",
    "ref4": "https://www.youtube.com/watch?v=i_mL3LT0lDg",
    "ref5": "https://www.youtube.com/watch?v=E01h7ljKtpk",
    "ref6": "https://www.youtube.com/watch?v=zBao3QunnGY",
}


def download(url: str, out_dir: Path, name: str, max_height: int = 720,
             start: float | None = None, end: float | None = None) -> None:
    opts = {
        # hanya video (tanpa audio) <= 720p, utamakan H.264 (avc1):
        # YouTube sering memberi AV1/VP9 yang GAGAL didekode OpenCV (0 frame terbaca)
        "format": (f"bv*[vcodec^=avc1][height<={max_height}]/"
                   f"b[vcodec^=avc1][height<={max_height}]/bv*[height<={max_height}]/b"),
        "outtmpl": str(out_dir / f"{name}.%(ext)s"),
        "noplaylist": True,
    }
    if start is not None and end is not None:
        opts["download_ranges"] = download_range_func(None, [(start, end)])
        opts["force_keyframes_at_cuts"] = True
    with YoutubeDL(opts) as ydl:
        ydl.download([url])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="URL YouTube lain (opsional)")
    ap.add_argument("--name", default="custom")
    ap.add_argument("--out", default="data/videos")
    ap.add_argument("--max-height", type=int, default=720)
    ap.add_argument("--start", type=float, help="detik awal potongan")
    ap.add_argument("--end", type=float, help="detik akhir potongan")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    targets = {args.name: args.url} if args.url else REFERENSI
    for name, url in targets.items():
        print(f"[unduh] {name} <- {url}")
        download(url, out, name, args.max_height, args.start, args.end)


if __name__ == "__main__":
    main()
```

Pemakaian:

```bash
python scripts/download_youtube.py                              # semua video referensi
python scripts/download_youtube.py --url "https://youtu.be/XXXX" --name kafe1 --start 60 --end 180
```

Setelah terunduh, **catat** di `data/videos/README.md` (tidak di-commit, tapi salin ringkasannya ke logbook): judul, URL, resolusi, FPS, durasi, jenis scene (kafe/ritel/koridor), sudut kamera (atas/miring), tingkat keramaian. Tabel ini dipakai kembali di Fase 2 & Fase 9.

Cek metadata cepat:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate \
        -show_entries format=duration -of compact data/videos/ref1.mp4
```

> **Wajib cek codec:** kolom `codec_name` harus `h264`. Bila `av1`/`vp9`, OpenCV tidak dapat membaca frame-nya. Konversi:
> `ffmpeg -i ref1.mp4 -c:v libx264 -crf 20 -pix_fmt yuv420p -an ref1_h264.mp4`

> **Tips memilih video uji:** pilih potongan 1–3 menit dengan sudut kamera **tetap** (CCTV sungguhan, bukan video dengan kamera bergerak/cut adegan). Zona tidak bermakna jika kamera berpindah.

### 3.5 Simulator kamera RTSP (persiapan Fase 4)

Jalankan MediaMTX (tanpa konfigurasi sudah menerima publish di port 8554):

```bash
./mediamtx                               # terminal 1
# terminal 2: "siarkan" video YouTube sebagai kamera RTSP, loop tanpa henti
ffmpeg -re -stream_loop -1 -i data/videos/ref1.mp4 -c:v libx264 -preset veryfast \
       -tune zerolatency -g 50 -an -f rtsp rtsp://localhost:8554/cam1
# terminal 3: uji
ffplay -rtsp_transport tcp rtsp://localhost:8554/cam1
```

Dengan ini, **video YouTube (target awal) dan kamera RTSP (target akhir) memakai jalur kode yang sama** — cukup mengganti URL sumber.

### 3.6 Verifikasi lingkungan

`scripts/check_env.py`:

```python
"""Cek kesiapan lingkungan + catat spesifikasi perangkat uji (Proposal §10 'Kinerja')."""
import platform
import shutil

import cv2
import torch
import ultralytics
import supervision as sv

print("OS          :", platform.platform())
print("CPU         :", platform.processor() or platform.machine())
print("Python      :", platform.python_version())
print("OpenCV      :", cv2.__version__)
print("  FFmpeg    :", "YES" if "FFMPEG:                      YES" in cv2.getBuildInformation() else "cek manual")
print("PyTorch     :", torch.__version__, "| CUDA:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU         :", torch.cuda.get_device_name(0))
print("Ultralytics :", ultralytics.__version__)
print("Supervision :", sv.__version__)
print("ffmpeg bin  :", shutil.which("ffmpeg"))

from ultralytics import YOLO
model = YOLO("models/yolo11n.pt")     # bobot disimpan di models/ (bukan root repo)
res = model("https://ultralytics.com/images/bus.jpg", verbose=False)[0]
print("Uji deteksi :", len(res.boxes), "objek terdeteksi")
```

Pindahkan bobot yang terunduh ke `models/` dan selalu rujuk path `models/yolo11n.pt` di fase berikutnya.

### 3.7 Logbook & manajemen tugas

- `docs/logbook/YYYY-MM-DD.md` — catatan harian/mingguan (bahan Bab IV & bimbingan).
- Buat board sederhana (GitHub Projects/Trello) dengan kolom *Backlog → Fase aktif → Review → Selesai*, isi dengan checklist tiap dokumen fase.
- Commit kecil & sering; tag akhir fase: `git tag v0.0-setup`.

---

## 4. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| Struktur repo + `pyproject.toml` + `.gitignore` | root |
| Skrip unduh YouTube | `scripts/download_youtube.py` |
| Video uji (≥3 potongan, sudut tetap) + tabel metadata | `data/videos/` |
| Skrip cek lingkungan + hasil (spesifikasi perangkat) | `scripts/check_env.py`, `docs/logbook/` |
| Simulator RTSP berjalan | MediaMTX + perintah ffmpeg di README |

## 5. Definition of Done

- [ ] `python scripts/check_env.py` sukses dan GPU (jika ada) terdeteksi.
- [ ] Minimal 3 video uji tersimpan, tercatat metadatanya.
- [ ] `ffplay rtsp://localhost:8554/cam1` menampilkan video YouTube yang disiarkan ulang.
- [ ] Repo ter-commit dengan struktur Roadmap §5, tag `v0.0-setup`.
- [ ] Spesifikasi perangkat uji tercatat di logbook.

## 6. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Versi CUDA PyTorch tidak cocok driver | Pakai perintah resmi pytorch.org, cek `torch.cuda.is_available()` |
| yt-dlp gagal (perubahan YouTube) | `pip install -U yt-dlp`; alternatif unduh manual lalu taruh di `data/videos/` |
| Video referensi tidak berkamera tetap | Pilih potongan yang tetap, atau cari video “CCTV cafe/store footage” berlisensi Creative Commons |
| Tidak ada GPU | Tetap lanjut dengan CPU + `yolo11n` + imgsz 480; optimasi di Fase 8 |

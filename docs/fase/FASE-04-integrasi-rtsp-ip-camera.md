# FASE 4 — Integrasi Protokol RTSP pada Consumer IP Camera

> Sebelumnya: [FASE-03](FASE-03-tracking-analitik-inti.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-05 Aplikasi Desktop](FASE-05-aplikasi-desktop-pyqt6.md)
> Referensi Proposal: **§3** (“Kamera IP konsumer (EZVIZ, TP-Link) sering mengunci akses stream lokal… tantangan integrasi… nilai jual”), **§4 RM-1** (aliran RTSP → data terstruktur real-time), **§6** (Video Ingest OpenCV/FFmpeg, RTSP atau ONVIF), **§7** (Video I/O: OpenCV + FFmpeg, alternatif GStreamer), **§8.3** (ganti SOURCE ke URL RTSP), **§12** (judul #3: “Terintegrasi Protokol RTSP pada Consumer IP Camera”)
> Estimasi: **3 minggu** · Milestone: **M3 — Live RTSP**

---

## 1. Tujuan

Fase ini adalah **inti kontribusi Teknik Komputer** dari fokus akhir TA.

1. Membangun **`RTSPSource`** yang andal: thread terpisah, *latest-frame* (latensi rendah), timeout, **auto-reconnect** dengan *exponential backoff*, statistik stream.
2. Mendukung **penemuan kamera** di jaringan lokal: ONVIF WS-Discovery + pemindaian port 554 + **template URL per merek**.
3. Menangani variasi kamera konsumer: H.264 vs **H.265**, *main-stream* vs *sub-stream*, TCP vs UDP, kredensial khusus (verification code, camera account).
4. Menyusun **Matriks Kompatibilitas Kamera Konsumer** (hasil riset yang bisa dipresentasikan).
5. Mengukur **latensi end-to-end**, **waktu pemulihan** (reconnect), dan **stabilitas** stream — data untuk RM-1 dan Proposal §10.
6. Menjalankan **pipeline Fase 3 tanpa perubahan** di atas sumber RTSP.

## 2. Dasar Teori Singkat (bahan Bab II)

| Topik | Poin yang perlu dijelaskan |
|---|---|
| **RTSP** (RFC 2326 / RTSP 2.0 RFC 7826) | Protokol *kontrol* sesi: `OPTIONS → DESCRIBE (SDP) → SETUP → PLAY → TEARDOWN`. Data video dikirim lewat **RTP**, dikontrol **RTCP** |
| **RTP over UDP vs TCP (interleaved)** | UDP: latensi rendah tetapi rawan paket hilang/NAT; TCP interleaved: andal, sedikit lebih lambat. Di jaringan Wi-Fi rumah/toko, **TCP** umumnya lebih stabil |
| **Autentikasi** | Basic / **Digest** (MD5) dalam RTSP; kredensial di URL `rtsp://user:pass@ip:554/path` (karakter khusus harus di-*URL-encode*) |
| **Codec** | H.264 (AVC) vs H.265 (HEVC): HEVC hemat bandwidth ~40% tapi decoding lebih berat; GOP / I-frame interval memengaruhi waktu tampil frame pertama |
| **ONVIF** | Standar interoperabilitas kamera IP: *Profile S* (streaming). WS-Discovery (multicast UDP 3702) untuk menemukan kamera, layanan Media `GetProfiles` / `GetStreamUri` untuk mendapat URL RTSP |
| **Main/Sub-stream** | Main: resolusi penuh (rekam/tampil), Sub: resolusi rendah (analitik hemat komputasi) |

## 3. Pengadaan Kamera Uji

Minimal **2 merek, idealnya 3–4**, harga konsumer (Rp200–600 ribu). Pilih yang **terdokumentasi mendukung RTSP**:

| Prioritas | Merek/Seri (contoh) | Alasan |
|---|---|---|
| Wajib | **TP-Link Tapo** C200/C210/C220 | Sangat umum di Indonesia, RTSP + ONVIF setelah membuat *Camera Account* |
| Wajib | **Imou / Dahua** (Ranger, Cue) atau **Hikvision/EZVIZ** | Mewakili ekosistem “cloud-first” yang RTSP-nya harus diaktifkan/dicari (sesuai Proposal §3) |
| Disarankan | Kamera generik **V380 / iCSee (XMEye) / CareCam** | Sangat murah & banyak di pasar lokal, format URL tidak standar — menarik secara riset |
| Pembanding | Kamera **tanpa RTSP** (mis. sebagian seri Xiaomi) | Untuk mendokumentasikan batasan (“tidak didukung”) |

Siapkan juga: router terpisah/VLAN uji, kabel LAN, dan **MediaMTX** (dari Fase 0) sebagai kamera virtual.

## 4. Template URL RTSP per Merek

> Selalu verifikasi di kamera nyata; firmware berbeda bisa berbeda path. Nilai ini menjadi data awal `configs/camera_brands.json`.

| Merek | Main-stream | Sub-stream | Catatan kredensial / aktivasi |
|---|---|---|---|
| TP-Link Tapo | `rtsp://{u}:{p}@{ip}:554/stream1` | `.../stream2` | Buat **Camera Account** di app Tapo → Advanced Settings; ONVIF port 2020 |
| Hikvision | `rtsp://{u}:{p}@{ip}:554/Streaming/Channels/101` | `.../102` | Akun admin perangkat |
| EZVIZ | `rtsp://admin:{code}@{ip}:554/H.264` atau `/h264/ch1/main/av_stream` | `/h264/ch1/sub/av_stream` | Password = **verification code** di stiker; beberapa model perlu mengaktifkan RTSP / “LAN live view” di app, sebagian model tidak menyediakan |
| Dahua / Imou | `rtsp://{u}:{p}@{ip}:554/cam/realmonitor?channel=1&subtype=0` | `...subtype=1` | Imou: user `admin`, password = safety code / password perangkat |
| Reolink | `rtsp://{u}:{p}@{ip}:554/h264Preview_01_main` | `.../h264Preview_01_sub` | Model H.265: `h265Preview_01_main` |
| XMEye / iCSee (generik) | `rtsp://{ip}:554/user={u}&password={p}&channel=1&stream=0.sdp` | `...stream=1.sdp` | Format kredensial di path, bukan di userinfo |
| V380 (generik) | `rtsp://{u}:{p}@{ip}:554/live/ch00_0` | `.../live/ch00_1` | Bergantung firmware; sebagian perlu diaktifkan dari app |
| Kamera virtual (MediaMTX) | `rtsp://localhost:8554/cam1` | — | Untuk pengembangan & uji otomatis |

`configs/camera_brands.json`:

```json
{
  "tapo":      { "label": "TP-Link Tapo", "port": 554, "onvif_port": 2020,
                 "main": "rtsp://{user}:{password}@{host}:{port}/stream1",
                 "sub":  "rtsp://{user}:{password}@{host}:{port}/stream2" },
  "hikvision": { "label": "Hikvision", "port": 554, "onvif_port": 80,
                 "main": "rtsp://{user}:{password}@{host}:{port}/Streaming/Channels/101",
                 "sub":  "rtsp://{user}:{password}@{host}:{port}/Streaming/Channels/102" },
  "dahua":     { "label": "Dahua / Imou", "port": 554, "onvif_port": 80,
                 "main": "rtsp://{user}:{password}@{host}:{port}/cam/realmonitor?channel=1&subtype=0",
                 "sub":  "rtsp://{user}:{password}@{host}:{port}/cam/realmonitor?channel=1&subtype=1" },
  "xmeye":     { "label": "XMEye / iCSee", "port": 554, "onvif_port": 8899,
                 "main": "rtsp://{host}:{port}/user={user}&password={password}&channel=1&stream=0.sdp",
                 "sub":  "rtsp://{host}:{port}/user={user}&password={password}&channel=1&stream=1.sdp" },
  "custom":    { "label": "URL manual", "main": "{url}", "sub": "{url}" }
}
```

---

## 5. Implementasi

### 5.1 Struktur file

```
aio_cctv/sources/
├── base.py                 # (Fase 3) Frame, FrameSource
├── file_source.py          # (Fase 3)
├── rtsp_source.py          # RTSPSource + StreamStats
├── url_builder.py          # bangun URL dari template merek, encode kredensial, masking log
├── probe.py                # ffprobe wrapper: codec, resolusi, fps
└── discovery.py            # ONVIF WS-Discovery + GetStreamUri + scan port 554
configs/camera_brands.json
scripts/
├── rtsp_probe.py           # CLI uji satu URL -> baris matriks kompatibilitas
├── rtsp_latency.py         # ukur latensi & jitter
└── run_live.py             # pipeline Fase 3 di atas RTSP
```

### 5.2 URL builder & masking — `aio_cctv/sources/url_builder.py`

```python
import json
import re
from pathlib import Path
from urllib.parse import quote

# path relatif terhadap lokasi file ini (bukan folder kerja), agar tetap jalan dari GUI/folder lain
BRANDS_FILE = Path(__file__).resolve().parents[2] / "configs" / "camera_brands.json"
BRANDS = json.loads(BRANDS_FILE.read_text(encoding="utf-8"))


def build_url(brand: str, host: str, user: str = "", password: str = "",
              stream: str = "main", port: int | None = None, url: str = "") -> str:
    b = BRANDS[brand]
    return b[stream].format(
        host=host, port=port or b.get("port", 554), url=url,
        user=quote(user, safe=""), password=quote(password, safe=""),   # '@', ':' dll aman
    )


_CRED = re.compile(r"(rtsp://[^:/@]+:)([^@]+)(@)")
_XM = re.compile(r"(password=)([^&]+)")


def mask(url: str) -> str:
    """Sembunyikan password sebelum ditulis ke log/UI."""
    return _XM.sub(r"\1****", _CRED.sub(r"\1****\3", url))
```

### 5.3 RTSPSource — `aio_cctv/sources/rtsp_source.py`

Desain kunci:
- **Thread penangkap** membaca secepat kamera mengirim; konsumen (pipeline) selalu mengambil **frame terbaru** → frame lama dibuang, latensi tidak menumpuk walau inferensi lebih lambat dari FPS kamera.
- **Timeout** buka & baca (OpenCV ≥ 4.6) → deteksi kamera mati tanpa hang.
- **State machine**: `idle → connecting → streaming → reconnecting → … → stopped`.
- **Backoff reconnect** 1 → 2 → 4 → 5 s (batas `max_backoff_s` = 5 s). Batas besar (mis. 30 s) membuat kamera yang sudah hidup kembali baru tersambung hingga 30 s kemudian. Uji simulator: dengan batas 30 s, frame baru muncul 7,6 s setelah stream kembali.

```python
"""Sumber RTSP tahan-putus untuk consumer IP camera."""
import os

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")  # sebelum import cv2

import logging
import threading
import time
from dataclasses import dataclass, field

import cv2

from aio_cctv.sources.base import Frame
from aio_cctv.sources.url_builder import mask

log = logging.getLogger(__name__)


@dataclass
class StreamStats:
    frames_received: int = 0
    frames_consumed: int = 0
    reconnects: int = 0
    last_frame_wall: float = 0.0
    connected_since: float | None = None
    downtime_s: float = 0.0
    recv_fps: float = 0.0
    _fps_window: list = field(default_factory=list)

    @property
    def dropped(self) -> int:           # frame yang dilewati karena konsumen lebih lambat
        return self.frames_received - self.frames_consumed


class RTSPSource:
    def __init__(self, url: str, name: str = "cam", open_timeout_ms: int = 5000,
                 read_timeout_ms: int = 5000, max_backoff_s: float = 5.0,
                 hw_accel: bool = False):
        self.url, self.name = url, name
        self.open_timeout_ms, self.read_timeout_ms = open_timeout_ms, read_timeout_ms
        self.max_backoff_s, self.hw_accel = max_backoff_s, hw_accel
        self.state = "idle"
        self.stats = StreamStats()
        self.fps, self.resolution = 0.0, (0, 0)
        self._latest: Frame | None = None
        self._last_consumed_seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._opened = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- siklus hidup ----------
    def start(self, wait_s: float = 10.0) -> bool:
        self._thread = threading.Thread(target=self._run, name=f"rtsp-{self.name}", daemon=True)
        self._thread.start()
        return self._opened.wait(wait_s)       # True bila koneksi pertama berhasil

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.state = "stopped"

    @property
    def finished(self) -> bool:
        return self._stop.is_set()

    # ---------- konsumen ----------
    def read(self) -> Frame | None:
        with self._lock:
            f = self._latest
        if f is None or f.seq == self._last_consumed_seq:
            return None
        self._last_consumed_seq = f.seq
        self.stats.frames_consumed += 1
        return f

    # ---------- produsen (thread) ----------
    def _open(self) -> cv2.VideoCapture | None:
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms,
                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.read_timeout_ms]
        if self.hw_accel:
            params += [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY]
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            return None
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        self.resolution = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                           int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        return cap

    def _run(self) -> None:
        backoff, seq, down_since = 1.0, 0, None
        while not self._stop.is_set():
            self.state = "connecting"
            log.info("[%s] membuka %s", self.name, mask(self.url))
            cap = self._open()
            if cap is None:
                self.state = "reconnecting"
                down_since = down_since or time.monotonic()
                log.warning("[%s] gagal terhubung, coba lagi %.0fs", self.name, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.max_backoff_s)
                continue

            if down_since is not None:
                self.stats.downtime_s += time.monotonic() - down_since
                down_since = None
            self.state, backoff = "streaming", 1.0
            self.stats.connected_since = time.time()
            self._opened.set()

            while not self._stop.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    break
                seq += 1
                now_m, now_w = time.monotonic(), time.time()
                self._update_fps(now_m)
                self.stats.frames_received += 1
                self.stats.last_frame_wall = now_w
                with self._lock:
                    self._latest = Frame(img, now_m, now_w, seq)
            cap.release()
            if not self._stop.is_set():
                self.state = "reconnecting"
                self.stats.reconnects += 1
                down_since = time.monotonic()
                log.warning("[%s] stream terputus, reconnect #%d", self.name, self.stats.reconnects)

    def _update_fps(self, now: float) -> None:
        w = self.stats._fps_window
        w.append(now)
        while w and now - w[0] > 2.0:
            w.pop(0)
        self.stats.recv_fps = len(w) / 2.0
```

> **Catatan timestamp:** `Frame.ts` = waktu **tiba** frame (monotonic). Latensi jaringan/dekode konstan tidak memengaruhi *durasi* dwell. Bila dibutuhkan presisi lebih (PTS dari kamera), gunakan **PyAV** (`av.open(url, options={"rtsp_transport": "tcp"})`, `frame.pts * stream.time_base`) sebagai backend alternatif `PyAVSource` — bahan perbandingan di Fase 9.

### 5.4 Probe — `aio_cctv/sources/probe.py`

```python
import json
import subprocess


def probe(url: str, timeout_s: int = 10) -> dict:
    """Ambil info stream via ffprobe: codec, resolusi, fps, profil."""
    cmd = ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,profile,width,height,avg_frame_rate,r_frame_rate",
           "-of", "json", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    if out.returncode != 0:
        return {"ok": False, "error": out.stderr.strip()[-300:]}   # mis. 401 Unauthorized, 404
    s = json.loads(out.stdout)["streams"][0]
    num, den = map(int, s.get("avg_frame_rate", "0/1").split("/"))
    return {"ok": True, "codec": s["codec_name"], "profile": s.get("profile"),
            "width": s["width"], "height": s["height"], "fps": round(num / den, 2) if den else None}
```

Pesan error ffprobe dipetakan menjadi pesan ramah pengguna di GUI (Fase 5):

| Error mentah | Pesan untuk pengguna |
|---|---|
| `401 Unauthorized` | Username/password salah (Tapo: gunakan *Camera Account*, EZVIZ: *verification code*) |
| `404 Not Found` / `Invalid data` | Path stream salah — coba template merek lain |
| `Connection refused` | Port RTSP tertutup / RTSP belum diaktifkan di aplikasi kamera |
| `timeout` | IP salah, kamera beda jaringan, atau firewall |

### 5.5 Discovery — `aio_cctv/sources/discovery.py`

```python
"""Temukan kamera di LAN: ONVIF WS-Discovery lalu fallback scan port 554."""
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse


def onvif_discover(timeout: int = 3) -> list[dict]:
    from wsdiscovery import QName
    from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery

    wsd = WSDiscovery()
    wsd.start()
    try:
        nvt = QName("http://www.onvif.org/ver10/network/wsdl", "NetworkVideoTransmitter")
        services = wsd.searchServices(types=[nvt], timeout=timeout)
        found = []
        for s in services:
            for xaddr in s.getXAddrs():
                u = urlparse(xaddr)
                found.append({"host": u.hostname, "onvif_port": u.port or 80, "xaddr": xaddr})
        return found
    finally:
        wsd.stop()


def onvif_stream_uris(host: str, port: int, user: str, password: str) -> list[dict]:
    """Minta URL RTSP resmi dari kamera (Media.GetStreamUri) — paling andal bila ONVIF aktif."""
    from onvif import ONVIFCamera

    cam = ONVIFCamera(host, port, user, password)
    media = cam.create_media_service()
    uris = []
    for prof in media.GetProfiles():
        req = media.create_type("GetStreamUri")
        req.ProfileToken = prof.token
        req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
        uri = media.GetStreamUri(req).Uri
        res = prof.VideoEncoderConfiguration.Resolution if prof.VideoEncoderConfiguration else None
        uris.append({"profile": prof.Name, "uri": uri,
                     "resolution": (res.Width, res.Height) if res else None})
    return uris


def scan_rtsp_port(cidr: str, port: int = 554, timeout: float = 0.4) -> list[str]:
    """Fallback untuk kamera tanpa ONVIF: cari host yang membuka port RTSP."""
    def check(ip: str) -> str | None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return ip if s.connect_ex((ip, port)) == 0 else None

    hosts = [str(h) for h in ipaddress.ip_network(cidr, strict=False).hosts()]
    with ThreadPoolExecutor(max_workers=64) as ex:
        return [ip for ip in ex.map(check, hosts) if ip]
```

> URL dari ONVIF biasanya **tanpa kredensial**; sisipkan user/password (ter-encode) sebelum dibuka. Pemindaian jaringan hanya dilakukan pada **jaringan milik sendiri** (etika & keamanan).

### 5.6 Menjalankan pipeline di atas RTSP — `scripts/run_live.py`

```python
import argparse
import time

import cv2

from aio_cctv.pipeline import Pipeline
from aio_cctv.sources.rtsp_source import RTSPSource
from aio_cctv.zones.models import Profile

ap = argparse.ArgumentParser()
ap.add_argument("--profile", required=True)
ap.add_argument("--url", help="override URL RTSP")
args = ap.parse_args()

profile = Profile.load(args.profile)
src = RTSPSource(args.url or profile.source.uri, name=profile.profile_id)
if not src.start():
    raise SystemExit("Kamera tidak dapat dihubungi (cek URL/kredensial)")
pipe = Pipeline(profile, src.resolution, src.fps)   # zona ternormalisasi -> otomatis cocok resolusi

while True:
    f = src.read()
    if f is None:
        time.sleep(0.003)
        continue
    res = pipe.step(f.image, f.ts)
    vis = pipe.annotate(f.image, res)
    s = src.stats
    cv2.putText(vis, f"{src.state} | rx {s.recv_fps:.1f}fps | drop {s.dropped} | reconn {s.reconnects}",
                (10, vis.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imshow("AIO-CCTV | Live RTSP", vis)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
src.stop()
```

> Karena resolusi baru diketahui setelah koneksi, pipeline dibuat **setelah** `start()`. Bila resolusi berubah saat reconnect (pengguna mengganti setelan kamera), pipeline perlu dibangun ulang — ditangani di `CameraManager` Fase 5.

### 5.7 Strategi dual-stream

- **Analitik** memakai **sub-stream** (mis. 640×360, 10–15 fps) → komputasi ringan, cukup untuk deteksi orang indoor.
- **Tampilan** (opsional) memakai main-stream, atau sub-stream di-*upscale* di mode grid.
- Karena koordinat zona ternormalisasi (Fase 1), zona yang digambar di snapshot main-stream **langsung berlaku** di sub-stream.
- Evaluasi trade-off akurasi vs FPS di Fase 9 (sub vs main).

---

## 6. Pengujian Fase 4

### 6.1 Matriks kompatibilitas (luaran riset utama)

Isi untuk setiap kamera/firmware:

| Merek/Model | Firmware | RTSP bawaan? | Cara aktivasi | ONVIF | URL main/sub | Codec | Resolusi/FPS main | Resolusi/FPS sub | Latensi (ms) | Stabil 24 jam? | Catatan |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Tapo C200 | … | Ya | Camera Account | Ya (2020) | stream1/stream2 | H.264 | 1920×1080/15 | 640×360/15 | … | … | … |
| … | | | | | | | | | | | |

### 6.2 Latensi end-to-end — `scripts/rtsp_latency.py`

Metode **stopwatch di layar**: arahkan kamera ke monitor yang menampilkan jam milidetik; tampilkan di monitor yang sama jendela aplikasi; ambil screenshot → latensi = selisih dua angka. Ulangi 20× → rata-rata & standar deviasi. Bandingkan: TCP vs UDP, H.264 vs H.265, main vs sub, OpenCV vs PyAV.

Untuk kamera virtual, latensi dapat diotomatisasi dengan *burn-in* timestamp:

```bash
ffmpeg -re -f lavfi -i testsrc2=size=1280x720:rate=25 \
  -vf "drawtext=text='%{localtime\:%T}.%{eif\:mod(t*1000\,1000)\:d\:3}':fontsize=48:x=20:y=20:fontcolor=white" \
  -c:v libx264 -tune zerolatency -g 25 -f rtsp rtsp://localhost:8554/clock
```

### 6.3 Uji ketahanan (reconnect & gangguan jaringan)

| Skenario | Cara | Yang diukur |
|---|---|---|
| Kabel LAN/Wi-Fi kamera dicabut 10 s / 60 s | manual | Waktu pulih (detik sampai frame kembali), aplikasi tidak crash |
| Kamera di-restart | cabut daya | Sama seperti di atas |
| Paket hilang 1%, 5% | `sudo tc qdisc add dev <if> root netem loss 5%` (Linux, di PC/gateway) | Artefak, FPS diterima, reconnect |
| Delay/jitter | `tc qdisc change dev <if> root netem delay 200ms 50ms` | Latensi, stabilitas |
| Bandwidth sempit | `tc qdisc add dev <if> root tbf rate 1mbit burst 32kbit latency 400ms` | Main vs sub-stream |
| Durasi panjang | jalankan 24 jam | Kebocoran memori (RSS), jumlah reconnect, FPS rata-rata |

Hapus aturan: `sudo tc qdisc del dev <if> root`.

### 6.4 Uji fungsional

- [ ] Pipeline Fase 3 berjalan di atas MediaMTX **dan** ≥2 kamera fisik tanpa ubah kode analitik.
- [ ] Profil yang dibuat dari snapshot main-stream berlaku benar di sub-stream.
- [ ] Password mengandung karakter khusus (`@`, `#`, `:`) terhubung dengan benar (URL-encode).
- [ ] Password tidak pernah muncul di log (cek `mask`).

## 7. Keamanan & Etika Jaringan

- Kamera tidak perlu dan **tidak boleh** diekspos ke internet (port-forward 554) — aplikasi hanya bekerja di LAN.
- Ganti password bawaan; kredensial disimpan di **OS keyring** (diimplementasikan Fase 5), bukan di JSON profil.
- Pemindaian port hanya di jaringan sendiri.
- Tidak melakukan modifikasi firmware yang melanggar garansi/ketentuan produsen dalam lingkup TA; kamera tanpa RTSP dicatat sebagai **batasan**.

## 8. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| `RTSPSource`, `url_builder`, `probe`, `discovery` | `aio_cctv/sources/` |
| Database template merek | `configs/camera_brands.json` |
| CLI probe, latency, live | `scripts/` |
| **Matriks kompatibilitas kamera konsumer** | `docs/logbook/matriks-kompatibilitas.md` |
| Data latensi, waktu pulih, uji 24 jam | `docs/logbook/` |
| **Demo M3** — analitik live pada kamera fisik + demo cabut kabel lalu pulih | `docs/demo/m3.mp4` |

## 9. Definition of Done

- [ ] Live analitik berjalan di ≥2 merek kamera fisik + MediaMTX.
- [ ] Reconnect otomatis terbukti (cabut kabel → pulih tanpa restart aplikasi).
- [ ] Uji 24 jam tanpa crash & tanpa pertumbuhan memori signifikan.
- [ ] Matriks kompatibilitas terisi untuk semua kamera uji.
- [ ] Tag `v0.4-rtsp`.

## 10. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Kamera yang dibeli ternyata tidak ada RTSP | Cek dulu dokumentasi/forum sebelum membeli; Tapo adalah pilihan aman |
| H.265 gagal didekode / berat | Setel kamera ke H.264 di aplikasinya; atau aktifkan `hw_accel` |
| `cap.read()` hang tanpa timeout (OpenCV lama) | OpenCV ≥ 4.8; fallback watchdog: thread di-*recreate* bila `last_frame_wall` > 10 s |
| Artefak abu-abu/“smearing” (paket UDP hilang) | Paksa `rtsp_transport;tcp` |
| Kamera membatasi jumlah klien RTSP (sering 1–2) | Tutup aplikasi vendor/NVR lain saat uji; opsional relay lewat MediaMTX/go2rtc |

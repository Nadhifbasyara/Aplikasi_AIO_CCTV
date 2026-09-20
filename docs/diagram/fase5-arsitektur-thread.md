# Fase 5 — Arsitektur Thread Aplikasi Desktop

> Bahan Bab III. Sumber: implementasi `aio_cctv/gui/` ([FASE-05](../fase/FASE-05-aplikasi-desktop-pyqt6.md) §2).

Aturan yang dipegang seluruh aplikasi: **tidak ada pekerjaan berat di thread GUI**, dan
**thread pekerja tidak pernah menyentuh widget** — semua komunikasi lewat signal Qt, yang
secara otomatis menjadi *queued connection* saat melintasi thread.

```mermaid
flowchart TB
    subgraph GUI["Thread GUI — Qt main thread"]
        MW["MainWindow<br/>closeEvent → stop_all()"]
        LV["LiveViewPage<br/>grid VideoTile"]
        CP["CamerasPage"]
        ZP["ZoneEditorPage<br/>QGraphicsScene"]
        SP["SettingsPage<br/>QSettings"]
        CM["CameraManager<br/>registry worker"]
    end

    subgraph WT["Satu QThread per kamera — PipelineWorker"]
        SRC["RTSPSource / FileSource"]
        PIPE["Pipeline Fase 3<br/>Detector → Tracker → AnalyticsEngine"]
        SRC --> PIPE
    end

    subgraph RT["Thread penangkap RTSP — threading.Thread (daemon)"]
        CAP["cv2.VideoCapture<br/>latest-frame + auto-reconnect"]
    end

    subgraph TP["QThreadPool — tugas sekali jalan"]
        PROBE["probe() ffprobe"]
        SNAP["grab_frame()"]
        DISC["discover_cameras()<br/>ONVIF + scan port 554"]
        DET["Detector sekali jalan<br/>pratinjau deteksi"]
    end

    STORE[("cameras.json · profiles/*.json<br/>QSettings · OS keyring")]

    MW --> LV & CP & ZP & SP
    CP -->|start / stop / save| CM
    SP -->|simpan → restart worker| CM
    ZP -->|simpan profil → restart worker| CM
    CM -->|start| WT
    CAP -.->|frame terbaru| SRC
    PIPE -->|"frame_ready(cam_id, QImage)"| LV
    PIPE -->|"stats_ready(cam_id, dict)"| LV
    PIPE -->|"error(cam_id, str)"| LV
    PIPE -->|"result_ready(cam_id, FrameResult)"| AP["AnalyticsPage — Fase 6"]
    CP -->|Task| TP
    ZP -->|Task| TP
    TP -->|"done(obj) / failed(str)"| CP
    TP -->|"done(obj) / failed(str)"| ZP
    CM <--> STORE
    ZP <--> STORE
    SP <--> STORE
```

## Titik rawan dan penanganannya

| Titik rawan | Penanganan |
|---|---|
| Konversi `QImage` tiap frame membebani thread GUI | Frame di-*emit* maks `display_fps` kali per detik (bawaan 15), terpisah dari laju inferensi |
| Kamera mati membuat thread diam tanpa kabar | Blok `stats_ready` berada **di luar** cabang "frame diterima", sehingga state `connecting`/`reconnecting` tetap terkirim tiap detik |
| `QThread.finished` tiba **tertunda** (queued) | `_on_worker_finished` membandingkan identitas worker sebelum mencoret entri; tanpa ini, restart cepat membuat worker baru hilang dari registry dan berjalan tanpa tercatat |
| Worker belum berhenti saat `wait(5000)` habis | Referensinya ditahan di `CameraManager._retired`; QThread yang di-GC selagi berjalan membuat proses `abort` |
| Sinyal worker tiba setelah tile dihapus | Penerima mencari tile lewat `cam_id`; tile yang tidak ada diabaikan |
| Item scene dihapus saat aplikasi ditutup, sinyal masih menembak | `sip.isdeleted()` menyaring item mati di `ZoneEditorPage` |
| Operasi jaringan membekukan dialog | Semua lewat `QThreadPool` (`net_tasks.run_async`); dialog menandai dirinya mati (`_alive`) agar tugas yang telat tidak menyentuh widget |

## Urutan mulai dan berhenti

```mermaid
sequenceDiagram
    participant U as Pengguna
    participant LV as LiveViewPage
    participant CM as CameraManager
    participant W as PipelineWorker (QThread)
    participant S as FrameSource

    U->>LV: klik ▶ pada tile
    LV->>CM: start(cam_id)
    CM->>CM: workers[cam_id] = w
    CM-->>LV: worker_started(cam_id)
    LV->>W: sambungkan frame_ready / stats_ready / error
    Note right of CM: sinyal disambung DULU,<br/>baru thread dijalankan
    CM->>W: start()
    W->>S: start()
    loop tiap frame
        S-->>W: Frame
        W->>W: Pipeline.step()
        W-->>LV: frame_ready / stats_ready (queued)
    end
    U->>LV: tutup aplikasi
    LV->>CM: stop_all()
    CM->>W: requestInterruption() + wait(5000)
    W->>S: stop()
    W-->>CM: finished
    CM->>CM: cek identitas worker, hapus entri
```

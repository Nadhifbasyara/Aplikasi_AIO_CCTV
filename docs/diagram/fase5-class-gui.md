# Fase 5 — Class Diagram GUI

> Bahan Bab III. Sumber: implementasi `aio_cctv/gui/` ([FASE-05](../fase/FASE-05-aplikasi-desktop-pyqt6.md) §3).

Prinsip yang tergambar di bawah: **`CameraManager` tidak mengenal widget sama sekali.**
Halaman memanggil manager, dan menerima kabar balik hanya lewat signal. Karena itu editor
zona dan halaman Pengaturan bisa menjalankan ulang worker tanpa tahu apa pun soal
`LiveViewPage` — tile tetap tersambung karena Live View mendengarkan `worker_started`.

```mermaid
classDiagram
    class MainWindow {
        +CameraManager camera_manager
        +QStackedWidget content_area
        +closeEvent(event) stop_all()
        -_edit_zones(cam_id)
    }

    class CameraManager {
        +dict cameras
        +dict workers
        -list _retired
        +cameras_changed
        +worker_started(cam_id)
        +worker_stopped(cam_id)
        +load()
        +save()
        +source_uri(cam) str
        +start(cam_id, settings) PipelineWorker
        +stop(cam_id)
        +stop_all()
        +is_running(cam_id) bool
    }

    class CameraConfig {
        +str id
        +str name
        +str brand
        +str host
        +str user
        +str stream
        +str file_path
        +str url
        +str profile_id
        +bool enabled
    }

    class PipelineWorker {
        <<QThread>>
        +frame_ready(cam_id, QImage)
        +stats_ready(cam_id, dict)
        +result_ready(cam_id, FrameResult)
        +error(cam_id, str)
        +run()
        +stop() bool
    }

    class LiveViewPage {
        +dict tiles
        +status_changed(str)
        +online_changed(int)
        +rebuild()
        +start_all()
        +toggle(cam_id)
    }

    class VideoTile {
        +toggle_requested(cam_id)
        +set_frame(QImage)
        +set_stats(dict)
        +set_error(str)
        +set_idle()
    }

    class CamerasPage {
        +zones_requested(cam_id)
        +add_camera()
        +edit_camera()
        +delete_camera()
    }

    class AddCameraDialog {
        <<QDialog>>
        +saved(cam_id)
        -_uri() str
        -_on_test()
        -_on_save()
    }

    class DiscoverDialog {
        <<QDialog>>
        +selected_host() str
    }

    class ZoneEditorPage {
        +list shapes
        +set_camera(cam_id)
        +take_snapshot()
        +preview_detection()
        +save() bool
    }

    class ZoneCanvas {
        <<QGraphicsView>>
        +clicked(QPointF)
        +finish_requested()
        +cancel_requested()
        +menu_requested(item, pos)
    }

    class EditablePolygon {
        <<QGraphicsPolygonItem>>
        +str shape_id
        +str name
        +str ztype
        +str color
        +attach_handles()
        +move_vertex(i, pos)
        +normalized(w, h)
    }

    class EditableLine {
        <<QGraphicsLineItem>>
        +str shape_id
        +str name
        +str color
        +flip()
        +normalized(w, h)
    }

    class VertexHandle {
        <<QGraphicsEllipseItem>>
        +itemChange(change, value)
    }

    class SettingsPage {
        +settings_saved()
        +load()
        +save()
        +reset()
    }

    class Task {
        <<QRunnable>>
        +done(object)
        +failed(str)
    }

    class Profile {
        <<pydantic>>
        +list zones
        +list lines
        +load(path)
        +save(path)
    }

    MainWindow *-- CameraManager
    MainWindow *-- LiveViewPage
    MainWindow *-- CamerasPage
    MainWindow *-- ZoneEditorPage
    MainWindow *-- SettingsPage
    CameraManager "1" o-- "*" CameraConfig
    CameraManager "1" o-- "*" PipelineWorker
    LiveViewPage "1" o-- "*" VideoTile
    LiveViewPage ..> CameraManager : start / stop
    CamerasPage ..> AddCameraDialog : buka
    AddCameraDialog ..> DiscoverDialog : "Cari di jaringan"
    AddCameraDialog ..> Task : probe / snapshot
    DiscoverDialog ..> Task : discovery
    ZoneEditorPage *-- ZoneCanvas
    ZoneEditorPage "1" o-- "*" EditablePolygon
    ZoneEditorPage "1" o-- "*" EditableLine
    EditablePolygon "1" *-- "*" VertexHandle
    EditableLine "1" *-- "*" VertexHandle
    ZoneEditorPage ..> Profile : muat / simpan
    ZoneEditorPage ..> Task : snapshot / pratinjau deteksi
    SettingsPage ..> CameraManager : restart setelah simpan
    PipelineWorker ..> Profile : dipakai Pipeline
```

## Pemisahan berkas

| Lapisan | Berkas | Tidak boleh tahu soal |
|---|---|---|
| Data & proses | `core/paths.py`, `core/secrets.py`, `gui/camera_manager.py`, `gui/workers/` | widget apa pun |
| Tampilan | `gui/pages/`, `gui/widgets/`, `gui/dialogs/` | detail thread; hanya menyambung signal |
| Analitik | `aio_cctv/pipeline.py` dan turunannya (Fase 1–3) | Qt sama sekali |

`PipelineWorker` adalah satu-satunya titik temu antara Qt dan `Pipeline`; `Pipeline` sendiri
dipakai **tanpa perubahan** dari Fase 3.

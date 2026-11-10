"""Operasi jaringan (probe/snapshot/discovery) di QThreadPool supaya dialog
tidak pernah membeku — Fase 5 §5.5."""
import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from aio_cctv.sources.url_builder import mask

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class Task(QRunnable):
    """Jalankan `fn(*args)` di thread pool; hasilnya dikirim lewat signal."""

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:                     # termasuk modul opsional yang belum terpasang
            self._emit("failed", mask(f"{type(e).__name__}: {e}"))  # URL bisa berisi password
        else:
            self._emit("done", result)

    def _emit(self, name: str, value) -> None:
        try:                                       # akses self.signals pun gagal bila sudah dihapus
            getattr(self.signals, name).emit(value)
        except RuntimeError:                       # aplikasi/penerima sudah ditutup: abaikan
            log.debug("hasil tugas latar dibuang: penerima sudah tidak ada")


def run_async(fn: Callable, *args, on_done=None, on_failed=None, **kwargs) -> Task:
    task = Task(fn, *args, **kwargs)
    if on_done is not None:
        task.signals.done.connect(on_done)
    if on_failed is not None:
        task.signals.failed.connect(on_failed)
    QThreadPool.globalInstance().start(task)
    return task


def discover_cameras(cidr: str, port: int = 554) -> list[dict]:
    """ONVIF WS-Discovery lebih dulu, lalu lengkapi dengan scan port RTSP (Fase 4 §5.5)."""
    from aio_cctv.sources.discovery import onvif_discover, scan_rtsp_port

    found: dict[str, dict] = {}
    try:
        for d in onvif_discover():
            found[d["host"]] = {"host": d["host"], "via": f"ONVIF :{d['onvif_port']}"}
    except Exception as e:      # WSDiscovery belum terpasang / multicast diblok router
        log.warning("ONVIF discovery dilewati: %s", e)
    for ip in scan_rtsp_port(cidr, port=port):
        found.setdefault(ip, {"host": ip, "via": f"port {port} terbuka"})
    return sorted(found.values(), key=lambda d: d["host"])

"""Server web lokal sebagai thread latar di dalam aplikasi desktop (Fase 8 §8.1)."""
from __future__ import annotations

import logging
import socket
import threading
import time

import uvicorn

from aio_cctv.web.api import create_app
from aio_cctv.web.state import WebState

log = logging.getLogger(__name__)


def lan_addresses() -> list[str]:
    """IP LAN PC ini (untuk ditampilkan di Pengaturan bila akses LAN aktif)."""
    out = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))       # tidak mengirim apa pun; hanya memilih rute
            out.append(s.getsockname()[0])
    except OSError:
        pass
    return out


class WebServer:
    def __init__(self, state: WebState, port: int = 8080, allow_lan: bool = False):
        self.state, self.port, self.allow_lan = state, port, allow_lan
        self.host = "0.0.0.0" if allow_lan else "127.0.0.1"
        self.error = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.started and not self._server.should_exit

    def start(self, timeout: float = 5.0) -> bool:
        """True bila server berhasil mendengarkan; selain itu `error` berisi alasannya."""
        try:                                        # cek port lebih dulu: pesan error yang jelas
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.host, self.port))
        except OSError as e:
            self.error = f"port {self.port} tidak bisa dipakai ({e.strerror or e})"
            log.warning("dashboard web tidak dijalankan: %s", self.error)
            return False
        config = uvicorn.Config(create_app(self.state), host=self.host, port=self.port,
                                log_level="warning", access_log=False, lifespan="off")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None   # bukan thread utama
        self._thread = threading.Thread(target=self._server.run, name="web-dashboard", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._server.started and self._thread.is_alive():
            time.sleep(0.05)
        if not self._server.started:
            self.error = "server web gagal dijalankan"
            return False
        log.info("dashboard web: %s (LAN %s)", self.url, "aktif" if self.allow_lan else "mati")
        self.error = ""
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = self._thread = None

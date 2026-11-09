"""Jembatan aman-thread antara CameraManager (thread GUI) dan server web (thread uvicorn).

Server web tidak pernah menyentuh objek Qt; CameraManager mendorong salinan data ke sini.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from aio_cctv.core.framehub import FrameHub
from aio_cctv.web.auth import Sessions


@dataclass
class CameraView:
    id: str
    name: str
    module_id: str
    profile_path: Path
    running: bool = False
    stats: dict = field(default_factory=dict)
    error: str = ""


class WebState:
    def __init__(self, db_path: Path, frames: FrameHub):
        self.db_path, self.frames = db_path, frames
        self.sessions = Sessions()
        self.auth_required = False
        self.password_hash = ""
        self._lock = threading.Lock()
        self._cams: dict[str, CameraView] = {}
        self._modules: dict = {}

    # ------------------------------------------------- ditulis dari thread GUI
    def set_cameras(self, views: list[CameraView]) -> None:
        with self._lock:
            old = self._cams
            for v in views:                         # status berjalan & statistik dipertahankan
                if (prev := old.get(v.id)) is not None:
                    v.running, v.stats, v.error = prev.running, prev.stats, prev.error
            self._cams = {v.id: v for v in views}

    def set_modules(self, modules: dict) -> None:
        with self._lock:
            self._modules = dict(modules)

    def set_running(self, cam_id: str, running: bool) -> None:
        with self._lock:
            if (v := self._cams.get(cam_id)) is not None:
                v.running = running
                if not running:
                    v.stats = {}
                else:
                    v.error = ""

    def set_stats(self, cam_id: str, stats: dict) -> None:
        with self._lock:
            if (v := self._cams.get(cam_id)) is not None:
                v.stats = dict(stats)

    def set_error(self, cam_id: str, msg: str) -> None:
        with self._lock:
            if (v := self._cams.get(cam_id)) is not None:
                v.error = msg

    # ------------------------------------------------- dibaca thread web
    def cameras(self) -> list[CameraView]:
        with self._lock:
            return [CameraView(v.id, v.name, v.module_id, v.profile_path, v.running,
                               dict(v.stats), v.error) for v in self._cams.values()]

    def camera(self, cam_id: str) -> CameraView | None:
        with self._lock:
            v = self._cams.get(cam_id)
            return None if v is None else CameraView(v.id, v.name, v.module_id, v.profile_path,
                                                     v.running, dict(v.stats), v.error)

    def manifest(self, module_id: str):
        with self._lock:
            info = self._modules.get(module_id)
        return info.manifest if info is not None and info.valid else None

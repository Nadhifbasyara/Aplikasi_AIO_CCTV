"""Menemukan, memvalidasi, memasang, dan memuat paket modul (Fase 6 §5.2)."""
from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import threading
import types
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from aio_cctv.core.paths import BUNDLED_MODULES_DIR, USER_MODULES_DIR
from aio_cctv.modules.api import AnalyticsModule
from aio_cctv.modules.manifest import ModuleManifest, load_manifest

DEFAULT_MODULE = "baseline"
_import_lock = threading.Lock()      # worker kamera bisa memuat modul yang sama bersamaan


@dataclass
class ModuleInfo:
    path: Path
    manifest: ModuleManifest | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)   # mis. preset rusak (modul tetap valid)

    @property
    def id(self) -> str:
        return self.manifest.id if self.manifest else self.path.name

    @property
    def valid(self) -> bool:
        return self.manifest is not None and not self.errors


def inspect(module_dir: str | Path) -> ModuleInfo:
    """Validasi tanpa menjalankan kode modul: manifest, file, dan requirements."""
    module_dir = Path(module_dir).resolve()
    info = ModuleInfo(module_dir)
    try:
        info.manifest = load_manifest(module_dir)
    except FileNotFoundError:
        info.errors.append("module.yaml tidak ditemukan")
        return info
    except ValidationError as e:
        info.errors += [f"module.yaml: {'.'.join(map(str, err['loc']))}: {err['msg']}"
                        for err in e.errors()]
        return info
    except Exception as e:                      # YAML rusak, dsb.
        info.errors.append(f"module.yaml tidak terbaca: {e}")
        return info
    info.errors += info.manifest.check_files()
    info.errors += info.manifest.check_requirements()
    info.warnings += info.manifest.load_presets()[1]
    return info


def discover(dirs: list[Path] | None = None) -> dict[str, ModuleInfo]:
    """Semua modul di folder bawaan repo lalu folder pengguna (yang terakhir menang)."""
    found: dict[str, ModuleInfo] = {}
    for root in dirs or [BUNDLED_MODULES_DIR, USER_MODULES_DIR]:
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if (p / "module.yaml").is_file()):
            info = inspect(d)
            found[info.id] = info
    return found


def resolve(ref: str | Path) -> ModuleInfo:
    """Folder modul, atau id modul yang terpasang (dipakai skrip CLI)."""
    if Path(ref).is_dir():
        return inspect(ref)
    if (info := discover().get(str(ref))) is None:
        raise KeyError(f"modul '{ref}' tidak ditemukan di {BUNDLED_MODULES_DIR} "
                       f"maupun {USER_MODULES_DIR}")
    return info


def package_name(m: ModuleManifest) -> str:
    return "aio_mod_" + m.id.replace("-", "_")


def import_package(m: ModuleManifest) -> str:
    """Daftarkan folder modul sebagai paket Python bernama unik.

    Dengan begitu modul boleh memakai import relatif (`from .dwell import ...`) dan dua
    modul yang sama-sama punya `analyzer.py` tidak saling menimpa di sys.modules.
    """
    name = package_name(m)
    with _import_lock:
        pkg = sys.modules.get(name)
        if pkg is None or list(getattr(pkg, "__path__", [])) != [m.path]:
            for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
                del sys.modules[key]               # versi lama dari folder lain
            pkg = types.ModuleType(name)
            pkg.__path__ = [m.path]
            pkg.__package__ = name
            sys.modules[name] = pkg
    return name


def load_entrypoint(m: ModuleManifest) -> type[AnalyticsModule]:
    file, cls_name = m.entrypoint.split(":")
    pkg = import_package(m)
    with _import_lock:
        mod = importlib.import_module(f"{pkg}.{file}")
    cls = getattr(mod, cls_name, None)
    if cls is None:
        raise ImportError(f"kelas {cls_name} tidak ada di {file}.py")
    if not callable(getattr(cls, "process", None)):
        raise TypeError(f"{cls_name} tidak punya method process()")
    return cls


def install(src: str | Path, replace: bool = False, dest_root: Path = USER_MODULES_DIR) -> ModuleInfo:
    """Pasang dari folder atau ZIP ke folder modul pengguna. ZIP boleh berisi satu folder induk."""
    src = Path(src)
    with tempfile.TemporaryDirectory() as tmp:
        if src.is_file() and zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                root = Path(tmp).resolve()
                for name in z.namelist():           # tolak path yang keluar dari folder tujuan
                    if not (root / name).resolve().is_relative_to(root):
                        raise ValueError(f"ZIP berisi path tidak aman: {name}")
                z.extractall(root)
            candidates = [p.parent for p in Path(tmp).rglob("module.yaml")]
            if len(candidates) != 1:
                raise ValueError("ZIP harus berisi tepat satu module.yaml")
            src_dir = candidates[0]
        elif src.is_dir():
            src_dir = src
        else:
            raise ValueError(f"{src} bukan folder atau ZIP")

        info = inspect(src_dir)
        if info.manifest is None:
            raise ValueError("; ".join(info.errors))
        dest = dest_root / info.manifest.id
        if dest.exists():
            if not replace:
                raise FileExistsError(f"modul '{info.manifest.id}' sudah terpasang")
            shutil.rmtree(dest)
        shutil.copytree(src_dir, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return inspect(dest)

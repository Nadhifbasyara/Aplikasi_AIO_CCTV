"""Daftarkan modul baseline sebagai paket `aio_mod_baseline` agar bisa diimpor di tes."""
from aio_cctv.core.paths import BUNDLED_MODULES_DIR
from aio_cctv.modules.manifest import load_manifest
from aio_cctv.modules.registry import import_package

import_package(load_manifest(BUNDLED_MODULES_DIR / "baseline"))

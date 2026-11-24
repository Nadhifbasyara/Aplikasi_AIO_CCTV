"""Layanan Jalur B di sisi aplikasi: menyiapkan model & mengelola container DeepStream.

Satu container ("grup") per model. Kuncinya = `detector_key` modul + resolusi nvstreammux,
jadi semua kamera yang memakai model sama dibatch bersama (sama seperti InferenceServer
Jalur A). Kamera ditambah/dilepas lewat `POOL.acquire/release`. Pipeline DeepStream belum
mendukung tambah-kurang sumber tanpa restart di sini, jadi perubahan daftar kamera memicu
restart container (dikumpulkan dulu `DEBOUNCE_S` detik supaya kamera yang dinyalakan
bersamaan cukup satu kali restart). Engine TensorRT tersimpan di cache, sehingga restart
berikutnya hanya beberapa detik.

Persiapan model (sekali per bobot, otomatis saat kamera pertama dinyalakan; bisa juga lewat
`scripts/deepstream_setup.py`):
  1. ekspor .pt → ONNX (third_party/deepstream_yolo/export_yolo11.py, venv aplikasi)
  2. kompilasi parser YOLO di dalam container (sekali untuk semua model)
  3. engine TensorRT FP16 dibangun nvinfer saat pertama kali dijalankan (±30 detik)
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from platformdirs import user_cache_dir

from aio_cctv.inference.deepstream import shm

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
VENDOR = ROOT / "third_party" / "deepstream_yolo"
BASE_IMAGE = "nvcr.io/nvidia/deepstream:9.1-triton-multiarch"
IMAGE = "aio-cctv/deepstream:9.1"
CUDA_VER = "13.2"                     # CUDA di dalam image DeepStream 9.1
ENGINE_BATCH = 32                     # batch maksimum engine; mux boleh lebih (nvinfer memecah)
DEBOUNCE_S = 1.5
CACHE = Path(os.environ.get("AIO_DS_CACHE", user_cache_dir("aio-cctv", "tugasakhir"))) / \
    "deepstream"
NAME_PREFIX = "aio-ds-"


class DeepStreamUnavailable(RuntimeError):
    """Docker/image/GPU tidak siap. Pesannya ditampilkan ke pengguna."""


def _run(cmd: list[str], timeout: float = 600, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, **kw)


# ---------------------------------------------------------------- pemeriksaan lingkungan
def check_environment() -> None:
    if shutil.which("docker") is None:
        raise DeepStreamUnavailable("Docker tidak terpasang — backend DeepStream butuh Docker "
                                    "+ NVIDIA Container Toolkit.")
    if _run(["docker", "info"], timeout=20).returncode != 0:
        raise DeepStreamUnavailable("Docker tidak bisa diakses (layanan mati atau user bukan "
                                    "anggota grup docker).")
    ensure_image()


def ensure_image() -> None:
    if _run(["docker", "image", "inspect", IMAGE], timeout=30).returncode == 0:
        return
    if _run(["docker", "image", "inspect", BASE_IMAGE], timeout=30).returncode != 0:
        raise DeepStreamUnavailable(
            f"Image DeepStream belum diunduh (±10 GB). Jalankan sekali:  docker pull {BASE_IMAGE}")
    log.info("membangun image %s", IMAGE)
    r = _run(["docker", "build", "-q", "-t", IMAGE, str(HERE)], timeout=900)
    if r.returncode != 0:
        raise DeepStreamUnavailable(f"Gagal membangun image {IMAGE}: {r.stderr[-400:]}")


def _docker_gpu_args() -> list[str]:
    return ["--device", "nvidia.com/gpu=all"]


# ---------------------------------------------------------------- persiapan model
def parser_lib() -> Path:
    """Kompilasi parser DeepStream-Yolo di dalam container (sekali), kembalikan path .so."""
    out = CACHE / "parser" / "libnvdsinfer_custom_impl_Yolo.so"
    stamp = (VENDOR / "UPSTREAM_COMMIT").read_text().strip() + CUDA_VER
    marker = out.with_suffix(".built")
    if out.exists() and marker.exists() and marker.read_text() == stamp:
        return out
    src = CACHE / "parser" / "src"
    shutil.rmtree(src, ignore_errors=True)
    shutil.copytree(VENDOR / "nvdsinfer_custom_impl_Yolo", src)
    log.info("mengompilasi parser YOLO DeepStream (sekali, ±1 menit)")
    r = _run(["docker", "run", "--rm", "--entrypoint", "", *_docker_gpu_args(),
              "-u", f"{os.getuid()}:{os.getgid()}", "-v", f"{src}:/src", "-w", "/src",
              "-e", f"CUDA_VER={CUDA_VER}", IMAGE, "bash", "-c", f"make -j{os.cpu_count() or 4}"],
             timeout=900)
    if r.returncode != 0 or not (src / out.name).exists():
        raise DeepStreamUnavailable(f"Kompilasi parser YOLO gagal: {r.stdout[-300:]}"
                                    f"{r.stderr[-300:]}")
    shutil.copy2(src / out.name, out)
    marker.write_text(stamp)
    return out


def model_dir(manifest) -> Path:
    w = Path(manifest.weights_path)
    tag = hashlib.sha1(f"{w.resolve()}|{w.stat().st_mtime_ns}|{manifest.model.imgsz}".encode()) \
        .hexdigest()[:10]
    return CACHE / "models" / f"{w.stem}-{tag}"


def ensure_onnx(manifest) -> tuple[Path, list[str]]:
    """Ekspor bobot modul ke ONNX (sekali per bobot). Kembalikan (folder, nama kelas)."""
    d = model_dir(manifest)
    onnx, labels = d / "model.onnx", d / "labels.txt"
    if not (onnx.exists() and labels.exists()):
        d.mkdir(parents=True, exist_ok=True)
        weights = Path(manifest.weights_path)
        if weights.suffix != ".pt":
            raise DeepStreamUnavailable(f"Backend DeepStream butuh bobot .pt (modul memakai "
                                        f"{weights.suffix}).")
        shutil.copy2(weights, d / "model.pt")
        log.info("ekspor ONNX untuk DeepStream: %s", weights)
        r = _run([sys.executable, str(VENDOR / "export_yolo11.py"), "-w", "model.pt",
                  "-s", str(manifest.model.imgsz), "--dynamic", "--simplify"], cwd=d, timeout=600)
        (d / "model.pt").unlink(missing_ok=True)
        if r.returncode != 0 or not onnx.exists():
            raise DeepStreamUnavailable(f"Ekspor ONNX gagal (butuh paket onnx & onnxslim di "
                                        f"venv): {r.stderr[-400:]}")
    names = [ln.strip() for ln in labels.read_text().splitlines() if ln.strip()]
    return d, names


def infer_config(manifest, names: list[str]) -> str:
    """Isi config nvinfer, setara Detector Jalur A (kelas, conf, iou, imgsz dari manifest)."""
    m = manifest.model
    wanted = set(m.classes or names)
    missing = wanted - set(names)
    if missing:
        raise DeepStreamUnavailable(f"Kelas {sorted(missing)} tidak ada di model; "
                                    f"tersedia: {names}")
    drop = [str(i) for i, n in enumerate(names) if n not in wanted]
    lines = [
        "[property]",
        "gpu-id=0",
        "net-scale-factor=0.0039215697906911373",
        "model-color-format=0",
        "onnx-file=/aio/model/model.onnx",
        f"model-engine-file=/aio/model/model_b{ENGINE_BATCH}_gpu0_fp16.engine",
        "labelfile-path=/aio/model/labels.txt",
        f"batch-size={ENGINE_BATCH}",
        "network-mode=2",                       # FP16 (Proposal §2.6)
        f"num-detected-classes={len(names)}",
        "interval=0",
        "gie-unique-id=1",
        "process-mode=1",
        "network-type=0",
        "cluster-mode=2",                        # NMS
        "maintain-aspect-ratio=1",
        "symmetric-padding=1",
        "parse-bbox-func-name=NvDsInferParseYolo",
        "custom-lib-path=/aio/parser/libnvdsinfer_custom_impl_Yolo.so",
        "engine-create-func-name=NvDsInferYoloCudaEngineGet",
    ]
    if drop:
        lines.append("filter-out-class-ids=" + ";".join(drop))
    lines += ["", "[class-attrs-all]", f"nms-iou-threshold={m.iou}",
              f"pre-cluster-threshold={m.conf}", "topk=300", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- container per model
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cleanup_stale() -> None:
    """Hapus container aio-ds-<pid>-* milik proses aplikasi yang sudah mati (mis. crash)."""
    r = _run(["docker", "ps", "-a", "--filter", f"name={NAME_PREFIX}", "--format", "{{.Names}}"],
             timeout=20)
    for name in r.stdout.split():
        try:
            pid = int(name[len(NAME_PREFIX):].split("-")[0])
        except ValueError:
            continue
        if not _pid_alive(pid):
            _run(["docker", "rm", "-f", name], timeout=30)


class Group:
    """Satu container DeepStream untuk satu model; kamera bisa ditambah/dilepas."""

    def __init__(self, index: int, manifest, width: int, height: int):
        self.manifest, self.width, self.height = manifest, width, height
        self.name = f"{NAME_PREFIX}{os.getpid()}-{index}"
        self.run_dir = CACHE / "run" / self.name
        self.cams: dict[str, tuple[str, Path]] = {}     # cam_id -> (uri, slot path)
        self.slots: dict[str, shm.Slot] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._proc: subprocess.Popen | None = None
        self.model_dir: Path | None = None
        self.parser: Path | None = None

    def prepare(self) -> None:
        check_environment()
        self.parser = parser_lib()
        self.model_dir, names = ensure_onnx(self.manifest)
        (self.model_dir / "config_infer.txt").write_text(infer_config(self.manifest, names))
        self.names = names

    def add(self, cam_id: str, uri: str) -> shm.Slot:
        with self._lock:
            path = shm.slot_path(f"{self.name}-{hashlib.sha1(cam_id.encode()).hexdigest()[:8]}")
            slot = shm.Slot(path, self.width, self.height, create=True)
            self.cams[cam_id] = (uri, path)
            self.slots[cam_id] = slot
            self._schedule()
            return slot

    def remove(self, cam_id: str) -> None:
        with self._lock:
            self.cams.pop(cam_id, None)
            slot = self.slots.pop(cam_id, None)
            if self.cams:                         # kamera terakhir: GroupPool memanggil shutdown
                self._schedule()
        if slot is not None:
            slot.unlink()

    @property
    def empty(self) -> bool:
        return not self.cams

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(DEBOUNCE_S, self._restart)
        self._timer.daemon = True
        self._timer.start()

    def _stop_container(self) -> None:
        _run(["docker", "rm", "-f", self.name], timeout=30)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def _restart(self) -> None:
        with self._lock:
            cams = dict(self.cams)
            for slot in self.slots.values():          # frame lama tidak boleh dibaca lagi
                slot.set("state", shm.IDLE)
                slot.set("conn", shm.CONNECTING)
        self._stop_container()
        if not cams:
            shutil.rmtree(self.run_dir, ignore_errors=True)
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        cfg = {"infer_config": "/aio/model/config_infer.txt", "width": self.width,
               "height": self.height,
               "cams": [{"id": cid, "uri": uri, "slot": str(path)}
                        for cid, (uri, path) in cams.items()]}
        (self.run_dir / "cameras.json").write_text(json.dumps(cfg))
        # sumber file:// (skrip setup, uji) harus terlihat di container: mount read-only
        file_dirs = sorted({str(Path(unquote(urlparse(uri).path)).parent)
                            for uri, _ in cams.values() if uri.startswith("file://")})
        mounts = [arg for d in file_dirs for arg in ("-v", f"{d}:{d}:ro")]
        cmd = ["docker", "run", "--rm", "--name", self.name, "--entrypoint", "", *mounts,
               *_docker_gpu_args(), "--network", "host", "--ipc", "host",
               "--ulimit", "nofile=65536:65536",
               "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
               # cwd = folder model: parser YOLO menyimpan engine TensorRT di cwd, jadi engine
               # tersimpan di cache dan restart berikutnya tidak membangun ulang (±30 s)
               "-w", "/aio/model",
               "-v", f"{HERE}:/aio/ds:ro", "-v", f"{self.model_dir}:/aio/model",
               "-v", f"{self.parser.parent}:/aio/parser:ro", "-v", f"{self.run_dir}:/aio/run",
               IMAGE,
               "python3", "/aio/ds/runner.py", "/aio/run/cameras.json"]
        logf = open(self.run_dir / "runner.log", "w")  # noqa: SIM115 — dipakai Popen sampai container berhenti
        self._proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        log.info("DeepStream %s: %d kamera (%s)", self.name, len(cams),
                 Path(self.manifest.weights_path).name)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def log_tail(self, n: int = 400) -> str:
        try:
            return (self.run_dir / "runner.log").read_text(errors="replace")[-n:]
        except OSError:
            return ""

    def shutdown(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._stop_container()
        logs = CACHE / "logs"                      # simpan log terakhir untuk diagnosis
        logs.mkdir(parents=True, exist_ok=True)
        if (self.run_dir / "runner.log").exists():
            shutil.copy2(self.run_dir / "runner.log", logs / f"{self.name}.log")
        shutil.rmtree(self.run_dir, ignore_errors=True)
        for slot in self.slots.values():
            slot.unlink()
        self.slots.clear()
        self.cams.clear()


class GroupPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._groups: dict[tuple, Group] = {}
        self._of_cam: dict[str, tuple] = {}
        self._cleaned = False
        self._n = 0

    def acquire(self, key: tuple, manifest, cam_id: str, uri: str, width: int, height: int
                ) -> tuple[Group, shm.Slot]:
        with self._lock:
            if not self._cleaned:
                cleanup_stale()
                self._cleaned = True
            group = self._groups.get(key)
            if group is None:
                self._n += 1
                group = Group(self._n, manifest, width, height)
                group.prepare()                    # bisa lama saat pertama (ekspor/kompilasi)
                self._groups[key] = group
            self._of_cam[cam_id] = key
        return group, group.add(cam_id, uri)

    def release(self, cam_id: str) -> None:
        with self._lock:
            key = self._of_cam.pop(cam_id, None)
            group = self._groups.get(key) if key else None
        if group is None:
            return
        group.remove(cam_id)
        if group.empty:
            with self._lock:
                last = group.empty and self._groups.get(key) is group
                if last:
                    del self._groups[key]
            if last:                               # langsung, jangan menunggu debounce:
                group.shutdown()                   # aplikasi bisa keluar sebelum timer jalan

    def active(self) -> int:
        with self._lock:
            return len(self._groups)

    def shutdown(self) -> None:
        with self._lock:
            groups = list(self._groups.values())
            self._groups.clear()
            self._of_cam.clear()
        for g in groups:
            g.shutdown()


POOL = GroupPool()
atexit.register(POOL.shutdown)


def wait_streaming(slot: shm.Slot, timeout_s: float, cancel=None) -> bool:
    """Tunggu sumber mulai mengalir. cancel(): True = berhenti menunggu (kamera dimatikan)."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if slot.get("conn") == shm.STREAMING:
            return True
        if cancel is not None and cancel():
            return False
        time.sleep(0.1)
    return False

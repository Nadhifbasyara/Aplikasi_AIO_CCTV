"""Sampel pemakaian sumber daya PC untuk tabel system_minute (Fase 7 §2.2, bahan Fase 9)."""
import logging
import os

import psutil

log = logging.getLogger(__name__)


class SystemSampler:
    """CPU seluruh sistem, RAM proses aplikasi, utilisasi GPU & VRAM proses (bila NVIDIA)."""

    def __init__(self):
        self.proc = psutil.Process(os.getpid())
        psutil.cpu_percent(None)                    # panggilan pertama selalu 0: inisialisasi
        self._gpu = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml = pynvml
            self._gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception as e:                      # tanpa GPU NVIDIA / driver: lewati GPU
            log.info("pemantauan GPU tidak tersedia: %s", e)

    def sample(self) -> dict:
        out = {"cpu_pct": psutil.cpu_percent(None),
               "ram_mb": self.proc.memory_info().rss / 2**20,
               "gpu_pct": None, "vram_mb": None,
               # seluruh GPU (termasuk container DeepStream & desktop), daya, dan decoder
               # hardware — untuk membandingkan Jalur A vs B secara adil (Proposal §3.5.3)
               "vram_total_mb": None, "gpu_watt": None, "nvdec_pct": None}
        if self._gpu is not None:
            try:
                out["gpu_pct"] = float(self._nvml.nvmlDeviceGetUtilizationRates(self._gpu).gpu)
                procs = self._nvml.nvmlDeviceGetComputeRunningProcesses(self._gpu)
                mine = [p.usedGpuMemory for p in procs
                        if p.pid == self.proc.pid and p.usedGpuMemory is not None]
                used = sum(mine) if mine else self._nvml.nvmlDeviceGetMemoryInfo(self._gpu).used
                out["vram_mb"] = used / 2**20
            except Exception as e:
                log.debug("gagal membaca GPU: %s", e)
            for key, read in (
                    ("vram_total_mb",
                     lambda: self._nvml.nvmlDeviceGetMemoryInfo(self._gpu).used / 2**20),
                    ("gpu_watt", lambda: self._nvml.nvmlDeviceGetPowerUsage(self._gpu) / 1000),
                    ("nvdec_pct",
                     lambda: float(self._nvml.nvmlDeviceGetDecoderUtilization(self._gpu)[0]))):
                try:                                # tiap GPU/driver beda dukungan: per kolom
                    out[key] = read()
                except Exception as e:
                    log.debug("gagal membaca %s: %s", key, e)
        return out

"""Cek kesiapan lingkungan + catat spesifikasi perangkat uji (Proposal §10 'Kinerja')."""
import platform
import shutil

import cv2
import torch
import ultralytics
import supervision as sv

print("OS          :", platform.platform())
print("CPU         :", platform.processor() or platform.machine())
print("Python      :", platform.python_version())
print("OpenCV      :", cv2.__version__)
print("  FFmpeg    :", "YES" if "FFMPEG:                      YES" in cv2.getBuildInformation() else "cek manual")
print("PyTorch     :", torch.__version__, "| CUDA:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU         :", torch.cuda.get_device_name(0))
print("Ultralytics :", ultralytics.__version__)
print("Supervision :", sv.__version__)
print("ffmpeg bin  :", shutil.which("ffmpeg"))

from ultralytics import YOLO
model = YOLO("yolo11n.pt")            
res = model("https://ultralytics.com/images/bus.jpg", verbose=False)[0]
print("Uji deteksi :", len(res.boxes), "objek terdeteksi")
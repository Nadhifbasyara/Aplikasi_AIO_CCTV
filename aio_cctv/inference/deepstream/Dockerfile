# Image runtime Jalur B (Fase 9 §5.8): DeepStream 9.1 + pustaka Python yang dibutuhkan
# pyservicemaker namun tidak ikut di image NVIDIA. Dibangun otomatis oleh service.py:
#   docker build -t aio-cctv/deepstream:9.1 aio_cctv/inference/deepstream
FROM nvcr.io/nvidia/deepstream:9.1-triton-multiarch
RUN pip3 install --no-cache-dir --break-system-packages pyyaml==6.0.2

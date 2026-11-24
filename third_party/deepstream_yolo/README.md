# DeepStream-Yolo (vendor)

Salinan sebagian dari <https://github.com/marcoslucianops/DeepStream-Yolo> (lisensi MIT, lihat
`LICENSE.md`), commit di `UPSTREAM_COMMIT`. Dipakai Jalur B (DeepStream, Fase 9 §5.8):

- `nvdsinfer_custom_impl_Yolo/` — parser keluaran YOLO + pembuat engine TensorRT untuk `nvinfer`.
  Dikompilasi otomatis di dalam container oleh `aio_cctv/inference/deepstream/service.py`.
- `export_yolo11.py` — ekspor bobot `.pt` YOLO11 ke ONNX berformat keluaran yang dipahami parser.
  **Diubah:** `dynamo=False` pada `torch.onnx.export` (PyTorch ≥ 2.9 memakai exporter dynamo
  yang butuh `onnxscript`).

Tidak ada perubahan lain dari upstream.

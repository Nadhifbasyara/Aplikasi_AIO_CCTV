#!/usr/bin/env bash
# Pengukuran final ablation Fase 9 (§7) — jalankan saat PC tidak dipakai (±7–8 jam per ulangan).
#   nohup scripts/jalan_semalam.sh 1 > bench-r1.log 2>&1 &   # malam 1: ulangan 1 (±7–8 jam)
#   nohup scripts/jalan_semalam.sh 2 > bench-r2.log 2>&1 &   # malam 2: ulangan 2
# Tanpa argumen = ulangan 1 dan 2 berturut-turut (±15 jam).
# Video lain: VIDEO=data/bench/klipku.mp4 nohup scripts/jalan_semalam.sh 1 > bench-r1.log 2>&1 &
# Aplikasi (main.py) HARUS ditutup dulu. Hasil: docs/hasil-evaluasi/kapasitas-final.csv
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
OUT=docs/hasil-evaluasi/kapasitas-final.csv
VIDEO=${VIDEO:-data/bench/klip720p15.mp4}
[[ -f "$VIDEO" ]] || { echo "video tidak ditemukan: $VIDEO" >&2; exit 1; }
COMMON=(--warmup 60 --duration 600 --cams 1,2,4,8,10,12,14,16,18,20,24 --video "$VIDEO" --out "$OUT")

for run in ${@:-1 2}; do
  echo "===== ulangan $run — $(date) — video $VIDEO ====="
  $PY scripts/benchmark_capacity.py --label "0 baseline r$run" "${COMMON[@]}" \
      --set inference=per_camera --set target_fps=0
  $PY scripts/benchmark_capacity.py --label "1 shared B1 r$run" "${COMMON[@]}" \
      --set inference=shared --set max_batch=1 --set target_fps=0
  $PY scripts/benchmark_capacity.py --label "2 shared B8 r$run" "${COMMON[@]}" \
      --set max_batch=8 --set target_fps=0
  $PY scripts/benchmark_capacity.py --label "3 +target10 r$run" "${COMMON[@]}"
  $PY scripts/benchmark_capacity.py --label "4 render hidden r$run" "${COMMON[@]}" --render hidden
done
echo "===== selesai — $(date) ====="

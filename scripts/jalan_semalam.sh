#!/usr/bin/env bash
# Pengukuran final ablation Fase 9 (§7) — jalankan saat PC tidak dipakai.
#
#   scripts/jalan_semalam.sh [ULANGAN…] [python|deepstream]
#
# Jalur A (Python, bawaan; ±7–8 jam per ulangan) → docs/hasil-evaluasi/kapasitas-final.csv
#   nohup scripts/jalan_semalam.sh 1 > bench-r1.log 2>&1 &
#   nohup scripts/jalan_semalam.sh 2 > bench-r2.log 2>&1 &
# Jalur B (DeepStream; ±3,5 jam per ulangan, 2 ulangan muat semalam)
#   → docs/hasil-evaluasi/kapasitas-final-deepstream.csv
#   nohup scripts/jalan_semalam.sh deepstream > bench-ds.log 2>&1 &        # ulangan 1 dan 2
# Tanpa nomor ulangan = ulangan 1 dan 2 berturut-turut.
# Video lain: VIDEO=data/bench/klipku.mp4 nohup scripts/jalan_semalam.sh … &
# Aplikasi (main.py) HARUS ditutup dulu.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
VIDEO=${VIDEO:-data/bench/klip720p15.mp4}
[[ -f "$VIDEO" ]] || { echo "video tidak ditemukan: $VIDEO" >&2; exit 1; }

JALUR=python
RUNS=()
for a in "$@"; do
  case "$a" in
    python|deepstream) JALUR=$a ;;
    [0-9]*) RUNS+=("$a") ;;
    *) echo "argumen tidak dikenal: $a (pakai nomor ulangan dan/atau python|deepstream)" >&2
       exit 1 ;;
  esac
done
[[ ${#RUNS[@]} -eq 0 ]] && RUNS=(1 2)

# hanya satu pengukuran sekaligus: dua salinan saling membebani dan hasilnya tidak valid
exec 9>/tmp/aio-cctv-jalan-semalam.lock
if ! flock -n 9; then
  echo "pengukuran lain masih berjalan (jalan_semalam.sh) — tunggu selesai atau hentikan dulu" >&2
  exit 1
fi

if pgrep -f '^(\S*/)?python[0-9.]* main\.py( |$)' >/dev/null; then
  echo "aplikasi (main.py) masih berjalan — tutup dulu supaya hasil tidak terganggu" >&2
  exit 1
fi

if [[ $JALUR == python ]]; then
  OUT=docs/hasil-evaluasi/kapasitas-final.csv
  COMMON=(--warmup 60 --duration 600 --cams 1,2,4,8,10,12,14,16,18,20,24
          --video "$VIDEO" --out "$OUT")
else
  OUT=docs/hasil-evaluasi/kapasitas-final-deepstream.csv
  COMMON=(--warmup 60 --duration 600 --cams 8,16,24,32,40,48,56,64,80
          --video "$VIDEO" --out "$OUT" --set backend=deepstream)
  # siapkan engine TensorRT lebih dulu supaya N pertama tidak ikut menunggu build (±30 s)
  $PY scripts/deepstream_setup.py --module baseline || exit 1
fi

for run in "${RUNS[@]}"; do
  echo "===== jalur $JALUR, ulangan $run — $(date) — video $VIDEO ====="
  if [[ $JALUR == python ]]; then
    $PY scripts/benchmark_capacity.py --label "0 baseline r$run" "${COMMON[@]}" \
        --set inference=per_camera --set target_fps=0
    $PY scripts/benchmark_capacity.py --label "1 shared B1 r$run" "${COMMON[@]}" \
        --set inference=shared --set max_batch=1 --set target_fps=0
    $PY scripts/benchmark_capacity.py --label "2 shared B8 r$run" "${COMMON[@]}" \
        --set max_batch=8 --set target_fps=0
    $PY scripts/benchmark_capacity.py --label "3 +target10 r$run" "${COMMON[@]}"
    $PY scripts/benchmark_capacity.py --label "4 render hidden r$run" "${COMMON[@]}" --render hidden
  else
    # padanan baris A3 (tile tampil) dan A4 (analisis saja); B4 = tanpa batas FPS analisis
    $PY scripts/benchmark_capacity.py --label "B2 deepstream visible r$run" "${COMMON[@]}"
    $PY scripts/benchmark_capacity.py --label "B3 deepstream hidden r$run" "${COMMON[@]}" \
        --render hidden
    $PY scripts/benchmark_capacity.py --label "B4 deepstream hidden fps penuh r$run" \
        "${COMMON[@]}" --render hidden --set target_fps=0
  fi
done
echo "===== selesai — $(date) ====="

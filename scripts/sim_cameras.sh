#!/usr/bin/env bash
# Simulasi N kamera RTSP dari satu file video (Fase 9 §3.2).
#
# Pemakaian:
#   scripts/sim_cameras.sh <video> <N> [prefix]      # mulai; Ctrl+C untuk berhenti
#   scripts/sim_cameras.sh stop                      # hentikan semua simulasi
#
# Hasil: rtsp://127.0.0.1:8554/<prefix>1 … <prefix>N (prefix bawaan "cam").
# Video di-stream ulang dengan `-c copy` (tanpa encode ulang) supaya simulator hampir tidak
# memakai CPU dan tidak mengganggu pengukuran. Siapkan klip dulu, mis.:
#   ffmpeg -i data/videos/ref1.mp4 -vf "fps=15,scale=1280:-2" -c:v libx264 -preset veryfast \
#     -g 15 -bf 0 -an data/bench/klip720p15.mp4
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "stop" ]]; then
  pkill -f "ffmpeg .*rtsp://127.0.0.1:8554/" || true
  pkill -f "^./mediamtx" || true
  echo "simulasi dihentikan"
  exit 0
fi

VIDEO=${1:?pemakaian: sim_cameras.sh <video> <N> [prefix]}
N=${2:?jumlah kamera}
PREFIX=${3:-cam}

if ! (exec 3<>/dev/tcp/127.0.0.1/8554) 2>/dev/null; then
  [[ -x ./mediamtx ]] || { echo "mediamtx tidak ditemukan di folder proyek" >&2; exit 1; }
  ./mediamtx >/tmp/mediamtx-sim.log 2>&1 &
  echo "mediamtx dijalankan (log: /tmp/mediamtx-sim.log)"
  for _ in $(seq 20); do (exec 3<>/dev/tcp/127.0.0.1/8554) 2>/dev/null && break; sleep 0.2; done
fi

pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT INT TERM
for i in $(seq 1 "$N"); do
  ffmpeg -nostdin -loglevel error -re -stream_loop -1 -i "$VIDEO" -c copy \
    -f rtsp -rtsp_transport tcp "rtsp://127.0.0.1:8554/${PREFIX}${i}" &
  pids+=($!)
done
echo "$N kamera: rtsp://127.0.0.1:8554/${PREFIX}1 … ${PREFIX}${N}  (Ctrl+C untuk berhenti)"
wait

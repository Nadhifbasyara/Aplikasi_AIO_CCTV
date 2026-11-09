# FASE 10 — Pengujian & Evaluasi

> Sebelumnya: [FASE-09](FASE-09-optimasi-sumber-daya-packaging.md) · Kembali ke [Roadmap](00-ROADMAP.md) · Berikutnya: [FASE-11 Laporan TA](FASE-11-dokumentasi-laporan-ta.md)
> Referensi: rumusan masalah **RM-1 s.d. RM-3** ([Roadmap §1](00-ROADMAP.md#usulan-rumusan-masalah-sesuaikan-dengan-proposal-resmi-pasca-sidang)), Proposal **§10** (bagian kinerja & skalabilitas), **§5** (uji lapangan terbatas)
> Estimasi: **3 minggu** · Milestone: **M5 — Terukur & siap rilis**
> *Revisi 2026-11-09: dahulu FASE-09. Evaluasi **akurasi model dan tracking** (mAP, MOTA/IDF1, error dwell/counting pada MOT17/MOT20/Mall) **dihapus** dari TA ini karena merupakan ranah TA-1 & TA-2. Diganti dengan uji integrasi modul, keandalan RTSP/DVR, dan kapasitas sumber daya.*

---

## 1. Tujuan

Menghasilkan **bukti kuantitatif** untuk setiap rumusan masalah TA ini, dengan memisahkan secara tegas apa yang diukur di sini dan apa yang menjadi ranah rekan:

| Diukur di TA ini | **Tidak** diukur di TA ini (dikutip dari TA-1/TA-2 bila perlu) |
|---|---|
| Kebenaran integrasi modul (paritas), isolasi error, ganti modul tanpa ubah kode | mAP / precision / recall model YOLO11 |
| Keandalan RTSP kamera IP & DVR (latensi, pemulihan, uptime) | MOTA, IDF1, HOTA, ID switch |
| Kapasitas kamera per PC, pemakaian CPU/GPU/RAM, latensi end-to-end, kontribusi tiap optimasi | Akurasi dwell, counting, klasifikasi aktivitas/produktivitas |
| Fungsional (black-box) & usability (SUS) | Validitas metrik perilaku konsumen/produktivitas |

## 2. Peta Evaluasi

| RM | Aspek | Metrik | Data / alat | Skrip |
|---|---|---|---|---|
| **RM-1** | Kepatuhan modul | Lulus/gagal per butir `check_module` | Paket baseline, TA-1, TA-2 | `check_module.py` (F6) |
| RM-1 | **Paritas integrasi** | Selisih jumlah event per tipe; selisih maks metrik; per backend | `samples/` + `expected.jsonl` dari rekan | `tests/test_parity.py` |
| RM-1 | Tanpa ubah kode | Baris kode host diubah (harus 0), waktu & langkah konfigurasi | Protokol Fase 8 §7 | `git diff --stat` |
| RM-1 | Isolasi error | Kamera lain tetap ≥ `F_min` saat satu modul crash | Modul uji yang melempar exception | `tests/test_isolation.py` |
| RM-1 | Fungsional | Black-box (§5.1) | — | tabel uji |
| RM-1 | Usability | **SUS**, tingkat keberhasilan tugas, waktu tugas | ≥ 10 responden | kuesioner |
| **RM-2** | Kompatibilitas | Merek/model kamera & DVR yang berhasil, beserta URL & codec | Perangkat fisik | matriks Fase 4 + DVR Fase 9 |
| RM-2 | Latensi | Glass-to-glass (ms), rata-rata ± SD | Stopwatch di depan kamera vs layar | `rtsp_latency.py` (F4) |
| RM-2 | Pemulihan | Waktu pulih setelah putus (s); kabel dicabut, `tc netem` loss/delay | Kamera fisik + MediaMTX | uji Fase 4 |
| RM-2 | Uptime | % waktu streaming selama 24 jam; reconnect/24 jam | `camera_health` | query |
| RM-2 | DVR multi-channel | Channel bersamaan yang stabil per DVR; bandwidth | DVR ≥ 4 channel | Fase 9 §6 |
| **RM-3** | **Kapasitas** | `N_max` per perangkat × modul (kriteria Fase 9 §2) | Simulasi MediaMTX + kamera nyata | `benchmark_capacity.py` |
| RM-3 | Kontribusi optimasi | Δ`N_max`, Δ CPU/GPU/VRAM per teknik | Matriks ablation Fase 9 §7 | `benchmark_capacity.py` |
| RM-3 | Latensi pipeline | `latency_ms` p50/p95 vs N | `camera_health` | query |
| RM-3 | Stabilitas | Memori & FPS selama 24–72 jam | Soak test Fase 9 §8.5 | query + grafik |

## 3. Persiapan

### 3.1 Perangkat

| Kategori | Minimal | Keterangan |
|---|---|---|
| PC edge | 2 kelas (GPU NVIDIA & tanpa GPU diskrit) | Spesifikasi lengkap dicatat (CPU, GPU, RAM, OS, driver) |
| Kamera IP | ≥ 2 merek berbeda dengan RTSP | Dari matriks Fase 4 |
| DVR/NVR | ≥ 1 unit, ≥ 4 channel | Pinjam dari lab/toko/kerabat bila perlu |
| PC simulator | 1 (boleh laptop) | Menjalankan MediaMTX + ffmpeg (Fase 9 §3.2) |
| Jaringan | Switch/router LAN | Catat topologi untuk diagram deployment |

### 3.2 Modul & data

- Modul **baseline**, **TA-1 v1**, **TA-2 v1** (atau versi terakhir yang tersedia; catat versi & hash).
- Video uji per modul, sesuai domain (area komersial untuk TA-1, area kerja untuk TA-2). Sebaiknya video yang sama dengan yang dipakai rekan, agar uji paritas bermakna.
- Untuk simulasi kapasitas: satu klip 1280×720 @15 FPS yang mewakili kepadatan tipikal. **Kepadatan orang memengaruhi `module_ms`**, jadi gunakan klip sepi dan ramai lalu laporkan keduanya.

## 4. Prosedur Uji Utama

### 4.1 Uji paritas (RM-1)

1. Rekan menjalankan modulnya **di luar aplikasi** pada `samples/demo.mp4` → `expected.jsonl` (per frame: metrik & event).
2. Host menjalankan modul yang sama melalui pipeline aplikasi dengan stride 1 dan backend `.pt`.
3. Bandingkan: jumlah event per tipe (harus sama), nilai metrik per frame (|Δ| ≤ 1e-6), dan `track_id` per frame (harus sama untuk tracker deterministik).
4. Ulangi untuk backend TensorRT FP16 / OpenVINO. Selisih yang muncul di sini **dilaporkan apa adanya** sebagai konsekuensi optimasi, beserta besarnya.

| Modul | Backend | Δ event (per tipe) | Δ metrik maks | Track ID identik? | Status |
|---|---|---|---|---|---|
| Baseline | PyTorch | | | | |
| Baseline | TensorRT FP16 | | | | |
| TA-1 | PyTorch | | | | |
| TA-1 | TensorRT FP16 | | | | |
| TA-2 | PyTorch | | | | |
| TA-2 | TensorRT FP16 | | | | |

### 4.2 Kapasitas (RM-3)

Ikuti Fase 9 §3.3 dan §7. Hasil akhir yang dicari:

| Perangkat | Modul | `N_max` baseline | `N_max` teroptimasi | Peningkatan | Hambatan utama |
|---|---|---|---|---|---|
| PC RTX 5060 | Baseline | | | ×… | CPU decode / GPU / GIL / modul |
| PC RTX 5060 | TA-1 | | | | |
| PC RTX 5060 | TA-2 | | | | |
| PC tanpa GPU | Baseline | | | | |
| PC tanpa GPU | TA-1 | | | | |

Grafik wajib: (a) FPS min per kamera vs N, (b) latensi p95 vs N, (c) VRAM vs N untuk baseline vs InferenceServer, (d) *waterfall* kontribusi tiap teknik terhadap `N_max`.

### 4.3 Keandalan RTSP/DVR (RM-2)

Ulangi uji Fase 4 dengan **build rilis** dan tambahkan DVR:

| Perangkat | Stream | Latensi ± SD (ms) | Waktu pulih (s) | Uptime 24 jam (%) | Reconnect / 24 jam | FPS diterima |
|---|---|---|---|---|---|---|
| Kamera A | sub | | | | | |
| Kamera B | sub | | | | | |
| DVR ch1–ch4 | sub | | | | | |

## 5. Pengujian Sistem

### 5.1 Black-box (fungsional)

| ID | Fitur | Skenario | Hasil yang diharapkan |
|---|---|---|---|
| BB-01 | Tambah kamera | Merek + IP + akun benar → Tes Koneksi | Codec/resolusi + snapshot tampil |
| BB-02 | Tambah kamera | Password salah | Pesan "Username/password salah" |
| BB-03 | Discovery | "Cari di jaringan" | Kamera ONVIF di LAN terdaftar |
| BB-04 | **Tambah DVR** | IP DVR + akun, 4 channel | 4 kamera terbentuk dalam satu grup |
| BB-05 | **Pasang modul** | ZIP modul TA-1 | Modul tampil "valid" + info versi/penulis |
| BB-06 | **Pasang modul** | ZIP dengan manifest rusak | Ditolak dengan pesan jelas |
| BB-07 | **Pilih modul per kamera** | Kamera A = TA-1, B = TA-2 | Keduanya berjalan bersamaan dengan overlay masing-masing |
| BB-08 | Editor zona | Gambar zona + beri peran | Tersimpan & dikirim ke modul |
| BB-09 | Editor zona | Poligon 2 titik | Ditolak |
| BB-10 | Live | Cabut kabel kamera 30 s | reconnecting → streaming tanpa restart |
| BB-11 | **Isolasi** | Modul error di kamera A | Tile A "modul error"; B tetap berjalan |
| BB-12 | Alert | Metrik melewati ambang selama `for_s` | Notifikasi tray sekali per cooldown |
| BB-13 | Alert | Kamera offline 60 s | Alert `camera_offline` |
| BB-14 | Dashboard | Rentang 7 hari | Widget sesuai manifest, tampil < 1 s |
| BB-15 | Laporan | Ekspor PDF harian | PDF benar, angka sama dengan dashboard |
| BB-16 | Wizard preset | Preset modul TA-1 | Profil + aturan + dashboard terbentuk |
| BB-17 | Import/Export | Export lalu import profil | Zona & modul identik |
| BB-18 | Privasi | Aktifkan blur | Semua orang ter-blur di tampilan & snapshot |
| BB-19 | **Overload** | Tambah kamera melebihi `N_max` | FPS diturunkan merata; alert `overload`; aplikasi tidak macet |
| BB-20 | Instalasi | Pasang di PC bersih | Aplikasi berjalan tanpa Python |
| BB-21 | **Dashboard web** | Buka `http://localhost:8080`; lalu dari HP di LAN dengan & tanpa izin akses LAN | Angka sama dengan dashboard desktop; dari HP ditolak bila izin mati, minta login bila aktif |

### 5.2 Usability — SUS

- Responden ≥ 10 (calon pengguna: pemilik/staf usaha, admin kantor, mahasiswa non-IT).
- Tugas: (1) tambah DVR & pilih 2 channel, (2) pilih modul + preset dan gambar zona, (3) baca jam tersibuk / metrik utama di dashboard, (4) atur alert, (5) ekspor laporan.
- Catat tingkat keberhasilan & waktu per tugas, lalu isi 10 butir SUS. Skor = (Σ(ganjil − 1) + Σ(5 − genap)) × 2,5; > 68 = di atas rata-rata.

### 5.3 Uji lapangan terbatas

Pasang aplikasi di **1 lokasi nyata** ≥ 3 hari (dengan izin dan pemberitahuan area terpantau). Sebaiknya lokasi yang sama dengan uji lapangan TA-1 atau TA-2, agar bisa dikerjakan bersama. Fokus TA ini: uptime, reconnect, pemakaian sumber daya, dan tidak ada intervensi manual. Fokus rekan: validitas analitiknya.

## 6. Analisis

Untuk setiap metrik: tabel → grafik → **analisis penyebab**. Contoh: "Pada modul TA-2, `N_max` hanya naik dari 4 ke 6 meskipun inferensi dipercepat 3×, karena 70% waktu per frame ada di ekstraksi fitur Re-ID (`module_ms`). Batas host terlihat dari modul baseline yang mencapai 14 kamera." Hubungkan kembali ke setiap RM.

## 7. Keluaran (Deliverables)

| Keluaran | Lokasi |
|---|---|
| Skrip uji (paritas, isolasi, kapasitas) | `tests/`, `scripts/` |
| Tabel & grafik semua metrik | `docs/hasil-evaluasi/` |
| Data mentah SUS & black-box | `docs/hasil-evaluasi/` |
| Spesifikasi perangkat & topologi uji | `docs/hasil-evaluasi/` |

## 8. Definition of Done

- [ ] Setiap RM punya jawaban berbasis data.
- [ ] Uji paritas lulus untuk baseline + ≥ 1 modul rekan di backend PyTorch; selisih backend lain terdokumentasi.
- [ ] `N_max` sebelum/sesudah optimasi untuk ≥ 2 perangkat × ≥ 2 modul.
- [ ] Black-box 21/21 terisi; bug diperbaiki atau dicatat sebagai batasan.
- [ ] SUS ≥ 10 responden.
- [ ] Tag `v1.0.0` → **Milestone M5**.

## 9. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Modul v1 rekan belum final saat pengujian | Uji kapasitas & keandalan dengan baseline + modul versi terakhir; catat versinya; ulangi cepat bila v1 datang (skrip otomatis) |
| Hasil paritas berbeda karena tracker non-deterministik | Minta rekan menetapkan seed; bandingkan statistik agregat bila per frame tidak mungkin |
| DVR fisik sulit didapat | Ajukan peminjaman lab; minimal uji pada NVR/DVR milik toko/kantor dengan izin |
| Waktu uji kapasitas panjang | Otomatisasi penuh (`benchmark_capacity.py` berjalan semalam) |

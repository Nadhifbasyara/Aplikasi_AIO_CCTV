# FASE 11 — Dokumentasi, Penulisan Laporan TA & Persiapan Sidang

> Sebelumnya: [FASE-10](FASE-10-pengujian-evaluasi.md) · Kembali ke [Roadmap](00-ROADMAP.md)
> Referensi: Roadmap §0–§1 (ruang lingkup & RM pasca sidang pra-progres), Proposal **§3** (latar belakang), **§6–§7** (arsitektur & teknologi), **§11** (etika), **§14** (referensi)
> Estimasi: **berjalan paralel sejak Fase 1** (logbook) + **3 minggu** finalisasi · Milestone: **M6 — Sidang**
> *Revisi 2026-11-09: dahulu FASE-10. Isi buku disesuaikan dengan posisi aplikasi sebagai **host** modul TA-1/TA-2; ditambah panduan pembuat modul & antisipasi pertanyaan soal pembagian kontribusi.*

---

## 1. Tujuan

1. Menyusun **buku Tugas Akhir** yang menjawab RM-1 s.d. RM-3 dengan bukti dari Fase 4–10.
2. Menyusun **dokumentasi produk**: README, manual pengguna, panduan pengembang, dan **panduan pembuat modul** (kontrak Fase 6).
3. Menyiapkan **demo video**, **slide sidang**, dan **antisipasi pertanyaan penguji**.
4. Menyelaraskan narasi dengan TA-1 & TA-2: istilah sama, diagram arsitektur sama, dan saling mengutip.

## 2. Kebiasaan Sejak Fase 1 (jangan ditunda ke akhir)

| Kapan | Apa yang dicatat | Lokasi |
|---|---|---|
| Setiap sesi kerja | Apa yang dikerjakan, masalah, solusi, keputusan & alasannya | `docs/logbook/YYYY-MM-DD.md` |
| Setiap akhir fase | Screenshot, video demo, tabel hasil, diagram | `docs/logbook/img/`, `docs/demo/`, `docs/diagram/` |
| Setiap bimbingan | Masukan pembimbing & tindak lanjut | `docs/logbook/bimbingan.md` |
| Setiap rapat dengan TA-1/TA-2 | Keputusan kontrak, versi modul yang diserahkan, masalah integrasi | `docs/logbook/integrasi-tim.md` |
| Setiap literatur dibaca | Sitasi + ringkasan 3 kalimat + relevansi | Zotero/Mendeley + `docs/literatur.md` |

## 3. Pemetaan Isi Buku TA

> Sesuaikan dengan pedoman penulisan kampus.

| Bab | Isi | Sumber di proyek |
|---|---|---|
| **Abstrak** | Masalah (CCTV pasif, PC edge terbatas, model analitik terpisah dari aplikasi), metode (host modular + RTSP/DVR + optimasi), hasil utama (`N_max` sebelum→sesudah, uji paritas, waktu pulih RTSP, SUS) | Hasil Fase 10 |
| **Bab I Pendahuluan** | Latar belakang; posisi TA ini dalam topik bersama (diagram 3 TA); RM-1..3; tujuan; batasan (tidak melatih/mengevaluasi model, tidak merancang metrik perilaku); manfaat | Roadmap §0–§1, Proposal §3 |
| **Bab II Tinjauan Pustaka** | Penelitian terkait (VMS/NVR komersial, Frigate, DeepStream, Savant, analitik edge); dasar teori: RTSP/RTP, ONVIF, H.264/H.265 & HW decode, YOLO secara ringkas, inferensi teroptimasi (TensorRT, ONNX Runtime, OpenVINO, FP16, batching), konkurensi Python (GIL, thread vs proses, shared memory), arsitektur plugin, Qt threading, SQLite, UU PDP | Fase 4, 6, 9 |
| **Bab III Metodologi & Perancangan** | Alur penelitian; kebutuhan fungsional/non-fungsional; arsitektur host–modul; **kontrak modul**; rancangan InferenceServer & penjadwalan; DVR multi-channel; skema data generik; rancangan UI; rancangan pengujian (definisi `N_max`, uji paritas) | Fase 5–9 |
| **Bab IV Implementasi & Pengujian** | Implementasi per komponen; integrasi modul TA-1/TA-2; hasil paritas, keandalan RTSP/DVR, ablation & `N_max`, soak test, black-box, SUS + analisis | Fase 6–10 |
| **Bab V Penutup** | Kesimpulan per RM; saran (Re-ID lintas kamera di host, inferensi Re-ID bersama, cluster multi-PC, kamera cloud-only, sinkronisasi cloud) | Fase 10 §6 |
| **Lampiran** | Kontrak modul v1 + JSON Schema profil, matriks kompatibilitas kamera/DVR, tabel ablation lengkap, black-box, kuesioner SUS, manual pengguna | Fase 4, 6, 8, 9, 10 |

### Kesimpulan yang harus bisa ditulis (per RM)

| RM | Bentuk kalimat kesimpulan (isi dengan angka hasil) |
|---|---|
| RM-1 | "Aplikasi berhasil memuat dan menjalankan *k* modul analitik (baseline, TA-1, TA-2) melalui kontrak v1 **tanpa perubahan kode host**. Hasil di dalam aplikasi identik dengan hasil mandiri modul pada backend PyTorch (Δ = 0), dan berselisih … pada TensorRT FP16. Skor SUS …" |
| RM-2 | "Integrasi RTSP berhasil pada *n* merek kamera dan *m* DVR (… channel bersamaan) dengan latensi … ± … ms, waktu pulih rata-rata … s, dan uptime 24 jam …%." |
| RM-3 | "Kombinasi InferenceServer bersama, batching, TensorRT FP16, sub-stream, dan FPS adaptif menaikkan kapasitas dari … menjadi … kamera (×…) pada PC RTX 5060, dan dari … menjadi … pada PC tanpa GPU. Teknik dengan kontribusi terbesar adalah …" |

## 4. Diagram yang Dibutuhkan

| Diagram | Isi | Fase sumber |
|---|---|---|
| Posisi 3 TA | Host (TA ini) ↔ modul TA-1 ↔ modul TA-2 | Roadmap §0 |
| Arsitektur sistem | Komponen host + modul + InferenceServer | Roadmap §4 |
| Komponen host–modul | Registry, host, kontrak `api.py`, manifest | Fase 6 |
| Use case | Pemilik/admin lokasi, teknisi pemasang, pembuat modul | Fase 5–8 |
| Activity | Tambah DVR; pasang modul; wizard preset | Fase 6, 8, 9 |
| Sequence | Frame → InferenceServer (batch) → modul → GUI/DB/alert; reconnect RTSP | Fase 4, 6, 9 |
| Class | Source/RTSPSource; Detector/InferenceServer; ModuleManifest/AnalyticsModule; CameraManager/Worker | Fase 4–9 |
| State machine | `RTSPSource`: idle → connecting → streaming → reconnecting → stopped | Fase 4 |
| ERD | Skema SQLite generik | Fase 7 |
| Deployment | PC edge, kamera, DVR, switch, (opsional) PC simulator | Fase 9, 10 |

Dibuat dengan Mermaid/PlantUML (di-versioning di git) lalu diekspor ke PNG/SVG.

## 5. Dokumentasi Produk

| Dokumen | Isi |
|---|---|
| `README.md` | Deskripsi, fitur, screenshot, instalasi, quick start dengan modul baseline |
| `docs/manual-pengguna.md` | Instal; tambah kamera & DVR per merek; pasang modul; wizard preset; dashboard; alert; laporan; privasi; FAQ RTSP/DVR; rekomendasi jumlah kamera per spesifikasi PC |
| `docs/kontrak-modul.md` | **Panduan pembuat modul**: struktur paket, `module.yaml`, `api.py`, preset, aturan (read-only frame, tanpa citra di event), `check_module.py`, uji paritas, contoh `modules/baseline/` |
| `docs/panduan-pengembang.md` | Arsitektur host, menambah merek kamera/DVR, menambah backend inferensi, menjalankan test & benchmark, build installer |
| `CHANGELOG.md` | Riwayat versi `v0.0` → `v1.0.0` |
| `LICENSE` | Lihat catatan lisensi |

> **Catatan lisensi:** Ultralytics YOLO berlisensi **AGPL-3.0**. Distribusi aplikasi yang memuatnya, termasuk bobot YOLO11 hasil fine-tune TA-1/TA-2, mewajibkan kode sumber terbuka dengan lisensi kompatibel, atau lisensi Enterprise. Sepakati lisensi bersama TA-1/TA-2 dan bahas di laporan.

## 6. Demo & Sidang

### 6.1 Video demo (3–5 menit)

1. Masalah: CCTV pasif dan PC edge terbatas (10 s).
2. Tambah **DVR** → 4 channel muncul sekaligus; tambah kamera IP.
3. **Pasang modul** TA-1 dan TA-2 dari ZIP → pilih per kamera → wizard preset → gambar zona.
4. Live view multi-kamera dengan overlay dari dua modul berbeda; cabut kabel → pulih otomatis.
5. Dashboard & alert dari metrik modul → laporan PDF.
6. **Grafik kapasitas**: baseline vs teroptimasi (N kamera, VRAM, FPS).

### 6.2 Slide sidang (± 15–20 slide)

Judul → Latar belakang → **Posisi 3 TA** → RM → Batasan → Arsitektur host–modul → Kontrak modul → RTSP/DVR → Optimasi (InferenceServer, backend, decode) → Demo → Hasil per RM → Kesimpulan → Saran.

Siapkan **demo cadangan** berupa video rekaman demo + MediaMTX lokal + modul baseline, untuk berjaga-jaga jika kamera/jaringan atau modul rekan bermasalah di ruang sidang.

### 6.3 Antisipasi pertanyaan penguji

| Pertanyaan | Poin jawaban |
|---|---|
| **Model & analitiknya dari teman. Apa kontribusi Anda?** | (1) Aplikasi host + kontrak modul yang memungkinkan model/analitik pihak lain berjalan tanpa ubah kode, dibuktikan dengan uji paritas; (2) integrasi RTSP kamera IP & DVR yang andal; (3) optimasi sumber daya yang menaikkan kapasitas ×… (ablation per teknik). Tidak ada satu pun yang dikerjakan TA-1/TA-2 |
| Apa beda dengan "Platform Analitik Video" di judul TA-1? | TA-1 merancang **logika & metrik** analitik perilaku konsumen dan mengevaluasi MOT. TA ini adalah **runtime/aplikasi** tempat analitik itu berjalan pada banyak kamera. Batas tanggung jawab tertulis (Fase 6 §3) |
| Mengapa deteksi dijalankan host, bukan modul? | Agar satu model bisa dipakai bersama dan di-batch lintas kamera, yang merupakan sumber peningkatan kapasitas terbesar (angka Fase 9) |
| Bagaimana menjamin hasil di aplikasi sama dengan hasil penelitian teman? | Uji paritas per frame (Fase 10 §4.1), termasuk dampak FP16 |
| Bagaimana jika modul teman lambat? | `module_ms` diukur terpisah; kapasitas dilaporkan per modul; batas host terlihat dari modul baseline |
| Mengapa Python, bukan C++/DeepStream? | Ekosistem modul rekan (Ultralytics/boxmot) berbasis Python; kontrak sederhana bagi pembuat modul; GIL ditangani dengan pengukuran dan opsi multi-proses; DeepStream dibahas di Bab II sebagai pembanding |
| Kenapa tidak pakai pengenalan wajah? | UU PDP No. 27/2022; analitik anonim; Re-ID TA-2 hanya di memori, tidak disimpan |
| Bagaimana keamanan kredensial kamera/DVR? | OS keyring, masking log, LAN-only, tanpa port-forward |
| Mengapa SQLite? | Zero-install, cukup untuk skala edge, skema generik tidak perlu migrasi per modul |

## 7. Jadwal Finalisasi (3 minggu terakhir)

| Minggu | Kegiatan |
|---|---|
| 1 | Finalisasi Bab III & IV dari logbook + hasil Fase 10; semua diagram final; samakan diagram arsitektur dengan TA-1/TA-2 |
| 2 | Bab I, II, V, abstrak; revisi pembimbing; manual pengguna & panduan pembuat modul |
| 3 | Video demo, slide, latihan presentasi (≥ 3×, sebaiknya juga gladi bersama TA-1/TA-2), cek plagiasi, pengumpulan |

## 8. Keluaran (Deliverables)

- Buku TA (PDF) + source (LaTeX/Word)
- README, manual pengguna, panduan pembuat modul, panduan pengembang, CHANGELOG
- Video demo, slide sidang
- Rilis `v1.0.0` (installer + source code) dan arsip data hasil evaluasi

## 9. Definition of Done

- [ ] Buku TA disetujui pembimbing.
- [ ] Setiap RM dijawab dengan angka di Bab IV dan kesimpulan di Bab V.
- [ ] Pembagian kontribusi dengan TA-1/TA-2 tertulis jelas di Bab I dan disetujui bersama.
- [ ] Demo berjalan dari installer di laptop presentasi (+ demo cadangan offline).
- [ ] Semua dokumen produk lengkap; repo bersih & ter-tag `v1.0.0`.
- [ ] **Milestone M6 — siap sidang.**

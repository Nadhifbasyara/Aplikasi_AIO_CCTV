# FASE 10 — Dokumentasi, Penulisan Laporan TA & Persiapan Sidang

> Sebelumnya: [FASE-09](FASE-09-pengujian-evaluasi.md) · Kembali ke [Roadmap](00-ROADMAP.md)
> Referensi Proposal: **seluruh dokumen**, khususnya **§1** (abstrak), **§3–§5** (Bab I), **§7, §13, §14** (Bab II), **§6, §9** (Bab III), **§10** (Bab IV), **§11** (etika), **§12** (judul)
> Estimasi: **berjalan paralel sejak Fase 1** (logbook) + **3 minggu** finalisasi · Milestone: **M6 — Sidang**

---

## 1. Tujuan

1. Menyusun **buku Tugas Akhir** yang menjawab Rumusan Masalah dengan bukti dari Fase 1–9.
2. Menyusun **dokumentasi produk**: README, manual pengguna, panduan pengembang.
3. Menyiapkan **demo video**, **slide sidang**, dan **antisipasi pertanyaan penguji**.

## 2. Kebiasaan Sejak Fase 1 (jangan ditunda ke akhir)

| Kapan | Apa yang dicatat | Lokasi |
|---|---|---|
| Setiap sesi kerja | Apa yang dikerjakan, masalah, solusi, keputusan & alasannya | `docs/logbook/YYYY-MM-DD.md` |
| Setiap akhir fase | Screenshot, video demo, tabel hasil, diagram | `docs/logbook/img/`, `docs/demo/`, `docs/diagram/` |
| Setiap bimbingan | Masukan pembimbing & tindak lanjut | `docs/logbook/bimbingan.md` |
| Setiap literatur dibaca | Sitasi + ringkasan 3 kalimat + relevansi | Zotero/Mendeley + `docs/literatur.md` |

## 3. Pemetaan Isi Buku TA

> Sesuaikan dengan pedoman penulisan kampus.

| Bab | Isi | Sumber di proyek |
|---|---|---|
| **Abstrak** | Masalah, metode, hasil kuantitatif utama (FPS, MOTA/IDF1, MAPE dwell, kompatibilitas kamera, SUS) | Proposal §1 + hasil Fase 9 |
| **Bab I Pendahuluan** | Latar belakang (CCTV pasif, kamera konsumer mengunci RTSP, kebutuhan UMKM), rumusan masalah, tujuan, batasan, manfaat, sistematika | Proposal §3, §4, §5 — **sesuaikan fokus desktop all-in-one** (Roadmap §1) |
| **Bab II Tinjauan Pustaka** | Penelitian terkait (tabel perbandingan ≥ 10 paper/produk); dasar teori: CNN & YOLO, RT-DETR, MOT (SORT → DeepSORT → ByteTrack → BoT-SORT), metrik MOT, RTSP/RTP/RTCP, ONVIF, codec H.264/H.265, arsitektur edge computing, Qt event loop & threading, SQLite, UU PDP | Proposal §7, §13, §14; Fase 4 §2 |
| **Bab III Metodologi & Perancangan** | Alur penelitian (Proposal §9), kebutuhan fungsional/non-fungsional, arsitektur sistem, diagram (use case, activity, sequence, class, deployment, ERD), rancangan skema profil, rancangan UI, rancangan pengujian | Roadmap §4, Fase 1, 3, 5, 6, 7 |
| **Bab IV Implementasi & Pengujian** | Implementasi per modul (potongan kode penting), perbaikan dari POC (tabel Fase 3 §2), hasil pengujian setiap metrik + analisis | Fase 1–9 |
| **Bab V Penutup** | Kesimpulan per RM, saran (multi-kamera ReID lintas kamera, VLM, SaaS penuh, dukungan kamera cloud-only) | Fase 9 §6, Fase 7 §8, Fase 8 §4.5 |
| **Lampiran** | Matriks kompatibilitas kamera lengkap, tabel black-box, kuesioner SUS, manual pengguna, JSON Schema profil | Fase 4, 7, 9 |

### Kesimpulan yang harus bisa ditulis (per RM)

| RM | Bentuk kalimat kesimpulan (isi dengan angka hasil) |
|---|---|
| RM-1 | “Pipeline RTSP berbasis thread *latest-frame* dengan auto-reconnect berhasil diintegrasikan pada *n* dari *m* merek kamera konsumer dengan latensi rata-rata … ms dan waktu pulih … s …” |
| RM-2 | “ByteTrack mencapai MOTA … dan IDF1 … pada MOT17; *grace period* … s menurunkan MAPE dwell dari …% menjadi …%” |
| RM-3 | “Adaptasi ke *k* jenis usaha dilakukan hanya melalui profil konfigurasi dengan 0 baris kode dalam rata-rata … menit” |
| RM-4 | “Sistem mencapai … FPS pada RTX 3060 (TensorRT FP16) dan … FPS pada CPU (OpenVINO), mendukung hingga … kamera per perangkat” |

## 4. Diagram yang Dibutuhkan

| Diagram | Isi | Fase sumber |
|---|---|---|
| Arsitektur sistem | Komponen aplikasi all-in-one + opsi cloud | Roadmap §4 |
| Use case | Pemilik usaha, Admin/teknisi | Fase 5–7 |
| Activity | Tambah kamera; konfigurasi zona via template | Fase 5, 7 |
| Sequence | RTSP connect/reconnect; frame → pipeline → GUI → DB | Fase 4, 5, 6 |
| Class | Profile/Zone/Line; Pipeline/Detector/Tracker/Engine; CameraManager/Worker | Fase 1, 3, 5 |
| State machine | `RTSPSource`: idle → connecting → streaming → reconnecting → stopped | Fase 4 |
| ERD | Tabel SQLite | Fase 6 |
| Deployment | PC lokasi, kamera, router, (opsional) cloud | Fase 4, 7 |

Buat dengan Mermaid/PlantUML (dapat di-*versioning* di git) lalu ekspor ke PNG/SVG untuk buku.

## 5. Dokumentasi Produk

| Dokumen | Isi |
|---|---|
| `README.md` | Deskripsi, fitur, screenshot, instalasi (dev & installer), quick start dengan video YouTube demo |
| `docs/manual-pengguna.md` | Langkah bergambar: instal, tambah kamera per merek (Tapo, EZVIZ, Imou, V380…), template, dashboard, laporan, alert, privasi, FAQ troubleshooting RTSP |
| `docs/panduan-pengembang.md` | Arsitektur, struktur modul, cara menambah template/merek kamera/backend detektor, menjalankan test & evaluasi, build installer |
| `CHANGELOG.md` | Riwayat versi `v0.0` → `v1.0.0` |
| `LICENSE` | Pilih lisensi kode (mis. AGPL-3.0 sesuai lisensi Ultralytics, atau pertimbangkan implikasinya bila ingin komersial) |

> **Catatan lisensi:** Ultralytics YOLO berlisensi **AGPL-3.0** — distribusi aplikasi yang memakainya mewajibkan kode sumber terbuka dengan lisensi kompatibel, atau lisensi Enterprise. Bahas di laporan (relevan dengan klaim “bernilai jual” di Proposal §12).

## 6. Demo & Sidang

### 6.1 Video demo (3–5 menit)

1. Masalah: CCTV pasif (10 s).
2. **Target awal**: video YouTube → gambar zona → deteksi (M1).
3. Analitik: ID + dwell + heatmap + counting (M2).
4. **Fokus akhir**: tambah kamera Tapo lewat aplikasi → live analitik → cabut kabel → pulih otomatis (M3–M4).
5. Template kafe → dashboard jam sibuk → alert antrean → laporan PDF.
6. Ringkasan angka hasil evaluasi.

### 6.2 Slide sidang (± 15–20 slide)

Judul → Latar belakang → RM → Tujuan & batasan → Tinjauan singkat (YOLO, ByteTrack, RTSP) → Arsitektur → Demo/screenshot → Hasil per RM (tabel & grafik) → Kesimpulan → Saran.

Siapkan **demo cadangan**: video rekaman demo + MediaMTX lokal, jika kamera/jaringan di ruang sidang bermasalah.

### 6.3 Antisipasi pertanyaan penguji

| Pertanyaan | Poin jawaban |
|---|---|
| Mengapa desktop, bukan web/SaaS seperti di proposal? | Edge processing: privasi (video tidak keluar), hemat bandwidth, tanpa biaya server; arsitektur tetap siap disinkronkan ke cloud (Fase 7 §8) — sesuai Proposal §6 |
| Apa kontribusi Anda dibanding sekadar memakai YOLO + Supervision? | Integrasi RTSP kamera konsumer (matriks kompatibilitas, reconnect, dual-stream), engine dwell berbasis timestamp + grace period, profil konfigurabel + template, aplikasi end-to-end terukur |
| Bagaimana jika ID berganti (ID switch)? | Grace period, parameter lost-track, perbandingan BoT-SORT, dampaknya dikuantifikasi di evaluasi dwell |
| Kenapa tidak pakai pengenalan wajah? | UU PDP No. 27/2022 (Proposal §11); analitik anonim sudah cukup untuk insight bisnis |
| Bagaimana dengan kamera yang tidak mendukung RTSP? | Didokumentasikan sebagai batasan; alternatif NVR/relay; saran penelitian lanjut |
| Seberapa valid video YouTube sebagai data uji? | Hanya pendahuluan & demo; klaim akurasi memakai dataset standar (MOT17/20, Mall) dan rekaman sendiri berlabel |
| Mengapa SQLite bukan TimescaleDB? | Zero-install di PC pengguna, cukup untuk skala 1–16 kamera, skema mudah dimigrasi |
| Bagaimana keamanan kredensial kamera? | OS keyring, masking log, LAN-only, tidak ada port-forward |

## 7. Jadwal Finalisasi (3 minggu terakhir)

| Minggu | Kegiatan |
|---|---|
| 1 | Finalisasi Bab III & IV dari logbook + hasil Fase 9; semua diagram final |
| 2 | Bab I, II, V, abstrak; revisi pembimbing; manual pengguna |
| 3 | Video demo, slide, latihan presentasi (≥ 3×), pemeriksaan plagiasi, pengumpulan |

## 8. Keluaran (Deliverables)

- Buku TA (PDF) + source (LaTeX/Word)
- README, manual pengguna, panduan pengembang, CHANGELOG
- Video demo, slide sidang
- Rilis `v1.0.0` (installer + source code) dan arsip data hasil evaluasi

## 9. Definition of Done

- [ ] Buku TA disetujui pembimbing.
- [ ] Setiap RM dijawab dengan angka di Bab IV dan kesimpulan di Bab V.
- [ ] Demo berjalan dari installer di laptop presentasi (+ demo cadangan offline).
- [ ] Semua dokumen produk lengkap; repo bersih & ter-tag `v1.0.0`.
- [ ] **Milestone M6 — siap sidang.**

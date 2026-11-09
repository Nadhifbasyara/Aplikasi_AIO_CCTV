"""Penulis SQLite non-blocking (Fase 7 §3.2): worker kamera tidak pernah menunggu disk."""
import logging
import queue
import threading
from pathlib import Path

from aio_cctv.storage.db import connect, init_db

log = logging.getLogger(__name__)


class StorageWriter(threading.Thread):
    def __init__(self, db_path: Path, flush_interval: float = 1.0, max_queue: int = 100_000):
        super().__init__(name="storage-writer", daemon=True)
        self.db_path, self.flush_interval = db_path, flush_interval
        self.q: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=max_queue)
        self._stop_event = threading.Event()
        self._flushed = threading.Condition()
        self.dropped = 0                            # baris dibuang karena antrean penuh
        self.written = 0
        init_db(db_path)

    def put(self, sql: str, params: tuple) -> None:
        try:
            self.q.put_nowait((sql, params))
        except queue.Full:
            self.dropped += 1                       # lebih baik kehilangan 1 baris daripada macet

    def _drain(self, con) -> None:
        batch: dict[str, list[tuple]] = {}
        while True:
            try:
                sql, params = self.q.get_nowait()
            except queue.Empty:
                break
            batch.setdefault(sql, []).append(params)
        if batch:
            try:
                with con:                           # satu transaksi per flush
                    for sql, rows in batch.items():
                        con.executemany(sql, rows)
                self.written += sum(len(r) for r in batch.values())
            except Exception:                       # jangan matikan writer karena satu batch
                log.exception("gagal menulis %d jenis baris", len(batch))
        with self._flushed:
            self._flushed.notify_all()

    def run(self) -> None:
        con = connect(self.db_path)
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(self.flush_interval)
                self._drain(con)
            self._drain(con)                        # sisa antrean saat berhenti
        finally:
            con.close()

    def flush(self, timeout: float = 5.0) -> None:
        """Tunggu sampai antrean saat ini tertulis (dipakai tes & ekspor laporan)."""
        with self._flushed:
            self._flushed.wait_for(self.q.empty, timeout=timeout)
            self._flushed.wait(timeout=self.flush_interval * 2)

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=5)

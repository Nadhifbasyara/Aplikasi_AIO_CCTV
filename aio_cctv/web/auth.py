"""Login dashboard web (Fase 8 §8.4): hash kata sandi PBKDF2 di OS keyring, sesi cookie."""
from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets
import threading
import time

from aio_cctv.core import secrets

KEYRING_KEY = "__dashboard_web__"           # entri keyring (service yang sama dengan kamera)
ITERATIONS = 200_000
SESSION_TTL_S = 12 * 3600
COOKIE = "aio_session"


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or pysecrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, AttributeError):
        return False


def set_web_password(password: str) -> None:
    secrets.set_password(KEYRING_KEY, hash_password(password))


def web_password_hash() -> str:
    try:
        return secrets.get_password(KEYRING_KEY)
    except Exception:                       # keyring tidak tersedia: login tidak mungkin
        return ""


class Sessions:
    def __init__(self, ttl_s: float = SESSION_TTL_S):
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}

    def new(self) -> str:
        token = pysecrets.token_urlsafe(32)
        with self._lock:
            now = time.monotonic()
            self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
            self._tokens[token] = now + self.ttl_s
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            exp = self._tokens.get(token)
            return exp is not None and exp > time.monotonic()

    def drop(self, token: str | None) -> None:
        with self._lock:
            self._tokens.pop(token or "", None)

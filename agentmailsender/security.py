"""Password hashing, API keys, session tokens and at-rest encryption of secrets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet, InvalidToken

from .config import Config

log = logging.getLogger("agentmailsender")

# --------------------------------------------------------------------------- passwords

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * _SCRYPT_R * 2


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=_SCRYPT_MAXMEM, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n_i, r_i, p_i = int(n), int(r), int(p)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=n_i,
            r=r_i,
            p=p_i,
            maxmem=128 * n_i * r_i * 2,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A valid hash used to keep login timing constant for unknown usernames.
_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password")
    verify_password(password, _DUMMY_HASH)


def generate_password(length: int = 20) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# --------------------------------------------------------------------------- tokens


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


API_KEY_PREFIX = "ams"


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(full_key, lookup_prefix, sha256_hash)``. Only the hash is stored."""
    lookup = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    full = f"{API_KEY_PREFIX}_{lookup}_{secret}"
    return full, lookup, hash_token(full)


def parse_api_key(token: str) -> str | None:
    """Return the lookup prefix of a well-formed key, else ``None``."""
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_PREFIX or len(parts[1]) != 12:
        return None
    return parts[1]


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


# --------------------------------------------------------------------------- secrets at rest


class SecretBox:
    """Symmetric encryption for credentials stored in the database."""

    PREFIX = "enc:v1:"

    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    @classmethod
    def from_config(cls, config: Config) -> "SecretBox":
        if config.secret_key:
            key = base64.urlsafe_b64encode(hashlib.sha256(config.secret_key.encode()).digest())
            return cls(key)
        path = config.secret_key_file
        if path.exists():
            return cls(path.read_bytes().strip())
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - Windows
            pass
        log.warning(
            "Generated a new encryption key in %s. Back it up together with the database: "
            "without it stored SMTP passwords and webhook secrets cannot be decrypted.",
            path,
        )
        return cls(key)

    def encrypt(self, value: str) -> str:
        return self.PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        if not value.startswith(self.PREFIX):
            return value
        try:
            return self._fernet.decrypt(value[len(self.PREFIX) :].encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "Unable to decrypt a stored secret: the encryption key (AMS_SECRET_KEY or data/secret.key) changed."
            ) from exc

    @classmethod
    def is_encrypted(cls, value: object) -> bool:
        return isinstance(value, str) and value.startswith(cls.PREFIX)


# --------------------------------------------------------------------------- login throttling


class LoginRateLimiter:
    """In-memory sliding window limiter for failed login attempts (per IP and per username)."""

    def __init__(self, max_failures: int = 10, window_seconds: int = 900):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._failures[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        return bucket

    def is_blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return any(len(self._prune(k, now)) >= self.max_failures for k in keys)

    def record_failure(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                self._prune(key, now).append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)

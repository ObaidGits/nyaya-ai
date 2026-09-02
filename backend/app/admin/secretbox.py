"""Encrypted-at-rest secret storage for the admin console (DECISIONS.md D-098).

Console-entered API keys are persisted inside ``admin.json`` as Fernet
ciphertext. The master key is STABLE across container recreation by
construction — it is either

1. provided by the operator as ``NYAYA_SECRET_KEY`` (a urlsafe-base64
   32-byte Fernet key), or
2. generated exactly ONCE and written to ``secret.key`` next to
   ``admin.json`` — i.e. on the same persistent Docker volume — using an
   exclusive-create so a concurrent or restarted process can never
   regenerate (and thereby orphan) it.

A dynamically regenerated key would leave undecryptable ciphertext in the
store; this module makes that failure mode impossible unless the operator
explicitly rotates ``NYAYA_SECRET_KEY`` or deletes the key file. When no
usable key exists, secret persistence is DISABLED (never a crash, never
data destruction): keys stay memory-only, matching the pre-D-098 behavior.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

KEY_FILE_NAME = "secret.key"


class SecretBox:
    """Fernet cipher with a stable, volume-persisted master key."""

    def __init__(self, env_key: str | None, key_path: Path | None) -> None:
        self._key_path = key_path
        self._fernet: Fernet | None = None
        self._disabled_reason: str | None = None

        if env_key:
            key = env_key.strip()
            try:
                self._fernet = Fernet(key.encode("ascii"))
                return
            except (ValueError, TypeError):
                # Never crash on bad configuration: disable persistence and
                # say so loudly. Existing on-disk ciphertext is untouched.
                self._disabled_reason = (
                    "NYAYA_SECRET_KEY is set but is not a valid Fernet key "
                    "(expected a urlsafe-base64 32-byte value); secret "
                    "persistence is disabled for this process"
                )
                logger.error(self._disabled_reason)
                return
        if key_path is not None:
            try:
                self._fernet = Fernet(self._load_or_create_key(key_path))
            except OSError as exc:
                self._disabled_reason = (
                    f"the secret master key file {key_path} could not be read or "
                    f"created ({exc}); secret persistence is disabled for this process"
                )
                logger.error(self._disabled_reason)

    @property
    def available(self) -> bool:
        return self._fernet is not None

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def encrypt(self, plaintext: str) -> str:
        """Encrypt to a Fernet token (raises RuntimeError when disabled)."""
        if self._fernet is None:
            raise RuntimeError("secret box unavailable")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Decrypt a Fernet token; raises InvalidToken on wrong/garbled key."""
        if self._fernet is None:
            raise RuntimeError("secret box unavailable")
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")

    @staticmethod
    def _load_or_create_key(path: Path) -> bytes:
        """Existing key wins; otherwise create once, exclusively.

        ``os.open`` with ``O_CREAT | O_EXCL`` is atomic: two processes racing
        to create the key cannot clobber each other, and a restarted process
        can never overwrite (and thus rotate away from) an existing key.
        """
        try:
            data = path.read_bytes().strip()
            if data:
                # Validate the stored key material; an empty/corrupt file is
                # regenerated ONLY when it holds no usable key (nothing to
                # lose — no ciphertext could have been written with it).
                Fernet(data)
                return data
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            raise OSError(f"unreadable existing key file {path}: {exc}") from exc

        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key


__all__ = ["KEY_FILE_NAME", "SecretBox"]

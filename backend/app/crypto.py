"""
API Key encryption/decryption utilities using Fernet symmetric encryption.

Usage flow:
  1. Generate a master key:  python scripts/keytool.py genkey
     → writes backend/.zan.key  (gitignored)
  2. Encrypt your API key:   python scripts/keytool.py encrypt <raw_key>
     → prints enc:<token>
  3. Put the output in .env: ZAN_API_KEY=enc:<token>

At runtime, config.py automatically decrypts any value prefixed with "enc:".
"""
from __future__ import annotations

import os
from pathlib import Path

ENC_PREFIX = "enc:"
_KEY_FILE = Path(__file__).resolve().parent.parent / ".zan.key"


def _load_fernet_key() -> bytes | None:
    """
    Look for the Fernet master key in order:
      1. .zan.key file next to backend/
      2. ZAN_MASTER_KEY environment variable
    """
    if _KEY_FILE.exists():
        raw = _KEY_FILE.read_text().strip()
        if raw:
            return raw.encode()
    env_val = os.environ.get("ZAN_MASTER_KEY", "").strip()
    if env_val:
        return env_val.encode()
    return None


def is_encrypted(value: str) -> bool:
    return value.startswith(ENC_PREFIX)


def decrypt_value(value: str) -> str:
    """Return plaintext. If the value is not encrypted, return as-is."""
    if not is_encrypted(value):
        return value
    key = _load_fernet_key()
    if key is None:
        raise RuntimeError(
            "ZAN_MASTER_KEY not found. "
            "Run: python scripts/keytool.py genkey  then restart."
        )
    from cryptography.fernet import Fernet, InvalidToken  # lazy import

    try:
        return Fernet(key).decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Failed to decrypt ZAN_API_KEY. "
            "Make sure .zan.key matches the key used during encryption."
        ) from exc


def encrypt_value(plain: str) -> str:
    """Return 'enc:<fernet_token>'."""
    key = _load_fernet_key()
    if key is None:
        raise RuntimeError(
            "ZAN_MASTER_KEY not found. Run: python scripts/keytool.py genkey"
        )
    from cryptography.fernet import Fernet  # lazy import

    return ENC_PREFIX + Fernet(key).encrypt(plain.encode()).decode()


def generate_key() -> str:
    """Generate a new Fernet key and save it to .zan.key. Returns the key string."""
    from cryptography.fernet import Fernet  # lazy import

    key = Fernet.generate_key().decode()
    _KEY_FILE.write_text(key + "\n")
    return key

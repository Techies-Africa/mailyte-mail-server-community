"""Envelope encryption for private-key-shaped columns (phase-07 C2).

DKIM (dkim_keys.private_key), PGP (pgp_keys.private_key) and S/MIME
(smime_certs.private_key) all store signing/decryption private keys. A
database dump -- SQL injection, a stolen backup, a compromised replica, an
insider -- must not yield usable key material. It did before this: those
columns were plaintext TEXT.

Scheme: AES-256-GCM, one Key Encryption Key (KEK) held in a mounted file
(never an env var, never a DB row -- H6/C2), read once per process and
cached. GCM's authentication tag is why this catches tampering as well as
disclosure: a ciphertext modified in the database fails to decrypt rather
than decrypting to garbage that gets fed to a signer.

key_version exists so a KEK can be rotated without re-encrypting every row
atomically -- old rows keep decrypting under their recorded version while
new writes use the current one. Only version 1 exists today; adding
version 2 means loading both KEK files and switching on the column's
key_version at decrypt time, not touched here since there is only one.
"""

import base64
import os
import threading
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CURRENT_KEY_VERSION = 1
_NONCE_LENGTH_BYTES = 12  # standard for AES-GCM; the dkim_keys nonce column assumes this

_kek_cache: dict[int, bytes] = {}
_kek_lock = threading.Lock()


class KEKNotConfiguredError(RuntimeError):
    """Raised when the KEK file is missing or unreadable. Fail closed --
    callers must not fall back to storing plaintext."""


@dataclass(frozen=True)
class EncryptedField:
    ciphertext: bytes
    nonce: bytes
    key_version: int = CURRENT_KEY_VERSION


def _kek_path(key_version: int = CURRENT_KEY_VERSION) -> str:
    # KEK_PATH_V2, KEK_PATH_V3, ... once a second version ever exists.
    env_name = "ENCRYPTION_KEK_PATH" if key_version == 1 else f"ENCRYPTION_KEK_PATH_V{key_version}"
    return os.getenv(env_name, "/run/secrets/encryption_kek")


def _load_kek(key_version: int = CURRENT_KEY_VERSION) -> bytes:
    """Read the KEK from its mounted file, once per process per version."""
    with _kek_lock:
        cached = _kek_cache.get(key_version)
        if cached is not None:
            return cached

        path = _kek_path(key_version)
        try:
            with open(path, "rb") as f:
                raw = f.read().strip()
        except FileNotFoundError:
            # `from None`: the FileNotFoundError adds nothing the message
            # below doesn't already name, and chaining it makes the traceback
            # read as an internal error rather than a configuration one.
            raise KEKNotConfiguredError(
                f"Encryption KEK not found at {path!r}. Run scripts/generate_dkim_kek.sh "
                f"and mount the result before this service can encrypt or decrypt private keys."
            ) from None

        # Accept either raw 32 bytes or base64-encoded 32 bytes -- the
        # generation script writes base64 (safer to eyeball / copy), a KMS
        # export might hand back raw bytes.
        if len(raw) == 32:
            key = raw
        else:
            try:
                key = base64.b64decode(raw, validate=True)
            except Exception as exc:
                raise KEKNotConfiguredError(
                    f"KEK at {path!r} is neither 32 raw bytes nor valid base64: {exc}"
                ) from exc
            if len(key) != 32:
                raise KEKNotConfiguredError(
                    f"KEK at {path!r} decodes to {len(key)} bytes, need exactly 32 (AES-256)."
                )

        _kek_cache[key_version] = key
        return key


def encrypt_private_key(plaintext_pem: str) -> EncryptedField:
    """Encrypt a PEM-encoded private key for storage. Never logs the input
    or output -- callers must not either (H2)."""
    if not plaintext_pem:
        raise ValueError("encrypt_private_key requires non-empty plaintext")
    kek = _load_kek(CURRENT_KEY_VERSION)
    nonce = os.urandom(_NONCE_LENGTH_BYTES)
    ciphertext = AESGCM(kek).encrypt(nonce, plaintext_pem.encode("utf-8"), associated_data=None)
    return EncryptedField(ciphertext=ciphertext, nonce=nonce, key_version=CURRENT_KEY_VERSION)


def decrypt_private_key(
    ciphertext: bytes, nonce: bytes, key_version: int = CURRENT_KEY_VERSION
) -> str:
    """Decrypt a stored private key. Only ever call this in a signing or
    rotation/export path, hold the result in memory only, and never log it."""
    kek = _load_kek(key_version)
    plaintext = AESGCM(kek).decrypt(nonce, ciphertext, associated_data=None)
    return plaintext.decode("utf-8")

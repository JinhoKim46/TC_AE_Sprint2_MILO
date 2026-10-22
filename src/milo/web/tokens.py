"""Auth tokens inside the Flask session cookie, encrypted (D136, verify m6 T1).

The Flask cookie is signed, not encrypted: anyone holding it can base64-decode its contents. The Supabase access
and refresh tokens are therefore sealed with Fernet (AES-128-CBC plus HMAC-SHA256, authenticated) under a key
derived with HKDF-SHA256 from `MILO_SECRET_KEY`. A cookie that does not decrypt (tampered, or sealed under an
older secret) yields nothing, and the request is treated as signed out. Tokens are never logged.
Server-side token storage is the stronger follow-up (docs/requests/m6.md).
"""

from __future__ import annotations

import base64
import json

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SALT = b"milo-session-tokens"
INFO = b"milo/web/tokens v1"
MAX_AGE_S = (
    30 * 86_400
)  # a sealed blob older than this is refused; Supabase refresh tokens rotate sooner


def _fernet(secret: str | bytes) -> Fernet:
    raw = secret.encode() if isinstance(secret, str) else secret
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=INFO).derive(raw)
    return Fernet(base64.urlsafe_b64encode(key))


class TokenSeal:
    def __init__(self, secret: str | bytes) -> None:
        self._f = _fernet(secret)

    def seal(self, tokens: dict[str, str]) -> str:
        return self._f.encrypt(json.dumps(tokens).encode()).decode("ascii")

    def open(self, blob: object) -> dict[str, str] | None:
        if not isinstance(blob, str) or not blob:
            return None
        try:
            data = json.loads(self._f.decrypt(blob.encode("ascii"), ttl=MAX_AGE_S))
        except (InvalidToken, ValueError, UnicodeError):
            return None
        if not isinstance(data, dict) or not all(
            isinstance(v, str) for v in data.values()
        ):
            return None
        return {str(k): v for k, v in data.items()}

    def __repr__(self) -> str:
        return "TokenSeal(<key hidden>)"

"""Input validation (SPEC section 8). Shipped in S0.

Strip, then reject empty; length 1 to 4000; reject control characters other than tab, newline and carriage
return. Uploads: MIME, 5 MB, UTF-8.
"""

from __future__ import annotations

import re

from milo.errors import InvalidInput

MAX_QUESTION_CHARS = 4000
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_UPLOAD_MIME = ("application/pdf", "text/plain")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_question(text: object) -> str:
    """Return the stripped text, or raise `InvalidInput` with detail `empty`, `too_long` or `control_characters`."""
    if not isinstance(text, str):
        raise InvalidInput("empty")
    # Control characters are checked on the raw text, so they cannot hide at the edges where strip() would drop them.
    if _CONTROL.search(text):
        raise InvalidInput("control_characters")
    cleaned = text.strip()
    if not cleaned:
        raise InvalidInput("empty")
    if len(cleaned) > MAX_QUESTION_CHARS:
        raise InvalidInput("too_long")
    return cleaned


def validate_upload(data: bytes, mime: str) -> str:
    """Return the decoded text of an upload, or raise `InvalidInput`. PDF parsing belongs to the web milestone."""
    if mime not in ALLOWED_UPLOAD_MIME:
        raise InvalidInput("mime_not_allowed")
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidInput("file_too_large")
    if mime == "application/pdf":
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidInput("not_utf8") from exc

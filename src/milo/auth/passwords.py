"""Password rules (SPEC D92: at least 12 characters). Supabase stores and checks the password; we only gate it."""

from __future__ import annotations

from milo.errors import InvalidInput

MIN_LENGTH = 12
MAX_BYTES = 72  # bcrypt's limit, which Supabase Auth enforces

COMMON = frozenset(
    {
        "password1234",
        "passwordpassword",
        "123456789012",
        "1234567890123",
        "qwertyuiopas",
        "qwertyqwerty",
        "letmeinletmein",
        "iloveyouiloveyou",
        "administrator",
    }
)

MESSAGES = {
    "password_too_short": f"Use at least {MIN_LENGTH} characters.",
    "password_too_long": "That password is too long. Use at most 72 characters.",
    "password_weak": "That password is too easy to guess. Try a longer phrase.",
}


def validate_password(password: str, email: str | None = None) -> str:
    """Return the password unchanged, or raise `InvalidInput` with a code from `MESSAGES`."""
    if len(password) < MIN_LENGTH:
        raise InvalidInput("password_too_short")
    if len(password.encode("utf-8")) > MAX_BYTES:
        raise InvalidInput("password_too_long")
    lowered = password.lower()
    if (
        lowered in COMMON
        or len(set(password)) < 4
        or (email and lowered in (email.lower(), email.lower().split("@")[0]))
    ):
        raise InvalidInput("password_weak")
    return password

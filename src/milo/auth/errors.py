"""Auth errors. Messages never say whether an email is registered (SPEC D92)."""

from __future__ import annotations

from milo.errors import MiloError


class AuthError(MiloError):
    """The caller is not authenticated: a bad token, or a failed sign-in. The web layer answers 401."""

    code = "unauthorized"


class InvalidCredentials(AuthError):
    """Wrong email or password, or an unknown email. One message for both (SPEC D92)."""

    def __init__(self) -> None:
        super().__init__("invalid_credentials")


class EmailNotConfirmed(AuthError):
    def __init__(self) -> None:
        super().__init__("email_not_confirmed")


class RateLimited(MiloError):
    """429 `rate_limited` with the window in seconds (SPEC D131). `retry_after_s` is the time left in it."""

    code = "rate_limited"

    def __init__(self, scope: str, window_s: int, retry_after_s: int) -> None:
        super().__init__(str(window_s))
        self.scope = scope
        self.window_s = window_s
        self.retry_after_s = retry_after_s


MESSAGES = {
    "unauthorized": "Please sign in to continue.",
    "unauthorized:invalid_credentials": "That email and password do not match. Check them and try again.",
    "unauthorized:email_not_confirmed": "Please confirm your email first. We sent you a link.",
    "rate_limited": "Too many attempts. Please wait a little and try again.",
}


def user_message(error: MiloError) -> str:
    return MESSAGES.get(error.error) or MESSAGES.get(
        error.code, "Something went wrong. Please try again."
    )

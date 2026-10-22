"""A thin client over the Supabase Auth API (SPEC D88, D93): sign-up, confirm, login, refresh, logout, reset.

Uses only the project's URL and its publishable key, never the secret or service-role key (D74, D88). Both come
from the caller (`milo.settings` is the only reader of the environment). Sign-up and reset answer the same way
whether or not the email is registered (D92). No request or response body is logged, and tokens stay out of reprs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from milo.auth.errors import (
    AuthError,
    EmailNotConfirmed,
    InvalidCredentials,
    RateLimited,
)
from milo.errors import InvalidInput, ProviderFailure

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
CONSENT_VERSION_RE = re.compile(r"^v\d{1,3}$")
TIMEOUT_S = 10.0
# Supabase's own email limits answer 429 with a window of about an hour on the free tier.
EMAIL_WINDOW_S = 3600


def normalise_email(email: str) -> str:
    value = email.strip().lower()
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise InvalidInput("email")
    return value


@dataclass(frozen=True)
class AuthSession:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    user_id: str  # 32 hex characters
    email: str | None
    consent_version: str | None = (
        None  # from the sign-up metadata, if it is a well-formed version
    )


def _user_hex(raw: Any) -> str:
    import uuid

    try:
        return uuid.UUID(str(raw)).hex
    except ValueError:
        raise AuthError("invalid_response") from None


class SupabaseAuth:
    def __init__(
        self,
        base_url: str,
        publishable_key: str,
        *,
        client: httpx.Client | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._base = base_url.rstrip("/") + "/auth/v1"
        self._key = publishable_key
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=False)

    def __repr__(self) -> str:
        return "SupabaseAuth()"

    # ---- transport ----

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        bearer: str | None = None,
    ) -> httpx.Response:
        headers = {"apikey": self._key}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            return self._client.request(
                method, self._base + path, json=json, params=params, headers=headers
            )
        except httpx.HTTPError:
            raise ProviderFailure("auth_unavailable") from None

    @staticmethod
    def _error_code(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return ""
        if isinstance(body, dict):
            return str(body.get("error_code") or body.get("code") or "")
        return ""

    def _fail(
        self, resp: httpx.Response
    ) -> AuthError | RateLimited | InvalidInput | ProviderFailure:
        code = self._error_code(resp)
        if resp.status_code == 429 or code in (
            "over_email_send_rate_limit",
            "over_request_rate_limit",
        ):
            return RateLimited("supabase", EMAIL_WINDOW_S, EMAIL_WINDOW_S)
        if code == "weak_password":
            return InvalidInput("password_weak")
        if code == "email_not_confirmed":
            return EmailNotConfirmed()
        if code in (
            "invalid_credentials",
            "user_not_found",
            "validation_failed",
            "bad_jwt",
        ):
            return InvalidCredentials() if code != "bad_jwt" else AuthError("bad_token")
        if resp.status_code >= 500:
            return ProviderFailure("auth_unavailable")
        return AuthError(code or "auth_failed")

    def _session(self, resp: httpx.Response) -> AuthSession:
        try:
            body = resp.json()
            user = body["user"]
            meta = user.get("user_metadata") or {}
            consent = meta.get("consent_version")
            return AuthSession(
                access_token=str(body["access_token"]),
                refresh_token=str(body.get("refresh_token", "")),
                expires_in=int(body.get("expires_in", 3600)),
                user_id=_user_hex(user["id"]),
                email=user.get("email") if isinstance(user.get("email"), str) else None,
                consent_version=consent
                if isinstance(consent, str) and CONSENT_VERSION_RE.match(consent)
                else None,
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AuthError("invalid_response") from None

    # ---- operations ----

    def sign_up(
        self,
        email: str,
        password: str,
        *,
        redirect_to: str | None = None,
        consent_version: str | None = None,
    ) -> None:
        """Create the account and send the confirmation email. Returns nothing: the answer is the same whether
        or not the email is already registered (D92). The consent version rides along as user metadata and is
        recorded when the account is confirmed (D96)."""
        body: dict[str, Any] = {"email": normalise_email(email), "password": password}
        if consent_version is not None:
            if not CONSENT_VERSION_RE.match(consent_version):
                raise InvalidInput("consent_version")
            body["data"] = {"consent_version": consent_version}
        params = {"redirect_to": redirect_to} if redirect_to else None
        resp = self._call("POST", "/signup", json=body, params=params)
        if resp.status_code >= 400:
            raise self._fail(resp)

    def verify(self, token_hash: str, kind: str = "signup") -> AuthSession:
        """Exchange the token in a confirmation or recovery link for a session (`kind` is signup or recovery)."""
        if kind not in ("signup", "recovery", "email"):
            raise InvalidInput("token_type")
        resp = self._call(
            "POST", "/verify", json={"type": kind, "token_hash": token_hash}
        )
        if resp.status_code >= 400:
            raise AuthError("invalid_link")
        return self._session(resp)

    def login(self, email: str, password: str) -> AuthSession:
        resp = self._call(
            "POST",
            "/token",
            params={"grant_type": "password"},
            json={"email": normalise_email(email), "password": password},
        )
        if resp.status_code >= 400:
            err = self._fail(resp)
            if isinstance(err, AuthError) and not isinstance(err, EmailNotConfirmed):
                raise InvalidCredentials()
            raise err
        return self._session(resp)

    def refresh(self, refresh_token: str) -> AuthSession:
        resp = self._call(
            "POST",
            "/token",
            params={"grant_type": "refresh_token"},
            json={"refresh_token": refresh_token},
        )
        if resp.status_code >= 400:
            raise AuthError("refresh_failed")
        return self._session(resp)

    def logout(self, access_token: str) -> None:
        """Revoke the session on the server (scope local). The caller also clears the Flask session (D136)."""
        resp = self._call(
            "POST", "/logout", params={"scope": "local"}, bearer=access_token
        )
        if resp.status_code >= 500:
            raise ProviderFailure("auth_unavailable")

    def request_reset(self, email: str, *, redirect_to: str | None = None) -> None:
        """Send the reset email. The answer does not reveal whether the email is registered (D92)."""
        params = {"redirect_to": redirect_to} if redirect_to else None
        resp = self._call(
            "POST", "/recover", json={"email": normalise_email(email)}, params=params
        )
        if resp.status_code == 429:
            raise self._fail(resp)
        if resp.status_code >= 500:
            raise ProviderFailure("auth_unavailable")

    def set_password(self, access_token: str, new_password: str) -> None:
        resp = self._call(
            "PUT", "/user", json={"password": new_password}, bearer=access_token
        )
        if resp.status_code >= 400:
            raise self._fail(resp)

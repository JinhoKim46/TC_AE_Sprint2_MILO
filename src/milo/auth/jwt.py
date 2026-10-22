"""Verify Supabase session tokens locally against the project's JWKS (SPEC D88, D136).

Rules (D136): accept only the algorithm the matching JWK advertises, and only RS256 or ES256; `aud` must be
`authenticated`; `iss` must equal the project's auth URL; 60 seconds of clock skew; `exp`, `sub`, `aud` and `iss`
are required. A token with `alg=none` or an HMAC algorithm is rejected before any key is looked up. The `role`
claim is never trusted for operator rights (D144 re-reads `profiles.role`).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import jwt as pyjwt

from milo.auth.errors import AuthError

ALLOWED_ALGORITHMS = ("RS256", "ES256")
AUDIENCE = "authenticated"
LEEWAY_S = 60
JWKS_TTL_S = 600.0
JWKS_MIN_REFRESH_S = 30.0

Fetch = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class Claims:
    sub: str  # the account id, 32 hex characters
    email: str | None
    exp: int
    session_id: str | None


def fetch_jwks_over_http(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Default JWKS fetch. https only (loopback may use http for a local project); no redirects."""
    import httpx

    parts = urlsplit(url)
    if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise AuthError("jwks_url_not_https")
    resp = httpx.get(url, timeout=timeout, follow_redirects=False)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise AuthError("jwks_invalid")
    return data


class JwksCache:
    """Keys by `kid`. Refetches when the TTL passes, or at most once per `min_refresh_s` for an unknown `kid`."""

    def __init__(
        self,
        fetch: Fetch,
        *,
        ttl_s: float = JWKS_TTL_S,
        min_refresh_s: float = JWKS_MIN_REFRESH_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_s
        self._min_refresh = min_refresh_s
        self._clock = clock
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None

    def _refresh(self) -> None:
        try:
            data = self._fetch()
        except AuthError:
            raise
        except Exception:
            self._fetched_at = self._clock()  # do not hammer a failing endpoint
            raise AuthError("jwks_unavailable") from None
        self._keys = {
            str(k["kid"]): k
            for k in data.get("keys", [])
            if isinstance(k, dict) and k.get("kid")
        }
        self._fetched_at = self._clock()

    def get(self, kid: str) -> dict[str, Any] | None:
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at >= self._ttl:
            self._refresh()
        elif kid not in self._keys and now - self._fetched_at >= self._min_refresh:
            self._refresh()
        return self._keys.get(kid)


def advertised_algorithm(jwk: dict[str, Any]) -> str | None:
    """The one algorithm a JWK allows: its `alg`, or the one its key type implies."""
    alg = jwk.get("alg")
    if isinstance(alg, str):
        return alg
    if jwk.get("kty") == "RSA":
        return "RS256"
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        return "ES256"
    return None


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        jwks: JwksCache | None = None,
        jwks_url: str | None = None,
        fetch: Fetch | None = None,
        audience: str = AUDIENCE,
        leeway_s: int = LEEWAY_S,
    ) -> None:
        if jwks is None:
            if fetch is None:
                if not jwks_url:
                    raise ValueError(
                        "a JWKS cache, a fetch function or a JWKS URL is required"
                    )
                url = jwks_url

                def fetch() -> dict[str, Any]:
                    return fetch_jwks_over_http(url)

            jwks = JwksCache(fetch)
        self._jwks = jwks
        self.issuer = issuer
        self.audience = audience
        self.leeway_s = leeway_s

    def verify(self, token: str) -> Claims:
        """Return the claims of a valid access token, or raise `AuthError` (detail names the reason, not the token)."""
        if not isinstance(token, str) or token.count(".") != 2 or len(token) > 8192:
            raise AuthError("malformed")
        try:
            header = pyjwt.get_unverified_header(token)
        except pyjwt.PyJWTError:
            raise AuthError("malformed") from None
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise AuthError("algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError("kid")
        jwk = self._jwks.get(kid)
        if jwk is None:
            raise AuthError("unknown_key")
        if advertised_algorithm(jwk) != alg or jwk.get("use", "sig") != "sig":
            raise AuthError("algorithm")
        try:
            key = pyjwt.PyJWK.from_dict(jwk, algorithm=alg).key
            payload = pyjwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_s,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except pyjwt.ExpiredSignatureError:
            raise AuthError("expired") from None
        except pyjwt.InvalidAudienceError:
            raise AuthError("audience") from None
        except pyjwt.InvalidIssuerError:
            raise AuthError("issuer") from None
        except pyjwt.InvalidSignatureError:
            raise AuthError("signature") from None
        except pyjwt.PyJWTError:
            raise AuthError("invalid") from None
        if payload.get("is_anonymous") is True:
            raise AuthError("anonymous")
        try:
            sub = uuid.UUID(str(payload["sub"])).hex
        except ValueError:
            raise AuthError("subject") from None
        email = payload.get("email")
        sid = payload.get("session_id")
        return Claims(
            sub=sub,
            email=email if isinstance(email, str) else None,
            exp=int(payload["exp"]),
            session_id=sid if isinstance(sid, str) else None,
        )

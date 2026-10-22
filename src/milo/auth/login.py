"""Sign-up, sign-in, confirmation, reset and token checks, with the limits of SPEC D92 and D131.

`LoginService` ties the Supabase Auth client, the JWT verifier, the rate limiter and the repositories together.
The web layer owns the Flask session (regenerated at sign-in and sign-out, D137) and the CSRF token; this class
returns tokens and claims and never touches cookies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from milo.auth import ratelimit as rl
from milo.auth.accounts import AuthSession, SupabaseAuth, normalise_email
from milo.auth.errors import AuthError, InvalidCredentials
from milo.auth.jwt import Claims, JwtVerifier
from milo.auth.passwords import validate_password
from milo.auth.ratelimit import RateLimiter
from milo.errors import NotFound, ProviderFailure

CONSENT_PURPOSE = "interview"


@dataclass(frozen=True)
class LoginResult:
    session: AuthSession
    claims: Claims


class LoginService:
    def __init__(
        self,
        accounts: SupabaseAuth,
        verifier: JwtVerifier,
        limiter: RateLimiter,
        repos: Any,
    ) -> None:
        self.accounts = accounts
        self.verifier = verifier
        self.limiter = limiter
        self.repos = repos

    # ---- sign-up and confirmation ----

    def sign_up(
        self,
        email: str,
        password: str,
        ip: str,
        *,
        redirect_to: str | None = None,
        consent_version: str | None = None,
    ) -> None:
        """Always returns quietly for a well-formed request: an existing email looks the same (D92)."""
        address = normalise_email(email)
        validate_password(password, address)
        self.limiter.hit(rl.SIGNUP_IP, ip)
        self.limiter.hit(rl.SIGNUP_EMAIL, address)
        self.accounts.sign_up(
            address, password, redirect_to=redirect_to, consent_version=consent_version
        )

    def confirm(self, token_hash: str) -> LoginResult:
        """Handle the email link. The profile row exists by now (the trigger of D89); the customer row and the
        consent chosen on the sign-up form (D96) are written for the owner here."""
        session = self.accounts.verify(token_hash, "signup")
        claims = self.verifier.verify(session.access_token)
        if session.consent_version:
            self.repos.consents.record(
                claims.sub, CONSENT_PURPOSE, session.consent_version
            )
        else:
            self.repos.customers.ensure(claims.sub)
        return LoginResult(session, claims)

    # ---- sign-in and sign-out ----

    def login(self, email: str, password: str, ip: str) -> LoginResult:
        """Five failures per fifteen minutes lock the account, whether or not the email exists (D92).

        A locked account is refused before Supabase is asked, so the right password does not unlock it early."""
        address = normalise_email(email)
        self.limiter.check(rl.LOGIN_FAIL_ACCOUNT, address)
        self.limiter.check(rl.LOGIN_FAIL_IP, ip)
        try:
            session = self.accounts.login(address, password)
        except InvalidCredentials:
            self.limiter.record(rl.LOGIN_FAIL_ACCOUNT, address)
            self.limiter.record(rl.LOGIN_FAIL_IP, ip)
            raise
        claims = self.verifier.verify(session.access_token)
        self.limiter.reset(rl.LOGIN_FAIL_ACCOUNT, address)
        self.limiter.reset(rl.LOGIN_FAIL_IP, ip)
        try:
            self.repos.profiles.touch_last_seen(claims.sub)
        except NotFound:
            pass  # a confirmed account always has a profile; a missing one is the trigger's problem, not a sign-in error
        return LoginResult(session, claims)

    def authenticate(self, access_token: str) -> Claims:
        """Per request: verify the token locally (D88, D136). The web layer then sets `milo.user_id`."""
        return self.verifier.verify(access_token)

    def refresh(self, refresh_token: str) -> LoginResult:
        session = self.accounts.refresh(refresh_token)
        return LoginResult(session, self.verifier.verify(session.access_token))

    def logout(self, access_token: str) -> None:
        try:
            self.accounts.logout(access_token)
        except (AuthError, ProviderFailure):
            pass  # the caller clears its own session either way (D136)

    # ---- password reset ----

    def request_reset(
        self, email: str, ip: str, *, redirect_to: str | None = None
    ) -> None:
        address = normalise_email(email)
        self.limiter.hit(rl.RESET_IP, ip)
        self.limiter.hit(rl.RESET_EMAIL, address)
        self.accounts.request_reset(address, redirect_to=redirect_to)

    def complete_reset(self, token_hash: str, new_password: str) -> None:
        validate_password(new_password)  # before the single-use link is spent
        session = self.accounts.verify(token_hash, "recovery")
        claims = self.verifier.verify(session.access_token)
        validate_password(new_password, claims.email)
        self.accounts.set_password(session.access_token, new_password)
        self.logout(session.access_token)
        self.limiter.reset(rl.LOGIN_FAIL_ACCOUNT, claims.email or "")

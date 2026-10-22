"""Accounts for the web layer (SPEC D88, D92, D93, D136, D137).

Two implementations of one small surface:

- `SupabaseAccounts` wraps `milo.auth.login.LoginService` (Supabase Auth, JWT verified on every request).
  `create_app` uses it.
- `MemoryAccounts` keeps fictional accounts in process for the tests, the dev factory and `evals/ui/record.py`.
  It refuses to exist with `MILO_ENV=prod`. Its tokens are random strings mapped to a user id.

The Flask session holds only the tokens; the user id is derived from them on every request, and the role is
re-read from `profiles` every request (D144).
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any, Protocol

from werkzeug.security import check_password_hash, generate_password_hash

from milo.auth import ratelimit as rl
from milo.auth.accounts import normalise_email
from milo.auth.errors import AuthError, EmailNotConfirmed, InvalidCredentials
from milo.auth.passwords import validate_password
from milo.auth.ratelimit import RateLimiter
from milo.errors import MiloError, NotFound
from milo.settings import is_prod

Tokens = dict[str, str]


class Accounts(Protocol):
    def sign_up(self, email: str, password: str, first_name: str, ip: str) -> None:
        """Quiet for a well-formed request whether or not the email exists (D92). Raises `InvalidInput`."""

    def confirm(self, token: str) -> bool: ...

    def login(self, email: str, password: str, ip: str) -> Tokens:
        """Raises `AuthError` (401) or `RateLimited` (429)."""

    def resolve(self, tokens: Tokens) -> tuple[str, Tokens | None] | None:
        """The user id (32 hex) for the stored tokens, and refreshed tokens when they changed; None if invalid."""

    def logout(self, tokens: Tokens) -> None: ...

    def request_reset(self, email: str, ip: str) -> None: ...

    def complete_reset(self, token: str, password: str) -> bool: ...


class SupabaseAccounts:
    """`Accounts` over `LoginService` (production)."""

    def __init__(self, service: Any, *, redirect_to: str | None = None) -> None:
        self.service = service
        self.redirect_to = redirect_to

    def sign_up(self, email: str, password: str, first_name: str, ip: str) -> None:
        self.service.sign_up(email, password, ip, redirect_to=self.redirect_to)

    def confirm(self, token: str) -> bool:
        try:
            self.service.confirm(token)
        except MiloError:
            return False
        return True

    def login(self, email: str, password: str, ip: str) -> Tokens:
        result = self.service.login(email, password, ip)
        return {"a": result.session.access_token, "r": result.session.refresh_token}

    def resolve(self, tokens: Tokens) -> tuple[str, Tokens | None] | None:
        try:
            return str(self.service.authenticate(tokens.get("a", "")).sub), None
        except AuthError as exc:
            if exc.detail != "expired" or not tokens.get("r"):
                return None
        try:
            result = self.service.refresh(tokens["r"])
        except MiloError:
            return None
        fresh = {"a": result.session.access_token, "r": result.session.refresh_token}
        return str(result.claims.sub), fresh

    def logout(self, tokens: Tokens) -> None:
        if tokens.get("a"):
            self.service.logout(tokens["a"])

    def request_reset(self, email: str, ip: str) -> None:
        self.service.request_reset(email, ip, redirect_to=self.redirect_to)

    def complete_reset(self, token: str, password: str) -> bool:
        try:
            self.service.complete_reset(token, password)
        except MiloError:
            return False
        return True


class MemoryAccounts:
    """In-memory accounts over the repositories (tests, dev factory, `record.py`). Never in production."""

    def __init__(self, repos: Any) -> None:
        if is_prod():
            raise RuntimeError("in-memory accounts refuse to run with MILO_ENV=prod")
        self.repos = repos
        self.limiter = RateLimiter(repos.rate_limits)
        self.users: dict[str, dict[str, Any]] = {}  # email -> account
        self.pending: dict[str, str] = {}  # confirmation token -> email
        self.resets: dict[str, str] = {}  # reset token -> email
        self.sessions: dict[str, str] = {}  # access token -> user id
        self.outbox: list[dict[str, str]] = []  # the emails that would be sent

    # ---- seeding (what an operator does by hand in the database, D94) ----

    def add_confirmed(
        self, email: str, password: str, first_name: str, *, role: str = "owner"
    ) -> str:
        address = normalise_email(email)
        uid = uuid.uuid4().hex
        self.users[address] = {
            "id": uid,
            "hash": generate_password_hash(password),
            "confirmed": False,
            "first_name": first_name,
        }
        self._activate(address)
        if role != "owner":
            self.repos.profiles.set_role_for_test(uid, role)
        return uid

    def _activate(self, address: str) -> None:
        acct = self.users[address]
        acct["confirmed"] = True
        self.repos.profiles.create_confirmed(acct["id"], address)
        self.repos.customers.ensure(acct["id"])

    def first_name(self, user_id: str) -> str | None:
        for acct in self.users.values():
            if acct["id"] == user_id:
                return str(acct["first_name"])
        return None

    # ---- Accounts ----

    def sign_up(self, email: str, password: str, first_name: str, ip: str) -> None:
        address = normalise_email(email)
        validate_password(password, address)
        self.limiter.hit(rl.SIGNUP_IP, ip)
        self.limiter.hit(rl.SIGNUP_EMAIL, address)
        if address in self.users:
            return  # the same reply as a new address (D92)
        self.users[address] = {
            "id": uuid.uuid4().hex,
            "hash": generate_password_hash(password),
            "confirmed": False,
            "first_name": first_name,
        }
        token = secrets.token_urlsafe(24)
        self.pending[token] = address
        self.outbox.append({"to": address, "kind": "confirm", "token": token})

    def confirm(self, token: str) -> bool:
        address = self.pending.pop(token, None)
        if address is None:
            return False
        self._activate(address)
        return True

    def login(self, email: str, password: str, ip: str) -> Tokens:
        try:
            address = normalise_email(email)
        except MiloError:
            raise InvalidCredentials() from None
        self.limiter.check(rl.LOGIN_FAIL_ACCOUNT, address)
        self.limiter.check(rl.LOGIN_FAIL_IP, ip)
        acct = self.users.get(address)
        if acct is None or not check_password_hash(acct["hash"], password):
            self.limiter.record(rl.LOGIN_FAIL_ACCOUNT, address)
            self.limiter.record(rl.LOGIN_FAIL_IP, ip)
            raise InvalidCredentials()
        if not acct["confirmed"]:
            raise EmailNotConfirmed()
        self.limiter.reset(rl.LOGIN_FAIL_ACCOUNT, address)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = acct["id"]
        return {"a": token, "r": ""}

    def resolve(self, tokens: Tokens) -> tuple[str, Tokens | None] | None:
        uid = self.sessions.get(tokens.get("a", ""))
        if uid is None:
            return None
        try:
            self.repos.profiles.get(uid)
        except NotFound:
            return None
        return uid, None

    def logout(self, tokens: Tokens) -> None:
        self.sessions.pop(tokens.get("a", ""), None)

    def request_reset(self, email: str, ip: str) -> None:
        address = normalise_email(email)
        self.limiter.hit(rl.RESET_IP, ip)
        self.limiter.hit(rl.RESET_EMAIL, address)
        if address in self.users:
            token = secrets.token_urlsafe(24)
            self.resets[token] = address
            self.outbox.append({"to": address, "kind": "reset", "token": token})

    def complete_reset(self, token: str, password: str) -> bool:
        address = self.resets.get(token)
        if address is None:
            return False
        validate_password(password, address)
        del self.resets[token]
        acct = self.users[address]
        acct["hash"] = generate_password_hash(password)
        for t in [t for t, u in self.sessions.items() if u == acct["id"]]:
            del self.sessions[t]
        return True

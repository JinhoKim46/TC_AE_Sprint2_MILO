"""Supabase Auth client, JWT verification, passwords, rate limits and lockout (SPEC section 23, D92, D131, D136).

Offline: the Auth API is a fake behind `httpx.MockTransport` (no socket), tokens are signed with keys made in the
test, and the limits run over the in-memory `rate_limits` counters.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from milo import settings
from milo.auth import ratelimit as rl
from milo.auth.accounts import SupabaseAuth, normalise_email
from milo.auth.errors import (
    AuthError,
    EmailNotConfirmed,
    InvalidCredentials,
    RateLimited,
    user_message,
)
from milo.auth.jwt import JwksCache, JwtVerifier, fetch_jwks_over_http
from milo.auth.login import LoginService
from milo.auth.passwords import MESSAGES as PASSWORD_MESSAGES
from milo.auth.passwords import validate_password
from milo.errors import InvalidInput, ProviderFailure
from milo.store import memory_repositories
from milo.store.memory import MemoryStore

BASE = "https://project.example.test"
ISSUER = BASE + "/auth/v1"
PUBLISHABLE = "sb_publishable_test_value"
GOOD_PASSWORD = "correct horse battery"


# ---- keys and tokens ----


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def jwk_of(key: Any, kid: str, alg: str | None, kty_alg: bool = True) -> dict[str, Any]:
    algo = (
        pyjwt.algorithms.RSAAlgorithm
        if isinstance(key, rsa.RSAPrivateKey)
        else pyjwt.algorithms.ECAlgorithm
    )
    jwk = algo.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig"})
    if alg and kty_alg:
        jwk["alg"] = alg
    return jwk


def claims(**over: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {
        "sub": str(uuid.uuid4()),
        "email": "owner@example.test",
        "aud": "authenticated",
        "iss": ISSUER,
        "iat": now,
        "exp": now + 3600,
        "role": "authenticated",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def sign(payload: dict[str, Any], key: Any, alg: str, kid: str | None = "k1") -> str:
    headers = {"kid": kid} if kid else {}
    return pyjwt.encode(payload, key, algorithm=alg, headers=headers)


def verifier(*jwks: dict[str, Any], clock: Any = time.time) -> JwtVerifier:
    return JwtVerifier(
        issuer=ISSUER, jwks=JwksCache(lambda: {"keys": list(jwks)}, clock=clock)
    )


# ---- JWT verification (D136) ----


def test_valid_rs256_and_es256_tokens_are_accepted(rsa_key: Any, ec_key: Any) -> None:
    v = verifier(jwk_of(rsa_key, "k1", "RS256"), jwk_of(ec_key, "k2", "ES256"))
    sub = str(uuid.uuid4())
    got = v.verify(sign(claims(sub=sub), rsa_key, "RS256", "k1"))
    assert got.sub == uuid.UUID(sub).hex and got.email == "owner@example.test"
    assert v.verify(sign(claims(), ec_key, "ES256", "k2")).exp > time.time()
    assert not hasattr(got, "role"), (
        "the token's role claim is never used for rights (D144)"
    )


def test_a_jwk_without_alg_implies_it_from_the_key_type(
    rsa_key: Any, ec_key: Any
) -> None:
    v = verifier(jwk_of(rsa_key, "k1", None), jwk_of(ec_key, "k2", None))
    assert v.verify(sign(claims(), rsa_key, "RS256", "k1"))
    assert v.verify(sign(claims(), ec_key, "ES256", "k2"))


def b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_alg_none_is_rejected(rsa_key: Any) -> None:
    v = verifier(jwk_of(rsa_key, "k1", "RS256"))
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': 'k1'})}.{b64(claims())}."
    with pytest.raises(AuthError) as info:
        v.verify(unsigned)
    assert info.value.error == "unauthorized:algorithm"
    for alg in ("None", "NONE", "nOnE", ""):
        forged = f"{b64({'alg': alg, 'kid': 'k1'})}.{b64(claims())}."
        with pytest.raises(AuthError):
            v.verify(forged)


def test_hmac_key_confusion_is_rejected(rsa_key: Any) -> None:
    """A token signed HS256 with the public key as the secret must not verify."""
    pem = rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    forged = b64({"alg": "HS256", "typ": "JWT", "kid": "k1"}) + "." + b64(claims())
    import hashlib
    import hmac

    sig = (
        base64.urlsafe_b64encode(
            hmac.new(pem, forged.encode(), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(AuthError) as info:
        verifier(jwk_of(rsa_key, "k1", "RS256")).verify(f"{forged}.{sig}")
    assert info.value.error == "unauthorized:algorithm"


def test_the_token_algorithm_must_match_the_one_the_key_advertises(
    rsa_key: Any, ec_key: Any
) -> None:
    # The key under kid k1 is RSA (advertised RS256); a token that claims ES256 under k1 is refused.
    v = verifier(jwk_of(rsa_key, "k1", "RS256"))
    with pytest.raises(AuthError) as info:
        v.verify(sign(claims(), ec_key, "ES256", "k1"))
    assert info.value.error == "unauthorized:algorithm"
    # An RSA key advertised as RS512 is not accepted for RS256 either.
    odd = jwk_of(rsa_key, "k3", "RS512")
    with pytest.raises(AuthError):
        verifier(odd).verify(sign(claims(), rsa_key, "RS256", "k3"))
    # RS512 and PS256 tokens are outside the allowed pair.
    with pytest.raises(AuthError) as info:
        verifier(odd).verify(sign(claims(), rsa_key, "RS512", "k3"))
    assert info.value.error == "unauthorized:algorithm"


@pytest.mark.parametrize(
    "over,reason",
    [
        ({"aud": "anon"}, "audience"),
        ({"aud": None}, "invalid"),
        ({"iss": "https://elsewhere.example.test/auth/v1"}, "issuer"),
        ({"iss": None}, "invalid"),
        ({"exp": int(time.time()) - 90}, "expired"),
        ({"exp": None}, "invalid"),
        ({"sub": None}, "invalid"),
        ({"sub": "not-a-uuid"}, "subject"),
        ({"iat": int(time.time()) + 300}, "invalid"),
        ({"is_anonymous": True}, "anonymous"),
    ],
)
def test_claims_are_checked(rsa_key: Any, over: dict[str, Any], reason: str) -> None:
    v = verifier(jwk_of(rsa_key, "k1", "RS256"))
    with pytest.raises(AuthError) as info:
        v.verify(sign(claims(**over), rsa_key, "RS256"))
    assert info.value.error == f"unauthorized:{reason}"


def test_sixty_seconds_of_clock_skew_are_allowed_and_no_more(rsa_key: Any) -> None:
    v = verifier(jwk_of(rsa_key, "k1", "RS256"))
    now = int(time.time())
    assert v.verify(
        sign(claims(exp=now - 30), rsa_key, "RS256")
    )  # expired 30 s ago, inside the skew
    assert v.verify(
        sign(claims(iat=now + 30), rsa_key, "RS256")
    )  # issued 30 s in the future
    with pytest.raises(AuthError):
        v.verify(sign(claims(exp=now - 120), rsa_key, "RS256"))
    assert v.leeway_s == 60


def test_bad_signature_unknown_key_missing_kid_and_malformed(rsa_key: Any) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = verifier(jwk_of(rsa_key, "k1", "RS256"))
    with pytest.raises(AuthError) as info:
        v.verify(sign(claims(), other, "RS256", "k1"))
    assert info.value.error == "unauthorized:signature"
    with pytest.raises(AuthError) as info:
        v.verify(sign(claims(), rsa_key, "RS256", "nope"))
    assert info.value.error == "unauthorized:unknown_key"
    with pytest.raises(AuthError) as info:
        v.verify(sign(claims(), rsa_key, "RS256", None))
    assert info.value.error == "unauthorized:kid"
    for junk in ("", "abc", "a.b", "a.b.c", "x" * 9000, None, 12):
        with pytest.raises(AuthError):
            v.verify(junk)  # type: ignore[arg-type]
    tampered = sign(claims(), rsa_key, "RS256").split(".")
    tampered[1] = b64(claims(sub=str(uuid.uuid4())))
    with pytest.raises(AuthError):
        v.verify(".".join(tampered))


def test_jwks_cache_ttl_unknown_kid_throttle_and_rotation(
    rsa_key: Any, ec_key: Any
) -> None:
    now = [1000.0]
    calls = []
    keys = [jwk_of(rsa_key, "k1", "RS256")]

    def fetch() -> dict[str, Any]:
        calls.append(now[0])
        return {"keys": list(keys)}

    v = JwtVerifier(
        issuer=ISSUER,
        jwks=JwksCache(fetch, ttl_s=600, min_refresh_s=30, clock=lambda: now[0]),
    )
    token = sign(claims(), rsa_key, "RS256", "k1")
    v.verify(token), v.verify(token)
    assert len(calls) == 1, "keys are cached"
    for _ in range(
        5
    ):  # a flood of random kids refetches at most once per throttle window
        with pytest.raises(AuthError):
            v.verify(sign(claims(), rsa_key, "RS256", uuid.uuid4().hex))
    assert len(calls) == 1
    now[0] += 31
    keys.append(jwk_of(ec_key, "k2", "ES256"))  # rotation: a new kid appears
    assert v.verify(sign(claims(), ec_key, "ES256", "k2"))
    assert len(calls) == 2
    now[0] += 601
    v.verify(token)
    assert len(calls) == 3, "the TTL forces a refresh"


def test_jwks_fetch_failure_is_an_auth_error_without_details(rsa_key: Any) -> None:
    def boom() -> dict[str, Any]:
        raise RuntimeError("connection string leaked here")

    v = JwtVerifier(issuer=ISSUER, jwks=JwksCache(boom))
    with pytest.raises(AuthError) as info:
        v.verify(sign(claims(), rsa_key, "RS256"))
    assert info.value.error == "unauthorized:jwks_unavailable" and "leaked" not in str(
        info.value
    )


def test_default_jwks_fetch_refuses_plain_http() -> None:
    with pytest.raises(AuthError):
        fetch_jwks_over_http(
            "http://project.example.test/auth/v1/.well-known/jwks.json"
        )
    with pytest.raises(ValueError):
        JwtVerifier(issuer=ISSUER)


# ---- a fake Supabase Auth API ----


class FakeSupabase:
    """Just enough of the Auth API: sign-up, verify, token, logout, recover, user."""

    def __init__(self, key: Any, on_confirm: Any = None) -> None:
        self.key = key
        self.users: dict[str, dict[str, Any]] = {}
        self.links: dict[str, tuple[str, str]] = {}  # token_hash -> (email, type)
        self.requests: list[httpx.Request] = []
        self.logged_out: list[str] = []
        self.on_confirm = on_confirm
        self.fail_with: int | None = None

    def mint(self, user: dict[str, Any]) -> dict[str, Any]:
        token = sign(
            claims(sub=user["id"], email=user["email"]), self.key, "RS256", "k1"
        )
        return {
            "access_token": token,
            "refresh_token": "refresh-" + uuid.uuid4().hex,
            "expires_in": 3600,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "user_metadata": user["meta"],
            },
        }

    def link(self, email: str, kind: str) -> str:
        token_hash = uuid.uuid4().hex
        self.links[token_hash] = (email, kind)
        return token_hash

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"msg": "boom"})
        assert request.headers["apikey"] == PUBLISHABLE
        path = request.url.path.removeprefix("/auth/v1")
        body = json.loads(request.content) if request.content else {}
        if path == "/signup":
            email = body["email"]
            if len(body["password"]) < 8:
                return httpx.Response(
                    422, json={"error_code": "weak_password", "msg": "weak"}
                )
            if email not in self.users:
                self.users[email] = {
                    "id": str(uuid.uuid4()),
                    "email": email,
                    "password": body["password"],
                    "confirmed": False,
                    "meta": body.get("data", {}),
                }
                self.last_link = self.link(email, "signup")
            return httpx.Response(
                200, json={"id": "x", "email": email}
            )  # the same answer either way
        if path == "/verify":
            email, kind = self.links.get(body["token_hash"], (None, None))
            if email is None or kind != body["type"]:
                return httpx.Response(403, json={"error_code": "otp_expired"})
            del self.links[body["token_hash"]]
            user = self.users[email]
            if kind == "signup":
                user["confirmed"] = True
                if self.on_confirm:
                    self.on_confirm(user)
            return httpx.Response(200, json=self.mint(user))
        if path == "/token" and request.url.params["grant_type"] == "password":
            user = self.users.get(body["email"])
            if user is None or user["password"] != body["password"]:
                return httpx.Response(400, json={"error_code": "invalid_credentials"})
            if not user["confirmed"]:
                return httpx.Response(400, json={"error_code": "email_not_confirmed"})
            return httpx.Response(200, json=self.mint(user))
        if path == "/token" and request.url.params["grant_type"] == "refresh_token":
            if not body["refresh_token"].startswith("refresh-"):
                return httpx.Response(
                    400, json={"error_code": "refresh_token_not_found"}
                )
            return httpx.Response(200, json=self.mint(next(iter(self.users.values()))))
        if path == "/logout":
            self.logged_out.append(request.headers["authorization"])
            return httpx.Response(204)
        if path == "/recover":
            if body["email"] in self.users:
                self.last_link = self.link(body["email"], "recovery")
            return httpx.Response(200, json={})
        if path == "/user" and request.method == "PUT":
            who = pyjwt.decode(
                request.headers["authorization"].split()[1],
                options={"verify_signature": False},
            )
            for user in self.users.values():
                if user["id"] == who["sub"]:
                    user["password"] = body["password"]
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class World:
    def __init__(self, rsa_key: Any) -> None:
        self.now = [1_700_000_000.0]
        self.repos = memory_repositories(MemoryStore(), lambda: self.now[0])
        self.api = FakeSupabase(
            rsa_key,
            on_confirm=lambda u: self.repos.profiles.create_confirmed(
                u["id"], u["email"]
            ),
        )
        self.accounts = SupabaseAuth(BASE, PUBLISHABLE, client=self.api.client())
        self.service = LoginService(
            self.accounts,
            verifier(jwk_of(rsa_key, "k1", "RS256")),
            rl.RateLimiter(self.repos.rate_limits, lambda: self.now[0]),
            self.repos,
        )

    def register(
        self,
        email: str = "owner@example.test",
        ip: str = "198.51.100.7",
        confirm: bool = True,
    ) -> Any:
        self.service.sign_up(email, GOOD_PASSWORD, ip, consent_version="v0")
        if not confirm:
            return None
        return self.service.confirm(self.api.last_link)


@pytest.fixture
def world(rsa_key: Any) -> World:
    return World(rsa_key)


# ---- sign-up and confirmation ----


def test_sign_up_confirm_creates_profile_customer_and_consent(world: World) -> None:
    world.service.sign_up(
        "  Owner@Example.test ",
        GOOD_PASSWORD,
        "198.51.100.7",
        redirect_to="http://localhost:5000/confirm",
        consent_version="v0",
    )
    req = world.api.requests[0]
    sent = json.loads(req.content)
    assert sent == {
        "email": "owner@example.test",
        "password": GOOD_PASSWORD,
        "data": {"consent_version": "v0"},
    }
    assert req.url.params["redirect_to"] == "http://localhost:5000/confirm"
    assert req.headers["apikey"] == PUBLISHABLE and "authorization" not in req.headers
    assert world.repos.sessions.store.select("profiles", "x") == [], (
        "no profile before the email is confirmed"
    )
    result = world.service.confirm(world.api.last_link)
    who = result.claims.sub
    assert world.repos.profiles.get(who)["email"] == "owner@example.test"
    assert world.repos.profiles.role(who) == "owner", (
        "sign-up never yields an operator (D94)"
    )
    assert world.repos.customers.get(who) is not None
    assert world.repos.consents.has(who, "interview")
    assert world.repos.consents.history(who)[0]["version"] == "v0"


def test_confirmation_links_work_once_and_only_for_their_kind(world: World) -> None:
    world.register(confirm=False)
    link = world.api.last_link
    with pytest.raises(AuthError):
        world.accounts.verify(link, "recovery")  # wrong kind
    world.service.confirm(link)
    with pytest.raises(AuthError):
        world.service.confirm(link)  # single use
    with pytest.raises(AuthError):
        world.service.confirm("0" * 32)
    with pytest.raises(InvalidInput):
        world.accounts.verify(link, "magiclink")


def test_login_needs_a_confirmed_email_and_never_reveals_registration(
    world: World,
) -> None:
    world.register(confirm=False)
    with pytest.raises(EmailNotConfirmed):
        world.service.login("owner@example.test", GOOD_PASSWORD, "198.51.100.7")
    world.service.confirm(world.api.last_link)
    ok = world.service.login("owner@example.test", GOOD_PASSWORD, "198.51.100.7")
    assert (
        ok.claims.sub
        and world.repos.profiles.get(ok.claims.sub)["last_seen_at"] is not None
    )
    with pytest.raises(InvalidCredentials) as wrong:
        world.service.login("owner@example.test", "wrong password!!", "198.51.100.7")
    with pytest.raises(InvalidCredentials) as unknown:
        world.service.login("nobody@example.test", "wrong password!!", "198.51.100.7")
    assert (
        wrong.value.error == unknown.value.error == "unauthorized:invalid_credentials"
    )
    assert user_message(wrong.value) == user_message(unknown.value)


def test_sign_up_answers_the_same_for_a_registered_email(world: World) -> None:
    world.register()
    assert (
        world.service.sign_up("owner@example.test", GOOD_PASSWORD, "198.51.100.8")
        is None
    )
    assert (
        world.service.sign_up("fresh@example.test", GOOD_PASSWORD, "198.51.100.8")
        is None
    )


def test_sign_up_rejects_bad_input_before_calling_supabase(world: World) -> None:
    for email in (
        "",
        "no-at-sign",
        "a@b",
        "x" * 300 + "@example.test",
        "two@@example.test",
    ):
        with pytest.raises(InvalidInput):
            world.service.sign_up(email, GOOD_PASSWORD, "198.51.100.7")
    with pytest.raises(InvalidInput) as short:
        world.service.sign_up("a@example.test", "short", "198.51.100.7")
    assert (
        short.value.detail == "password_too_short"
        and short.value.detail in PASSWORD_MESSAGES
    )
    with pytest.raises(InvalidInput):
        world.accounts.sign_up(
            "a@example.test", GOOD_PASSWORD, consent_version="latest"
        )
    assert world.api.requests == []


def test_supabase_errors_map_to_typed_errors(world: World) -> None:
    world.api.fail_with = 500
    with pytest.raises(ProviderFailure):
        world.accounts.login("a@example.test", GOOD_PASSWORD)
    world.api.fail_with = 429
    with pytest.raises(RateLimited) as limited:
        world.accounts.sign_up("a@example.test", GOOD_PASSWORD)
    assert limited.value.error == "rate_limited:3600"

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    down = SupabaseAuth(
        BASE, PUBLISHABLE, client=httpx.Client(transport=httpx.MockTransport(refuse))
    )
    with pytest.raises(ProviderFailure) as info:
        down.login("a@example.test", GOOD_PASSWORD)
    assert info.value.error == "provider_error:auth_unavailable"

    world.api.fail_with = None
    weak = SupabaseAuth(BASE, PUBLISHABLE, client=world.api.client())
    with pytest.raises(InvalidInput):
        weak.sign_up("a@example.test", "short")  # the server's own weak_password answer


def test_the_client_holds_only_the_publishable_key_and_hides_tokens(
    world: World,
) -> None:
    result = world.register()
    assert PUBLISHABLE not in repr(world.accounts)
    assert result.session.access_token not in repr(result.session)
    assert result.session.refresh_token not in repr(result.session)
    world.service.login("owner@example.test", GOOD_PASSWORD, "198.51.100.7")
    assert {r.headers["apikey"] for r in world.api.requests} == {PUBLISHABLE}


# ---- sessions: authenticate, refresh, logout, reset ----


def test_authenticate_refresh_and_logout(world: World) -> None:
    result = world.register()
    assert (
        world.service.authenticate(result.session.access_token).sub == result.claims.sub
    )
    with pytest.raises(AuthError):
        world.service.authenticate(result.session.access_token + "x")
    again = world.service.refresh(result.session.refresh_token)
    assert again.claims.sub == result.claims.sub
    with pytest.raises(AuthError):
        world.service.refresh("not-a-refresh-token")
    world.service.logout(result.session.access_token)
    assert world.api.logged_out == ["Bearer " + result.session.access_token]
    world.api.fail_with = 401
    world.service.logout(
        result.session.access_token
    )  # sign-out never fails the caller (D136)


def test_password_reset_flow(world: World) -> None:
    world.register()
    world.service.request_reset(
        "nobody@example.test", "198.51.100.9", redirect_to="http://localhost:5000/reset"
    )
    assert not world.api.links, "an unknown email gets the same answer and no link"
    world.service.request_reset("owner@example.test", "198.51.100.9")
    link = world.api.last_link
    with pytest.raises(InvalidInput):
        world.service.complete_reset(link, "short")
    assert link in world.api.links, "a weak password does not spend the single-use link"
    world.service.complete_reset(link, "a brand new long passphrase")
    assert link not in world.api.links
    with pytest.raises(InvalidCredentials):
        world.service.login("owner@example.test", GOOD_PASSWORD, "198.51.100.9")
    assert world.service.login(
        "owner@example.test", "a brand new long passphrase", "198.51.100.9"
    )
    assert world.api.logged_out, "the recovery session is closed"


# ---- lockout and rate limiting (D92, D131, D142) ----


def test_five_failures_lock_the_account_for_the_window(world: World) -> None:
    world.register()
    n_before = len(world.api.requests)
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            world.service.login(
                "owner@example.test", "wrong password!!", "198.51.100.7"
            )
    sent = len(world.api.requests)
    assert sent == n_before + 5
    with pytest.raises(RateLimited) as locked:
        world.service.login(
            "owner@example.test", GOOD_PASSWORD, "198.51.100.7"
        )  # even the right password
    assert len(world.api.requests) == sent, (
        "a locked account is refused before Supabase is asked"
    )
    assert locked.value.window_s == 900 and 1 <= locked.value.retry_after_s <= 900
    assert locked.value.error == "rate_limited:900"
    world.now[0] += 901
    assert world.service.login("owner@example.test", GOOD_PASSWORD, "198.51.100.7")


def test_lockout_is_the_same_for_an_unknown_email(world: World) -> None:
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            world.service.login(
                "ghost@example.test", "wrong password!!", "198.51.100.7"
            )
    with pytest.raises(RateLimited):
        world.service.login("ghost@example.test", "wrong password!!", "198.51.100.7")


def test_a_success_clears_the_failure_count(world: World) -> None:
    world.register()
    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            world.service.login(
                "owner@example.test", "wrong password!!", "198.51.100.7"
            )
    world.service.login("owner@example.test", GOOD_PASSWORD, "198.51.100.7")
    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            world.service.login(
                "owner@example.test", "wrong password!!", "198.51.100.8"
            )


def test_failures_from_one_address_lock_that_address(world: World) -> None:
    for i in range(
        5
    ):  # a different email each time: only the address counter can stop this
        with pytest.raises(InvalidCredentials):
            world.service.login(
                f"user{i}@example.test", "wrong password!!", "203.0.113.5"
            )
    with pytest.raises(RateLimited) as limited:
        world.service.login("user99@example.test", "wrong password!!", "203.0.113.5")
    assert limited.value.scope == "login_fail_ip"
    with pytest.raises(InvalidCredentials):
        world.service.login("user99@example.test", "wrong password!!", "203.0.113.6")


def test_sign_up_and_reset_are_limited_per_address_and_per_email(world: World) -> None:
    for _ in range(3):
        world.service.sign_up("same@example.test", GOOD_PASSWORD, "198.51.100.1")
    with pytest.raises(RateLimited) as by_email:
        world.service.sign_up("same@example.test", GOOD_PASSWORD, "198.51.100.1")
    assert by_email.value.scope == "signup_email"
    for i in range(9):
        world.service.sign_up(f"n{i}@example.test", GOOD_PASSWORD, "198.51.100.2")
    world.service.sign_up("n9@example.test", GOOD_PASSWORD, "198.51.100.2")
    with pytest.raises(RateLimited) as by_ip:
        world.service.sign_up("n10@example.test", GOOD_PASSWORD, "198.51.100.2")
    assert by_ip.value.scope == "signup_ip"
    for _ in range(3):
        world.service.request_reset("same@example.test", "198.51.100.3")
    with pytest.raises(RateLimited):
        world.service.request_reset("same@example.test", "198.51.100.3")


def test_account_limits_use_the_named_constants(world: World) -> None:
    limiter = rl.RateLimiter(world.repos.rate_limits, lambda: world.now[0])
    assert rl.MESSAGES.limit == settings.ACCOUNT_MESSAGES_PER_MINUTE == 10
    assert rl.UPLOADS.limit == settings.ACCOUNT_UPLOADS_PER_HOUR == 5
    who = uuid.uuid4().hex
    for _ in range(10):
        limiter.hit(rl.MESSAGES, who)
    with pytest.raises(RateLimited) as limited:
        limiter.hit(rl.MESSAGES, who)
    assert limited.value.window_s == 60
    world.now[0] += 60
    assert limiter.hit(rl.MESSAGES, who) == 1
    for _ in range(5):
        limiter.hit(rl.UPLOADS, who)
    with pytest.raises(RateLimited):
        limiter.hit(rl.UPLOADS, who)
    limiter.hit(rl.UPLOADS, uuid.uuid4().hex)  # another account is unaffected


def test_limit_keys_are_hashed_and_case_insensitive(world: World) -> None:
    world.service.sign_up("Hidden@Example.test", GOOD_PASSWORD, "198.51.100.77")
    keys = " ".join(f"{s[0]} {s[1]}" for s in world.repos.rate_limits.store._limits)
    assert "hidden" not in keys.lower() and "198.51" not in keys
    assert rl.make_key("A@B.test") == rl.make_key(" a@b.test ")


def test_limits_hold_across_service_instances(world: World, rsa_key: Any) -> None:
    """The counters live in the repository, not in the service, so a second worker sees the same count (D142)."""
    other = LoginService(
        world.accounts,
        world.service.verifier,
        rl.RateLimiter(world.repos.rate_limits, lambda: world.now[0]),
        world.repos,
    )
    for _ in range(3):
        world.service.sign_up("shared@example.test", GOOD_PASSWORD, "198.51.100.40")
    with pytest.raises(RateLimited):
        other.sign_up("shared@example.test", GOOD_PASSWORD, "198.51.100.41")


# ---- passwords and emails ----


def test_password_policy() -> None:
    assert validate_password("twelve chars!") == "twelve chars!"
    for bad, code in [
        ("short", "password_too_short"),
        ("a" * 11, "password_too_short"),
        ("é" * 40, "password_too_long"),
        ("a" * 12, "password_weak"),
        ("password1234", "password_weak"),
        ("owner@example.test", "password_weak"),
    ]:
        with pytest.raises(InvalidInput) as info:
            validate_password(bad, "owner@example.test")
        assert info.value.detail == code, bad
    assert set(PASSWORD_MESSAGES) == {
        "password_too_short",
        "password_too_long",
        "password_weak",
    }
    assert normalise_email(" A@B.EXAMPLE ") == "a@b.example"


def test_auth_modules_hold_no_secret_names() -> None:
    from pathlib import Path

    for path in Path("src/milo/auth").glob("*.py"):
        text = path.read_text()
        for needle in ("service_role", "SUPABASE" + "_", "secret_key", "os.environ"):
            assert needle not in text, (path.name, needle)

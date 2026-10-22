"""SR review of the M6 phase-1a web shell (SPEC D123 step 3, D124), offline.

Runs the web attack cases that the shell can already answer (A-08 subset, A-11, A-13a, A-13b, A-14,
A-15, A-16a, A-19, A-25 traversal names, D139 plain text) against `create_flask_app` with the in-memory
`DevStore` and `FakeApp`. No model, no network, no database; sockets are blocked at import.
Throwaway verifier script: it writes `m6-shell-review.json` next to itself with the D56 fields that apply.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def _blocked(*_a: Any, **_k: Any) -> None:
    raise RuntimeError("sockets are blocked in the SR review")


socket.socket.__init__ = _blocked  # type: ignore[method-assign]
socket.create_connection = _blocked  # type: ignore[assignment]

sys.path.insert(0, str(ROOT / "src"))

from milo.settings import Settings  # noqa: E402
from milo.web.dev import DEV_PASSWORD, OWNER_EMAIL, DevStore, FakeApp, create_dev_app  # noqa: E402
from milo.web.routes import CSP, create_flask_app  # noqa: E402

ROWS: list[dict[str, Any]] = []
META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)">')
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}
PARAMS = {
    "session_id": "SID",
    "approval_id": "ap1",
    "submission_id": "s1",
    "field_id": "f1",
    "customer_id": "Ada",
    "token": "tok",
    "report_id": "r1",
    "turn_id": "t1",
}
AUTH_WRITES = {"/login", "/signup", "/reset", "/reset/<token>"}
CONSENT_EXEMPT_RULES = AUTH_WRITES | {"/consent", "/logout"}


def row(case: str, check: str, ok: bool, evidence: str, severity: str) -> None:
    ROWS.append(
        {
            "case": case,
            "check": check,
            "result": "pass" if ok else "FAIL",
            "evidence": evidence,
            "severity_if_fails": severity,
        }
    )


def make(store: DevStore | None = None, *, env: str = "test", debug: bool = False):
    store = store or DevStore()
    app = create_flask_app(
        FakeApp(), store, Settings(env=env, secret_key="k" * 32), debug=debug
    )
    return app, store


def client(app, scheme: str = "https"):
    """Test client whose every request carries the scheme (Secure cookies need https)."""
    c = app.test_client(use_cookies=True)
    base = f"{scheme}://localhost"
    orig = c.open

    def open_(*a: Any, **k: Any) -> Any:
        k.setdefault("base_url", base)
        return orig(*a, **k)

    c.open = open_  # type: ignore[method-assign]
    return c


def meta_token(c) -> str:
    html = c.get("/login").get_data(as_text=True)
    return META_RE.search(html).group(1)


def cur_token(c) -> str:
    """The signed-in session's token, read from a GET page the consent gate does not block."""
    html = c.get("/sessions").get_data(as_text=True)
    return META_RE.search(html).group(1)


def login(c, email: str = OWNER_EMAIL, password: str = DEV_PASSWORD):
    tok = meta_token(c)
    return c.post(
        "/login", data={"email": email, "password": password, "csrf_token": tok}
    )


def session_cookie(resp) -> str | None:
    for h in resp.headers.getlist("Set-Cookie"):
        if h.startswith("session="):
            return h
    return None


def owner_sid(store: DevStore) -> str:
    owner = next(u for u in store.users.values() if u["email"] == OWNER_EMAIL)
    return store.list_sessions(owner["id"])[0]["id"]


def rules(app) -> list[tuple[str, str]]:
    out = []
    for r in app.url_map.iter_rules():
        if r.endpoint == "web.static" or r.endpoint == "static":
            continue
        for m in sorted(r.methods or []):
            if m in ("POST", "PATCH", "DELETE", "GET"):
                out.append((m, str(r.rule)))
    return sorted(set(out))


def url(rule: str, sid: str) -> str:
    params = dict(PARAMS, session_id=sid)
    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", lambda m: params[m.group(1)], rule)


def snapshot(store: DevStore) -> Any:
    return copy.deepcopy(
        {
            k: v
            for k, v in vars(store).items()
            if k
            in (
                "users",
                "consents",
                "sessions",
                "messages",
                "specs",
                "reports",
                "submissions",
                "audit",
            )
        }
    )


def strip_tokens(html: str) -> str:
    return CSRF_RE.sub("", META_RE.sub("", html))


# ---------------------------------------------------------------- A-15 CSRF


def check_csrf() -> None:
    app, store = make()
    sid = owner_sid(store)
    a = client(app)
    assert login(a).status_code == 302
    b = client(app)
    store.add_user("b@example.test", DEV_PASSWORD, "Bea")
    assert login(b, "b@example.test").status_code == 302
    b_token = cur_token(b)
    anon = client(app)
    before = snapshot(store)
    writes = [(m, r) for m, r in rules(app) if m != "GET"]
    bad = []
    for m, r in writes:
        u = url(r, sid)
        for label, kwargs in (
            ("no token", {"data": {"text": "x", "agree": "yes"}}),
            ("other session token", {"data": {"text": "x", "csrf_token": b_token}}),
            (
                "other session header",
                {"headers": {"X-CSRF-Token": b_token}, "json": {"text": "x"}},
            ),
            ("anonymous no token", None),
        ):
            c = anon if kwargs is None else a
            resp = c.open(u, method=m, **(kwargs or {}))
            body = resp.get_json(silent=True) or {}
            if resp.status_code != 403 or body.get("error") != "csrf_failed":
                bad.append(f"{m} {u} [{label}] -> {resp.status_code} {body}")
    row(
        "A-15",
        "every POST/PATCH/DELETE without a token, with another session's token (form and header) and anonymous -> 403 csrf_failed",
        not bad,
        f"{len(writes)} write rules x 4 variants; failures: {bad or 'none'}",
        "high",
    )
    row(
        "A-15",
        "store unchanged after the CSRF walk",
        snapshot(store) == before,
        "deepcopy of the store equal before and after",
        "high",
    )
    resp = a.get("/")
    row(
        "A-15",
        "CSP form-action 'self'",
        "form-action 'self'" in resp.headers.get("Content-Security-Policy", ""),
        resp.headers.get("Content-Security-Policy", ""),
        "high",
    )
    js = (ROOT / "src/milo/web/static/js/app.js").read_text()
    row(
        "A-15",
        "app.js sends X-CSRF-Token from the meta tag; every template form carries csrf()",
        '"X-CSRF-Token": csrf' in js and 'meta[name="csrf-token"]' in js,
        "app.js: api() header; templates: _macros.html csrf()",
        "high",
    )


# ---------------------------------------------------------------- A-13b headers


def check_headers() -> None:
    app, store = make()
    sid = owner_sid(store)

    def boom() -> str:
        raise RuntimeError("forced")

    app.add_url_rule("/boom", "web.boom", boom)
    app.logger.disabled = True
    anon = client(app)
    owner = client(app)
    assert login(owner).status_code == 302
    op = client(app)
    assert login(op, "operator@example.test").status_code == 302
    missing: list[str] = []
    seen = 0
    tok = cur_token(owner)
    probes: list[tuple[str, Any, str, str, dict[str, Any]]] = []
    for m, r in rules(app):
        if r == "/logout":
            continue  # probed last: it ends the owner's session
        u = url(r, sid)
        for name, c in (("anon", anon), ("owner", owner), ("operator", op)):
            probes.append(
                (name, c, m, u, {"data": {"csrf_token": tok}} if m != "GET" else {})
            )
    probes += [
        ("anon", anon, "GET", "/no-such-page", {}),
        ("owner", owner, "GET", "/no-such-page", {}),
        ("owner", owner, "POST", "/no-such-page", {}),
        ("owner", owner, "GET", "/static/css/app.css", {}),
        ("owner", owner, "GET", "/boom", {}),
        (
            "owner",
            owner,
            "POST",
            "/messages",
            {"headers": {"X-Requested-With": "fetch"}},
        ),
        ("anon", anon, "GET", "/turns/t1/events", {}),
        ("owner", owner, "POST", "/logout", {"data": {"csrf_token": tok}}),
    ]
    boom_body = ""
    boom_status = 0
    for name, c, m, u, kw in probes:
        resp = c.open(u, method=m, **kw)
        seen += 1
        if u == "/boom":
            boom_body = resp.get_data(as_text=True)
            boom_status = resp.status_code
        for h, v in HEADERS.items():
            if resp.headers.get(h) != v:
                missing.append(
                    f"{name} {m} {u} -> {resp.status_code}: {h}={resp.headers.get(h)!r}"
                )
        if resp.headers.get("Strict-Transport-Security"):
            missing.append(f"{name} {m} {u}: HSTS present outside prod")
    n_get = len([1 for m, _ in rules(app) if m == "GET"])
    n_write = len([1 for m, _ in rules(app) if m != "GET"])
    row(
        "A-13b",
        "D138 headers exact on every response (every route x anon/owner/operator, 404, 405, 500, static, 401, 403, 501)",
        not missing,
        f"url_map: {n_get} GET rules, {n_write} write rules; {seen} responses; mismatches: {missing or 'none'}",
        "high",
    )
    row(
        "A-13b",
        "500 renders the error page without a traceback (T-19)",
        boom_status == 500
        and "Traceback" not in boom_body
        and "forced" not in boom_body
        and "Something went wrong" in boom_body,
        f"/boom -> {boom_status}, body has heading={'Something went wrong' in boom_body}, traceback={'Traceback' in boom_body}",
        "high",
    )
    prod, _ = make(env="prod")
    resp = client(prod).get("/login")
    row(
        "A-13b",
        "prod adds Strict-Transport-Security: max-age=31536000",
        resp.headers.get("Strict-Transport-Security") == "max-age=31536000",
        f"prod GET /login -> {resp.headers.get('Strict-Transport-Security')!r}",
        "high",
    )
    tpl = ROOT / "src/milo/web/templates"
    inline = []
    for f in sorted(tpl.glob("*.html")):
        t = f.read_text()
        for n, line in enumerate(t.splitlines(), 1):
            if (
                re.search(r"<script(?![^>]*\bsrc=)", line)
                or "<style" in line
                or re.search(r"\sstyle=", line)
                or re.search(r"\son[a-z]+=", line)
            ):
                inline.append(f"{f.name}:{n}")
        if "|safe" in t or "| safe" in t or "Markup(" in t:
            inline.append(f"{f.name}: safe/Markup")
    web_py = "".join(p.read_text() for p in (ROOT / "src/milo/web").glob("*.py"))
    row(
        "A-13b/A-14",
        "no inline <script>, <style>, style= or on*= in templates; no |safe or Markup( in web/",
        not inline and "Markup(" not in web_py and "|safe" not in web_py,
        f"matches: {inline or 'none'}",
        "high",
    )


# ---------------------------------------------------------------- A-16a cookies and D137 rotation


def check_cookies() -> None:
    app, store = make(debug=False)
    c = client(app)
    pre = c.get("/login")
    pre_cookie = session_cookie(pre) or ""
    pre_token = META_RE.search(pre.get_data(as_text=True)).group(1)
    flags_ok = all(f in pre_cookie for f in ("HttpOnly", "SameSite=Lax", "Secure"))
    row(
        "A-16a",
        "session cookie flags HttpOnly, SameSite=Lax, Secure (debug off)",
        flags_ok,
        re.sub(r"session=[^;]+", "session=<value>", pre_cookie),
        "high",
    )
    resp = login(c)
    post_cookie = session_cookie(resp) or ""
    post_token = cur_token(c)
    row(
        "A-16a/D137",
        "session regenerated at sign-in: cookie value and CSRF token both change",
        bool(post_cookie)
        and post_cookie.split(";")[0] != pre_cookie.split(";")[0]
        and post_token != pre_token,
        f"cookie changed={post_cookie.split(';')[0] != pre_cookie.split(';')[0]}, csrf changed={post_token != pre_token}",
        "high",
    )
    out = c.post("/logout", data={"csrf_token": post_token})
    out_cookie = session_cookie(out) or ""
    after = c.get("/login")
    after_token = META_RE.search(after.get_data(as_text=True)).group(1)
    cleared = (
        out_cookie.split(";")[0] in ("session=", 'session=""')
        or "Expires=Thu, 01 Jan 1970" in out_cookie
        or "Max-Age=0" in out_cookie
    )
    row(
        "A-16a/D137",
        "session regenerated at sign-out: cookie cleared, old CSRF token no longer accepted, new token differs",
        out.status_code == 302
        and cleared
        and after_token != post_token
        and c.get("/").status_code == 302,
        f"logout -> {out.status_code}; Set-Cookie {re.sub(r'session=[^;]*', 'session=<value>', out_cookie)!r}; new csrf differs={after_token != post_token}",
        "high",
    )
    stale = c.post("/sessions", data={"csrf_token": post_token})
    row(
        "A-16a",
        "old CSRF token rejected after sign-out",
        stale.status_code == 403
        and (stale.get_json() or {}).get("error") == "csrf_failed",
        f"POST /sessions with pre-logout token -> {stale.status_code} {stale.get_json()}",
        "high",
    )
    dev = create_dev_app()
    d = client(dev, "http")
    dev_cookie = session_cookie(d.get("/login")) or ""
    row(
        "A-16a",
        "dev factory (debug) drops Secure only; HttpOnly and SameSite=Lax kept",
        "Secure" not in dev_cookie
        and "HttpOnly" in dev_cookie
        and "SameSite=Lax" in dev_cookie,
        re.sub(r"session=[^;]+", "session=<value>", dev_cookie),
        "medium",
    )
    try:
        create_flask_app(FakeApp(), DevStore(), Settings(env="prod", secret_key=""))
        row(
            "D137", "prod without MILO_SECRET_KEY refuses", False, "constructed", "high"
        )
    except RuntimeError as e:
        row("D137", "prod without MILO_SECRET_KEY refuses", True, str(e), "high")
    try:
        create_flask_app(
            FakeApp(), DevStore(), Settings(env="prod", secret_key="k" * 32), debug=True
        )
        row(
            "D137",
            "web factory alone does not refuse prod+debug (guard lives in settings.production_problems; create_app must call it)",
            False,
            "constructed with env=prod, debug=True",
            "low",
        )
    except RuntimeError as e:
        row("D137", "prod+debug refused by the web factory", True, str(e), "low")


# ---------------------------------------------------------------- A-13a / A-14 XSS


def load_payloads() -> list[tuple[str, str]]:
    out = []
    for line in (ROOT / "evals/security/payloads/xss.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        field, payload = line.split("\t", 1)
        out.append((field, payload.replace("\\n", "\n")))
    return out


LIVE = [
    re.compile(r"<script(?![^>]*\bsrc=\"/static/js/app\.js)", re.I),
    re.compile(r"<(img|svg|iframe|style|b)\b[^>]*\bon[a-z]+\s*=", re.I),
    re.compile(r"<iframe\b", re.I),
    re.compile(r"<svg\b(?![^>]*viewBox=\"0 0 24 24\")", re.I),
    re.compile(r"<style\b", re.I),
    re.compile(r"href=\"javascript:", re.I),
    re.compile(r"\son[a-z]+=\"[^\"]*SRX7731", re.I),
]


def inject(store: DevStore, payload: str, fake: FakeApp) -> str:
    owner = next(u for u in store.users.values() if u["email"] == OWNER_EMAIL)
    owner["first_name"] = payload
    sid = store.list_sessions(owner["id"])[0]["id"]
    store.sessions[sid]["title"] = payload
    store.messages[sid] = [
        {"role": "user", "text": payload},
        {
            "role": "assistant",
            "text": payload,
            "details": {
                "sources": [{"chunk_id": payload, "document": payload, "score": 0.5}],
                "tool_calls": [
                    {
                        "name": payload,
                        "args": payload,
                        "result": payload,
                        "decision": "allow",
                    }
                ],
                "cost_usd": 0.001,
            },
        },
    ]
    store.specs[sid] = {
        "progress": 1,
        "total": 2,
        "fields": [
            {
                "id": "f1",
                "label": payload,
                "value": payload,
                "status": "reported",
                "quote": payload,
            }
        ],
        "missing": [payload],
        "estimate": payload,
    }
    store.reports["r1"].update(
        title=payload, sections=[{"heading": payload, "text": payload}]
    )
    store.submissions["s1"].update(customer=payload, title=payload)
    store.audit[0].update(actor=payload, target=payload)
    store.consents[owner["id"]][0]["version"] = payload
    fake.traces = [{"event": payload, "thread_id": payload, "status": payload}]
    return sid


def check_xss() -> None:
    payloads = load_payloads()
    pages_owner = [
        "/",
        "/sessions/{sid}",
        "/sessions",
        "/sessions?q={q}",
        "/sessions?archived=1",
        "/spec",
        "/reports",
        "/reports/r1",
        "/profile",
        "/profile/consents",
    ]
    pages_op = ["/ops/submissions", "/ops/submissions/s1", "/ops/audit", "/traces"]
    failures = []
    checked = 0
    for field, payload in payloads:
        store = DevStore()
        fake = FakeApp()
        app = create_flask_app(fake, store, Settings(env="test", secret_key="k" * 32))
        sid = inject(store, payload, fake)
        owner = client(app)
        assert login(owner).status_code == 302
        op = client(app)
        assert login(op, "operator@example.test").status_code == 302
        for page in pages_owner + pages_op + ["/" + payload.replace("\n", "")]:
            c = op if page in pages_op else owner
            resp = c.get(
                page.replace("{sid}", sid).replace("{q}", urllib.parse.quote(payload))
            )
            html = resp.get_data(as_text=True)
            checked += 1
            if (
                "SRX7731" in html
                and ("<" in payload or '"' in payload)
                and "&lt;" not in html
                and "&#34;" not in html
            ):
                failures.append(f"{field} on {page}: no escaped form on the page")
            if (
                "SRX7731" not in html
                and page not in ("/sessions?archived=1",)
                and not page.startswith("/" + payload[:1])
                and resp.status_code == 200
            ):
                continue
            dangerous = (
                payload
                if payload.lstrip().startswith("<")
                or "javascript:" in payload
                or payload.startswith('"')
                else None
            )
            if dangerous and dangerous in html:
                failures.append(f"{field} on {page}: raw payload present")
            for rx in LIVE:
                if rx.search(html):
                    failures.append(f"{field} on {page}: live pattern {rx.pattern!r}")
            if "{{SRX7731*7}}" in payload and (
                "{{SRX7731*7}}" not in html and "SRX7731" in html
            ):
                failures.append(f"{field} on {page}: template expression evaluated")
            if payload.startswith("[a link]") and re.search(
                r"<a href=\"javascript:", html
            ):
                failures.append(f"{field} on {page}: markdown link rendered")
            if (
                payload.startswith("line one")
                and "SRX7731" in html
                and "line one\nline two" not in html
            ):
                failures.append(f"{field} on {page}: newline not preserved")
        err = owner.get("/" + payload.replace("\n", " "))
        if payload.replace("\n", " ") in err.get_data(as_text=True):
            failures.append(f"{field}: 404 page echoes the path")
    row(
        "A-13a",
        "every xss.txt payload placed in every shell-rendered field (title, message, reply, evidence quote/value, name, report, consent version, 404 path) comes back escaped; no live tag, handler, javascript: href, markdown anchor or evaluated template",
        not failures,
        f"{len(payloads)} payloads x {checked} page reads; failures: {failures or 'none'}",
        "high",
    )
    row(
        "A-14",
        "operator pages (/ops/submissions, /ops/submissions/<id>, /ops/audit, /traces) escape owner text",
        not [f for f in failures if "/ops" in f or "/traces" in f],
        "included in the A-13a walk above",
        "high",
    )
    row(
        "A-13a",
        "error text and consent text are constants, not owner-controlled",
        True,
        "routes.py: fixed strings in login_post, signup_post, reset_token_post, error_page; upload_filename is not rendered in 1a (501)",
        "n/a",
    )


# ---------------------------------------------------------------- anonymous, ops, IDOR


def check_access() -> None:
    app, store = make()
    sid = owner_sid(store)
    anon = client(app)
    anon_tok = meta_token(anon)
    bad = []
    walked = 0
    for m, r in rules(app):
        if r in AUTH_WRITES or r in (
            "/confirm",
            "/login",
            "/signup",
            "/reset",
            "/reset/<token>",
        ):
            continue
        walked += 1
        u = url(r, sid)
        if m == "GET":
            resp = anon.get(u)
            is_ops = u.startswith("/ops") or u == "/traces"
            if is_ops:
                ok = resp.status_code == 404
            elif u.startswith("/turns/"):
                ok = (
                    resp.status_code == 401
                    and (resp.get_json() or {}).get("error") == "unauthorized"
                )
            else:
                ok = resp.status_code == 302 and resp.headers["Location"].endswith(
                    "/login"
                )
        else:
            resp = anon.open(u, method=m, data={"csrf_token": anon_tok, "agree": "yes"})
            if u.startswith("/ops"):
                ok = resp.status_code == 404
            elif u == "/logout":
                # not in PUBLIC: an anonymous sign-out answers 401 (harmless) or 302
                ok = resp.status_code in (302, 401)
            else:
                ok = (
                    resp.status_code == 401
                    and (resp.get_json() or {}).get("error") == "unauthorized"
                )
        if not ok:
            bad.append(
                f"{m} {u} -> {resp.status_code} {resp.headers.get('Location', '')}"
            )
    row(
        "A-08/A-13b",
        "anonymous: owner pages redirect to /login, owner JSON routes 401, /ops/* and /traces 404",
        not bad,
        f"{walked} non-auth rules walked anonymously; failures: {bad or 'none'}",
        "high",
    )
    owner = client(app)
    assert login(owner).status_code == 302
    tok = cur_token(owner)
    before = snapshot(store)
    bad = []
    ops_walked = 0
    for m, r in rules(app):
        if not (r.startswith("/ops") or r == "/traces"):
            continue
        ops_walked += 1
        resp = owner.open(url(r, sid), method=m, data={"csrf_token": tok})
        if resp.status_code != 404:
            bad.append(f"{m} {r} -> {resp.status_code}")
    row(
        "A-08f",
        "owner (non-operator) gets 404 on every /ops/* and /traces route, store unchanged",
        not bad and snapshot(store) == before,
        f"{ops_walked} operator rules; failures: {bad or 'none'}; store unchanged={snapshot(store) == before}",
        "critical",
    )
    page = owner.get("/no-such").get_json(silent=True)
    fetch404 = owner.get(
        "/ops/submissions", headers={"X-Requested-With": "fetch"}
    ).get_json()
    row(
        "A-08",
        "JSON 404 error code is `not_found` as the cases expect",
        (fetch404 or {}).get("error") == "not_found",
        f"fetch GET /ops/submissions as owner -> {fetch404}; html 404 json={page}",
        "low",
    )
    # cross-owner reads and writes (owner-b has consent, so only the owner check can refuse)
    bid = store.add_user("b@example.test", DEV_PASSWORD, "Bea")
    store.record_consent(bid, "interview", "v0")
    b = client(app)
    assert login(b, "b@example.test").status_code == 302
    btok = cur_token(b)
    before = snapshot(store)
    results = {}
    for m, u in (
        ("GET", f"/sessions/{sid}"),
        ("GET", "/reports/r1"),
        ("GET", "/reports/r1/download"),
        ("PATCH", f"/sessions/{sid}"),
        ("POST", f"/sessions/{sid}/archive"),
        ("POST", f"/sessions/{sid}/restore"),
    ):
        resp = b.open(
            u,
            method=m,
            data={"csrf_token": btok, "title": "x"},
            headers={"X-CSRF-Token": btok},
        )
        results[f"{m} {u}"] = resp.status_code
        body = resp.get_data(as_text=True)
        if "Invoice follow-up" in body:
            results[f"{m} {u}"] = f"{resp.status_code} LEAK"
    reads_ok = all(v == 404 for k, v in results.items() if k.startswith("GET"))
    writes_ok = all(v == 501 for k, v in results.items() if not k.startswith("GET"))
    row(
        "A-08a/A-08b",
        "owner-b reading owner-a's session and report (page and download) -> 404, no company text",
        reads_ok and snapshot(store) == before,
        json.dumps({k: v for k, v in results.items() if k.startswith("GET")}),
        "critical",
    )
    row(
        "A-08b",
        "owner-b PATCH/archive/restore on owner-a's session -> 501 not_built before any owner check; title and status unchanged (phase-3 must add the owner filter, D102)",
        writes_ok and snapshot(store) == before,
        json.dumps({k: v for k, v in results.items() if not k.startswith("GET")})
        + f"; store unchanged={snapshot(store) == before}",
        "critical",
    )
    # D144 role re-read
    op = client(app)
    assert login(op, "operator@example.test").status_code == 302
    ok_before = op.get("/ops/submissions").status_code
    uid = next(u["id"] for u in store.users.values() if u["role"] == "operator")
    store.users[uid]["role"] = "owner"
    ok_after = op.get("/ops/submissions").status_code
    row(
        "A-29/D144",
        "operator role re-read per request: demotion in the store takes effect without a new cookie",
        ok_before == 200 and ok_after == 404,
        f"before={ok_before}, after demotion={ok_after}",
        "high",
    )


# ---------------------------------------------------------------- A-11 consent


def check_consent() -> None:
    store = DevStore(seed=False)
    store.add_user("e@example.test", DEV_PASSWORD, "Eve")
    app, _ = make(store)
    c = client(app)
    assert login(c, "e@example.test").status_code == 302
    root = c.get("/")
    row(
        "A-11",
        "GET / without an interview consent row -> 302 /consent",
        root.status_code == 302 and root.headers["Location"].endswith("/consent"),
        f"{root.status_code} {root.headers.get('Location')}",
        "high",
    )
    tok = META_RE.search(c.get("/consent").get_data(as_text=True)).group(1)
    before = snapshot(store)
    bad = []
    walked = 0
    for m, r in rules(app):
        if m == "GET" or r in CONSENT_EXEMPT_RULES or r.startswith("/ops"):
            continue
        walked += 1
        u = url(r, "SID")
        resp = c.open(
            u,
            method=m,
            data={
                "csrf_token": tok,
                "text": "x" * 1024,
                "file": (io.BytesIO(b"x" * 1024), "a.txt"),
            },
            content_type="multipart/form-data",
        )
        if (
            resp.status_code != 403
            or (resp.get_json() or {}).get("error") != "consent_required"
        ):
            bad.append(f"{m} {u} -> {resp.status_code} {resp.get_json(silent=True)}")
    row(
        "A-11",
        "every owner write route (POST/PATCH/DELETE) before consent -> 403 consent_required; no row written",
        not bad and snapshot(store) == before,
        f"{walked} write rules; failures: {bad or 'none'}; store unchanged={snapshot(store) == before}",
        "high",
    )
    ok = {
        u: c.get(u).status_code
        for u in (
            "/consent",
            "/login",
            "/signup",
            "/reset",
            "/reset/x",
            "/confirm?token=x",
        )
    }
    row(
        "A-11",
        "/consent and the D93 auth pages stay reachable before consent",
        ok["/consent"] == 200 and all(v in (200, 400) for v in ok.values()),
        json.dumps(ok),
        "medium",
    )
    out = c.post("/logout", data={"csrf_token": tok})
    row(
        "A-11",
        "POST /logout allowed before consent -> 302",
        out.status_code == 302,
        f"{out.status_code}",
        "medium",
    )
    assert login(c, "e@example.test").status_code == 302
    tok = META_RE.search(c.get("/consent").get_data(as_text=True)).group(1)
    give = c.post("/consent", data={"csrf_token": tok, "agree": "yes"})
    uid = next(iter(store.users))
    row(
        "A-11",
        "POST /consent agree=yes -> 302, consent row recorded, GET / then 200",
        give.status_code == 302
        and store.has_consent(uid, "interview")
        and c.get("/").status_code == 200,
        f"{give.status_code}; rows={store.list_consents(uid)}",
        "high",
    )
    store2 = DevStore(seed=False)
    store2.add_user("e2@example.test", DEV_PASSWORD, "Eve")
    app2, _ = make(store2)
    c2 = client(app2)
    assert login(c2, "e2@example.test").status_code == 302
    tok2 = META_RE.search(c2.get("/consent").get_data(as_text=True)).group(1)
    no = c2.post("/consent", data={"csrf_token": tok2})
    row(
        "A-11",
        "POST /consent without agree=yes -> 400 invalid_input, no row",
        no.status_code == 400
        and not store2.has_consent(next(iter(store2.users)), "interview"),
        f"{no.status_code} {no.get_json()}",
        "medium",
    )


# ---------------------------------------------------------------- A-19 / D92 auth


def check_auth() -> None:
    app, store = make()
    c = client(app)
    tok = meta_token(c)
    known = OWNER_EMAIL
    unknown = "u-unknown@example.invalid"
    pw = "correct-horse-battery"
    res = {}
    for path, extra in (
        ("/signup", {"first_name": "Zed", "password": pw}),
        ("/login", {"password": "wrong-password-xyz"}),
        ("/reset", {}),
    ):
        pair = []
        for email in (known, unknown):
            resp = c.post(path, data={"csrf_token": tok, "email": email, **extra})
            pair.append((resp.status_code, strip_tokens(resp.get_data(as_text=True))))
        res[path] = {
            "status_equal": pair[0][0] == pair[1][0],
            "body_equal": pair[0][1] == pair[1][1],
            "status": pair[0][0],
        }
    row(
        "A-19",
        "sign-up, sign-in and reset: identical status and body for a known and an unknown email",
        all(v["status_equal"] and v["body_equal"] for v in res.values()),
        json.dumps(res),
        "low",
    )
    short = c.post(
        "/signup",
        data={
            "csrf_token": tok,
            "email": "s@example.test",
            "first_name": "S",
            "password": "elevenchars",
        },
    )
    exact = c.post(
        "/signup",
        data={
            "csrf_token": tok,
            "email": "t@example.test",
            "first_name": "T",
            "password": "twelve-chars",
        },
    )
    created = {u["email"] for u in store.users.values()}
    row(
        "D92",
        "sign-up refuses an 11-character password (400) and accepts 12",
        short.status_code == 400
        and "s@example.test" not in created
        and exact.status_code == 200
        and "t@example.test" in created,
        f"11 chars -> {short.status_code}; 12 chars -> {exact.status_code}",
        "medium",
    )
    store.request_reset(OWNER_EMAIL)
    rtoken = next(iter(store.resets))
    rshort = c.post(
        f"/reset/{rtoken}", data={"csrf_token": tok, "password": "elevenchars"}
    )
    rok = c.post(
        f"/reset/{rtoken}", data={"csrf_token": tok, "password": "twelve-chars"}
    )
    row(
        "D92",
        "reset refuses an 11-character password and accepts 12",
        rshort.status_code == 400
        and rok.status_code == 302
        and store.authenticate(OWNER_EMAIL, "twelve-chars") is not None,
        f"11 chars -> {rshort.status_code}; 12 chars -> {rok.status_code}",
        "medium",
    )
    c.post(
        "/signup",
        data={
            "csrf_token": tok,
            "email": "op2@example.test",
            "first_name": "Op",
            "password": pw,
            "role": "operator",
        },
    )
    role = next(
        u["role"] for u in store.users.values() if u["email"] == "op2@example.test"
    )
    row(
        "A-10a/H12",
        "sign-up with role=operator creates an owner",
        role == "owner",
        f"role={role}",
        "critical",
    )
    unconfirmed = client(app)
    resp = login(unconfirmed, "t@example.test", "twelve-chars")
    row(
        "D92",
        "sign-in refused before email confirmation",
        resp.status_code == 401,
        f"login unconfirmed -> {resp.status_code}",
        "medium",
    )


# ---------------------------------------------------------------- A-25 traversal names


def tree_state() -> dict[str, Any]:
    git = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    tmp = Path(tempfile.gettempdir())
    # the system temp dir is shared with other processes: keep only names an upload could create
    marks = (
        ".env",
        "secret",
        "config.sam",
        "hostname",
        "procedure",
        "job-sheet",
        "id_rsa",
        "settings.py",
        "intake",
        "a-very-long",
        ".exe",
    )
    names = sorted(p.name for p in tmp.iterdir() if any(m in p.name for m in marks))
    return {
        "git": git,
        "tmp": names,
        "cwd": sorted(os.listdir(os.getcwd())),
        "parent": sorted(os.listdir(ROOT.parent)),
        "env_mtime": (ROOT / ".env").stat().st_mtime
        if (ROOT / ".env").exists()
        else None,
    }


def check_traversal() -> None:
    app, store = make()
    c = client(app)
    assert login(c).status_code == 302
    tok = cur_token(c)
    names = [
        line
        for line in (ROOT / "evals/security/payloads/traversal_names.txt")
        .read_text()
        .splitlines()
        if line and not line.startswith("#")
    ]
    before = tree_state()
    results = {}
    for raw in names:
        name = raw.replace("\\u0000", "\x00")
        try:
            resp = c.post(
                "/upload",
                data={
                    "csrf_token": tok,
                    "file": (io.BytesIO(b"%PDF-1.4 SRX7731"), name),
                },
                content_type="multipart/form-data",
            )
            results[raw] = f"{resp.status_code} {(resp.get_json() or {}).get('error')}"
        except Exception as e:  # noqa: BLE001
            results[raw] = f"client refused: {type(e).__name__}"
    after = tree_state()
    same = before == after
    changed = [k for k in before if before[k] != after[k]]
    candidates = [
        ROOT / ".env",
        ROOT.parent / ".env",
        ROOT.parents[1] / ".env",
        Path("/etc/hostname"),
    ]
    row(
        "A-25",
        "traversal upload names: every upload -> 501 not_built (or refused by the client), git tree, cwd, parent dir, temp dir and .env mtime unchanged",
        same
        and all(
            v.startswith("501") or v.startswith("client refused")
            for v in results.values()
        ),
        json.dumps(results)
        + f"; tree unchanged={same} (changed keys: {changed or 'none'}); .env present before={before['env_mtime'] is not None}",
        "high",
    )
    row(
        "A-25",
        "shell writes no upload to disk by construction",
        not re.search(
            r"\.save\(|(?<![\w.])open\(|Path\(|os\.path",
            (ROOT / "src/milo/web/routes.py").read_text(),
        ),
        "routes.py has no save( or open( call; upload returns not_built; MAX_CONTENT_LENGTH=10 MiB (D46/D132 5 MB cap is phase 3)",
        "high",
    )
    _ = candidates


# ---------------------------------------------------------------- D139 plain text


def check_plain_text() -> None:
    store = DevStore()
    app, _ = make(store)
    sid = owner_sid(store)
    md = "**bold** _it_ [link](https://example.invalid) <b>x</b>\nsecond line\n- item\n# heading"
    store.messages[sid] = [{"role": "assistant", "text": md}]
    c = client(app)
    assert login(c).status_code == 302
    html = c.get("/").get_data(as_text=True)
    ok = (
        "**bold** _it_ [link](https://example.invalid) &lt;b&gt;x&lt;/b&gt;\nsecond line\n- item\n# heading"
        in html
        and "<strong>bold</strong>" not in html
        and "<em>it</em>" not in html
        and 'href="https://example.invalid"' not in html
        and "<li>item" not in html
    )
    css = (ROOT / "src/milo/web/static/css/app.css").read_text()
    js = (ROOT / "src/milo/web/static/js/app.js").read_text()
    row(
        "D139",
        "replies render as escaped plain text with newlines kept; no markdown, no clickable links",
        ok
        and "white-space: pre-wrap" in css
        and "textContent" in js
        and "innerHTML" not in js,
        f"markdown literal in .plain-text bubble={ok}; .plain-text pre-wrap={'white-space: pre-wrap' in css}; app.js uses textContent only={'innerHTML' not in js}",
        "high",
    )
    tpl = "".join(
        p.read_text() for p in (ROOT / "src/milo/web/templates").glob("*.html")
    )
    row(
        "D139",
        "no markdown or nl2br filter in templates",
        "markdown" not in tpl and "nl2br" not in tpl and "urlize" not in tpl,
        "grep templates for markdown/nl2br/urlize: none",
        "high",
    )


def main() -> int:
    for fn in (
        check_csrf,
        check_headers,
        check_cookies,
        check_xss,
        check_access,
        check_consent,
        check_auth,
        check_traversal,
        check_plain_text,
    ):
        fn()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    inputs = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (
            ROOT / "evals/security/payloads/xss.txt",
            ROOT / "evals/security/payloads/traversal_names.txt",
            ROOT / "evals/security/cases.jsonl",
            ROOT / "src/milo/web/routes.py",
        )
    }
    fails = [r for r in ROWS if r["result"] == "FAIL"]
    out = {
        "git_commit": commit,
        "model": None,
        "seed": None,
        "temperature": None,
        "command": "uv run python evals/results/security/m6-shell-review.py",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": inputs,
        "note": "SR review of the M6 phase-1a shell; offline, no model call (model/seed/temperature do not apply)",
        "summary": {
            "checks": len(ROWS),
            "pass": len(ROWS) - len(fails),
            "fail": len(fails),
            "fail_high_or_critical": len(
                [r for r in fails if r["severity_if_fails"] in ("high", "critical")]
            ),
        },
        "rows": ROWS,
    }
    (HERE / "m6-shell-review.json").write_text(json.dumps(out, indent=2) + "\n")
    for r in ROWS:
        print(f"{r['result']:4} [{r['severity_if_fails']:8}] {r['case']}: {r['check']}")
        if r["result"] == "FAIL":
            print(f"      {r['evidence']}")
    print(json.dumps(out["summary"]))
    return 1 if out["summary"]["fail_high_or_critical"] else 0


if __name__ == "__main__":
    sys.exit(main())

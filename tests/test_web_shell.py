"""M6 phase 1a: web shell, security headers, CSRF, consent gate, escaping (SPEC section 16, D81, D135, D138, D139).

Offline: an in-memory store and a fake App, no model, no database.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from milo.settings import Settings
from milo.web.dev import (
    DEV_PASSWORD,
    OPERATOR_EMAIL,
    OWNER_EMAIL,
    DevStore,
    FakeApp,
    create_dev_app,
)
from milo.web.routes import CSP, TAGLINE, create_flask_app

WEB = Path(__file__).resolve().parents[1] / "src" / "milo" / "web"
XSS = "<script>alert(1)</script>"
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def make(store: DevStore | None = None, *, env: str = "test", debug: bool = True):
    store = store or DevStore()
    flask_app = create_flask_app(
        FakeApp(), store, Settings(env=env, secret_key="k" * 32), debug=debug
    )
    return flask_app, store


def token_of(client) -> str:
    html = client.get("/login").get_data(as_text=True)
    return CSRF_RE.search(html).group(1)


def login(client, email=OWNER_EMAIL, password=DEV_PASSWORD):
    tok = token_of(client)
    return client.post(
        "/login", data={"email": email, "password": password, "csrf_token": tok}
    )


@pytest.fixture
def owner():
    flask_app, store = make()
    c = flask_app.test_client()
    assert login(c).status_code == 302
    c.store = store
    return c


@pytest.fixture
def operator():
    flask_app, store = make()
    c = flask_app.test_client()
    assert login(c, OPERATOR_EMAIL).status_code == 302
    return c


def csrf(client) -> dict[str, str]:
    html = client.get("/sessions").get_data(as_text=True)
    return {"X-CSRF-Token": CSRF_RE.search(html).group(1)}


# ---- source greps ----


def templates() -> list[Path]:
    return sorted((WEB / "templates").glob("*.html"))


def test_no_safe_filter_anywhere():
    for p in [*templates(), *WEB.glob("*.py")]:
        text = p.read_text()
        assert "|safe" not in text.replace(" ", ""), p
        assert "Markup(" not in text, p


def test_no_inline_script_or_style_in_templates():
    for p in templates():
        text = p.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), p
        assert not re.search(r"<style", text), p
        assert not re.search(r"\sstyle\s*=", text), p
        assert not re.search(r"\son[a-z]+\s*=", text), p


def test_static_files_exist_and_have_tokens():
    css = (WEB / "static/css/app.css").read_text()
    assert ":root" in css and "prefers-color-scheme: dark" in css
    assert (WEB / "static/js/app.js").read_text().startswith("//")


# ---- headers and cookies ----


def test_security_headers_exact_on_every_response():
    flask_app, _ = make()
    c = flask_app.test_client()
    for path in ("/login", "/signup", "/nope", "/static/css/app.css", "/"):
        r = c.get(path)
        assert r.headers["Content-Security-Policy"] == CSP
        assert CSP == (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "same-origin"
        assert "Strict-Transport-Security" not in r.headers


def test_hsts_only_in_production():
    flask_app, _ = make(env="prod", debug=False)
    r = flask_app.test_client().get("/login")
    assert r.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_cookie_flags():
    flask_app, _ = make(debug=False)
    c = flask_app.test_client()
    cookie = c.get("/login", base_url="https://localhost").headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie
    dev_app, _ = make(debug=True)
    cookie = dev_app.test_client().get("/login").headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie


def test_session_regenerated_at_sign_in_and_out():
    flask_app, _ = make()
    c = flask_app.test_client()
    before = token_of(c)
    login(c)
    with c.session_transaction() as s:
        assert s["csrf"] != before and "uid" in s
        after = s["csrf"]
    tok = CSRF_RE.search(c.get("/sessions").get_data(as_text=True)).group(1)
    c.post("/logout", data={"csrf_token": tok})
    with c.session_transaction() as s:
        assert "uid" not in s and s.get("csrf") != after


# ---- footer, pages ----


def test_footer_on_every_page():
    flask_app, _ = make()
    c = flask_app.test_client()
    for path in ("/login", "/signup", "/reset", "/reset/abc", "/confirm", "/missing"):
        assert TAGLINE in c.get(path).get_data(as_text=True), path


def test_footer_on_signed_in_and_operator_pages(operator):
    for path in (
        "/",
        "/spec",
        "/reports",
        "/profile",
        "/profile/consents",
        "/sessions",
        "/traces",
        "/ops/submissions",
        "/ops/submissions/s1",
        "/ops/audit",
        "/reports/r1x",
    ):
        r = operator.get(path)
        assert TAGLINE in r.get_data(as_text=True), path


def test_every_shell_page_renders_in_dev_app():
    c = create_dev_app().test_client()
    login(c, OPERATOR_EMAIL)
    owner = create_dev_app().test_client()
    login(owner)
    owner_pages = [
        "/",
        "/spec",
        "/reports",
        "/reports/r1",
        "/profile",
        "/profile/consents",
        "/sessions",
        "/sessions?archived=1",
        "/consent",
    ]
    for path in owner_pages:
        assert owner.get(path).status_code in (200,), path
    for path in ("/traces", "/ops/submissions", "/ops/submissions/s1", "/ops/audit"):
        assert c.get(path).status_code == 200, path
    anon = create_dev_app().test_client()
    for path in ("/login", "/signup", "/reset", "/reset/tok", "/confirm"):
        assert anon.get(path).status_code in (200, 400), path
    assert anon.get("/nope").status_code == 404


def test_conversation_is_three_columns_and_has_details_drawer(owner):
    html = owner.get("/").get_data(as_text=True)
    assert (
        'class="panel panel-left"' in html
        and 'id="main"' in html
        and 'id="spec-panel"' in html
    )
    assert (
        "New diagnosis" in html
        and "Today" in html
        and "Last 7 days" in html
        and "Older" in html
    )
    assert "Reply details" in html and "Shelved diagnosis" not in html
    assert "Shelved diagnosis" in owner.get("/sessions?archived=1").get_data(
        as_text=True
    )


# ---- auth gate, consent, operator ----


OWNER_PAGES = [
    "/",
    "/spec",
    "/reports",
    "/reports/r1",
    "/profile",
    "/profile/consents",
    "/sessions",
    "/sessions/x",
    "/consent",
]


def test_anonymous_owner_pages_redirect_to_login():
    flask_app, _ = make()
    c = flask_app.test_client()
    for path in OWNER_PAGES:
        r = c.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/login"), path


def test_ops_is_404_for_non_operator_and_anonymous(owner):
    anon = make()[0].test_client()
    for path in (
        "/traces",
        "/ops/submissions",
        "/ops/submissions/s1",
        "/ops/audit",
        "/ops/export/s1.json",
    ):
        assert owner.get(path).status_code == 404, path
        assert anon.get(path).status_code == 404, path
    r = owner.post("/ops/customers/x/delete", headers=csrf(owner))
    assert r.status_code == 404


def test_operator_role_is_reread_each_request(operator):
    assert operator.get("/ops/audit").status_code == 200
    # demote in the store; the same cookie loses access at once (D144)
    flask_app, store = make()
    c = flask_app.test_client()
    login(c, OPERATOR_EMAIL)
    assert c.get("/ops/audit").status_code == 200
    for u in store.users.values():
        u["role"] = "owner"
    assert c.get("/ops/audit").status_code == 404


def test_consent_redirect_from_root_when_no_consent_row():
    store = DevStore()
    store.add_user("new@example.test", "long-enough-pass", "Nia")
    flask_app, _ = make(store)
    c = flask_app.test_client()
    login(c, "new@example.test", "long-enough-pass")
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/consent")


def test_consent_gate_blocks_every_write_until_consent():
    store = DevStore()
    store.add_user("new@example.test", "long-enough-pass", "Nia")
    flask_app, _ = make(store)
    c = flask_app.test_client()
    login(c, "new@example.test", "long-enough-pass")
    hdr = csrf(c)
    writes = [
        ("post", "/messages"),
        ("post", "/upload"),
        ("post", "/submit"),
        ("post", "/sessions"),
        ("patch", "/sessions/abc"),
        ("post", "/sessions/abc/archive"),
        ("post", "/sessions/abc/restore"),
        ("post", "/approvals/a1"),
        ("post", "/withdraw/s1"),
        ("post", "/spec/f1/request"),
        ("post", "/profile/delete-request"),
    ]
    for method, path in writes:
        r = getattr(c, method)(path, headers=hdr)
        assert r.status_code == 403 and r.get_json() == {"error": "consent_required"}, (
            path
        )
    # consent itself is allowed, then the write reaches the 501 placeholder
    assert c.post("/consent", data={"agree": "yes"}, headers=hdr).status_code == 302
    r = c.post("/messages", headers=hdr)
    assert r.status_code == 501 and r.get_json()["error"] == "not_built"


def test_data_bound_actions_return_501_not_built(owner):
    hdr = csrf(owner)
    for method, path in [
        ("post", "/messages"),
        ("post", "/upload"),
        ("post", "/submit"),
        ("post", "/sessions"),
        ("patch", "/sessions/abc"),
        ("post", "/approvals/a1"),
        ("post", "/withdraw/s1"),
        ("get", "/turns/t1/events?after=0"),
    ]:
        r = getattr(owner, method)(path, headers=hdr)
        assert r.status_code == 501 and r.get_json()["error"] == "not_built", path


# ---- CSRF ----


def test_post_without_token_is_403_csrf_failed(owner):
    for method, path in [
        ("post", "/messages"),
        ("post", "/submit"),
        ("post", "/consent"),
        ("post", "/logout"),
        ("patch", "/sessions/abc"),
        ("post", "/profile/delete-request"),
    ]:
        r = getattr(owner, method)(path)
        assert r.status_code == 403 and r.get_json() == {"error": "csrf_failed"}, path
    r = owner.post("/messages", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403 and r.get_json() == {"error": "csrf_failed"}


def test_login_post_needs_token():
    c = make()[0].test_client()
    r = c.post("/login", data={"email": OWNER_EMAIL, "password": DEV_PASSWORD})
    assert r.status_code == 403 and r.get_json() == {"error": "csrf_failed"}


def test_every_form_has_hidden_csrf_field(owner):
    c = create_dev_app().test_client()
    login(c, OPERATOR_EMAIL)
    anon = create_dev_app().test_client()
    pages = [(owner, p) for p in ("/", "/spec", "/profile", "/sessions", "/consent")]
    pages += [(c, "/ops/submissions/s1")] + [
        (anon, p) for p in ("/login", "/signup", "/reset", "/reset/t")
    ]
    for client, path in pages:
        html = client.get(path).get_data(as_text=True)
        forms = re.findall(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", html, re.S)
        assert forms, path
        for body in forms:
            assert 'name="csrf_token"' in body, path
    assert 'name="csrf-token"' in owner.get("/").get_data(as_text=True)


def test_app_js_sends_csrf_header_and_no_innerhtml():
    js = (WEB / "static/js/app.js").read_text()
    assert "X-CSRF-Token" in js
    assert "innerHTML" not in js and "eval(" not in js


# ---- escaping (D139) ----


def test_script_payload_is_escaped_in_every_rendered_field():
    store = DevStore(seed=False)
    uid = store.add_user(OWNER_EMAIL, DEV_PASSWORD, XSS)
    store.record_consent(uid, "interview", "v0")
    op = store.add_user(OPERATOR_EMAIL, DEV_PASSWORD, "Ops", role="operator")
    store.record_consent(op, "interview", "v0")
    sid = store.add_session(uid, XSS)
    store.messages[sid] = [
        {"role": "user", "text": XSS},
        {
            "role": "assistant",
            "text": XSS,
            "details": {
                "sources": [{"chunk_id": XSS, "document": XSS, "score": XSS}],
                "tool_calls": [
                    {"name": XSS, "args": XSS, "result": XSS, "decision": XSS}
                ],
                "cost_usd": 0.0,
            },
        },
    ]
    store.specs[sid] = {
        "progress": 1,
        "total": 2,
        "missing": [XSS],
        "estimate": XSS,
        "fields": [
            {"id": "f1", "label": XSS, "value": XSS, "status": "reported", "quote": XSS}
        ],
    }
    store.reports["r9"] = {
        "id": "r9",
        "user_id": uid,
        "title": XSS,
        "created_at": datetime.now(),
        "sections": [{"heading": XSS, "text": XSS}],
    }
    store.submissions["s9"] = {
        "id": "s9",
        "customer": XSS,
        "status": XSS,
        "title": XSS,
        "at": None,
    }
    store.audit.append(
        {"at": datetime.now(), "actor": XSS, "action": XSS, "target": XSS}
    )
    fake = FakeApp()
    fake.traces = [{"event": XSS, "thread_id": XSS, "status": XSS}]
    flask_app = create_flask_app(fake, store, Settings(secret_key="k" * 32), debug=True)
    owner_c = flask_app.test_client()
    login(owner_c)
    op_c = flask_app.test_client()
    login(op_c, OPERATOR_EMAIL)
    checks = [
        (owner_c, p)
        for p in (
            f"/sessions/{sid}",
            "/",
            "/spec",
            "/reports",
            "/reports/r9",
            "/sessions",
            "/profile",
        )
    ]
    checks += [
        (op_c, p)
        for p in ("/ops/submissions", "/ops/submissions/s9", "/ops/audit", "/traces")
    ]
    for client, path in checks:
        html = client.get(path).get_data(as_text=True)
        assert XSS not in html, path
        assert "<script>alert" not in html, path
    assert ESCAPED in owner_c.get(f"/sessions/{sid}").get_data(as_text=True)
    assert ESCAPED in owner_c.get("/profile").get_data(as_text=True)


def test_error_pages_do_not_echo_the_path():
    c = make()[0].test_client()
    html = c.get("/%3Cscript%3Ealert(1)%3C/script%3E").get_data(as_text=True)
    assert "<script>alert" not in html


# ---- accounts ----


def test_sign_up_confirm_login_flow():
    flask_app, store = make()
    c = flask_app.test_client()
    tok = CSRF_RE.search(c.get("/signup").get_data(as_text=True)).group(1)
    r = c.post(
        "/signup",
        data={
            "email": "x@example.test",
            "password": "long-enough-pass",
            "first_name": "Xi",
            "csrf_token": tok,
        },
    )
    assert r.status_code == 200 and "Check your email" in r.get_data(as_text=True)
    assert (
        login(c, "x@example.test", "long-enough-pass").status_code == 401
    )  # not confirmed yet
    token = next(iter(store.pending))
    assert c.get(f"/confirm?token={token}").status_code == 200
    assert login(c, "x@example.test", "long-enough-pass").status_code == 302
    assert c.get("/confirm?token=bad").status_code == 400


def test_sign_up_cannot_create_operator():
    flask_app, store = make()
    c = flask_app.test_client()
    tok = CSRF_RE.search(c.get("/signup").get_data(as_text=True)).group(1)
    c.post(
        "/signup",
        data={
            "email": "y@example.test",
            "password": "long-enough-pass",
            "first_name": "Y",
            "role": "operator",
            "csrf_token": tok,
        },
    )
    assert all(
        u["role"] == "owner"
        for u in store.users.values()
        if u["email"] == "y@example.test"
    )


def test_bad_login_is_401_and_generic():
    c = make()[0].test_client()
    r = login(c, OWNER_EMAIL, "wrong-password")
    assert r.status_code == 401 and "Email or password is not right." in r.get_data(
        as_text=True
    )


def test_owner_isolation_on_session_and_report():
    store = DevStore()
    other = store.add_user("o2@example.test", "long-enough-pass", "Oz")
    store.record_consent(other, "interview", "v0")
    sid = store.add_session(other, "Private")
    store.reports["rz"] = {
        "id": "rz",
        "user_id": other,
        "title": "Hidden",
        "created_at": datetime.now(),
        "sections": [],
    }
    flask_app, _ = make(store)
    c = flask_app.test_client()
    login(c)
    assert c.get(f"/sessions/{sid}").status_code == 404
    assert c.get("/reports/rz").status_code == 404
    assert c.get("/reports/rz/download").status_code == 404


def test_dev_factory_refuses_prod(monkeypatch):
    monkeypatch.setenv("MILO_ENV", "prod")
    with pytest.raises(RuntimeError):
        create_dev_app()


def test_prod_requires_secret_key():
    with pytest.raises(RuntimeError):
        create_flask_app(FakeApp(), DevStore(), Settings(env="prod", secret_key=""))

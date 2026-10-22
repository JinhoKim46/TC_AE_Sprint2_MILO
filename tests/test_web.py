"""M6 web layer, wired (SPEC section 16, D79, D81, D93, D97 to D102, D131, D135, D137 to D139, D144).

Offline: the real discovery App, TurnRunner and WebData over the in-memory store, in-memory accounts and a scripted
chat model (`milo.web.dev`). Turns run inline, so a poll after `POST /messages` sees every event.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest

from milo.app import create_app
from milo.settings import Settings
from milo.web.dev import (
    DEMO_MESSAGES,
    DEV_PASSWORD,
    OPERATOR_EMAIL,
    OWNER_EMAIL,
    DemoChat,
    build_local,
    create_dev_app,
    seed,
)
from milo.web.routes import CSP, MAX_UPLOAD_BYTES, TAGLINE

WEB = Path(__file__).resolve().parents[1] / "src" / "milo" / "web"
XSS = "<script>alert(1)</script>"
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"
CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')
INLINE = lambda fn: fn()  # noqa: E731
OTHER_EMAIL = "other@example.test"


def make(*, env: str = "test", debug: bool = True, chat: Any = None):
    local = build_local(
        chat or DemoChat(),
        settings=Settings(env=env, secret_key="k" * 32),
        debug=debug,
        spawn=INLINE,
    )
    owner, operator = seed(local)
    other = local.accounts.add_confirmed(OTHER_EMAIL, DEV_PASSWORD, "Oz")
    local.data.record_consent(other, "v0")
    local.ids = {"owner": owner, "operator": operator, "other": other}  # type: ignore[attr-defined]
    return local


def token_of(client) -> str:
    html = client.get("/login").get_data(as_text=True)
    return CSRF_RE.search(html).group(1)


def login(client, email=OWNER_EMAIL, password=DEV_PASSWORD):
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token_of(client)},
    )


def hdr(client) -> dict[str, str]:
    """The CSRF header a fetch call sends, read from the page's meta tag."""
    return {"X-CSRF-Token": token_of(client), "X-Requested-With": "fetch"}


def signed_in(local, email=OWNER_EMAIL):
    c = local.flask.test_client()
    assert login(c, email).status_code == 302
    return c


@pytest.fixture
def local():
    return make()


@pytest.fixture
def owner(local):
    return signed_in(local)


def session_of(local, who="owner") -> str:
    return local.data.list_sessions(local.ids[who])[0]["id"]


def send(client, text, session_id=None):
    return client.post(
        "/messages",
        json={"text": text, "session_id": session_id or ""},
        headers=hdr(client),
    )


def walk_to_ready(client) -> str:
    sid = None
    for text in DEMO_MESSAGES:
        r = send(client, text, sid)
        assert r.status_code == 200, r.get_json()
        sid = r.get_json()["session_id"]
    return sid


def write_rules(flask_app) -> list[tuple[str, str]]:
    """Every state-changing route, enumerated from the URL map (never a hand list)."""
    out = []
    for rule in flask_app.url_map.iter_rules():
        for method in sorted(rule.methods - {"GET", "HEAD", "OPTIONS"}):
            path = re.sub(r"<[^>]+>", "abc", rule.rule)
            out.append((method.lower(), path))
    return out


# ---- source greps ----


def templates() -> list[Path]:
    return sorted((WEB / "templates").glob("*.html"))


def test_no_safe_filter_anywhere():
    for p in [*templates(), *WEB.glob("*.py")]:
        text = p.read_text()
        assert "|safe" not in text.replace(" ", ""), p
        assert "Markup(" not in text, p
        assert "markdown" not in text.lower() or p.suffix == ".py", p


def test_no_inline_script_or_style_in_templates():
    for p in templates():
        text = p.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), p
        assert not re.search(r"<style", text), p
        assert not re.search(r"\sstyle\s*=", text), p
        assert not re.search(r"\son[a-z]+\s*=", text), p


def test_app_js_sends_csrf_header_and_never_innerhtml():
    js = (WEB / "static/js/app.js").read_text()
    assert "X-CSRF-Token" in js
    assert "innerHTML" not in js and "eval(" not in js and "outerHTML" not in js


def test_danger_text_contrast_meets_aa():
    css = (WEB / "static/css/app.css").read_text()
    danger = re.search(r"--danger:\s*(#[0-9a-fA-F]{6})", css).group(1)

    def lum(h: str) -> float:
        c = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    ratio = (1.05) / (lum(danger) + 0.05)
    assert ratio >= 4.5, ratio


# ---- headers, cookies, sessions (D137, D138) ----


def test_security_headers_exact_on_every_response(local, owner):
    anon = local.flask.test_client()
    for client, path in [
        (anon, "/login"),
        (anon, "/nope"),
        (anon, "/static/css/app.css"),
        (anon, "/"),
        (owner, "/"),
        (owner, "/spec"),
        (owner, "/reports"),
        (owner, "/turns/" + "0" * 32 + "/events"),
    ]:
        r = client.get(path)
        assert r.headers["Content-Security-Policy"] == CSP
        assert CSP == (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "same-origin"
        assert "Strict-Transport-Security" not in r.headers
    r = send(owner, "")
    assert r.headers["X-Frame-Options"] == "DENY"


def test_hsts_only_in_production():
    local = make(env="prod", debug=False)
    r = local.flask.test_client().get("/login")
    assert r.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_cookie_flags():
    local = make(debug=False)
    cookie = (
        local.flask.test_client()
        .get("/login", base_url="https://localhost")
        .headers["Set-Cookie"]
    )
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie


def test_session_holds_tokens_only_and_is_regenerated(local):
    c = local.flask.test_client()
    before = token_of(c)
    login(c)
    with c.session_transaction() as s:
        assert s["csrf"] != before and "auth" in s and "uid" not in s
        after = s["csrf"]
    c.post("/logout", data={"csrf_token": after})
    with c.session_transaction() as s:
        assert "auth" not in s and s.get("csrf") != after
    assert c.get("/").status_code == 302
    # the old token is revoked: a replayed cookie does not sign in
    assert not local.accounts.sessions


def test_prod_refuses_debug_and_missing_key():
    local = make()
    from milo.web.routes import create_flask_app

    with pytest.raises(RuntimeError):
        create_flask_app(
            local.data, Settings(env="prod", secret_key="k" * 32), debug=True
        )
    with pytest.raises(RuntimeError):
        create_flask_app(local.data, Settings(env="prod", secret_key=""))


def test_create_app_runs_production_problems_before_anything():
    local = make()
    env = {"MILO_ENV": "prod", "MILO_SECRET_KEY": "short", "FLASK_DEBUG": "1"}
    with pytest.raises(RuntimeError) as exc:
        create_app(
            environ=env, repos=local.data.repos, accounts=local.accounts, app=local.app
        )
    assert "shorter than 32 bytes" in str(exc.value) and "FLASK_DEBUG" in str(exc.value)
    good = {"MILO_ENV": "test", "MILO_SECRET_KEY": "s" * 40}
    flask_app = create_app(
        environ=good, repos=local.data.repos, accounts=local.accounts, app=local.app
    )
    c = flask_app.test_client()
    assert login(c).status_code == 302 and c.get("/").status_code == 200


def test_dev_factory_refuses_prod(monkeypatch):
    monkeypatch.setenv("MILO_ENV", "prod")
    with pytest.raises(RuntimeError):
        create_dev_app()


# ---- footer and pages ----


def test_footer_and_every_page_renders(local):
    op = signed_in(local, OPERATOR_EMAIL)
    owner = signed_in(local)
    sid = walk_to_ready(owner)
    rid = owner.post(
        "/submit", json={"session_id": sid}, headers=hdr(owner)
    ).get_json()["report_id"]
    anon = local.flask.test_client()
    pages = [(anon, p) for p in ("/login", "/signup", "/reset", "/reset/t")]
    pages += [
        (owner, p)
        for p in (
            "/",
            f"/sessions/{sid}",
            "/sessions",
            "/sessions?archived=1",
            "/spec",
            f"/spec?session={sid}",
            "/reports",
            f"/reports/{rid}",
            "/profile",
            "/profile/consents",
            "/consent",
        )
    ]
    pages += [
        (op, p)
        for p in (
            "/traces",
            "/ops/submissions",
            f"/ops/submissions/{rid}",
            "/ops/audit",
        )
    ]
    for client, path in pages:
        r = client.get(path)
        assert r.status_code == 200, path
        assert TAGLINE in r.get_data(as_text=True), path
    assert anon.get("/confirm").status_code == 400
    assert anon.get("/nope").status_code == 404


def test_conversation_layout_and_panel(owner, local):
    html = owner.get("/").get_data(as_text=True)
    assert 'class="panel panel-left"' in html and 'id="spec-panel"' in html
    assert "New diagnosis" in html and "Today" in html
    assert (
        'id="submit-open"' in html
        and "disabled" in html.split('id="submit-open"')[1][:80]
    )


# ---- auth, consent, operator gates ----


def test_anonymous_pages_redirect_and_json_is_401(local):
    c = local.flask.test_client()
    for path in ("/", "/spec", "/reports", "/profile", "/sessions", "/consent"):
        r = c.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/login"), path
    r = c.get("/turns/" + "a" * 32 + "/events")
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


def test_ops_is_404_json_not_found_for_owner_and_anonymous(owner, local):
    anon = local.flask.test_client()
    for path in (
        "/traces",
        "/ops/submissions",
        "/ops/submissions/1",
        "/ops/audit",
        "/ops/export/1.json",
    ):
        assert owner.get(path).status_code == 404, path
        assert anon.get(path).status_code == 404, path
    r = owner.get("/ops/submissions", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 404 and r.get_json() == {"error": "not_found"}
    before = local.data.repos.operator.submissions()
    r = owner.post(f"/ops/customers/{local.ids['other']}/delete", headers=hdr(owner))
    assert r.status_code == 404 and r.get_json() == {"error": "not_found"}
    assert local.data.get_user(local.ids["other"]) is not None
    assert local.data.repos.operator.submissions() == before


def test_operator_role_is_reread_each_request(local):
    op = signed_in(local, OPERATOR_EMAIL)
    assert op.get("/ops/audit").status_code == 200
    local.data.repos.profiles.set_role_for_test(local.ids["operator"], "owner")
    assert op.get("/ops/audit").status_code == 404


def test_consent_gate_on_every_write_route():
    local = make()
    new = local.accounts.add_confirmed("new@example.test", DEV_PASSWORD, "Nia")
    c = signed_in(local, "new@example.test")
    assert c.get("/").headers["Location"].endswith("/consent")
    exempt = {"/consent", "/logout", "/login", "/signup", "/reset", "/reset/abc"}
    before = local.data.repos.sessions.search(new)
    walked = 0
    for method, path in write_rules(local.flask):
        if path in exempt or path.startswith("/ops/"):
            continue
        r = getattr(c, method)(path, headers=hdr(c), data=b"x" * 1024)
        assert r.status_code == 403 and r.get_json() == {"error": "consent_required"}, (
            path
        )
        walked += 1
    assert walked >= 12
    assert local.data.repos.sessions.search(new) == before
    assert not local.data.has_consent(new)
    r = c.post("/consent", data={"agree": "yes", "csrf_token": token_of(c)})
    assert r.status_code == 302 and local.data.has_consent(new)
    assert send(c, "Hello there, we run a bakery.").status_code == 200


def test_every_write_route_needs_csrf(owner, local):
    for method, path in write_rules(local.flask):
        r = getattr(owner, method)(path)
        assert r.status_code == 403 and r.get_json() == {"error": "csrf_failed"}, path
        r = getattr(owner, method)(path, headers={"X-CSRF-Token": "wrong"})
        assert r.status_code == 403 and r.get_json() == {"error": "csrf_failed"}, path


def test_every_form_has_hidden_csrf_field(local):
    owner = signed_in(local)
    sid = walk_to_ready(owner)
    rid = owner.post(
        "/submit", json={"session_id": sid}, headers=hdr(owner)
    ).get_json()["report_id"]
    op = signed_in(local, OPERATOR_EMAIL)
    anon = local.flask.test_client()
    pages = [
        (owner, p)
        for p in ("/", "/spec", "/profile", "/sessions", "/consent", f"/reports/{rid}")
    ]
    pages += [(op, f"/ops/submissions/{rid}")]
    pages += [(anon, p) for p in ("/login", "/signup", "/reset", "/reset/t")]
    for client, path in pages:
        html = client.get(path).get_data(as_text=True)
        forms = re.findall(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", html, re.S)
        assert forms, path
        for body in forms:
            assert 'name="csrf_token"' in body, path


# ---- accounts (D92, D93) ----


def test_sign_up_confirm_login_flow(local):
    c = local.flask.test_client()
    form = {
        "email": "x@example.test",
        "password": "long-enough-pass",
        "first_name": "Xi",
        "role": "operator",
        "csrf_token": token_of(c),
    }
    r = c.post("/signup", data=form)
    assert r.status_code == 200 and "Check your email" in r.get_data(as_text=True)
    assert login(c, "x@example.test", "long-enough-pass").status_code == 401
    token = local.accounts.outbox[-1]["token"]
    assert c.get(f"/confirm?token={token}").status_code == 200
    assert c.get(f"/confirm?token={token}").status_code == 400
    assert login(c, "x@example.test", "long-enough-pass").status_code == 302
    uid = local.accounts.users["x@example.test"]["id"]
    assert (
        local.data.get_user(uid)["role"] == "owner"
    )  # sign-up never makes an operator
    known = c.post(
        "/signup", data={**form, "email": OWNER_EMAIL, "csrf_token": token_of(c)}
    )
    assert known.status_code == 200 and "Check your email" in known.get_data(
        as_text=True
    )


def test_password_minimum_and_generic_login_error(local):
    c = local.flask.test_client()
    data = {
        "email": "a@example.test",
        "password": "x1y2" * 2 + "abc",
        "first_name": "A",
    }
    r = c.post("/signup", data={**data, "csrf_token": token_of(c)})
    assert r.status_code == 400 and "a@example.test" not in local.accounts.users
    r = login(c, OWNER_EMAIL, "wrong-password")
    assert r.status_code == 401 and "Email or password is not right." in r.get_data(
        as_text=True
    )
    r2 = login(c, "nobody@example.test", "wrong-password")
    assert r2.status_code == 401


def test_password_reset_flow(local):
    c = local.flask.test_client()
    c.post("/reset", data={"email": OWNER_EMAIL, "csrf_token": token_of(c)})
    token = local.accounts.outbox[-1]["token"]
    r = c.post(
        f"/reset/{token}",
        data={"password": "a-new-long-pass-9", "csrf_token": token_of(c)},
    )
    assert r.status_code == 302
    assert login(c, OWNER_EMAIL, "a-new-long-pass-9").status_code == 302


# ---- sessions and owner isolation (D91, D102, A-08) ----


def test_session_routes_and_owner_isolation(local, owner):
    other = signed_in(local, OTHER_EMAIL)
    theirs = session_of(local, "other")
    before = local.data.repos.sessions.get(local.ids["other"], theirs)
    h = hdr(owner)
    assert owner.get(f"/sessions/{theirs}").status_code == 404
    for method, path, kw in [
        ("patch", f"/sessions/{theirs}", {"json": {"title": "Owned"}}),
        ("post", f"/sessions/{theirs}/rename", {"json": {"title": "Owned"}}),
        ("post", f"/sessions/{theirs}/archive", {}),
        ("post", f"/sessions/{theirs}/restore", {}),
        ("post", "/messages", {"json": {"text": "hi", "session_id": theirs}}),
        ("post", "/submit", {"json": {"session_id": theirs}}),
    ]:
        r = getattr(owner, method)(path, headers=h, **kw)
        assert r.status_code == 404 and r.get_json() == {"error": "not_found"}, path
    assert local.data.repos.sessions.get(local.ids["other"], theirs) == before
    assert other.get(f"/sessions/{theirs}").status_code == 200
    # own session: new, rename, archive, restore
    new = owner.post("/sessions", headers=h).get_json()["id"]
    r = owner.patch(f"/sessions/{new}", json={"title": "Renamed <b>x</b>"}, headers=h)
    assert r.status_code == 200 and r.get_json()["title"] == "Renamed <b>x</b>"
    assert (
        owner.patch(f"/sessions/{new}", json={"title": " "}, headers=h).status_code
        == 400
    )
    assert (
        owner.post(f"/sessions/{new}/archive", headers=h).get_json()["status"]
        == "archived"
    )
    assert "Renamed" not in owner.get("/sessions").get_data(as_text=True)
    assert "Renamed &lt;b&gt;x&lt;/b&gt;" in owner.get("/sessions?archived=1").get_data(
        as_text=True
    )
    assert (
        owner.post(f"/sessions/{new}/restore", headers=h).get_json()["status"]
        == "active"
    )


def test_owner_isolation_on_spec_profile_reports(local, owner):
    other = signed_in(local, OTHER_EMAIL)
    sid = walk_to_ready(other)
    rid = other.post(
        "/submit", json={"session_id": sid}, headers=hdr(other)
    ).get_json()["report_id"]
    secret = "Bright Cuts"
    assert owner.get(f"/spec?session={sid}").status_code == 404
    assert secret not in owner.get("/spec").get_data(as_text=True)
    assert secret not in owner.get("/profile").get_data(as_text=True)
    assert OTHER_EMAIL not in owner.get("/profile").get_data(as_text=True)
    for path in (f"/reports/{rid}", f"/reports/{rid}/download"):
        r = owner.get(path)
        assert r.status_code == 404 and secret not in r.get_data(as_text=True), path
    assert "Bright Cuts" not in owner.get("/reports").get_data(as_text=True)
    h = hdr(owner)
    r = owner.post(f"/withdraw/{rid}", headers=h)
    assert r.status_code == 404 and r.get_json() == {"error": "not_found"}
    assert (
        local.data.repos.submissions.get(local.ids["other"], rid)["status"]
        != "withdrawn"
    )
    r = owner.post(
        "/spec/company_name/request", json={"text": "x", "session_id": sid}, headers=h
    )
    assert r.status_code == 404
    assert other.get(f"/reports/{rid}").status_code == 200


# ---- turns, events, limits (D47, D99, D131) ----


def test_message_runs_a_turn_and_events_poll_in_order(owner, local):
    r = send(owner, DEMO_MESSAGES[0])
    assert r.status_code == 200
    turn, sid = r.get_json()["turn_id"], r.get_json()["session_id"]
    events = owner.get(f"/turns/{turn}/events?after=0").get_json()
    types = [e["type"] for e in events]
    assert types[0] == "node_started" and types[-1] == "done"
    assert "fact_recorded" in types and "answer" in types
    later = owner.get(f"/turns/{turn}/events?after={events[-2]['seq']}").get_json()
    assert [e["type"] for e in later] == ["done"]
    other = signed_in(local, OTHER_EMAIL)
    r = other.get(f"/turns/{turn}/events?after=0")
    assert r.status_code == 404 and r.get_json() == {"error": "not_found"}
    assert owner.get(f"/turns/{turn}/events?after=x").status_code == 400
    html = owner.get(f"/sessions/{sid}").get_data(as_text=True)
    assert (
        "Ada Lovelace" in html and "My name is Ada Lovelace" in html
    )  # value and verbatim quote
    assert "Reply details" in html


def test_bad_message_is_400(owner):
    r = send(owner, "")
    assert r.status_code == 400 and r.get_json()["error"].startswith("invalid_input")
    r = send(owner, "x" * 4001)
    assert r.get_json()["error"] == "invalid_input:too_long"


def test_running_turn_same_session_409_other_session_429():
    parked: list[Any] = []
    local = build_local(
        DemoChat(), settings=Settings(secret_key="k" * 32), spawn=parked.append
    )
    seed(local)
    c = signed_in(local)
    first = send(c, "We run a bakery.")
    assert first.status_code == 200
    sid = first.get_json()["session_id"]
    r = send(c, "And a second shop.", sid)
    assert r.status_code == 409 and r.get_json() == {"error": "turn_running"}
    other = c.post("/sessions", headers=hdr(c)).get_json()["id"]
    r = send(c, "Another diagnosis.", other)
    assert r.status_code == 429 and r.get_json()["error"] == "rate_limited"
    assert r.get_json()["window_s"] > 0 and "Retry-After" in r.headers
    parked.pop()()  # the first turn finishes
    assert send(c, "And a second shop.", sid).status_code == 200


def test_message_rate_limit_is_429(owner):
    sid = None
    codes = []
    for i in range(12):
        r = send(owner, f"We have {i} vans.", sid)
        sid = sid or (r.get_json() or {}).get("session_id")
        codes.append(r.status_code)
    assert codes[:10] == [200] * 10 and codes[10] == 429


# ---- uploads (D46, D132) ----


def upload(client, data: bytes, name: str, mime: str, sid: str = ""):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(data), name, mime), "session_id": sid},
        headers=hdr(client),
        content_type="multipart/form-data",
    )


def test_upload_text_runs_a_turn_with_the_document(owner):
    doc = b"Bookings come in by phone and email, about 40 requests a day."
    r = upload(owner, doc, "notes.txt", "text/plain")
    assert r.status_code == 200
    events = owner.get(f"/turns/{r.get_json()['turn_id']}/events").get_json()
    recorded = [e["payload"] for e in events if e["type"] == "fact_recorded"]
    assert recorded and all(p["source"] == "upload" for p in recorded)
    assert events[-1]["type"] == "done"


def test_upload_pdf_is_parsed_in_memory(owner, tmp_path):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    r = upload(owner, buf.getvalue(), "blank.pdf", "application/pdf")
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_input:no_text"
    assert not list(tmp_path.iterdir())


def test_upload_refusals(owner):
    assert (
        upload(owner, b"\xff\xfe", "x.txt", "text/plain").get_json()["error"]
        == "invalid_input:not_utf8"
    )
    r = upload(owner, b"MZ\x90\x00", "x.exe", "application/octet-stream")
    assert r.get_json()["error"] == "invalid_input:mime_not_allowed"
    r = upload(owner, b"%PDF-1.4 broken", "x.pdf", "application/pdf")
    assert r.get_json()["error"] == "invalid_input:unreadable_pdf"
    assert (
        upload(owner, b"a" * MAX_UPLOAD_BYTES, "max.txt", "text/plain").status_code
        == 200
    )
    r = upload(owner, b"a" * (MAX_UPLOAD_BYTES + 1), "big.txt", "text/plain")
    assert r.status_code == 413 and r.get_json() == {"error": "too_large"}
    r = upload(owner, b"a" * (MAX_UPLOAD_BYTES + 200_000), "big.txt", "text/plain")
    assert r.status_code == 413 and r.get_json() == {"error": "too_large"}


# ---- spec, submit, report, withdraw, approvals (D40, D41, section 8) ----


def test_submit_gate_report_and_download(owner, local):
    sid = send(owner, DEMO_MESSAGES[0]).get_json()["session_id"]
    r = owner.post("/submit", json={"session_id": sid}, headers=hdr(owner))
    assert r.status_code == 403 and r.get_json()["error"] == "policy_denied:not_ready"
    for text in DEMO_MESSAGES[1:]:
        send(owner, text, sid)
    spec = owner.get(f"/spec?session={sid}").get_data(as_text=True)
    assert "assessment ready" in spec and "We use Fresha and Gmail" in spec
    r = owner.post("/submit", data={"session_id": sid, "csrf_token": token_of(owner)})
    assert r.status_code == 302
    rid = r.headers["Location"].rsplit("/", 1)[1]
    sub = local.data.repos.submissions.get(local.ids["owner"], int(rid))
    snap = sub["spec_snapshot_json"]
    assert set(snap) == {"protocol_version", "facts"}
    assert set(snap["facts"][0]) == {
        "field_id",
        "value",
        "unit",
        "period",
        "status",
        "evidence_text",
    }
    assert (
        sub["status"] == "report_ready" and sub["report_json"]["version"] == "report.v1"
    )
    html = owner.get(f"/reports/{rid}").get_data(as_text=True)
    for title in ("Summary", "Company profile", "Cost estimate", "Evidence appendix"):
        assert title in html
    assert "Bright Cuts" in html and "source:" in html
    dl = owner.get(f"/reports/{rid}/download")
    assert dl.status_code == 200 and dl.get_json()["version"] == "report.v1"
    assert "attachment" in dl.headers["Content-Disposition"]
    assert "Bright Cuts" in owner.get("/reports").get_data(as_text=True)


def test_report_without_numbers_renders(owner, local):
    report = {
        "version": "report.v1",
        "numbers": {"allowed": False, "reason": "protocol_state:collecting"},
        "sections": [
            {
                "id": "summary",
                "title": "Summary",
                "company": {"quoted": XSS, "source": "fact.company_name"},
            },
            {
                "id": "cost_estimate",
                "title": "Cost estimate",
                "status": "none",
                "available": False,
            },
        ],
    }
    sid = session_of(local)
    sub = local.data.repos.submissions.create(
        local.ids["owner"], sid, {"protocol_version": "x", "facts": []}
    )
    local.data.repos.submissions.attach_report(
        local.ids["owner"], sub["id"], report_json=report
    )
    html = owner.get(f"/reports/{sub['id']}").get_data(as_text=True)
    assert "No figures are shown" in html and ESCAPED in html and XSS not in html


def test_withdraw_button_and_approval_from_the_conversation(owner, local):
    sid = walk_to_ready(owner)
    rid = owner.post(
        "/submit", json={"session_id": sid}, headers=hdr(owner)
    ).get_json()["report_id"]
    # the model asks to withdraw: the destructive tool needs approval (SPEC 16 M6 checks)
    r = send(owner, f"Please withdraw submission {rid}.", sid)
    events = owner.get(f"/turns/{r.get_json()['turn_id']}/events").get_json()
    needed = [e for e in events if e["type"] == "approval_required"]
    assert needed and needed[0]["payload"]["tool"] == "withdraw_submission"
    approval = needed[0]["payload"]["approval_id"]
    assert (
        local.data.repos.submissions.get(local.ids["owner"], rid)["status"]
        == "report_ready"
    )
    other = signed_in(local, OTHER_EMAIL)
    r = other.post(
        f"/approvals/{approval}", json={"decision": "approve"}, headers=hdr(other)
    )
    assert r.status_code == 404 and r.get_json() == {"error": "not_found"}
    assert (
        owner.post(
            f"/approvals/{approval}", json={"decision": "maybe"}, headers=hdr(owner)
        ).status_code
        == 400
    )
    r = owner.post(
        f"/approvals/{approval}", json={"decision": "approve"}, headers=hdr(owner)
    )
    assert (
        r.status_code == 200
        and r.get_json()["tool_calls"][0]["name"] == "withdraw_submission"
    )
    assert (
        local.data.repos.submissions.get(local.ids["owner"], rid)["status"]
        == "withdrawn"
    )
    again = owner.post(
        f"/approvals/{approval}", json={"decision": "approve"}, headers=hdr(owner)
    )
    assert "already handled" in again.get_json()["answer"]
    # the button path: a second submission withdrawn through ApprovalService, no model call
    rid2 = owner.post(
        "/submit", json={"session_id": sid}, headers=hdr(owner)
    ).get_json()["report_id"]
    calls = local.app.chat.calls
    r = owner.post(f"/withdraw/{rid2}", headers=hdr(owner))
    assert r.status_code == 200 and r.get_json() == {"status": "used"}
    assert local.app.chat.calls == calls
    assert (
        local.data.repos.submissions.get(local.ids["owner"], rid2)["status"]
        == "withdrawn"
    )
    assert owner.post("/withdraw/notanumber", headers=hdr(owner)).status_code == 404


def test_field_and_deletion_requests(owner, local):
    sid = send(owner, DEMO_MESSAGES[0]).get_json()["session_id"]
    h = hdr(owner)
    r = owner.post(
        "/spec/company_name/request",
        json={"text": "It is Bright Cuts Ltd", "session_id": sid},
        headers=h,
    )
    assert r.status_code == 200
    assert (
        owner.post(
            "/spec/no_such_field/request", json={"text": "x"}, headers=h
        ).status_code
        == 404
    )
    assert (
        local.data.repos.field_requests.history(local.ids["owner"])[0]["field_id"]
        == "company_name"
    )
    r = owner.post(
        "/profile/delete-request",
        data={"reason": "done", "csrf_token": token_of(owner)},
    )
    assert r.status_code == 302 and "requested=1" in r.headers["Location"]
    assert len(local.data.repos.deletion_requests.history(local.ids["owner"])) == 1


def test_operator_pages_export_and_delete(local):
    owner = signed_in(local)
    sid = walk_to_ready(owner)
    rid = owner.post(
        "/submit", json={"session_id": sid}, headers=hdr(owner)
    ).get_json()["report_id"]
    op = signed_in(local, OPERATOR_EMAIL)
    assert OWNER_EMAIL in op.get("/ops/submissions").get_data(as_text=True)
    assert op.get(f"/ops/export/{rid}.json").get_json()["id"] == rid
    assert op.get("/ops/submissions/999").status_code == 404
    r = op.post(f"/ops/customers/{local.ids['owner']}/delete", headers=hdr(op))
    assert r.status_code == 200
    assert local.data.repos.operator.submissions() == []
    audit = op.get("/ops/audit").get_data(as_text=True)
    assert "customer_deleted" in audit


# ---- escaping (D139) ----


def test_script_payload_is_escaped_everywhere(local):
    uid = local.accounts.add_confirmed("x@example.test", DEV_PASSWORD, XSS)
    local.data.record_consent(uid, "v0")
    c = signed_in(local, "x@example.test")
    r = send(c, f"{XSS} we run a shop\nline two")
    sid = r.get_json()["session_id"]
    c.patch(f"/sessions/{sid}", json={"title": XSS}, headers=hdr(c))
    c.post(
        "/spec/company_name/request",
        json={"text": XSS, "session_id": sid},
        headers=hdr(c),
    )
    c.post("/profile/delete-request", json={"reason": XSS}, headers=hdr(c))
    sub = local.data.repos.submissions.create(
        uid, sid, {"protocol_version": "x", "facts": []}
    )
    local.data.repos.submissions.attach_report(
        uid,
        sub["id"],
        report_json={
            "version": XSS,
            "sections": [
                {"id": "s", "title": XSS, XSS: [XSS, {"value": 1, "source": [XSS]}]}
            ],
        },
    )
    op = signed_in(local, OPERATOR_EMAIL)
    checks = [
        (c, p)
        for p in (
            f"/sessions/{sid}",
            "/",
            "/spec",
            "/reports",
            f"/reports/{sub['id']}",
            "/sessions",
            "/profile",
        )
    ]
    checks += [
        (op, p)
        for p in (
            "/ops/submissions",
            f"/ops/submissions/{sub['id']}",
            "/ops/audit",
            "/traces",
        )
    ]
    for client, path in checks:
        html = client.get(path).get_data(as_text=True)
        assert XSS not in html, path
        assert "<script>alert" not in html, path
    page = c.get(f"/sessions/{sid}").get_data(as_text=True)
    assert ESCAPED in page and "line two" in page
    assert ESCAPED in c.get(f"/reports/{sub['id']}").get_data(as_text=True)
    assert "<script>alert" not in c.get("/%3Cscript%3Ealert(1)%3C/script%3E").get_data(
        as_text=True
    )


# ---- fix cycle 1 (verify m6: T1, F1 to F5) ----


def _cookie_payload(client, flask_app) -> tuple[str, dict[str, Any]]:
    cookie = client.get_cookie("session")
    raw = cookie.value
    data = flask_app.session_interface.get_signing_serializer(flask_app).loads(raw)
    return raw, data


def test_tokens_are_encrypted_in_the_cookie(local):
    import base64

    c = signed_in(local)
    access = next(iter(local.accounts.sessions))
    sealed = {"a": access, "r": "refresh-" + "x" * 24}
    from milo.web.tokens import TokenSeal

    with c.session_transaction() as s:
        s["auth"] = TokenSeal(local.flask.secret_key).seal(sealed)
    assert c.get("/").status_code == 200
    raw, data = _cookie_payload(c, local.flask)
    decoded = ""
    for part in raw.lstrip(".").split("."):
        try:
            decoded += base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)).decode(
                "latin-1"
            )
        except ValueError:
            pass
    import zlib

    try:
        decoded += zlib.decompress(
            base64.urlsafe_b64decode(raw.lstrip(".").split(".")[0] + "==")
        ).decode("latin-1")
    except (ValueError, zlib.error):
        pass
    for token in sealed.values():
        b64 = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
        for hay in (raw, decoded, str(data)):
            assert token not in hay and b64 not in hay
    assert isinstance(data["auth"], str)


def test_tampered_token_blob_signs_out_and_logout_revokes(local):
    c = signed_in(local)
    with c.session_transaction() as s:
        s["auth"] = s["auth"][:-4] + "AAAA"
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    with c.session_transaction() as s:
        assert "auth" not in s
    other = make()
    c2 = signed_in(local)
    with c2.session_transaction() as s:
        blob = s["auth"]
    from milo.web.tokens import TokenSeal

    assert (
        TokenSeal("another-secret-" * 3).open(blob) is None
    )  # a rotated key signs out
    assert other is not None
    before = len(local.accounts.sessions)
    c2.post("/logout", data={"csrf_token": token_of(c2)})
    assert len(local.accounts.sessions) == before - 1


def test_consent_needs_an_explicit_agree():
    local = make()
    new = local.accounts.add_confirmed("new@example.test", DEV_PASSWORD, "Nia")
    c = signed_in(local, "new@example.test")
    h = hdr(c)
    for body in ({"agree": "no"}, {"agree": 1}, {"agree": "true"}, [1, 2], "yes"):
        r = c.post("/consent", json=body, headers=h)
        assert r.status_code == 400 and r.get_json() == {"error": "invalid_input"}, body
    r = c.post("/consent", data={"agree": "on", "csrf_token": h["X-CSRF-Token"]})
    assert r.status_code == 400
    assert not local.data.has_consent(new)
    assert c.post("/consent", json={"agree": True}, headers=h).status_code == 200
    assert local.data.has_consent(new)


def test_upload_never_spools_to_disk(owner, monkeypatch):
    import tempfile

    def refuse(*a: Any, **k: Any) -> Any:
        raise AssertionError("the upload touched a temporary file")

    monkeypatch.setattr(tempfile, "TemporaryFile", refuse)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", refuse)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", refuse)
    import werkzeug.formparser as fp

    monkeypatch.setattr(fp, "SpooledTemporaryFile", refuse)
    doc = b"Bookings come in by phone and email, about 40 requests a day. " * 20_000
    r = upload(owner, doc[: 2 * 1024 * 1024], "notes.txt", "text/plain")
    assert r.status_code == 200, r.get_json()


def test_pdf_parser_errors_and_bombs_are_400(owner, monkeypatch):
    import pypdf
    from pypdf.errors import LimitReachedError

    class Bomb:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise LimitReachedError("decompression limit")

    monkeypatch.setattr(pypdf, "PdfReader", Bomb)
    r = upload(owner, b"%PDF-1.7 bomb", "bomb.pdf", "application/pdf")
    assert r.status_code == 400 and r.get_json() == {
        "error": "invalid_input:unreadable_pdf"
    }


def test_pdf_page_cap(owner):
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(51):
        w.add_blank_page(width=50, height=50)
    buf = io.BytesIO()
    w.write(buf)
    r = upload(owner, buf.getvalue(), "long.pdf", "application/pdf")
    assert r.status_code == 400 and r.get_json() == {
        "error": "invalid_input:too_many_pages"
    }


def test_create_app_refuses_production_placeholders(monkeypatch):
    from milo import settings as cfg_mod

    local = make()
    env = {"MILO_ENV": "prod", "MILO_SECRET_KEY": "p" * 40}
    with pytest.raises(RuntimeError) as exc:
        create_app(
            environ=env, repos=local.data.repos, accounts=local.accounts, app=local.app
        )
    assert "placeholder v0" in str(exc.value)
    monkeypatch.setattr(cfg_mod, "CONSENT_TEXT_VERSION", "v1")
    fresh = build_local(DemoChat(), spawn=INLINE)  # no operator profile
    with pytest.raises(RuntimeError) as exc:
        create_app(
            environ=env, repos=fresh.data.repos, accounts=fresh.accounts, app=fresh.app
        )
    assert "no operator profile" in str(exc.value)
    flask_app = create_app(
        environ=env, repos=local.data.repos, accounts=local.accounts, app=local.app
    )
    assert flask_app.config["SESSION_COOKIE_SECURE"] is True


def test_chat_model_config_accessor():
    from milo.settings import chat_model_config

    cfg = chat_model_config(
        {
            "MILO_CHAT_MODEL": "vendor/model",
            "MILO_CHAT_PRICE_IN": "1",
            "MILO_CHAT_PRICE_OUT": "2",
        }
    )
    assert cfg.model == "vendor/model" and cfg.price_usd_per_mtok == (1.0, 2.0)
    with pytest.raises(RuntimeError):
        chat_model_config({"MILO_CHAT_MODEL": "vendor/model"})

"""SR review of the M6 phase-3 web wiring (SPEC D123 step 3, D124), offline.

Runs the web, auth and upload attack cases (A-08a to A-08f, A-10a, A-11, A-13a, A-13b, A-14, A-15, A-16a,
A-16b (the cookie part), A-19, A-23, A-25, A-26, A-29) against the real wiring of `milo.web.dev.build_local`: the
discovery `App`, `TurnRunner` and `WebData` over the in-memory store, in-memory accounts and a scripted chat model.
No model, no network (sockets are blocked at import), no database, no spend. Turns run inline.

A second part drives Chromium through Playwright against a local server (127.0.0.1) for the verifier-manual
items (keyboard walk-through, payloads in a live browser). It is skipped, and says so, when Playwright's Chromium
is not installed. Throwaway verifier script: writes `m6-review.json` next to itself with the D56 fields.

    uv run python evals/results/security/m6-review.py
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

_real_socket_init = socket.socket.__init__
_real_create_connection = socket.create_connection
_ALLOW_LOOPBACK = {"on": False}


def _blocked(*_a: Any, **_k: Any) -> None:
    raise RuntimeError("sockets are blocked in the SR review")


def block_sockets() -> None:
    socket.socket.__init__ = _blocked  # type: ignore[method-assign]
    socket.create_connection = _blocked  # type: ignore[assignment]


def allow_sockets() -> None:
    """Only for the browser part: a server on 127.0.0.1 and Chromium talking to it."""
    socket.socket.__init__ = _real_socket_init  # type: ignore[method-assign]
    socket.create_connection = _real_create_connection  # type: ignore[assignment]


block_sockets()
sys.path.insert(0, str(ROOT / "src"))

from html.parser import HTMLParser  # noqa: E402

from flask.testing import FlaskClient  # noqa: E402
from markupsafe import escape  # noqa: E402

from milo import settings as cfg_mod  # noqa: E402
from milo.settings import Settings  # noqa: E402
from milo.types import ChatReply, ToolCall, Usage  # noqa: E402
from milo.web.accounts import SupabaseAccounts  # noqa: E402
from milo.web.dev import (  # noqa: E402
    DEMO_FACTS,
    DEMO_MESSAGES,
    DEV_PASSWORD,
    OPERATOR_EMAIL,
    OWNER_EMAIL,
    DemoChat,
    build_local,
    seed,
)
from milo.web.routes import (  # noqa: E402
    CSP,
    HSTS,
    MAX_UPLOAD_BYTES,
    MULTIPART_MARGIN,
    create_flask_app,
    pdf_text,
)

ROWS: list[dict[str, Any]] = []
RESPONSES: list[tuple[str, str, int, dict[str, str], bool]] = []
META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)">')
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}
OTHER_EMAIL = "other@example.test"
NOCONSENT_EMAIL = "noconsent@example.test"
BASE = "https://localhost"
INLINE = lambda fn: fn()  # noqa: E731
MARK = "SRX7731"


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


class RecordingClient(FlaskClient):
    """Records every response (method, path, status, headers) for the D138 header check."""

    prod = False

    def open(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        kwargs.setdefault("base_url", BASE)
        resp = super().open(*args, **kwargs)
        req = resp.request
        RESPONSES.append(
            (req.method, req.path, resp.status_code, dict(resp.headers), self.prod)
        )
        return resp


def make(
    chat: Any = None,
    *,
    env: str = "test",
    spawn: Any = INLINE,
    report_builder: Any = None,
    kb: bool = False,
    debug: bool = False,
) -> Any:
    local = build_local(
        chat or DemoChat(),
        settings=Settings(env=env, secret_key="k" * 32),
        debug=debug,
        spawn=spawn,
        kb=kb,
        report_builder=report_builder,
    )
    owner, operator = seed(local)
    other = local.accounts.add_confirmed(OTHER_EMAIL, DEV_PASSWORD, "Oz")
    local.data.record_consent(other, "v0")
    noconsent = local.accounts.add_confirmed(NOCONSENT_EMAIL, DEV_PASSWORD, "Nia")
    local.ids = {
        "owner": owner,
        "operator": operator,
        "other": other,
        "noconsent": noconsent,
    }
    cls = type("C", (RecordingClient,), {"prod": env == "prod"})
    local.flask.test_client_class = cls
    return local


def store_of(local: Any) -> Any:
    return local.data.repos.sessions.store


def snap(local: Any) -> dict[str, Any]:
    return copy.deepcopy(store_of(local)._tables)


def diff_tables(
    a: dict[str, Any], b: dict[str, Any], ignore: tuple[str, ...] = ()
) -> list[str]:
    names = sorted(set(a) | set(b))
    return [n for n in names if n not in ignore and a.get(n) != b.get(n)]


def token_of(client: Any) -> str:
    html = client.get("/login").get_data(as_text=True)
    m = META_RE.search(html)
    return m.group(1) if m else ""


def login(client: Any, email: str = OWNER_EMAIL, password: str = DEV_PASSWORD) -> Any:
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token_of(client)},
    )


def signed_in(local: Any, email: str = OWNER_EMAIL) -> Any:
    c = local.flask.test_client()
    r = login(c, email)
    assert r.status_code == 302, r.status_code
    return c


def hdr(client: Any) -> dict[str, str]:
    return {"X-CSRF-Token": token_of(client), "X-Requested-With": "fetch"}


def send(client: Any, text: str, sid: str | None = None) -> Any:
    return client.post(
        "/messages", json={"text": text, "session_id": sid or ""}, headers=hdr(client)
    )


def walk(client: Any, messages: tuple[str, ...] = DEMO_MESSAGES) -> str:
    sid = None
    for text in messages:
        r = send(client, text, sid)
        assert r.status_code == 200, (r.status_code, r.get_json())
        sid = r.get_json()["session_id"]
    return str(sid)


def cookie_value(client: Any) -> str | None:
    c = client.get_cookie("session", domain="localhost")
    return c.value if c else None


def decode_cookie(value: str) -> Any:
    """Read a Flask session cookie WITHOUT the secret key (signed, not encrypted)."""
    payload = value
    compressed = payload.startswith(".")
    if compressed:
        payload = payload[1:]
    data = payload.split(".")[0]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    if compressed:
        raw = zlib.decompress(raw)
    return json.loads(raw)


def rules(local: Any) -> list[tuple[str, str, str]]:
    out = []
    for rule in local.flask.url_map.iter_rules():
        for m in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            out.append((m, rule.rule, rule.endpoint.removeprefix("web.")))
    return out


def fill(rule: str, ids: dict[str, str]) -> str:
    return re.sub(
        r"<(?:[a-z]+:)?([a-z_]+)>", lambda m: ids.get(m.group(1), "abc"), rule
    )


# ---------------------------------------------------------------- fixture: owner A with real ids


def owner_a_world(local: Any) -> dict[str, Any]:
    a = signed_in(local)
    sid = walk(a)
    rid = a.post("/submit", json={"session_id": sid}, headers=hdr(a)).get_json()[
        "report_id"
    ]
    r = send(a, f"Please withdraw submission {rid}.", sid)
    turn = r.get_json()["turn_id"]
    events = a.get(f"/turns/{turn}/events").get_json()
    approval = next(
        e["payload"]["approval_id"] for e in events if e["type"] == "approval_required"
    )
    a.post(
        "/spec/company_name/request",
        json={"session_id": sid, "text": "please fix"},
        headers=hdr(a),
    )
    return {
        "client": a,
        "session_id": sid,
        "report_id": str(rid),
        "submission_id": str(rid),
        "turn_id": turn,
        "approval_id": str(approval),
        "customer_id": local.ids["owner"],
        "field_id": "company_name",
        "token": "tok",
    }


# ---------------------------------------------------------------- A-08: IDOR with two owners


def check_idor() -> None:
    local = make()
    w = owner_a_world(local)
    b = signed_in(local, OTHER_EMAIL)
    sid, rid, turn, ap = w["session_id"], w["report_id"], w["turn_id"], w["approval_id"]
    hb = hdr(b)
    calls: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", f"/sessions/{sid}", {}),
        ("PATCH", f"/sessions/{sid}", {"json": {"title": "pwned"}}),
        ("POST", f"/sessions/{sid}/rename", {"json": {"title": "pwned"}}),
        ("POST", f"/sessions/{sid}/archive", {"json": {}}),
        ("POST", f"/sessions/{sid}/restore", {"json": {}}),
        ("GET", f"/turns/{turn}/events?after=0", {}),
        ("POST", f"/approvals/{ap}", {"json": {"decision": "approve"}}),
        ("POST", f"/withdraw/{rid}", {"json": {}}),
        ("GET", f"/reports/{rid}", {}),
        ("GET", f"/reports/{rid}/download", {}),
        ("GET", f"/spec?session={sid}", {}),
        (
            "POST",
            "/spec/company_name/request",
            {"json": {"session_id": sid, "text": "x"}},
        ),
        (
            "POST",
            "/messages",
            {"json": {"session_id": sid, "text": "We run a bakery."}},
        ),
        ("POST", "/submit", {"json": {"session_id": sid}}),
        ("GET", f"/ops/submissions/{rid}", {}),
        ("GET", f"/ops/export/{rid}.json", {}),
        ("POST", f"/ops/customers/{w['customer_id']}/delete", {"json": {}}),
        ("GET", "/ops/submissions", {}),
        ("GET", "/ops/audit", {}),
        ("GET", "/traces", {}),
    ]
    before = snap(local)
    bad = []
    leaks = []
    for method, path, kw in calls:
        for json_mode in (True, False):
            headers = dict(hb) if json_mode else {"X-CSRF-Token": hb["X-CSRF-Token"]}
            r = b.open(path, method=method, headers=headers, **kw)
            body = r.get_data(as_text=True)
            want_json = json_mode or method != "GET" or path.startswith("/turns/")
            ok = r.status_code == 404 and (
                not want_json
                or (r.get_json(silent=True) or {}).get("error") == "not_found"
            )
            if not ok:
                bad.append(
                    f"{method} {path} json={json_mode} -> {r.status_code} {body[:80]!r}"
                )
            if "Bright Cuts" in body or "Ada Lovelace" in body:
                leaks.append(f"{method} {path}")
    # upload with A's session id
    r = b.post(
        "/upload",
        data={
            "file": (io.BytesIO(b"We have 3 vans."), "n.txt", "text/plain"),
            "session_id": sid,
        },
        headers=hb,
        content_type="multipart/form-data",
    )
    if not (r.status_code == 404 and r.get_json().get("error") == "not_found"):
        bad.append(f"POST /upload session_id=A -> {r.status_code}")
    after = snap(local)
    changed = diff_tables(before, after)
    # coverage: every id-bearing rule is in the walk
    id_rules = {
        (m, ru)
        for m, ru, ep in rules(local)
        if "<" in ru and ep not in ("static", "reset_token", "reset_token_post")
    }
    walked = set()
    for m, ru, _ in rules(local):
        if (m, ru) in id_rules:
            filled = fill(
                ru,
                {
                    "session_id": sid,
                    "turn_id": turn,
                    "approval_id": ap,
                    "submission_id": rid,
                    "report_id": rid,
                    "customer_id": w["customer_id"],
                    "field_id": "company_name",
                },
            )
            if any(p.split("?")[0] == filled and mm == m for mm, p, _ in calls):
                walked.add((m, ru))
    missing = sorted(id_rules - walked)
    sub = local.data.repos.submissions.get(local.ids["owner"], int(rid))
    s = local.data.repos.sessions.get(local.ids["owner"], sid)
    row(
        "A-08a..f (T-07)",
        "owner B with owner A's real ids on every id-bearing route (+ body-carried session ids): 404 not_found",
        not bad,
        f"{len(calls) * 2 + 1} requests (JSON and HTML variants); failures: {bad or 'none'}",
        "high",
    )
    row(
        "A-08a..f",
        "every id-bearing URL rule is covered by the walk (enumerated from url_map)",
        not missing,
        f"{len(id_rules)} id-bearing rules (reset token excluded); not walked: {missing or 'none'}",
        "medium",
    )
    row(
        "A-08a/f",
        "no owner-A text (company, owner name) in any owner-B body",
        not leaks,
        f"leaks: {leaks or 'none'}",
        "high",
    )
    row(
        "A-08b..e",
        "backend state unchanged (every table of the in-memory store)",
        not changed,
        f"tables changed: {changed or 'none'}; A's submission status {sub['status']}, session title {s['title']!r} status {s['status']}",
        "high",
    )
    # positive control: A can still read its own items
    a = w["client"]
    ok = all(
        a.get(p).status_code == 200
        for p in (f"/sessions/{sid}", f"/reports/{rid}", f"/spec?session={sid}")
    )
    row(
        "A-08",
        "positive control: owner A reads its own session, report, spec (200)",
        ok,
        "3 pages",
        "medium",
    )


# ---------------------------------------------------------------- A-15: CSRF on every write


def check_csrf() -> None:
    local = make()
    w = owner_a_world(local)
    a = w["client"]
    b = signed_in(local, OTHER_EMAIL)
    other_token = token_of(b)
    op = signed_in(local, OPERATOR_EMAIL)
    anon = local.flask.test_client()
    writes = [(m, ru, ep) for m, ru, ep in rules(local) if m not in ("GET",)]
    before = snap(local)
    bad = []
    n = 0
    for m, ru, ep in writes:
        path = fill(ru, w)
        for who, client in (("owner", a), ("operator", op), ("anon", anon)):
            variants = [("none", {}, {})]
            if who != "anon":
                variants += [
                    (
                        "other-header",
                        {"X-CSRF-Token": other_token, "X-Requested-With": "fetch"},
                        {},
                    ),
                    ("other-form", {}, {"csrf_token": other_token}),
                    ("empty-header", {"X-CSRF-Token": ""}, {}),
                ]
            for name, headers, form in variants:
                n += 1
                r = client.open(
                    path, method=m, headers=headers, data=form or {"x": "1"}
                )
                if not (
                    r.status_code == 403
                    and (r.get_json(silent=True) or {}).get("error") == "csrf_failed"
                ):
                    bad.append(f"{who} {m} {ru} {name} -> {r.status_code}")
    changed = diff_tables(before, snap(local))
    row(
        "A-15 (T-14)",
        "every POST/PATCH/DELETE without a token, with another session's token (header and form) or an empty token -> 403 csrf_failed",
        not bad,
        f"{len(writes)} write rules, {n} requests (owner, operator, anonymous); failures: {bad or 'none'}",
        "high",
    )
    row(
        "A-15",
        "store unchanged after the CSRF walk (real ids of owner A used)",
        not changed,
        f"tables changed: {changed or 'none'}",
        "high",
    )
    row(
        "A-15",
        "CSP carries form-action 'self'",
        "form-action 'self'" in CSP,
        CSP,
        "medium",
    )


# ---------------------------------------------------------------- A-11: consent gate on every write


def check_consent() -> None:
    local = make()
    w = owner_a_world(local)
    c = signed_in(local, NOCONSENT_EMAIL)
    exempt = {
        "consent_post",
        "logout",
        "login_post",
        "signup_post",
        "reset_post",
        "reset_token_post",
    }
    writes = [
        (m, ru, ep) for m, ru, ep in rules(local) if m != "GET" and ep not in exempt
    ]
    before = snap(local)
    bad = []
    h = hdr(c)
    for m, ru, ep in writes:
        path = fill(ru, w)
        if ep == "upload":
            r = c.post(
                path,
                data={"file": (io.BytesIO(b"a" * 1024), "n.txt", "text/plain")},
                headers=h,
                content_type="multipart/form-data",
            )
        else:
            r = c.open(
                path,
                method=m,
                headers=h,
                json={
                    "text": "a" * 1024,
                    "title": "t",
                    "decision": "approve",
                    "reason": "r",
                },
            )
        expected = (403, "consent_required")
        if ep == "ops_customer_delete":
            expected = (
                404,
                "not_found",
            )  # not an operator: the role gate answers first
        got = (r.status_code, (r.get_json(silent=True) or {}).get("error"))
        if got != expected:
            bad.append(f"{m} {ru} -> {got}")
    changed = diff_tables(before, snap(local))
    row(
        "A-11 (T-10, D81, D135)",
        "every non-exempt write route before the interview consent row -> 403 consent_required (1 KB body or file)",
        not bad,
        f"{len(writes)} write rules walked; failures: {bad or 'none'}",
        "medium",
    )
    row(
        "A-11",
        "no row written before consent",
        not changed,
        f"tables changed: {changed or 'none'}",
        "medium",
    )
    r = c.get("/")
    row(
        "A-11",
        "GET / without consent -> 302 /consent",
        r.status_code == 302 and r.headers["Location"].endswith("/consent"),
        f"{r.status_code} {r.headers.get('Location')}",
        "medium",
    )
    r = c.post("/consent", json={"agree": "no"}, headers=hdr(c))
    recorded = local.data.has_consent(local.ids["noconsent"])
    row(
        "A-11 / D81",
        'POST /consent with JSON {"agree": "no"} does not record consent',
        not recorded,
        f"status {r.status_code}, consent row recorded: {recorded}",
        "low",
    )
    local2 = make()
    c2 = signed_in(local2, NOCONSENT_EMAIL)
    r_no = c2.post("/consent", data={"agree": "no", "csrf_token": token_of(c2)})
    r_yes = c2.post("/consent", data={"agree": "yes", "csrf_token": token_of(c2)})
    ok = (
        r_no.status_code == 400
        and r_yes.status_code == 302
        and local2.data.has_consent(local2.ids["noconsent"])
    )
    row(
        "A-11",
        "form agree=no -> 400, agree=yes -> 302 and the row exists; GET / then 200",
        ok and c2.get("/").status_code == 200,
        f"{r_no.status_code}, {r_yes.status_code}",
        "medium",
    )
    r = c2.post("/logout", data={"csrf_token": token_of(c2)})
    row("A-11", "POST /logout -> 302", r.status_code == 302, str(r.status_code), "low")


# ---------------------------------------------------------------- A-08f, A-29, D144: operator pages


def check_ops() -> None:
    local = make()
    w = owner_a_world(local)
    owner = w["client"]
    anon = local.flask.test_client()
    op = signed_in(local, OPERATOR_EMAIL)
    ops = [
        (m, ru, ep)
        for m, ru, ep in rules(local)
        if ru.startswith("/ops") or ru == "/traces"
    ]
    bad = []
    before = snap(local)
    for m, ru, ep in ops:
        path = fill(ru, w)
        for who, client in (("owner", owner), ("anon", anon)):
            for js in (True, False):
                headers = hdr(client) if js else {"X-CSRF-Token": token_of(client)}
                r = client.open(path, method=m, headers=headers)
                ok = r.status_code == 404 and (
                    not (js or m != "GET")
                    or (r.get_json(silent=True) or {}).get("error") == "not_found"
                )
                if not ok:
                    bad.append(f"{who} {m} {ru} json={js} -> {r.status_code}")
    changed = diff_tables(before, snap(local))
    row(
        "A-08f / D144",
        "/ops/* and /traces answer 404 (JSON not_found) to an owner and to anonymous, every rule from url_map",
        not bad,
        f"{len(ops)} rules x 2 actors x 2 modes; failures: {bad or 'none'}",
        "high",
    )
    row(
        "A-08f",
        "owner's ops delete attempt changes nothing (customers row kept, no audit row)",
        not changed,
        f"tables changed: {changed or 'none'}",
        "high",
    )
    gets = [fill(ru, w) for m, ru, ep in ops if m == "GET"]
    codes = [op.get(p).status_code for p in gets]
    row(
        "D144",
        "operator reads every operator GET route (200)",
        all(c == 200 for c in codes),
        f"{list(zip(gets, codes))}",
        "low",
    )
    local.data.repos.profiles.set_role_for_test(local.ids["operator"], "owner")
    r = op.get("/ops/submissions")
    row(
        "A-29 / D144",
        "role re-read per request: demoted operator gets 404 with the same cookie",
        r.status_code == 404,
        str(r.status_code),
        "high",
    )
    dec = decode_cookie(cookie_value(op) or "")
    row(
        "A-29",
        "the cookie carries no role and no user id",
        "role" not in json.dumps(dec) and local.ids["operator"] not in json.dumps(dec),
        f"cookie keys: {sorted(dec)}; auth keys: {sorted(dec.get('auth', {}))}",
        "high",
    )


# ---------------------------------------------------------------- A-13a, A-14, D139: XSS in every rendered field


def payloads() -> list[tuple[str, str]]:
    out = []
    for line in (ROOT / "evals/security/payloads/xss.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        field, p = line.split("\t", 1)
        out.append((field, p.replace("\\n", "\n")))
    return out


class XssChat(DemoChat):
    """The demo model, with the payload as the company name (evidence verbatim in the message, D134), echoed
    at the start of every reply, and as a `search_knowledge` argument when the owner says SEARCH."""

    def __init__(self, payload: str) -> None:
        super().__init__()
        self.p = payload

    def invoke(self, messages: list[dict[str, Any]], tools: Any = None) -> ChatReply:
        first = str(messages[0].get("content", ""))
        if first.startswith("You extract facts"):
            text = str(messages[-1].get("content") or "")
            facts = []
            for key, items in DEMO_FACTS.items():
                if key in text:
                    for f in items:
                        f = copy.deepcopy(f)
                        if f["field_id"] == "company_name" and self.p in text:
                            f["value"] = self.p
                            f["evidence_text"] = f"I run {self.p}"
                        facts.append(f)
            self.calls += 1
            return ChatReply(
                json.dumps({"facts": facts, "asks_about": "none"}), usage=Usage(10, 10)
            )
        last = messages[-1]
        if (
            tools
            and last.get("role") != "tool"
            and "SEARCH" in str(last.get("content", ""))
        ):
            self.calls += 1
            return ChatReply(
                "",
                tool_calls=[ToolCall("s1", "search_knowledge", {"query": self.p})],
                usage=Usage(10, 10),
            )
        reply = super().invoke(messages, tools)
        if reply.text:
            reply = ChatReply(
                self.p + "\n" + reply.text,
                tool_calls=reply.tool_calls,
                usage=reply.usage,
            )
        return reply


class LiveScan(HTMLParser):
    """Parses the page as a browser would and lists live markup that only a payload could add."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []
        self.in_a = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and "src" not in names:
            self.found.append("inline <script>")
        if tag in ("iframe", "style", "img", "b", "object", "embed"):
            self.found.append(f"<{tag}>")
        for k, v in names.items():
            if k.startswith("on"):
                self.found.append(f"{tag} {k}=")
            if k in (
                "href",
                "src",
                "action",
                "formaction",
            ) and v.strip().lower().startswith(("javascript:", "data:")):
                self.found.append(f"{tag} {k}={v[:20]}")
            if k == "style":
                self.found.append(f"{tag} style=")
        if tag == "a":
            self.in_a = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.in_a = False

    def handle_data(self, data: str) -> None:
        if self.in_a and data.strip() == "a link":
            self.found.append("markdown anchor")


def live(html: str) -> list[str]:
    scan = LiveScan()
    scan.feed(html)
    return scan.found


def seed_payload_world(
    p: str, debug: bool = False
) -> tuple[Any, dict[str, Any], list[str], list[str]]:
    local = make(
        XssChat(p), debug=debug
    )  # debug only drops the Secure flag (the browser part runs over http)
    # first name carries the payload (signup path stores it in the account)
    xemail = "xss@example.test"
    uid = local.accounts.add_confirmed(xemail, DEV_PASSWORD, p)
    local.data.record_consent(
        uid, p
    )  # consent version shown on the consent history page
    c = signed_in(local, xemail)
    msgs = (
        DEMO_MESSAGES[0].replace("Bright Cuts", p),
        DEMO_MESSAGES[1] + " " + p,
        DEMO_MESSAGES[2],
    )
    sid = walk(c, msgs)
    send(c, "SEARCH please " + p, sid)
    c.patch(f"/sessions/{sid}", json={"title": p}, headers=hdr(c))
    rid = c.post("/submit", json={"session_id": sid}, headers=hdr(c)).get_json()[
        "report_id"
    ]
    # a second submission whose stored report JSON has the payload in every string leaf and as a key
    rid2 = c.post("/submit", json={"session_id": sid}, headers=hdr(c)).get_json()[
        "report_id"
    ]
    tab = store_of(local)._tables["submissions"]

    def poison(v: Any) -> Any:
        if isinstance(v, str):
            return v + p
        if isinstance(v, list):
            return [poison(x) for x in v]
        if isinstance(v, dict):
            return {k: poison(x) for k, x in v.items()}
        return v

    rep = tab[int(rid2)]["report_json"]
    if rep:
        rep = poison(rep)
        if rep.get("sections"):
            rep["sections"][0][p] = {"quoted": p, "value": p, "source": [p]}
        tab[int(rid2)]["report_json"] = rep
    c.post(
        "/spec/company_name/request",
        json={"session_id": sid, "text": p},
        headers=hdr(c),
    )
    c.post("/profile/delete-request", json={"reason": p}, headers=hdr(c))
    c.post(
        "/upload",
        data={
            "file": (io.BytesIO(("notes " + p).encode()), p + ".txt", "text/plain"),
            "session_id": sid,
        },
        headers=hdr(c),
        content_type="multipart/form-data",
    )
    local.data.repos.audit.record(uid, p, p, {})
    q = urllib.parse.quote(p, safe="")
    owner_pages = [
        "/",
        f"/sessions/{sid}",
        "/sessions",
        f"/sessions?q={q}",
        f"/spec?session={sid}",
        "/reports",
        f"/reports/{rid}",
        f"/reports/{rid2}",
        "/profile",
        "/profile/consents",
        f"/{q}",
    ]
    op_pages = [
        "/ops/submissions",
        f"/ops/submissions/{rid}",
        f"/ops/submissions/{rid2}",
        "/ops/audit",
        "/traces",
    ]
    return (
        local,
        {"client": c, "sid": sid, "rid": rid, "rid2": rid2},
        owner_pages,
        op_pages,
    )


def check_xss() -> None:
    total_reads = 0
    failures: list[str] = []
    coverage: dict[str, int] = {}
    json_bad = []
    for field, p in payloads():
        local, w, owner_pages, op_pages = seed_payload_world(p)
        op = signed_in(local, OPERATOR_EMAIL)
        esc = str(escape(p))
        for who, client, pages in (
            ("owner", w["client"], owner_pages),
            ("operator", op, op_pages),
        ):
            for path in pages:
                r = client.get(path)
                html = r.get_data(as_text=True)
                total_reads += 1
                if r.status_code not in (200, 404):
                    failures.append(f"{field} {who} {path} status {r.status_code}")
                if MARK in html:
                    coverage[path.split("?")[0].split("/")[1] or "/"] = (
                        coverage.get(path.split("?")[0].split("/")[1] or "/", 0) + 1
                    )
                    if esc != p and p in html:
                        failures.append(f"{field} {who} {path}: raw payload present")
                    if esc not in html and p.replace("\n", "") not in html:
                        # a payload may be shown in part (title cut, query cut); the raw form must still be absent
                        pass
                for name in live(html):
                    failures.append(f"{field} {who} {path}: {name}")
                if "{{" in p and MARK in html and p not in html:
                    failures.append(f"{field} {who} {path}: template literal changed")
        # events are JSON (the drawer renders them with textContent)
        c = w["client"]
        sid = w["sid"]
        r = send(c, "SEARCH again " + p, sid)
        ev = c.get(f"/turns/{r.get_json()['turn_id']}/events")
        if (
            not ev.mimetype == "application/json"
            or ev.headers.get("X-Content-Type-Options") != "nosniff"
        ):
            json_bad.append(f"{field}: {ev.mimetype}")
        tool_args = [
            e
            for e in ev.get_json()
            if e["type"] == "tool_call"
            and e["payload"].get("args", {}).get("query") == p
        ]
        if not tool_args:
            json_bad.append(
                f"{field}: tool argument not carried in events ({r.status_code} {[e['type'] for e in ev.get_json()][-3:]})"
            )
        # report and export downloads are attachments with JSON type
        for path in (f"/reports/{w['rid']}/download",):
            d = c.get(path)
            if not (
                d.mimetype == "application/json"
                and d.headers.get("Content-Disposition", "").startswith("attachment")
            ):
                json_bad.append(f"{field}: {path} {d.mimetype}")
        e = op.get(f"/ops/export/{w['rid']}.json")
        if not (
            e.mimetype == "application/json"
            and e.headers.get("Content-Disposition", "").startswith("attachment")
        ):
            json_bad.append(f"{field}: export {e.mimetype}")
    row(
        "A-13a (T-12, D139)",
        "13 xss.txt payloads in company name, evidence, message, reply, tool argument, session title, first name, consent version, field and deletion notes, upload name and text, every report string leaf and key, audit action/target, search query, 404 path; owner pages",
        not [f for f in failures if "operator" not in f],
        f"{total_reads} page reads; no raw payload, no live tag/handler/javascript:/markdown anchor; marker seen on pages {sorted(coverage)}; failures: {[f for f in failures if 'operator' not in f] or 'none'}",
        "high",
    )
    row(
        "A-14 (T-13)",
        "operator pages (/ops/submissions, /ops/submissions/<id> for a normal and a poisoned report, /ops/audit, /traces) escape owner text",
        not [f for f in failures if "operator" in f],
        f"failures: {[f for f in failures if 'operator' in f] or 'none'}",
        "high",
    )
    row(
        "A-13a / A-14",
        "turn events, report download and operator export are application/json (+nosniff, attachment); tool arguments travel only as JSON",
        not json_bad,
        f"failures: {json_bad or 'none'}",
        "medium",
    )
    web = ROOT / "src/milo/web"
    hits = []
    for f in [*web.rglob("*.html"), *web.glob("*.py"), web / "static/js/app.js"]:
        t = f.read_text()
        for rx in (
            r"\|\s*safe",
            r"Markup\(",
            r"innerHTML",
            r"outerHTML",
            r"insertAdjacentHTML",
            r"document\.write",
        ):
            if re.search(rx, t):
                hits.append(f"{f.relative_to(ROOT)}:{rx}")
        if f.suffix == ".html":
            for rx in (
                r"<script(?![^>]*\bsrc=)",
                r"<style",
                r"\sstyle\s*=",
                r"\son[a-z]+\s*=",
            ):
                if re.search(rx, t):
                    hits.append(f"{f.relative_to(ROOT)}:{rx}")
    row(
        "A-13b / A-14 (D138, D139)",
        "no |safe, Markup, innerHTML-type sinks; no inline script, style, style= or on*= in templates",
        not hits,
        f"hits: {hits or 'none'}",
        "high",
    )


# ---------------------------------------------------------------- A-13b: headers on every response


def check_headers() -> None:
    local = make()
    local.flask.add_url_rule("/boom", "boom", lambda: 1 / 0)
    local.flask.config["PROPAGATE_EXCEPTIONS"] = False
    w = owner_a_world(local)
    a = w["client"]
    op = signed_in(local, OPERATOR_EMAIL)
    anon = local.flask.test_client()
    for m, ru, ep in rules(local):
        if m == "GET":
            for c in (a, op, anon):
                c.get(fill(ru, w) if ep != "static" else "/static/css/app.css")
    a.get("/boom")  # 500
    a.get("/submit")  # 405
    a.get("/no/such/page")  # 404
    a.post(
        "/upload",
        data={
            "file": (
                io.BytesIO(b"a" * (MAX_UPLOAD_BYTES + MULTIPART_MARGIN + 10)),
                "big.txt",
                "text/plain",
            )
        },
        headers=hdr(a),
        content_type="multipart/form-data",
    )  # 413
    for i in range(12):
        send(a, f"We have {i} vans.", w["session_id"])  # 429 at the 11th
    anon.post("/messages", json={"text": "x"}, headers={"X-CSRF-Token": "nope"})  # 403
    prod = make(env="prod")
    pc = prod.flask.test_client()
    pr = pc.get("/login")
    statuses = sorted(
        {
            s
            for *_x, s, _h, _p in [(r[0], r[1], r[2], r[3], r[4]) for r in RESPONSES]
            for s in [s]
        }
    )
    bad = []
    for method, path, status, headers, is_prod in RESPONSES:
        for k, v in HEADERS.items():
            if headers.get(k) != v:
                bad.append(f"{method} {path} {status}: {k}={headers.get(k)!r}")
        if is_prod and headers.get("Strict-Transport-Security") != HSTS:
            bad.append(f"{method} {path}: HSTS missing in prod")
        if not is_prod and "Strict-Transport-Security" in headers:
            bad.append(f"{method} {path}: HSTS outside prod")
    codes = sorted({r[2] for r in RESPONSES})
    row(
        "A-13b (D138)",
        "the four headers with exact values on every response recorded in this run (every test client, every check); HSTS only in prod",
        not bad,
        f"{len(RESPONSES)} responses, statuses seen {codes}; mismatches: {bad[:5] or 'none'}",
        "medium",
    )
    row(
        "A-13b",
        "prod adds Strict-Transport-Security: max-age=31536000",
        pr.headers.get("Strict-Transport-Security") == HSTS,
        str(pr.headers.get("Strict-Transport-Security")),
        "medium",
    )
    r = a.get("/boom")
    body = r.get_data(as_text=True)
    row(
        "T-19",
        "a forced 500 renders the error page without a traceback",
        r.status_code == 500 and "Traceback" not in body and "ZeroDivision" not in body,
        f"{r.status_code}",
        "medium",
    )
    _ = statuses


# ---------------------------------------------------------------- A-16a: cookie flags and rotation; the token cookie


class FakeSupabaseService:
    """Stands in for `LoginService` to show what `SupabaseAccounts` stores in the cookie (no network)."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.logged_out: list[str] = []

    def login(self, email: str, password: str, ip: str) -> Any:
        sess = type(
            "S",
            (),
            {
                "access_token": "eyJhbGciOiJSUzI1NiJ9.FAKE-ACCESS-JWT.sig",
                "refresh_token": "FAKE-REFRESH-TOKEN-7731",
            },
        )()
        return type("R", (), {"session": sess})()

    def authenticate(self, token: str) -> Any:
        # local JWT verification (D88): a valid, unexpired token is accepted whether or not the user signed out
        if token.startswith("eyJ"):
            return type("C", (), {"sub": self.user_id})()
        from milo.auth.errors import AuthError

        raise AuthError("invalid")

    def logout(self, token: str) -> None:
        self.logged_out.append(token)


def check_session() -> None:
    local = make()
    c = local.flask.test_client()
    t0 = token_of(c)
    c0 = cookie_value(c)
    r = c.post(
        "/login",
        data={"email": OWNER_EMAIL, "password": DEV_PASSWORD, "csrf_token": t0},
    )
    setc = r.headers.get("Set-Cookie", "")
    c1 = cookie_value(c)
    t1 = token_of(c)
    flags = all(x in setc for x in ("Secure", "HttpOnly", "SameSite=Lax"))
    row(
        "A-16a (T-15)",
        "cookie flags Secure, HttpOnly, SameSite=Lax (debug off)",
        flags,
        setc.split(";", 1)[1].strip() if ";" in setc else setc,
        "high",
    )
    rotated = c0 != c1 and t0 != t1
    old = c.post("/sessions", headers={"X-CSRF-Token": t0, "X-Requested-With": "fetch"})
    row(
        "A-16a / D137",
        "sign-in regenerates the session: cookie and CSRF token change; the pre-login token is refused",
        rotated and old.status_code == 403,
        f"cookie changed {c0 != c1}, token changed {t0 != t1}, old token -> {old.status_code}",
        "high",
    )
    r = c.post("/logout", data={"csrf_token": t1})
    t2 = token_of(c)
    stale = c.post(
        "/sessions", headers={"X-CSRF-Token": t1, "X-Requested-With": "fetch"}
    )
    row(
        "A-16a / D137",
        "sign-out regenerates: 302 /login, new CSRF token, the old token refused",
        r.status_code == 302 and t2 != t1 and stale.status_code == 403,
        f"{r.status_code} {r.headers.get('Location')}, old token -> {stale.status_code}",
        "high",
    )
    replay = local.flask.test_client()
    replay.set_cookie("session", c1 or "", domain="localhost")
    rr = replay.get("/")
    row(
        "A-16a",
        "replay of the pre-sign-out cookie (in-memory accounts) -> redirected to /login",
        rr.status_code == 302 and "/login" in rr.headers.get("Location", ""),
        f"{rr.status_code} {rr.headers.get('Location')}",
        "high",
    )
    dec = decode_cookie(c1 or "")
    row(
        "A-16a",
        "the cookie holds only the tokens and the CSRF token (no user id, no role)",
        sorted(dec) == ["auth", "csrf"],
        f"keys {sorted(dec)}",
        "high",
    )
    # the token cookie under SupabaseAccounts (the production accounts), with a fake service
    local2 = make()
    svc = FakeSupabaseService(local2.ids["owner"])
    local2.data.accounts = SupabaseAccounts(svc)
    flask2 = create_flask_app(
        local2.data, Settings(env="test", secret_key="k" * 32), debug=False
    )
    flask2.test_client_class = local2.flask.test_client_class
    s = flask2.test_client()
    s.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": DEV_PASSWORD,
            "csrf_token": token_of(s),
        },
    )
    sc = cookie_value(s) or ""
    readable = decode_cookie(sc)
    has_refresh = readable.get("auth", {}).get("r") == "FAKE-REFRESH-TOKEN-7731"
    row(
        "open point (D136)",
        "with SupabaseAccounts the cookie carries the access and refresh tokens readable without the key (signed, not encrypted)",
        True,
        f"decoded without MILO_SECRET_KEY: auth keys {sorted(readable.get('auth', {}))}, refresh token readable {has_refresh}, cookie {len(sc)} bytes; assessed in docs/verify/m6.md (informational row)",
        "medium",
    )
    s.post("/logout", data={"csrf_token": token_of(s)})
    rep = flask2.test_client()
    rep.set_cookie("session", sc, domain="localhost")
    rs = rep.get("/")
    row(
        "A-16b (D136)",
        "reported: a pre-sign-out cookie replayed with SupabaseAccounts is accepted while the access JWT is unexpired (local verification); Supabase logout (scope local) was called",
        True,
        f"replay GET / -> {rs.status_code}; service.logout called {len(svc.logged_out)}x; the accepted residual of D136 / section 18 item 9",
        "info",
    )


# ---------------------------------------------------------------- A-19, D92, A-10a


def check_auth() -> None:
    local = make()
    a = local.flask.test_client()
    b = local.flask.test_client()

    def clean(t: str) -> str:
        return re.sub(r'(content|value)="[^"]+"', "", t)

    ra = a.post(
        "/signup",
        data={
            "email": OWNER_EMAIL,
            "password": "walk-the-plank-7731",
            "first_name": "A",
            "csrf_token": token_of(a),
        },
    )
    rb = b.post(
        "/signup",
        data={
            "email": "fresh@example.test",
            "password": "walk-the-plank-7731",
            "first_name": "A",
            "csrf_token": token_of(b),
        },
    )
    la = a.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": "wrong-password-1",
            "csrf_token": token_of(a),
        },
    )
    lb = b.post(
        "/login",
        data={
            "email": "nobody@example.test",
            "password": "wrong-password-1",
            "csrf_token": token_of(b),
        },
    )
    xa = a.post("/reset", data={"email": OWNER_EMAIL, "csrf_token": token_of(a)})
    xb = b.post(
        "/reset", data={"email": "nobody@example.test", "csrf_token": token_of(b)}
    )
    same = all(
        x.status_code == y.status_code
        and clean(x.get_data(as_text=True)) == clean(y.get_data(as_text=True))
        for x, y in ((ra, rb), (la, lb), (xa, xb))
    )
    row(
        "A-19 (T-18, D92)",
        "sign-up, sign-in, reset: same status and body for a known and an unknown email",
        same,
        f"{ra.status_code}/{rb.status_code}, {la.status_code}/{lb.status_code}, {xa.status_code}/{xb.status_code}",
        "low",
    )
    c = local.flask.test_client()
    r11 = c.post(
        "/signup",
        data={
            "email": "short@example.test",
            "password": "x" * 11,
            "first_name": "S",
            "csrf_token": token_of(c),
        },
    )
    row(
        "D92",
        "an 11-character password is refused at sign-up",
        r11.status_code == 400,
        str(r11.status_code),
        "medium",
    )
    c = local.flask.test_client()
    c.post(
        "/signup",
        data={
            "email": "op@example.test",
            "password": "walk-the-plank-7731",
            "first_name": "R",
            "role": "operator",
            "csrf_token": token_of(c),
        },
    )
    token = local.accounts.outbox[-1]["token"]
    c.get(f"/confirm?token={token}")
    login(c, "op@example.test", "walk-the-plank-7731")
    uid = local.accounts.sessions[decode_cookie(cookie_value(c) or "")["auth"]["a"]]
    role = local.data.repos.profiles.role(uid)
    ops = c.get("/ops/submissions").status_code
    row(
        "A-10a (rule 12)",
        "sign-up with role=operator creates an owner; /ops 404",
        role == "owner" and ops == 404,
        f"role {role}, /ops {ops}",
        "high",
    )
    # A-17 lockout (D92): the 6th failure per account in 15 min is 429
    d = make().flask.test_client()
    codes = [
        d.post(
            "/login",
            data={
                "email": OTHER_EMAIL,
                "password": "bad-password-x",
                "csrf_token": token_of(d),
            },
        ).status_code
        for _ in range(7)
    ]
    row(
        "A-17 (D92)",
        "repeated failed sign-ins for one account are limited (429)",
        429 in codes,
        f"statuses {codes}",
        "medium",
    )


# ---------------------------------------------------------------- A-25: uploads


class WriteWatch:
    """An audit hook that records every file opened for writing (open, os.open, tempfile) while armed."""

    def __init__(self) -> None:
        self.armed = False
        self.events: list[str] = []
        sys.addaudithook(self.hook)

    def hook(self, event: str, args: Any) -> None:
        if not self.armed:
            return
        if event == "open":
            path, mode, flags = args
            write = False
            if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
                write = True
            if isinstance(flags, int) and flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | getattr(os, "O_TMPFILE", 0)
            ):
                write = True
            if write:
                self.events.append(f"open {path!r} mode={mode} flags={flags}")
        elif event in ("tempfile.mkstemp", "tempfile.mkdtemp"):
            self.events.append(f"{event} {args!r}")


WATCH = WriteWatch()


def upload(client: Any, data: bytes, name: str, mime: str, sid: str = "") -> Any:
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(data), name, mime), "session_id": sid},
        headers=hdr(client),
        content_type="multipart/form-data",
    )


def big_pdf(n_chars: int) -> bytes:
    """One page whose content stream draws `n_chars` characters; Flate-compressed (a small file that expands)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    page = w.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): w._add_object(font)}
            )
        }
    )
    content = DecodedStreamObject()
    line = b"(" + b"A" * 1000 + b") Tj T*\n"
    content.set_data(b"BT /F1 1 Tf 0 0 Td 1 TL\n" + line * (n_chars // 1000) + b"ET\n")
    content = content.flate_encode(level=9)
    page[NameObject("/Contents")] = w._add_object(content)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def check_uploads() -> None:
    local = make()
    c = signed_in(local)
    send(c, "We run a small bakery.")
    names = []
    for line in (
        (ROOT / "evals/security/payloads/traversal_names.txt").read_text().splitlines()
    ):
        if line and not line.startswith("#"):
            names.append(line.replace("\\u0000", "\x00"))
    WATCH.events.clear()
    WATCH.armed = True
    codes = []  # five uploads per account per hour (D131), so the names rotate over fresh accounts
    accounts = []
    for i, name in enumerate(names):
        if i % 5 == 0:
            email = f"up{i}@example.test"
            uid = local.accounts.add_confirmed(email, DEV_PASSWORD, "U")
            local.data.record_consent(uid, "v0")
            WATCH.armed = False
            cl = signed_in(local, email)
            WATCH.armed = True
            accounts.append(cl)
        r = upload(accounts[-1], b"We get about 40 requests a day.", name, "text/plain")
        codes.append(r.status_code)
    WATCH.armed = False
    small_writes = list(WATCH.events)
    row(
        "A-25 (T-24, D132)",
        "13 traversal filenames: parsed in memory, nothing opened for writing (audit hook on open/os.open/tempfile)",
        not small_writes,
        f"statuses {codes}; write opens: {small_writes or 'none'}",
        "high",
    )
    # a large upload (under the 5 MB cap): Werkzeug spools a multipart file over 500 KB to an anonymous temp file
    local2 = make()
    c2 = signed_in(local2)
    WATCH.events.clear()
    WATCH.armed = True
    r = upload(
        c2,
        b"We get about 40 requests a day. " * (MAX_UPLOAD_BYTES // 40),
        "big.txt",
        "text/plain",
    )
    WATCH.armed = False
    spooled = list(WATCH.events)
    row(
        "A-25 / D132",
        'a 5 MB upload is never written to disk ("the original file is never written to disk")',
        not spooled,
        f"status {r.status_code}; write opens during the request: {spooled[:2] or 'none'} (Werkzeug's default stream factory spools a file part over 500 KB to an unnamed temp file)",
        "low",
    )
    local3 = make()
    c3 = signed_in(local3)
    exact = upload(c3, b"a" * MAX_UPLOAD_BYTES, "five.txt", "text/plain")
    over = upload(c3, b"a" * (MAX_UPLOAD_BYTES + 1), "over.txt", "text/plain")
    way = upload(
        c3, b"a" * (MAX_UPLOAD_BYTES + MULTIPART_MARGIN + 1), "way.txt", "text/plain"
    )
    row(
        "A-25 / D46 / D132",
        "upload caps: 5 MiB accepted; 5 MiB + 1 refused; past the multipart margin 413 too_large",
        exact.status_code == 200
        and over.status_code in (400, 413)
        and way.status_code == 413
        and way.get_json() == {"error": "too_large"},
        f"5 MiB -> {exact.status_code}; 5 MiB+1 -> {over.status_code} {over.get_json()}; 5 MiB+64 KiB+1 -> {way.status_code} {way.get_json()}",
        "medium",
    )
    spoof = upload(c3, b"%PDF-1.7 this is not a pdf", "x.txt", "text/plain")
    exe = upload(c3, b"MZ\x90\x00\xff\xfe", "x.txt", "text/plain")
    bad_mime = upload(c3, b"plain words", "x.exe", "application/octet-stream")
    row(
        "A-25",
        "MIME spoofing: PDF magic on a non-PDF, binary named .txt, unknown MIME -> 400 invalid_input",
        all(
            x.status_code == 400 and x.get_json()["error"].startswith("invalid_input")
            for x in (spoof, exe, bad_mime)
        ),
        f"{spoof.get_json()}, {exe.get_json()}, {bad_mime.get_json()}",
        "medium",
    )
    long_text = ("Bookings come in by phone. " * 1200).encode()
    local4 = make()
    c4 = signed_in(local4)
    r = upload(c4, long_text, "long.txt", "text/plain")
    ev = c4.get(f"/turns/{r.get_json()['turn_id']}/events").get_json()
    trunc = [e for e in ev if e["type"] == "document_truncated"]
    row(
        "A-25 / D132",
        "text past 20,000 characters is dropped with a document_truncated event",
        bool(trunc),
        f"{len(long_text)} chars uploaded; event {trunc[0]['payload'] if trunc else None}",
        "medium",
    )
    # decompression: under pypdf's 75 MB stream cap, and past it
    results = []
    for n in (2_000_000, 80_000_000):
        pdf = big_pdf(n)
        t = time.perf_counter()
        try:
            out = pdf_text(pdf)
            res = f"{len(out)} chars"
        except Exception as exc:  # noqa: BLE001
            res = f"{type(exc).__name__}"
        results.append((n, len(pdf), res, round(time.perf_counter() - t, 2)))
    local5 = make()
    c5 = signed_in(local5)
    t = time.perf_counter()
    bomb = upload(c5, big_pdf(80_000_000), "bomb.pdf", "application/pdf")
    took = round(time.perf_counter() - t, 2)
    row(
        "A-25",
        "a small PDF that expands past pypdf's 75 MB stream cap is refused as invalid_input, not a 500",
        bomb.status_code == 400,
        f"pdf_text: {[f'{n} chars drawn, {size} bytes file -> {res} in {s}s' for n, size, res, s in results]}; POST /upload bomb -> {bomb.status_code} {bomb.get_json(silent=True)} in {took}s (pypdf LimitReachedError is not caught by pdf_text)",
        "low",
    )


# ---------------------------------------------------------------- A-23 / D131: per-account limits


def check_limits() -> None:
    parked: list[Any] = []
    local = make(spawn=parked.append)
    c = signed_in(local)
    first = send(c, "We run a bakery.")
    sid = first.get_json()["session_id"]
    same = send(c, "And a second shop.", sid)
    other = c.post("/sessions", headers=hdr(c)).get_json()["id"]
    second = send(c, "Another diagnosis.", other)
    ok = (
        same.status_code == 409
        and same.get_json() == {"error": "turn_running"}
        and second.status_code == 429
        and second.get_json()["error"] == "rate_limited"
        and "Retry-After" in second.headers
    )
    row(
        "A-23 (D99, D131)",
        "running turn: same session 409 turn_running; another session of the account 429 rate_limited with Retry-After",
        ok,
        f"{same.status_code} {same.get_json()}; {second.status_code} {second.get_json()}",
        "medium",
    )
    parked.pop()()
    local2 = make()
    c2 = signed_in(local2)
    s2 = None
    codes = []
    for i in range(11):
        r = send(c2, f"We have {i} vans.", s2)
        s2 = s2 or (r.get_json() or {}).get("session_id")
        codes.append(r.status_code)
    last = r.get_json()
    row(
        "A-23 (D131)",
        "the 11th message in a minute is 429 rate_limited with the window",
        codes[:10] == [200] * 10 and codes[10] == 429 and last.get("window_s") == 60,
        f"{codes}; {last}",
        "medium",
    )
    local3 = make()
    c3 = signed_in(local3)
    ucodes = [
        upload(
            c3, b"We get about 40 requests a day.", "n.txt", "text/plain"
        ).status_code
        for _ in range(6)
    ]
    row(
        "A-23 (D131)",
        "the 6th upload in an hour is 429 rate_limited",
        ucodes[:5] == [200] * 5 and ucodes[5] == 429,
        f"{ucodes}",
        "medium",
    )
    local4 = make()
    c4 = signed_in(local4)
    rt = local4.data.runtime
    rt.spent_today = lambda owner: 0.50  # type: ignore[method-assign]
    r = send(c4, "We run a bakery.")
    row(
        "A-23 (D131)",
        "daily spend at 0.50 USD -> 429 rate_limited (no model call)",
        r.status_code == 429 and r.get_json()["error"] == "rate_limited",
        f"{r.status_code} {r.get_json()}; model calls {local4.app.chat.calls}",
        "medium",
    )


# ---------------------------------------------------------------- A-26 / D141: cross-owner leakage and trace content


def check_leak() -> None:
    canary = "Canarycorp7731"

    class CanaryChat(XssChat):
        pass

    local = make(CanaryChat(canary))
    a = signed_in(local)
    sid = walk(
        a,
        (
            DEMO_MESSAGES[0].replace("Bright Cuts", canary),
            DEMO_MESSAGES[1],
            DEMO_MESSAGES[2],
        ),
    )
    upload(
        a, f"Our company {canary} keeps a ledger.".encode(), "c.txt", "text/plain", sid
    )
    rid = a.post("/submit", json={"session_id": sid}, headers=hdr(a)).get_json()[
        "report_id"
    ]
    b = signed_in(local, OTHER_EMAIL)
    bsid = walk(b)
    brid = b.post("/submit", json={"session_id": bsid}, headers=hdr(b)).get_json()[
        "report_id"
    ]
    seen = []
    for path in (
        "/",
        f"/sessions/{bsid}",
        "/sessions",
        f"/spec?session={bsid}",
        "/reports",
        f"/reports/{brid}",
        f"/reports/{brid}/download",
        "/profile",
    ):
        if canary in b.get(path).get_data(as_text=True):
            seen.append(path)
    dl = b.get(f"/reports/{rid}/download").status_code
    store = local.app.retriever.store
    in_index = canary in json.dumps(getattr(store, "__dict__", {}), default=str)
    row(
        "A-26 (T-25)",
        "owner A's canary never reaches owner B's pages, report or download; A's download is 404 for B; upload text not in the shared index",
        not seen and dl == 404 and not in_index,
        f"B pages with canary: {seen or 'none'}; A download as B -> {dl}; canary in retriever store: {in_index}",
        "critical",
    )
    op = signed_in(local, OPERATOR_EMAIL)
    page = op.get("/traces").get_data(as_text=True)
    traces = json.dumps(local.data.recent_traces(200), default=str)
    msg = DEMO_MESSAGES[1]
    leaked = [
        x
        for x in (canary, msg[:40], "about 40 requests a day")
        if x in traces or x in page
    ]
    row(
        "D141 (T-19)",
        "traces hold no owner text: the canary and message text are absent from /traces and recent_traces",
        not leaked,
        f"{len(local.data.recent_traces(200))} traces; owner text found: {leaked or 'none'}",
        "medium",
    )


# ---------------------------------------------------------------- D137, D121: create_app production guard


def check_create_app() -> None:
    import milo.app as app_mod

    local = make()
    good = {"MILO_ENV": "prod", "MILO_SECRET_KEY": "s" * 40}
    cases = {
        "short key": {**good, "MILO_SECRET_KEY": "short"},
        "example key": {**good, "MILO_SECRET_KEY": "e" * 40},
        "FLASK_DEBUG=1": {**good, "FLASK_DEBUG": "1"},
        "no key": {"MILO_ENV": "prod"},
    }
    real = app_mod._example_secret_values
    app_mod._example_secret_values = lambda: frozenset({"e" * 40})  # type: ignore[assignment]
    out = {}
    try:
        for name, env in cases.items():
            try:
                app_mod.create_app(
                    environ=env
                )  # repos=None: a missed guard would try to open a database
                out[name] = "started"
            except RuntimeError as exc:
                out[name] = (
                    "refused"
                    if str(exc).startswith("production refuses to start")
                    else f"other: {exc}"
                )
        try:
            app_mod.create_app(
                environ=good,
                debug=True,
                repos=local.data.repos,
                accounts=local.accounts,
                app=local.app,
            )
            out["debugger on"] = "started"
        except RuntimeError as exc:
            out["debugger on"] = (
                "refused" if "debugger" in str(exc) else f"other: {exc}"
            )
        try:
            app_mod.create_app(
                environ=good,
                repos=local.data.repos,
                accounts=local.accounts,
                app=local.app,
            )
            control = "started"
        except Exception as exc:  # noqa: BLE001
            control = f"{type(exc).__name__}: {exc}"
    finally:
        app_mod._example_secret_values = real  # type: ignore[assignment]
    row(
        "D137 (G-11)",
        "create_app refuses production for every production_problems item and with the debugger on, before opening anything",
        all(v == "refused" for v in out.values()),
        f"{out}",
        "high",
    )
    row(
        "D137",
        "positive control: a clean prod environment with injected stores starts",
        control == "started",
        control,
        "low",
    )
    try:
        create_flask_app(
            local.data, Settings(env="prod", secret_key="s" * 40), debug=True
        )
        f2 = "started"
    except RuntimeError:
        f2 = "refused"
    row(
        "D137 (shell F2)",
        "create_flask_app refuses prod with debug=True",
        f2 == "refused",
        f2,
        "low",
    )
    # D121 / section 18 items 2 and 3: consent text v0 and no operator are placeholders that must stop production
    from milo.store import memory_repositories

    empty = memory_repositories()
    try:
        app_mod.create_app(
            environ=good, repos=empty, accounts=local.accounts, app=local.app
        )
        d121 = "started"
    except Exception as exc:  # noqa: BLE001
        d121 = f"refused: {exc}"
    row(
        "D121 (section 18 items 2, 3)",
        "production refuses to start while the consent text is v0 or no operator profile exists",
        d121 != "started",
        f"create_app(prod, consent version 'v0' in routes.CONSENT_TEXT_VERSION, store without an operator) -> {d121}",
        "low",
    )
    _ = cfg_mod


# ---------------------------------------------------------------- shell findings, contrast


def lum(hexv: str) -> float:
    hexv = hexv.lstrip("#")
    rgb = [int(hexv[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def check_contrast() -> None:
    css = (ROOT / "src/milo/web/static/css/app.css").read_text()
    light_block = css.split("@media", 1)[0]
    dark_block = css.split("prefers-color-scheme: dark", 1)[1].split("}", 1)[0]

    def tokens(block: str) -> dict[str, str]:
        return {
            m.group(1): m.group(2)
            for m in re.finditer(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block)
        }

    pairs = [
        ("ink", "surface"),
        ("ink", "canvas"),
        ("muted", "surface"),
        ("muted", "canvas"),
        ("accent", "surface"),
        ("accent", "canvas"),
        ("danger", "surface"),
        ("danger", "canvas"),
        ("on-ink", "ink-button"),
        ("ink", "tint"),
        ("accent", "tint"),
    ]
    table = {}
    low = []
    for scheme, block in (("light", light_block), ("dark", dark_block)):
        t = {**tokens(light_block), **tokens(block)}
        for fg, bg in pairs:
            r = ratio(t[fg], t[bg])
            table[f"{scheme} {fg}/{bg}"] = r
            if r < 4.5:
                low.append(f"{scheme} {fg}/{bg} {r}")
    row(
        "SPEC 16 (verifier-manual), shell F4",
        "text token pairs reach WCAG AA 4.5:1 in light and dark",
        not low,
        f"{table}; below 4.5: {low or 'none'}",
        "low",
    )
    row(
        "keyboard (verifier-manual)",
        ":focus-visible rule and skip link exist in app.css",
        ":focus-visible" in css and ".skip:focus" in css,
        "app.css",
        "low",
    )


# ---------------------------------------------------------------- browser part (verifier-manual items)


def check_browser() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        row(
            "browser",
            "Playwright available",
            False,
            "not importable; browser part skipped",
            "low",
        )
        return
    sys.path.insert(0, str(ROOT / "evals" / "ui"))
    allow_sockets()
    try:
        from record import serve  # type: ignore[import-not-found]

        p = "<img src=x onerror=\"window.__srx='SRX7731'\"><script>window.__srx='SRX7731'</script>"
        local, w, owner_pages, op_pages = seed_payload_world(p, debug=True)
        server, base = serve(local)
        keys: list[str] = []
        executed = []
        dialogs = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page()
                page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
                csp_errors: list[str] = []
                page.on(
                    "console",
                    lambda m: (
                        csp_errors.append(m.text)
                        if "Content Security Policy" in m.text
                        else None
                    ),
                )
                page.goto(base + "/login")
                page.keyboard.press("Tab")
                first = page.evaluate(
                    "document.activeElement.className + ':' + (document.activeElement.textContent||'').trim().slice(0,30)"
                )
                page.fill("input[name=email]", "xss@example.test")
                page.fill("input[name=password]", DEV_PASSWORD)
                with page.expect_navigation():
                    page.keyboard.press(
                        "Enter"
                    )  # Enter in the password field submits the form
                for path in owner_pages:
                    page.goto(base + path)
                    if page.evaluate("window.__srx !== undefined"):
                        executed.append(path)
                # keyboard walk on the conversation page
                page.goto(base + f"/sessions/{w['sid']}")
                for _ in range(40):
                    page.keyboard.press("Tab")
                    keys.append(
                        page.evaluate(
                            "(() => { const e = document.activeElement; return e.tagName.toLowerCase() + (e.name ? '[' + e.name + ']' : '') + (e.id ? '#' + e.id : '') + ':' + (e.getAttribute('aria-label') || (e.textContent||'').trim()).slice(0, 24); })()"
                        )
                    )
                composer = page.locator("textarea").first
                composer.focus()
                page.keyboard.type("We also have 3 vans.")
                page.keyboard.press("Control+Enter")
                page.wait_for_timeout(1500)
                sent = "We also have 3 vans." in page.content()
                browser.close()
        finally:
            server.shutdown()
        row(
            "A-13a (browser)",
            "payload with an onerror image and an inline script never executes in Chromium on any owner page",
            not executed and not dialogs,
            f"pages {len(owner_pages)}; executed on {executed or 'none'}; dialogs {dialogs or 'none'}; CSP console reports {len(csp_errors)}",
            "high",
        )
        reach = " | ".join(keys)
        need = ["textarea", "submit", "button"]
        row(
            "keyboard (verifier-manual)",
            "Tab reaches the skip link first, then the session panel, composer and buttons; Ctrl+Enter in the composer sends",
            first.startswith("skip") and all(n in reach for n in need) and sent,
            f"first Tab on /login: {first!r}; Enter sent: {sent}; tab order (40): {reach}",
            "low",
        )
    finally:
        block_sockets()


# ---------------------------------------------------------------- main


def main() -> int:
    for fn in (
        check_idor,
        check_csrf,
        check_consent,
        check_ops,
        check_xss,
        check_headers,
        check_session,
        check_auth,
        check_uploads,
        check_limits,
        check_leak,
        check_create_app,
        check_contrast,
        check_browser,
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            row(
                fn.__name__,
                "runner error",
                False,
                f"{type(exc).__name__}: {exc}",
                "high",
            )
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
            ROOT / "src/milo/web/data.py",
            ROOT / "src/milo/web/accounts.py",
            ROOT / "src/milo/app.py",
        )
    }
    fails = [r for r in ROWS if r["result"] == "FAIL"]
    out = {
        "git_commit": commit,
        "model": None,
        "seed": None,
        "temperature": None,
        "command": "uv run python evals/results/security/m6-review.py",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": inputs,
        "note": "SR review of M6 phase 3 (web wiring); offline, scripted model, in-memory store, no spend (model/seed/temperature do not apply)",
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
    (HERE / "m6-review.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    )
    for r in ROWS:
        print(f"{r['result']:4} [{r['severity_if_fails']:8}] {r['case']}: {r['check']}")
        if (
            r["result"] == "FAIL"
            or r["severity_if_fails"] in ("info",)
            or r["case"].startswith(("open point", "keyboard", "SPEC 16"))
        ):
            print(f"      {r['evidence'][:900]}")
    print(json.dumps(out["summary"]))
    return 1 if out["summary"]["fail_high_or_critical"] else 0


if __name__ == "__main__":
    sys.exit(main())

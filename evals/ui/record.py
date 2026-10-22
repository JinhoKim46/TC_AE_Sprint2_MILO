"""M6 browser walk (SPEC section 16 "M6 checks", D93): DOM checks and a screen recording.

Starts its own local server on 127.0.0.1 with the real web layer, the real discovery App and TurnRunner over the
in-memory store (SPEC D70), in-memory accounts and the scripted demo chat model (`milo.web.dev.DemoChat`): no
model call, no database, no spend. A seeded operator exists; the owner account is created through sign-up and
email confirmation (D93) with a known password. Playwright then drives the owner flow in Chromium:

sign-up, confirm, sign-in, consent, three messages (facts recorded with their evidence), the blocked action (the
owner asks in the conversation to withdraw a submission; the model calls `withdraw_submission`, which needs
approval; up to three attempts, counted), submit, the report, sign-out, sign-in again with the conversation
restored, and the operator's submissions page.

Writes `evals/results/m6/m6_dom_checks.json` (every entry must pass) and `docs/m6_recording.webm`.
Refuses to run when Playwright's Chromium is not installed; it never installs anything.

    uv run python evals/ui/record.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from common import provenance  # noqa: E402

from milo.settings import Settings  # noqa: E402
from milo.web.dev import (  # noqa: E402
    DEMO_MESSAGES,
    DEV_PASSWORD,
    OPERATOR_EMAIL,
    build_local,
)

OUT = ROOT / "evals" / "results" / "m6" / "m6_dom_checks.json"
VIDEO = ROOT / "docs" / "m6_recording.webm"
OWNER = ("walk-owner@example.test", "a-known-walk-password", "Ada")
TURN_TIMEOUT_MS = 20_000
MAX_ATTEMPTS = 3


def serve(local: Any) -> tuple[Any, str]:
    import logging

    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    server = make_server("127.0.0.1", 0, local.flask, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, check: str, ok: bool, detail: Any = "") -> bool:
        self.rows.append(
            {"check": check, "pass": bool(ok), "detail": str(detail)[:300]}
        )
        print(
            ("PASS " if ok else "FAIL ") + check + (f"  ({detail})" if detail else "")
        )
        return bool(ok)


def wait_turn(page: Any, before: int) -> str:
    """Wait for a new assistant bubble whose text is the answer (not the placeholder)."""
    page.wait_for_function(
        "n => { const b = document.querySelectorAll('.msg-assistant .bubble');"
        " return b.length > n && b[b.length - 1].textContent !== '...'; }",
        arg=before,
        timeout=TURN_TIMEOUT_MS,
    )
    page.wait_for_function(
        "() => { const d = document.querySelectorAll('.msg-assistant .drawer');"
        " const last = d[d.length - 1]; return last && /Model cost: \\d/.test(last.textContent); }",
        timeout=TURN_TIMEOUT_MS,
    )
    return str(page.locator(".msg-assistant .bubble").last.text_content())


def send(page: Any, text: str) -> str:
    before = page.locator(".msg-assistant .bubble").count()
    page.fill("#composer-input", text)
    page.click("#composer button[type=submit]")
    return wait_turn(page, before)


def sign_in(page: Any, base: str, email: str, password: str) -> None:
    page.goto(base + "/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    page.click("main form button[type=submit]")
    page.wait_for_load_state()


def walk(page: Any, base: str, local: Any, checks: Checks) -> dict[str, Any]:
    email, password, first = OWNER
    facts: dict[str, Any] = {}
    # sign-up and confirmation (D93)
    page.goto(base + "/signup")
    page.fill("input[name=first_name]", first)
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    page.click("main form button[type=submit]")
    checks.add(
        "signup_check_email", page.locator("h1").text_content() == "Check your email"
    )
    token = next(
        m["token"] for m in reversed(local.accounts.outbox) if m["to"] == email
    )
    page.goto(f"{base}/confirm?token={token}")
    checks.add(
        "email_confirmed", page.locator("h1").text_content() == "Email confirmed"
    )
    # sign-in, consent gate (D81)
    sign_in(page, base, email, password)
    checks.add("consent_gate_after_sign_in", page.url.endswith("/consent"), page.url)
    page.check("input[name=agree]")
    page.click("main form button[type=submit]")
    page.wait_for_load_state()
    checks.add(
        "conversation_layout",
        page.locator(".panel-left").count() == 1
        and page.locator("#spec-panel").count() == 1
        and page.locator(".empty-state").count() == 1,
    )
    checks.add(
        "footer_tagline", "Not a quote" in str(page.locator("footer").text_content())
    )
    # messages, evidence, facts recorded
    for i, text in enumerate(DEMO_MESSAGES):
        answer = send(page, text)
        checks.add(f"message_{i + 1}_answered", bool(answer.strip()), answer[:80])
    company = page.locator('#field-list [data-field="company_name"]')
    checks.add(
        "fact_recorded_in_panel",
        company.locator(".chip-text").text_content() == "reported"
        and "Bright Cuts" in str(company.locator(".field-value").text_content()),
    )
    checks.add(
        "evidence_quote_shown",
        company.locator(".quote").is_visible()
        and "I run Bright Cuts" in str(company.locator(".quote").text_content()),
    )
    page.locator(".msg-assistant .drawer").last.locator("summary").click()
    drawer = str(page.locator(".msg-assistant .drawer").last.text_content())
    checks.add(
        "reply_details_drawer",
        "Progress" in drawer
        and "extract finished" in drawer
        and "Model cost:" in drawer,
    )
    checks.add("estimate_card_shown", page.locator("#estimate-card").is_visible())
    checks.add("submit_enabled_when_ready", page.locator("#submit-open").is_enabled())
    # submit and the report (D41, D40)
    page.click("#submit-open")
    checks.add(
        "submit_confirmation_dialog", page.locator("#submit-dialog").is_visible()
    )
    page.click("#submit-confirm")
    page.wait_for_url("**/reports/*")
    report_id = page.url.rsplit("/", 1)[1]
    sections = page.locator(".report-section h2").all_text_contents()
    checks.add("report_has_ten_sections", len(sections) == 10, len(sections))
    checks.add(
        "report_shows_owner_quote",
        "Bright Cuts" in str(page.locator("main").text_content()),
    )
    facts["report_id"] = report_id
    # the blocked action, asked for in the conversation (SPEC 16 M6 checks)
    page.goto(base + "/")
    attempts = 0
    banner = page.locator("#approval-banner")
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        send(page, f"Please withdraw submission {report_id}.")
        if banner.is_visible():
            break
    facts["withdraw_attempts"] = attempts
    checks.add(
        "blocked_action_pending_approval", banner.is_visible(), f"attempts={attempts}"
    )
    owner_id = local.accounts.users[email]["id"]
    status = local.data.repos.submissions.get(owner_id, int(report_id))["status"]
    checks.add("nothing_changed_before_approval", status == "report_ready", status)
    page.click("[data-approval=keep]")
    page.wait_for_function("() => document.getElementById('approval-banner').hidden")
    status = local.data.repos.submissions.get(owner_id, int(report_id))["status"]
    checks.add("keep_runs_nothing", status == "report_ready", status)
    # sign-out, sign-in again, conversation restored
    count = page.locator(".msg-row").count()
    page.click("header form button[type=submit]")
    page.wait_for_load_state()
    checks.add("signed_out", page.url.endswith("/login"), page.url)
    sign_in(page, base, email, password)
    restored = page.locator(".msg-row").count()
    checks.add(
        "conversation_restored",
        restored >= count - 1 and restored >= 8,
        f"{restored} of {count}",
    )
    checks.add(
        "spec_restored",
        page.locator(
            '#field-list [data-field="company_name"] .chip-text'
        ).text_content()
        == "reported",
    )
    # the operator sees the submission (D42)
    page.click("header form button[type=submit]")
    sign_in(page, base, OPERATOR_EMAIL, DEV_PASSWORD)
    page.goto(base + "/ops/submissions")
    checks.add(
        "operator_sees_submission", email in str(page.locator("main").text_content())
    )
    return facts


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed; nothing recorded.")
        return 2
    local = build_local(settings=Settings(env="test"), debug=True, kb=True)
    local.accounts.add_confirmed(OPERATOR_EMAIL, DEV_PASSWORD, "Ops", role="operator")
    server, base = serve(local)
    checks = Checks()
    facts: dict[str, Any] = {}
    video_dir = Path(tempfile.mkdtemp(prefix="m6-video-"))
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:  # no browser installed: refuse, never install
                print(
                    f"Playwright Chromium is not available ({type(exc).__name__}); nothing recorded."
                )
                return 2
            ctx = browser.new_context(
                record_video_dir=str(video_dir), viewport={"width": 1280, "height": 860}
            )
            page = ctx.new_page()
            try:
                facts = walk(page, base, local, checks)
            except (
                Exception
            ) as exc:  # a failed wait is a failed check, recorded as such
                checks.add("walk_completed", False, f"{type(exc).__name__}: {exc}")
            else:
                checks.add("walk_completed", True)
            video = page.video
            ctx.close()
            browser.close()
            if video is not None:
                VIDEO.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(video.path()), VIDEO)
    finally:
        server.shutdown()
        shutil.rmtree(video_dir, ignore_errors=True)
    result = {
        **provenance(
            model="scripted-demo (milo.web.dev.DemoChat)",
            seed=0,
            temperature=0.0,
            command="uv run python evals/ui/record.py",
            inputs={
                "routes": ROOT / "src/milo/web/routes.py",
                "app_js": ROOT / "src/milo/web/static/js/app.js",
                "dev": ROOT / "src/milo/web/dev.py",
            },
        ),
        "browser": "chromium (playwright, headless)",
        "recording": str(VIDEO.relative_to(ROOT)) if VIDEO.exists() else None,
        "withdraw_attempts": facts.get("withdraw_attempts"),
        "rows": checks.rows,
        "summary": {
            "checks": len(checks.rows),
            "passed": sum(r["pass"] for r in checks.rows),
            "all_pass": all(r["pass"] for r in checks.rows),
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"{result['summary']['passed']} of {result['summary']['checks']} checks passed; wrote {OUT.relative_to(ROOT)}"
    )
    return 0 if result["summary"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

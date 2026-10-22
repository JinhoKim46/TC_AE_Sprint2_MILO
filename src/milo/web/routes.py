"""Routes of SPEC section 16 as amended by D79, D93 and D102 (phase 1a: the shell).

Pages render from the store protocol. Data-bound actions that phase 3 wires return 501 `not_built`.
Order of checks on every request: CSRF (403 `csrf_failed`), then identity, then the operator role
(re-read each request, D144), then the consent gate on writes (D135).
"""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any

from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from milo.settings import Settings

TAGLINE = "Indicative estimate. Not a quote. Non-binding."
CONSENT_TEXT_VERSION = "v0"  # placeholder consent text (D81, D121)
MIN_PASSWORD_LEN = 12
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-ancestors 'none'; form-action 'self'"
)
HSTS = "max-age=31536000"

# endpoints reachable without a session (D93)
PUBLIC = frozenset(
    {
        "static",
        "login",
        "login_post",
        "signup",
        "signup_post",
        "confirm",
        "reset",
        "reset_post",
        "reset_token",
        "reset_token_post",
    }
)
# writes allowed before consent (D135)
CONSENT_EXEMPT = PUBLIC | {"consent_post", "logout"}
OPS = frozenset(
    {
        "traces",
        "ops_submissions",
        "ops_submission",
        "ops_export",
        "ops_audit",
        "ops_customer_delete",
    }
)


def group_sessions(
    rows: list[dict[str, Any]], now: datetime
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Today, Last 7 days, Older; newest first inside each group (D101)."""
    ordered = sorted(rows, key=lambda r: r["updated_at"], reverse=True)
    buckets: dict[str, list[dict[str, Any]]] = {
        "Today": [],
        "Last 7 days": [],
        "Older": [],
    }
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for row in ordered:
        when = row["updated_at"]
        if when >= start:
            buckets["Today"].append(row)
        elif when >= start - timedelta(days=7):
            buckets["Last 7 days"].append(row)
        else:
            buckets["Older"].append(row)
    return [(name, items) for name, items in buckets.items() if items]


def not_built(what: str) -> tuple[Response, int]:
    return jsonify({"error": "not_built", "detail": what}), 501


def create_web(app: Any, store: Any, settings: Settings) -> Blueprint:
    bp = Blueprint(
        "web",
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )

    def endpoint() -> str:
        return (request.endpoint or "").removeprefix("web.")

    def wants_json() -> bool:
        return request.method != "GET" or request.path.startswith("/turns/")

    def csrf_token() -> str:
        token = session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf"] = token
        return str(token)

    def regenerate_session(user_id: str | None) -> None:
        """New session contents at sign-in and sign-out (D137)."""
        session.clear()
        if user_id:
            session["uid"] = user_id
        session["csrf"] = secrets.token_urlsafe(32)

    @bp.before_request
    def gate() -> Any:
        ep = endpoint()
        if not ep:
            return None
        if request.method not in SAFE_METHODS:
            sent = request.headers.get("X-CSRF-Token") or request.form.get(
                "csrf_token", ""
            )
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(str(sent), str(expected)):
                return jsonify({"error": "csrf_failed"}), 403
        if ep == "static":
            return None
        uid = session.get("uid")
        g.user = (
            store.get_user(uid) if uid else None
        )  # role re-read every request (D144)
        if uid and g.user is None:
            session.clear()
        if ep in PUBLIC:
            return None
        if ep in OPS:
            if g.user is None or g.user.get("role") != "operator":
                abort(404)
        elif g.user is None:
            if wants_json():
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("web.login"))
        if (
            request.method not in SAFE_METHODS
            and ep not in CONSENT_EXEMPT
            and not store.has_consent(g.user["id"], "interview")
        ):
            return jsonify({"error": "consent_required"}), 403
        return None

    @bp.after_app_request
    def headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if settings.env == "prod":
            resp.headers["Strict-Transport-Security"] = HSTS
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @bp.app_context_processor
    def inject() -> dict[str, Any]:
        return {
            "csrf_token": csrf_token,
            "tagline": TAGLINE,
            "user": getattr(g, "user", None),
        }

    def page(template: str, status: int = 200, **ctx: Any) -> Any:
        return render_template(template, **ctx), status

    def panel_context(user_id: str, current: str | None = None) -> dict[str, Any]:
        q = request.args.get("q", "").strip()
        show_archived = request.args.get("archived") == "1"
        rows = store.list_sessions(user_id, query=q, archived=show_archived)
        return {
            "groups": group_sessions(rows, datetime.now()),
            "query": q,
            "show_archived": show_archived,
            "current_id": current,
        }

    def conversation(session_id: str | None) -> Any:
        uid = g.user["id"]
        ctx = panel_context(uid, session_id)
        messages: list[dict[str, Any]] = []
        spec = None
        title = "New diagnosis"
        if session_id:
            row = store.get_session(uid, session_id)
            if row is None:
                abort(404)
            title = row["title"]
            messages = store.list_messages(uid, session_id)
            spec = store.get_spec(uid, session_id)
        return page(
            "conversation.html",
            title=title,
            session_id=session_id,
            messages=messages,
            spec=spec,
            suggestions=SUGGESTIONS,
            **ctx,
        )

    # ---- auth (D93) ----

    @bp.get("/login")
    def login() -> Any:
        return page("login.html", title="Sign in")

    @bp.post("/login")
    def login_post() -> Any:
        uid = store.authenticate(
            request.form.get("email", "").strip(), request.form.get("password", "")
        )
        if uid is None:
            return page(
                "login.html",
                401,
                title="Sign in",
                error="Email or password is not right.",
            )
        regenerate_session(uid)
        return redirect(url_for("web.index"))

    @bp.post("/logout")
    def logout() -> Any:
        regenerate_session(None)
        session.pop("csrf", None)
        return redirect(url_for("web.login"))

    @bp.get("/signup")
    def signup() -> Any:
        return page("signup.html", title="Create account")

    @bp.post("/signup")
    def signup_post() -> Any:
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        first = request.form.get("first_name", "").strip()
        if "@" not in email or not first or len(password) < MIN_PASSWORD_LEN:
            return page(
                "signup.html",
                400,
                title="Create account",
                error=f"Enter a name, an email and a password of at least {MIN_PASSWORD_LEN} characters.",
            )
        store.sign_up(email, password, first)
        # the same reply whether or not the address exists
        return page("confirm.html", title="Check your email", sent=True)

    @bp.get("/confirm")
    def confirm() -> Any:
        token = request.args.get("token", "")
        ok = bool(token) and store.confirm(token)
        return page(
            "confirm.html",
            200 if ok else 400,
            title="Confirm email",
            confirmed=ok,
            sent=False,
        )

    @bp.get("/reset")
    def reset() -> Any:
        return page("reset.html", title="Reset password")

    @bp.post("/reset")
    def reset_post() -> Any:
        store.request_reset(request.form.get("email", "").strip())
        return page("reset.html", title="Reset password", sent=True)

    @bp.get("/reset/<token>")
    def reset_token(token: str) -> Any:
        return page("reset_token.html", title="Set a new password", token=token)

    @bp.post("/reset/<token>")
    def reset_token_post(token: str) -> Any:
        password = request.form.get("password", "")
        if len(password) < MIN_PASSWORD_LEN or not store.reset_password(
            token, password
        ):
            return page(
                "reset_token.html",
                400,
                title="Set a new password",
                token=token,
                error="That link is not valid, or the password is too short.",
            )
        return redirect(url_for("web.login"))

    # ---- consent (D81) ----

    @bp.get("/consent")
    def consent() -> Any:
        return page(
            "consent.html", title="Before we start", version=CONSENT_TEXT_VERSION
        )

    @bp.post("/consent")
    def consent_post() -> Any:
        if request.form.get("agree") != "yes" and not (
            request.is_json and (request.get_json(silent=True) or {}).get("agree")
        ):
            return jsonify({"error": "invalid_input"}), 400
        store.record_consent(g.user["id"], "interview", CONSENT_TEXT_VERSION)
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("web.index"))

    # ---- conversation and sessions (D101, D102) ----

    @bp.get("/")
    def index() -> Any:
        if not store.has_consent(g.user["id"], "interview"):
            return redirect(url_for("web.consent"))
        rows = store.list_sessions(g.user["id"])
        return conversation(rows[0]["id"] if rows else None)

    @bp.get("/sessions")
    def sessions() -> Any:
        return page("sessions.html", title="Diagnoses", **panel_context(g.user["id"]))

    @bp.post("/sessions")
    def sessions_new() -> Any:
        return not_built("new session")

    @bp.get("/sessions/<session_id>")
    def session_open(session_id: str) -> Any:
        return conversation(session_id)

    @bp.patch("/sessions/<session_id>")
    def session_rename(session_id: str) -> Any:
        return not_built("rename session")

    @bp.post("/sessions/<session_id>/archive")
    def session_archive(session_id: str) -> Any:
        return not_built("archive session")

    @bp.post("/sessions/<session_id>/restore")
    def session_restore(session_id: str) -> Any:
        return not_built("restore session")

    # ---- data-bound actions (phase 3) ----

    @bp.post("/messages")
    def messages() -> Any:
        return not_built("messages")

    @bp.get("/turns/<turn_id>/events")
    def turn_events(turn_id: str) -> Any:
        return not_built("turn events")

    @bp.post("/upload")
    def upload() -> Any:
        return not_built("upload")

    @bp.post("/submit")
    def submit() -> Any:
        return not_built("submit")

    @bp.post("/approvals/<approval_id>")
    def approvals(approval_id: str) -> Any:
        return not_built("approvals")

    @bp.post("/withdraw/<submission_id>")
    def withdraw(submission_id: str) -> Any:
        return not_built("withdraw")

    @bp.post("/spec/<field_id>/request")
    def spec_request(field_id: str) -> Any:
        return not_built("field request")

    @bp.post("/profile/delete-request")
    def profile_delete_request() -> Any:
        return not_built("deletion request")

    @bp.get("/reports/<report_id>/download")
    def report_download(report_id: str) -> Any:
        if store.get_report(g.user["id"], report_id) is None:
            abort(404)
        return not_built("report download")

    # ---- owner pages ----

    @bp.get("/spec")
    def spec_page() -> Any:
        rows = store.list_sessions(g.user["id"])
        spec = store.get_spec(g.user["id"], rows[0]["id"]) if rows else None
        return page("spec.html", title="Specification review", spec=spec)

    @bp.get("/reports")
    def reports() -> Any:
        return page(
            "reports.html", title="Reports", reports=store.list_reports(g.user["id"])
        )

    @bp.get("/reports/<report_id>")
    def report_view(report_id: str) -> Any:
        report = store.get_report(g.user["id"], report_id)
        if report is None:
            abort(404)
        return page("report.html", title=report["title"], report=report)

    @bp.get("/profile")
    def profile() -> Any:
        return page(
            "profile.html", title="Profile", consents=store.list_consents(g.user["id"])
        )

    @bp.get("/profile/consents")
    def profile_consents() -> Any:
        return page(
            "profile_consents.html",
            title="Consent history",
            consents=store.list_consents(g.user["id"]),
        )

    # ---- operator (D144) ----

    @bp.get("/traces")
    def traces() -> Any:
        return page("traces.html", title="Traces", traces=app.recent_traces(50))

    @bp.get("/ops/submissions")
    def ops_submissions() -> Any:
        return page(
            "ops_submissions.html", title="Submissions", rows=store.ops_submissions()
        )

    @bp.get("/ops/submissions/<submission_id>")
    def ops_submission(submission_id: str) -> Any:
        row = store.ops_submission(submission_id)
        if row is None:
            abort(404)
        return page("ops_submission.html", title="Submission", row=row)

    @bp.get("/ops/export/<submission_id>.json")
    def ops_export(submission_id: str) -> Any:
        return not_built("export")

    @bp.get("/ops/audit")
    def ops_audit() -> Any:
        return page("ops_audit.html", title="Audit log", rows=store.ops_audit())

    @bp.post("/ops/customers/<customer_id>/delete")
    def ops_customer_delete(customer_id: str) -> Any:
        return not_built("customer delete")

    # ---- errors ----

    def error_page(status: int, heading: str, text: str) -> Any:
        if request.headers.get("X-Requested-With") == "fetch" or request.is_json:
            return jsonify({"error": heading.lower().replace(" ", "_")}), status
        return page(
            "error.html", status, title=heading, heading=heading, text=text, code=status
        )

    @bp.app_errorhandler(404)
    def not_found(_: Exception) -> Any:
        return error_page(
            404, "Page not found", "That page does not exist, or it is not yours."
        )

    @bp.app_errorhandler(403)
    def forbidden(_: Exception) -> Any:
        return error_page(403, "Not allowed", "You cannot do that.")

    @bp.app_errorhandler(405)
    def not_allowed(_: Exception) -> Any:
        return error_page(405, "Not allowed", "That action is not available here.")

    @bp.app_errorhandler(500)
    def server_error(_: Exception) -> Any:
        return error_page(500, "Something went wrong", "Please try again in a moment.")

    return bp


SUGGESTIONS = (
    "Describe what your company does",
    "Tell me about the process you want to improve",
    "Which tools and data do you use today?",
)


def create_flask_app(
    app: Any, store: Any, settings: Settings, *, debug: bool = False
) -> Flask:
    """A Flask app with the web blueprint registered. Cookie flags follow D137."""
    flask_app = Flask(__name__)
    key = settings.secret_key or (
        "" if settings.env == "prod" else secrets.token_hex(32)
    )
    if not key:
        raise RuntimeError("MILO_SECRET_KEY is required in production")
    flask_app.config.update(
        SECRET_KEY=key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not debug,
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        TESTING=False,
    )
    flask_app.debug = debug
    flask_app.jinja_env.autoescape = True
    flask_app.register_blueprint(create_web(app, store, settings))
    return flask_app

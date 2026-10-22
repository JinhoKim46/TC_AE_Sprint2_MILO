"""Routes of SPEC section 16 as amended by D79, D93, D102 (phase 3: wired to the App, the turn runner and the
repositories through `milo.web.data.WebData`).

Order of checks on every request: CSRF (403 `csrf_failed`), then identity (the session's tokens are verified on
every request, D88), then the operator role (re-read each request, D144; 404 for anyone else), then the consent
gate on every write (403 `consent_required`, D135). Errors map one way everywhere: `NotFound` 404 `not_found`,
`InvalidInput` 400, `PolicyDenied` 403, a busy session 409 `turn_running`, a limit 429 `rate_limited` (D131).
"""

from __future__ import annotations

import hmac
import io
import json
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

from milo.auth.errors import AuthError, RateLimited
from milo.errors import InvalidInput, MiloError, NotFound, PolicyDenied
from milo.settings import Settings
from milo.web.data import TurnBusy, WebData, label

TAGLINE = "Indicative estimate. Not a quote. Non-binding."
CONSENT_TEXT_VERSION = "v0"  # placeholder consent text (D81, D121)
MIN_PASSWORD_LEN = 12
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # D46, D132: the file itself
MULTIPART_MARGIN = 64 * 1024  # form fields and multipart framing around the file
MAX_PDF_PAGES = 50  # D132
MAX_UPLOAD_TEXT = (
    200_000  # characters handed on; the chain keeps 20,000 and reports the rest (D132)
)
UPLOAD_DEFAULT_TEXT = "I attached a document about how we work."

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
# one JSON error code per status (verify m6-shell F1)
ERROR_CODES = {
    400: "invalid_input",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "turn_running",
    413: "too_large",
    429: "rate_limited",
    500: "internal",
}


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
        if when.tzinfo is not None and start.tzinfo is None:
            when = when.astimezone().replace(tzinfo=None)
        if when >= start:
            buckets["Today"].append(row)
        elif when >= start - timedelta(days=7):
            buckets["Last 7 days"].append(row)
        else:
            buckets["Older"].append(row)
    return [(name, items) for name, items in buckets.items() if items]


def pdf_text(data: bytes) -> str:
    """Text of a PDF, parsed in memory (D132): at most 50 pages; nothing is written to disk."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise InvalidInput("too_many_pages")
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise InvalidInput("unreadable_pdf") from exc


def upload_text(data: bytes, filename: str, mimetype: str) -> str:
    """Decoded text of an upload (D46): PDF by its signature, else UTF-8 text with a text MIME or `.txt` name."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidInput("file_too_large")
    if not data:
        raise InvalidInput("empty_file")
    if data.startswith(b"%PDF-"):
        text = pdf_text(data)
    elif mimetype == "text/plain" or filename.lower().endswith(".txt"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidInput("not_utf8") from exc
    else:
        raise InvalidInput("mime_not_allowed")
    text = text.replace("\x00", "")
    if not text.strip():
        raise InvalidInput("no_text")
    return text[:MAX_UPLOAD_TEXT]


def create_web(data: WebData, settings: Settings) -> Blueprint:
    bp = Blueprint(
        "web",
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    accounts = data.accounts

    def endpoint() -> str:
        return (request.endpoint or "").removeprefix("web.")

    def wants_json() -> bool:
        return (
            request.method != "GET"
            or request.path.startswith("/turns/")
            or request.headers.get("X-Requested-With") == "fetch"
            or request.is_json
        )

    def is_fetch() -> bool:
        return request.headers.get("X-Requested-With") == "fetch" or request.is_json

    def body() -> dict[str, Any]:
        if request.is_json:
            got = request.get_json(silent=True)
            return got if isinstance(got, dict) else {}
        return dict(request.form)

    def csrf_token() -> str:
        token = session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf"] = token
        return str(token)

    def regenerate_session(tokens: dict[str, str] | None) -> None:
        """New session contents at sign-in and sign-out (D137). Only the tokens are kept, never the user id."""
        session.clear()
        if tokens:
            session["auth"] = tokens
        session["csrf"] = secrets.token_urlsafe(32)

    def client_ip() -> str:
        return request.remote_addr or "unknown"

    def uid() -> str:
        return str(g.user["id"])

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
        g.user = None
        tokens = session.get("auth")
        if isinstance(tokens, dict):
            found = accounts.resolve(tokens)
            if found is None:
                session.pop("auth", None)
            else:
                user_id, fresh = found
                if fresh:
                    session["auth"] = fresh
                g.user = data.get_user(user_id)  # role re-read every request (D144)
                if g.user is None:
                    session.pop("auth", None)
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
            and not data.has_consent(uid())
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
            "label": label,
        }

    def page(template: str, status: int = 200, **ctx: Any) -> Any:
        return render_template(template, **ctx), status

    def panel_context(user_id: str, current: str | None = None) -> dict[str, Any]:
        q = request.args.get("q", "").strip()[:120]
        show_archived = request.args.get("archived") == "1"
        rows = data.list_sessions(user_id, query=q, archived=show_archived)
        return {
            "groups": group_sessions(rows, datetime.now()),
            "query": q,
            "show_archived": show_archived,
            "current_id": current,
        }

    def newest_session(user_id: str) -> str | None:
        rows = data.list_sessions(user_id)
        return str(rows[0]["id"]) if rows else None

    def conversation(session_id: str | None) -> Any:
        user_id = uid()
        messages: list[dict[str, Any]] = []
        spec = None
        title = "New diagnosis"
        if session_id:
            row = data.get_session(user_id, session_id)
            if row is None:
                abort(404)
            session_id = str(row["id"])
            title = row["title"]
            messages = data.list_messages(user_id, session_id)
            spec = data.get_spec(user_id, session_id)
        return page(
            "conversation.html",
            title=title,
            session_id=session_id,
            messages=messages,
            spec=spec,
            suggestions=SUGGESTIONS,
            **panel_context(user_id, session_id),
        )

    def done(target: str, payload: dict[str, Any] | None = None) -> Any:
        """JSON for a fetch call, a redirect for a plain form post."""
        if is_fetch():
            return jsonify(payload or {"ok": True})
        return redirect(target)

    # ---- auth (D93) ----

    @bp.get("/login")
    def login() -> Any:
        return page("login.html", title="Sign in")

    @bp.post("/login")
    def login_post() -> Any:
        try:
            tokens = accounts.login(
                request.form.get("email", "").strip(),
                request.form.get("password", ""),
                client_ip(),
            )
        except RateLimited:
            return page(
                "login.html",
                429,
                title="Sign in",
                error="Too many attempts. Please wait a little and try again.",
            )
        except (AuthError, InvalidInput):
            return page(
                "login.html",
                401,
                title="Sign in",
                error="Email or password is not right.",
            )
        regenerate_session(tokens)
        found = accounts.resolve(tokens)
        if found is not None and data.has_consent(found[0]):
            if newest_session(found[0]) is None:
                data.create_session(found[0])  # sign-in creates the session (SPEC 16)
        return redirect(url_for("web.index"))

    @bp.post("/logout")
    def logout() -> Any:
        tokens = session.get("auth")
        if isinstance(tokens, dict):
            accounts.logout(tokens)
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
        first = request.form.get("first_name", "").strip()[:80]
        error = f"Enter a name, an email and a password of at least {MIN_PASSWORD_LEN} characters."
        if "@" not in email or not first or len(password) < MIN_PASSWORD_LEN:
            return page("signup.html", 400, title="Create account", error=error)
        try:
            accounts.sign_up(email, password, first, client_ip())
        except InvalidInput:
            return page("signup.html", 400, title="Create account", error=error)
        except RateLimited:
            return page(
                "signup.html",
                429,
                title="Create account",
                error="Too many attempts. Please wait a little and try again.",
            )
        # the same reply whether or not the address exists (D92)
        return page("confirm.html", title="Check your email", sent=True)

    @bp.get("/confirm")
    def confirm() -> Any:
        token = request.args.get("token", "") or request.args.get("token_hash", "")
        ok = bool(token) and accounts.confirm(token)
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
        try:
            accounts.request_reset(request.form.get("email", "").strip(), client_ip())
        except (InvalidInput, RateLimited):
            pass  # the same page either way (D92)
        return page("reset.html", title="Reset password", sent=True)

    @bp.get("/reset/<token>")
    def reset_token(token: str) -> Any:
        return page("reset_token.html", title="Set a new password", token=token)

    @bp.post("/reset/<token>")
    def reset_token_post(token: str) -> Any:
        password = request.form.get("password", "")
        try:
            ok = len(password) >= MIN_PASSWORD_LEN and accounts.complete_reset(
                token, password
            )
        except InvalidInput:
            ok = False
        if not ok:
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
        data.record_consent(uid(), CONSENT_TEXT_VERSION)
        if newest_session(uid()) is None:
            data.create_session(uid())
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("web.index"))

    # ---- conversation and sessions (D101, D102) ----

    @bp.get("/")
    def index() -> Any:
        if not data.has_consent(uid()):
            return redirect(url_for("web.consent"))
        return conversation(newest_session(uid()))

    @bp.get("/sessions")
    def sessions() -> Any:
        return page("sessions.html", title="Diagnoses", **panel_context(uid()))

    @bp.post("/sessions")
    def sessions_new() -> Any:
        sid = data.create_session(uid())
        return done(url_for("web.session_open", session_id=sid), {"id": sid})

    @bp.get("/sessions/<session_id>")
    def session_open(session_id: str) -> Any:
        return conversation(session_id)

    @bp.patch("/sessions/<session_id>")
    def session_rename(session_id: str) -> Any:
        row = data.rename_session(uid(), session_id, str(body().get("title", "")))
        return jsonify({"id": row["id"], "title": row["title"]})

    @bp.post("/sessions/<session_id>/rename")
    def session_rename_form(session_id: str) -> Any:
        row = data.rename_session(uid(), session_id, str(body().get("title", "")))
        return done(
            url_for("web.session_open", session_id=row["id"]),
            {"id": row["id"], "title": row["title"]},
        )

    @bp.post("/sessions/<session_id>/archive")
    def session_archive(session_id: str) -> Any:
        row = data.archive_session(uid(), session_id)
        return done(
            url_for("web.sessions", archived=1),
            {"id": row["id"], "status": row["status"]},
        )

    @bp.post("/sessions/<session_id>/restore")
    def session_restore(session_id: str) -> Any:
        row = data.restore_session(uid(), session_id)
        return done(
            url_for("web.session_open", session_id=row["id"]),
            {"id": row["id"], "status": row["status"]},
        )

    # ---- turns (D47, D99, D131) ----

    def session_for_write(given: Any) -> str:
        sid = str(given or "").strip()
        if sid:
            if data.get_session(uid(), sid) is None:
                raise NotFound()
            return sid
        return newest_session(uid()) or data.create_session(uid())

    @bp.post("/messages")
    def messages() -> Any:
        got = body()
        sid = session_for_write(got.get("session_id"))
        turn_id = data.send(uid(), sid, str(got.get("text", "")))
        if not is_fetch():
            return redirect(url_for("web.session_open", session_id=sid))
        return jsonify({"turn_id": turn_id, "session_id": sid})

    @bp.get("/turns/<turn_id>/events")
    def turn_events(turn_id: str) -> Any:
        try:
            after = max(0, int(request.args.get("after", "0")))
        except ValueError:
            raise InvalidInput("after") from None
        return jsonify(data.events(uid(), turn_id, after))

    @bp.post("/upload")
    def upload() -> Any:
        file = request.files.get("file")
        if file is None:
            raise InvalidInput("no_file")
        raw = file.stream.read(MAX_UPLOAD_BYTES + 1)
        text = upload_text(raw, file.filename or "", file.mimetype or "")
        sid = session_for_write(request.form.get("session_id"))
        message = request.form.get("text", "").strip() or UPLOAD_DEFAULT_TEXT
        turn_id = data.send(uid(), sid, message, upload=text.encode("utf-8"))
        return jsonify({"turn_id": turn_id, "session_id": sid})

    # ---- submit, approvals, withdraw (D41, section 8) ----

    @bp.post("/submit")
    def submit() -> Any:
        got = body()
        sid = str(got.get("session_id") or "").strip() or newest_session(uid())
        if not sid:
            raise NotFound()
        report_id = data.submit(uid(), sid)
        return done(
            url_for("web.report_view", report_id=report_id), {"report_id": report_id}
        )

    @bp.post("/approvals/<approval_id>")
    def approvals(approval_id: str) -> Any:
        decision = str(body().get("decision", ""))
        return jsonify(data.resolve_approval(uid(), approval_id, decision))

    @bp.post("/withdraw/<submission_id>")
    def withdraw(submission_id: str) -> Any:
        status = data.withdraw(uid(), submission_id)
        return done(
            url_for("web.report_view", report_id=submission_id), {"status": status}
        )

    # ---- specification, profile ----

    @bp.get("/spec")
    def spec_page() -> Any:
        sid = request.args.get("session") or newest_session(uid())
        row = data.get_session(uid(), sid) if sid else None
        if sid and row is None:
            abort(404)
        spec = data.get_spec(uid(), str(row["id"])) if row else None
        return page(
            "spec.html",
            title="Specification review",
            spec=spec,
            session=row,
            requested=request.args.get("requested") == "1",
        )

    @bp.post("/spec/<field_id>/request")
    def spec_request(field_id: str) -> Any:
        got = body()
        sid = str(got.get("session_id") or "").strip() or None
        data.field_request(uid(), field_id, str(got.get("text", "")), sid)
        target = (
            url_for("web.spec_page", session=sid, requested=1)
            if sid
            else url_for("web.spec_page", requested=1)
        )
        return done(target)

    @bp.get("/profile")
    def profile() -> Any:
        return page(
            "profile.html",
            title="Profile",
            consents=data.consents(uid()),
            info=data.profile(uid()),
            requested=request.args.get("requested") == "1",
        )

    @bp.get("/profile/consents")
    def profile_consents() -> Any:
        return page(
            "profile_consents.html",
            title="Consent history",
            consents=data.consents(uid()),
        )

    @bp.post("/profile/delete-request")
    def profile_delete_request() -> Any:
        data.deletion_request(uid(), str(body().get("reason", "")))
        return done(url_for("web.profile", requested=1))

    # ---- reports (D40; M5c owns the JSON, M6 renders it) ----

    @bp.get("/reports")
    def reports() -> Any:
        return page("reports.html", title="Reports", reports=data.list_reports(uid()))

    @bp.get("/reports/<report_id>")
    def report_view(report_id: str) -> Any:
        report = data.get_report(uid(), report_id)
        if report is None:
            abort(404)
        return page("report.html", title=report["title"], report=report)

    @bp.get("/reports/<report_id>/download")
    def report_download(report_id: str) -> Any:
        report = data.get_report(uid(), report_id)
        if report is None or report["json"] is None:
            abort(404)
        return json_file(report["json"], f"milo-report-{report['id']}.json")

    def json_file(payload: Any, name: str) -> Response:
        resp = Response(
            json.dumps(payload, indent=2, default=str), mimetype="application/json"
        )
        resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return resp

    # ---- operator (D144) ----

    @bp.get("/traces")
    def traces() -> Any:
        return page("traces.html", title="Traces", traces=data.recent_traces(50))

    @bp.get("/ops/submissions")
    def ops_submissions() -> Any:
        return page(
            "ops_submissions.html", title="Submissions", rows=data.ops_submissions()
        )

    @bp.get("/ops/submissions/<submission_id>")
    def ops_submission(submission_id: str) -> Any:
        row = data.ops_submission(submission_id)
        if row is None:
            abort(404)
        return page("ops_submission.html", title="Submission", row=row)

    @bp.get("/ops/export/<submission_id>.json")
    def ops_export(submission_id: str) -> Any:
        row = data.ops_submission(submission_id)
        if row is None:
            abort(404)
        return json_file(row, f"milo-submission-{row['id']}.json")

    @bp.get("/ops/audit")
    def ops_audit() -> Any:
        return page("ops_audit.html", title="Audit log", rows=data.ops_audit())

    @bp.post("/ops/customers/<customer_id>/delete")
    def ops_customer_delete(customer_id: str) -> Any:
        data.ops_delete_customer(uid(), customer_id)
        return done(url_for("web.ops_submissions"))

    # ---- errors ----

    def error_page(status: int, heading: str, text: str) -> Any:
        if wants_json():
            return jsonify({"error": ERROR_CODES.get(status, "error")}), status
        return page(
            "error.html", status, title=heading, heading=heading, text=text, code=status
        )

    @bp.app_errorhandler(MiloError)
    def milo_error(exc: MiloError) -> Any:
        if isinstance(exc, NotFound):
            return error_page(
                404, "Page not found", "That page does not exist, or it is not yours."
            )
        if isinstance(exc, RateLimited):
            resp = jsonify(
                {
                    "error": "rate_limited",
                    "scope": exc.scope,
                    "window_s": exc.window_s,
                    "retry_after_s": exc.retry_after_s,
                }
            )
            resp.headers["Retry-After"] = str(exc.retry_after_s)
            return resp, 429
        if isinstance(exc, TurnBusy):
            return jsonify({"error": "turn_running"}), 409
        if isinstance(exc, InvalidInput):
            return jsonify({"error": exc.error}), 400
        if isinstance(exc, PolicyDenied):
            return jsonify({"error": exc.error}), 403
        if isinstance(exc, AuthError):
            return jsonify({"error": "unauthorized"}), 401
        return error_page(500, "Something went wrong", "Please try again in a moment.")

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

    @bp.app_errorhandler(413)
    def too_large(_: Exception) -> Any:
        return error_page(413, "Too large", "That file is larger than 5 MB.")

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
    data: WebData, settings: Settings, *, debug: bool = False
) -> Flask:
    """A Flask app with the web blueprint registered. Cookie flags follow D137."""
    if settings.env == "prod" and debug:
        raise RuntimeError("production refuses to start with the debugger on (D137)")
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
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + MULTIPART_MARGIN,
        TESTING=False,
    )
    flask_app.debug = debug
    flask_app.jinja_env.autoescape = True
    flask_app.register_blueprint(create_web(data, settings))
    return flask_app

"""Settings. The only module that reads the database connection variables (SPEC D127, HARNESS rule 3).

Nothing here reads `.env` or opens a connection; values come from the process environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from milo.errors import ProdGuardError

# Connection variable names (SPEC D127, D153). Other modules go through the functions below.
# The session-pooler strings are the owner's administrative strings (role `postgres`): migrate,
# reset and the verifier's RLS test only. The app and operator strings are set by the owner after
# the role passwords exist (owner checklist item 10).
TEST_DB_VAR = "SUPABASE_test_session_pooler"
PROD_DB_VAR = "SUPABASE_prod_session_pooler"
TEST_APP_DB_VAR = "SUPABASE_test_app_pooler"
PROD_APP_DB_VAR = "SUPABASE_prod_app_pooler"
PROD_OPERATOR_DB_VAR = "SUPABASE_prod_operator_pooler"

# Per-account limits (SPEC D131). A breach returns 429 `rate_limited` with the window.
ACCOUNT_DAILY_SPEND_USD = 0.50
ACCOUNT_RUNNING_TURNS = 1
ACCOUNT_MESSAGES_PER_MINUTE = 10
ACCOUNT_UPLOADS_PER_HOUR = 5

# Contract limits (SPEC D2, D18, D23a).
MIN_SCORE = 0.30
MAX_MODEL_CALLS_PER_TURN = 4
RESERVE_USD = 1.00
CALL_TIMEOUT_S = 30.0
TURN_DEADLINE_S = 120.0
TURN_DEADLINE_ADVANCED_RAG_S = 180.0
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = (0.5, 1.0)
SECRET_KEY_MIN_BYTES = 32


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def is_prod(environ: Mapping[str, str] | None = None) -> bool:
    return _env(environ).get("MILO_ENV", "").strip().lower() == "prod"


def prod_guard_violations(environ: Mapping[str, str] | None = None) -> list[str]:
    """Names of production settings present in this process (SPEC D76, D127). Names only, never values."""
    env = _env(environ)
    found = []
    if is_prod(env):
        found.append("MILO_ENV=prod")
    if any(env.get(v) for v in (PROD_DB_VAR, PROD_APP_DB_VAR, PROD_OPERATOR_DB_VAR)):
        found.append("prod database variable")
    return found


def assert_not_prod(environ: Mapping[str, str] | None = None) -> None:
    """Raise when a test or eval process was given production settings."""
    found = prod_guard_violations(environ)
    if found:
        raise ProdGuardError(
            "refusing to run with production settings: " + ", ".join(found)
        )


def _url(env: Mapping[str, str], var: str) -> str:
    url = env.get(var, "")
    if not url:
        raise RuntimeError(f"database URL variable {var} is not set (see .env.example)")
    return url


def admin_database_url(environ: Mapping[str, str] | None = None) -> str:
    """The owner's session-pooler string (role `postgres`): migrate, reset and the RLS test only (D153)."""
    env = _env(environ)
    return _url(env, PROD_DB_VAR if is_prod(env) else TEST_DB_VAR)


def app_database_url(environ: Mapping[str, str] | None = None) -> str:
    """The `milo_app` string for `create_app` (D153): prod only when `MILO_ENV=prod`."""
    env = _env(environ)
    return _url(env, PROD_APP_DB_VAR if is_prod(env) else TEST_APP_DB_VAR)


def operator_database_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The `milo_operator` string (D153). Prod only; in test the operator path uses `SET ROLE`."""
    env = _env(environ)
    return _url(env, PROD_OPERATOR_DB_VAR) if is_prod(env) else None


def database_url(environ: Mapping[str, str] | None = None) -> str:
    """Kept for S0 callers; the administrative string (D153). New code uses the accessors above."""
    return admin_database_url(environ)


def production_problems(
    environ: Mapping[str, str] | None = None,
    example_values: frozenset[str] = frozenset(),
) -> list[str]:
    """Reasons production must refuse to start (SPEC D137). Empty when nothing is wrong."""
    env = _env(environ)
    problems = []
    key = env.get("MILO_SECRET_KEY", "")
    if len(key.encode()) < SECRET_KEY_MIN_BYTES:
        problems.append("MILO_SECRET_KEY shorter than 32 bytes")
    if key and key in example_values:
        problems.append("MILO_SECRET_KEY equals an example value")
    if env.get("FLASK_DEBUG", "").strip() not in ("", "0", "false", "False"):
        problems.append("FLASK_DEBUG is set")
    return problems


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = ""
    budget_usd: float = 0.0
    secret_key: str = ""
    advanced_rag: bool = False
    final: bool = False
    env: str = "test"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = _env(environ)
        try:
            budget = float(env.get("MILO_BUDGET_USD", "0") or 0)
        except ValueError:
            budget = 0.0
        return cls(
            openrouter_api_key=env.get("OPENROUTER_API_KEY", ""),
            budget_usd=budget,
            secret_key=env.get("MILO_SECRET_KEY", ""),
            advanced_rag=env.get("MILO_ADVANCED_RAG", "0") == "1",
            final=env.get("MILO_FINAL", "") == "1",
            env="prod" if is_prod(env) else "test",
        )

    @property
    def turn_deadline_s(self) -> float:
        return TURN_DEADLINE_ADVANCED_RAG_S if self.advanced_rag else TURN_DEADLINE_S

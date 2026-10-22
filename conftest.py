"""Root conftest (integrator-owned, SPEC 11.7, D76, D127).

- Wires the kit's contract tests to MILO: `PROJECT_APP` defaults to `milo.app:build_app`.
- Refuses to run unless `PROJECT_APP` names a `milo.` module, so a plain `pytest contract` can never test the
  kit's toy app. `pytest.UsageError` makes the run exit non-zero (an xfail marker would exit 0).
- Production guard: a test process that holds production settings is refused (the names live in `milo.settings`).
- Sockets stay blocked: `--disable-socket` comes from `pytest.ini`, and a run that removes it is refused.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("PROJECT_APP", "milo.app:build_app")

from milo import settings  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    found = settings.prod_guard_violations()
    if found:
        raise pytest.UsageError(
            "tests must not run with production settings: " + ", ".join(found)
        )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    app = os.environ.get("PROJECT_APP", "")
    if not app.startswith("milo."):
        raise pytest.UsageError(f"PROJECT_APP must name a milo module, got {app!r}")
    if not config.getoption("disable_socket", False):
        raise pytest.UsageError("--disable-socket must stay in force (see pytest.ini)")

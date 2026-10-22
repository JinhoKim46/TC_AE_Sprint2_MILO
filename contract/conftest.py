"""Fixtures for the contract tests. Offline: sockets are blocked, nothing is spent."""
from __future__ import annotations

import importlib
import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import Document, FakeBackend, HashEmbedder, ScriptedChat  # noqa: E402

# Point the tests at your app with PROJECT_APP="package.module:build_app". The default is the toy app.
APP_SPEC = os.environ.get("PROJECT_APP", "example_app:build_app")

DOCS = [
    Document("kb/alpha", "The alpha widget needs a firmware update every six months.\n\n"
                         "Hold the reset pin for ten seconds to restore factory defaults.",
             {"section": "hardware"}),
    Document("kb/beta", "Invoices are issued on the first day of each month.\n\n"
                        "A late payment adds a fee of five percent.",
             {"section": "billing"}),
    Document("kb/gamma", "Support is available on weekdays between nine and five.\n\n"
                         "Urgent outages are handled by the on-call team.",
             {"section": "support"}),
]
# A question that quotes a passage of DOCS verbatim; it must retrieve kb/alpha.
QUESTION = "How often does the alpha widget need a firmware update every six months?"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(*a, **k):
        raise RuntimeError("network access is blocked in contract tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def _load_builder():
    mod_name, _, attr = APP_SPEC.partition(":")
    try:
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr or "build_app")
    except (ImportError, AttributeError) as e:
        pytest.fail(f"cannot load build_app from PROJECT_APP={APP_SPEC!r}: {e!r}. "
                    "Expose `build_app(chat_model, embeddings, tools_backend) -> App`.", pytrace=False)


@pytest.fixture
def make_app():
    """make_app(script=None, docs=DOCS, backend=None, **chat_kwargs) -> (app, chat, backend)."""
    build_app = _load_builder()

    def _make(script=None, docs=DOCS, backend=None, **chat_kwargs):
        chat = ScriptedChat(script, **chat_kwargs)
        backend = backend or FakeBackend()
        app = build_app(chat, HashEmbedder(), backend)
        if docs:
            app.ingest(list(docs))
        return app, chat, backend

    return _make

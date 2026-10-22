"""Repositories for the discovery app (SPEC section 11, D70, D74, D75).

`memory_repositories()` builds the in-memory set (tests and discovery mode); `pg_repositories(db)` builds the
Postgres set over a `milo.store.db.Database`. Both sets expose the same attributes and the same method
signatures; every owner-facing call takes the owner id first and applies the owner filter (row-level security in
Postgres, `MemoryStore` in memory).

This package imports no database driver: only `Database.__init__` does, and only when no pool is injected.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Repositories:
    profiles: Any
    customers: Any
    consents: Any
    sessions: Any
    turns: Any
    messages: Any
    facts: Any
    evidence: Any
    submissions: Any
    approvals: Any
    traces: Any
    rate_limits: Any
    field_requests: Any
    deletion_requests: Any
    audit: Any
    operator: Any


def memory_repositories(
    store: Any = None, clock: Callable[[], float] = time.time
) -> Repositories:
    from milo.store import memory as m

    st = store if store is not None else m.MemoryStore()
    return Repositories(
        profiles=m.MemoryProfiles(st, clock),
        customers=m.MemoryCustomers(st, clock),
        consents=m.MemoryConsents(st, clock),
        sessions=m.MemorySessions(st, clock),
        turns=m.MemoryTurns(st, clock),
        messages=m.MemoryMessages(st, clock),
        facts=m.MemoryFacts(st, clock),
        evidence=m.MemoryEvidence(st, clock),
        submissions=m.MemorySubmissions(st, clock),
        approvals=m.MemoryApprovalRows(st, clock),
        traces=m.MemoryTraces(st, clock),
        rate_limits=m.MemoryRateLimits(st, clock),
        field_requests=m.MemoryFieldRequests(st, clock),
        deletion_requests=m.MemoryDeletionRequests(st, clock),
        audit=m.MemoryAudit(st, clock),
        operator=m.MemoryOperator(st, clock),
    )


def pg_repositories(db: Any, operator_db: Any = None) -> Repositories:
    """Postgres repositories. `operator_db` (a `milo_operator` connection) backs `audit` and `operator` only."""
    from milo.store import approvals, audit, facts, messages, ratelimits, requests
    from milo.store import sessions, submissions, traces, users

    op = operator_db if operator_db is not None else db
    return Repositories(
        profiles=users.PgProfiles(db),
        customers=users.PgCustomers(db),
        consents=users.PgConsents(db),
        sessions=sessions.PgSessions(db),
        turns=sessions.PgTurns(db),
        messages=messages.PgMessages(db),
        facts=facts.PgFacts(db),
        evidence=facts.PgEvidence(db),
        submissions=submissions.PgSubmissions(db),
        approvals=approvals.PgApprovals(db),
        traces=traces.PgTraces(db),
        rate_limits=ratelimits.PgRateLimits(db),
        field_requests=requests.PgFieldRequests(db),
        deletion_requests=requests.PgDeletionRequests(db),
        audit=audit.PgAudit(op),
        operator=audit.PgOperator(op),
    )

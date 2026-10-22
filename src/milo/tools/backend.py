"""`MiloBackend`: the tools backend (SPEC section 6, D19). Implements `specs()` and `call(name, args, *, user_id)`.

Defence in depth: `call` strips identity keys and look-alikes and validates the arguments itself, even though the
loop already did, because `App.resume_approval` and the `/withdraw` route reach the backend without the loop. The
`user_id` is the session's; nothing in `args` can change it. A cross-owner id gets the same `not_found` as a
missing one and changes nothing (D65). Every executed call is recorded in `executed`, with whether it changed
state, for the attack runner's `backend_state_changed` column.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from milo.pricing.estimator import Estimate, plan_estimate
from milo.pricing.rate_card import get_rate_card
from milo.pricing.schema import RateCard
from milo.protocol.schema import Protocol as ProtocolFile
from milo.protocol.schema import load_protocol
from milo.tools.args import clean_args
from milo.tools.specs import (
    UnknownTool,
    build_specs,
    copy_specs,
    validate_args,
)
from milo.tools.state import FactRejected, MemorySpecStore, SpecStore, check_fact
from milo.types import Source, ToolSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROTOCOL_PATH = REPO_ROOT / "protocols" / "discovery.v1.yaml"
SEARCH_FIELDS = ("chunk_id", "doc_id", "score", "text")


class SearchIndex(Protocol):
    def search(
        self, query: str, *, filters: dict[str, Any] | None = None
    ) -> list[Source]: ...


@dataclass
class ExecutedCall:
    name: str
    args: dict[str, Any]
    user_id: str
    state_changed: bool = False


@dataclass
class MiloBackend:
    retriever: SearchIndex
    protocol: ProtocolFile = field(
        default_factory=lambda: load_protocol(DEFAULT_PROTOCOL_PATH)
    )
    rate_card: RateCard = field(default_factory=get_rate_card)
    store: SpecStore | None = None
    executed: list[ExecutedCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.store is None:
            self.store = MemorySpecStore(self.protocol)
        self._specs = build_specs(
            [f.id for f in self.protocol.fields], self.rate_card.base_monthly_usd
        )
        self._by_name = {s.name: s for s in self._specs}

    # ---- backend protocol ----

    @property
    def pricing_fields(self) -> Collection[str]:
        """Protocol fields marked `pricing: true`; the loop hands them to the policy (SPEC section 8)."""
        return self.protocol.pricing_field_ids

    def specs(self) -> list[ToolSpec]:
        return copy_specs(self._specs)

    def call(self, name: str, args: dict[str, Any], *, user_id: str) -> Any:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id is required")
        spec = self._by_name.get(name)
        if spec is None:
            raise UnknownTool(name)
        cleaned = clean_args(args)
        validate_args(spec, cleaned)
        record = ExecutedCall(name, cleaned, user_id)
        self.executed.append(record)
        handler = {
            "search_knowledge": self._search_knowledge,
            "get_session_spec": self._get_session_spec,
            "estimate_cost": self._estimate_cost,
            "record_fact": self._record_fact,
            "withdraw_submission": self._withdraw_submission,
        }[name]
        return handler(cleaned, user_id, record)

    # ---- pricing for the chain (numbers go to events, not to the model) ----

    def estimate_for(self, user_id: str, module_ids: list[str]) -> Estimate:
        """The full estimate, figures included, for the `estimate_updated` event (SPEC section 5 step 7)."""
        return plan_estimate(
            self.rate_card,
            self.protocol,
            self._store.facts(user_id),
            {"module_ids": module_ids},
        )

    # ---- handlers ----

    @property
    def _store(self) -> SpecStore:
        assert self.store is not None
        return self.store

    def _search_knowledge(
        self, args: dict[str, Any], user_id: str, record: ExecutedCall
    ) -> dict[str, Any]:
        product = args.get("product")
        filters = {"product": product} if product else None
        hits = self.retriever.search(args["query"], filters=filters)
        return {
            "chunks": [
                {
                    "chunk_id": h.chunk_id,
                    "doc_id": h.doc_id,
                    "score": round(h.score, 4),
                    "text": h.text,
                }
                for h in hits
            ]
        }

    def _get_session_spec(
        self, args: dict[str, Any], user_id: str, record: ExecutedCall
    ) -> dict[str, Any]:
        return {"facts": self._store.facts(user_id)}

    def _estimate_cost(
        self, args: dict[str, Any], user_id: str, record: ExecutedCall
    ) -> dict[str, Any]:
        return self.estimate_for(user_id, list(args["module_ids"])).tool_output()

    def _record_fact(
        self, args: dict[str, Any], user_id: str, record: ExecutedCall
    ) -> dict[str, Any]:
        f = self.protocol.field_by_id(args["field_id"])
        try:
            value, unit, period = check_fact(
                f, args["value"], args.get("unit"), args.get("period")
            )
        except FactRejected as exc:
            return {"status": "rejected", "field_id": f.id, "reason": str(exc)}
        row, changed = self._store.put_fact(
            user_id, f, value, unit, period, args["evidence_text"]
        )
        record.state_changed = changed
        return {"status": row["status"], "field_id": f.id, "changed": changed}

    def _withdraw_submission(
        self, args: dict[str, Any], user_id: str, record: ExecutedCall
    ) -> dict[str, Any]:
        outcome = self._store.withdraw(user_id, int(args["submission_id"]))
        record.state_changed = outcome == "withdrawn"
        return {"status": outcome, "submission_id": int(args["submission_id"])}

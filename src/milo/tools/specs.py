"""Tool specs (SPEC section 6, D19). Every schema sets `additionalProperties: false`.

`estimate_cost` takes `module_ids` only: volume and integrations are read from the session facts in code, so the
model has no way to put a number into a price (SPEC section 5, D38). `record_fact` declares `value` as
`anyOf: [string, number, boolean, array]`; the per-field check happens in the merge.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

import jsonschema

from milo.types import ToolSpec

MAX_MODULES = 6
PRODUCTS = (
    "phone-intake",
    "email-triage",
    "ticket-creation",
    "staff-assignment",
    "document-data-entry",
    "customer-status-updates",
    "general",
)


class ToolError(Exception):
    """A tool call that cannot run. The loop turns it into a `tool_failed` result."""


class InvalidToolArguments(ToolError):
    """The arguments fail the tool's schema (also raised by the backend itself, never only by the loop)."""


class UnknownTool(ToolError):
    pass


def build_specs(
    field_ids: Iterable[str],
    modules: Iterable[str],
    products: Iterable[str] = PRODUCTS,
    max_modules: int = MAX_MODULES,
) -> list[ToolSpec]:
    module_enum = sorted(set(modules))
    return [
        ToolSpec(
            "search_knowledge",
            "Search the MILO knowledge base for articles about modules, pricing, implementation and policies. "
            "Returns the best matching excerpts with their ids and scores.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 3, "maxLength": 200},
                    "product": {
                        "anyOf": [
                            {"type": "string", "enum": list(products)},
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "read",
        ),
        ToolSpec(
            "get_session_spec",
            "Read the facts recorded so far about the owner's business, with their status and evidence.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            "read",
        ),
        ToolSpec(
            "estimate_cost",
            "Ask for a cost estimate for the chosen modules. Pass module ids only: volume and integrations come "
            "from the recorded facts. The figures are shown to the owner in the estimate panel, not returned here.",
            {
                "type": "object",
                "properties": {
                    "module_ids": {
                        "type": "array",
                        "items": {"type": "string", "enum": module_enum},
                        "minItems": 1,
                        "maxItems": max_modules,
                        "uniqueItems": True,
                    }
                },
                "required": ["module_ids"],
                "additionalProperties": False,
            },
            "read",
        ),
        ToolSpec(
            "record_fact",
            "Record one fact the owner stated about their business, with the owner's own words as evidence.",
            {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string", "enum": list(field_ids)},
                    "value": {
                        "anyOf": [
                            {"type": "string", "maxLength": 500},
                            {"type": "number"},
                            {"type": "boolean"},
                            {
                                "type": "array",
                                "items": {"type": "string", "maxLength": 200},
                                "maxItems": 20,
                            },
                        ]
                    },
                    "unit": {"type": ["string", "null"], "maxLength": 40},
                    "period": {"type": ["string", "null"], "maxLength": 40},
                    "evidence_text": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4000,
                    },
                },
                "required": ["field_id", "value", "evidence_text"],
                "additionalProperties": False,
            },
            "write",
        ),
        ToolSpec(
            "withdraw_submission",
            "Withdraw one of the owner's own submitted specifications. Needs the owner's approval first.",
            {
                "type": "object",
                "properties": {"submission_id": {"type": "integer", "minimum": 1}},
                "required": ["submission_id"],
                "additionalProperties": False,
            },
            "destructive",
        ),
    ]


def validate_args(spec: ToolSpec, args: Any) -> None:
    """Raise `InvalidToolArguments` (message: the first schema error) when `args` fail the spec's schema."""
    errors = sorted(
        jsonschema.Draft202012Validator(spec.parameters).iter_errors(args),
        key=lambda e: list(e.path),
    )
    if errors:
        raise InvalidToolArguments(errors[0].message)


def copy_specs(specs: list[ToolSpec]) -> list[ToolSpec]:
    return [
        ToolSpec(s.name, s.description, copy.deepcopy(s.parameters), s.risk)
        for s in specs
    ]

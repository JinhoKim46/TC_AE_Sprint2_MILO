"""Typed errors and the `Response.error` format (SPEC D23): `<code>[:<detail>]`."""

from __future__ import annotations

INVALID_INPUT = "invalid_input"
PROVIDER_ERROR = "provider_error"
POLICY_DENIED = "policy_denied"
NOT_FOUND = "not_found"

USER_MESSAGES = {
    "invalid_input:empty": "Please type a message first.",
    "invalid_input:too_long": "That message is too long. Please shorten it to 4000 characters or fewer.",
    "invalid_input:control_characters": "That message contains characters I cannot read. Please retype it as plain text.",
    INVALID_INPUT: "I could not read that message. Please rephrase it.",
    "provider_error:timeout": "The assistant took too long to answer. Please try again in a moment.",
    "provider_error:unavailable": "The assistant is temporarily unavailable. Please try again in a moment.",
    PROVIDER_ERROR: "The assistant is temporarily unavailable. Please try again in a moment.",
    POLICY_DENIED: "I cannot do that.",
    NOT_FOUND: "I could not find that request.",
}


def format_error(code: str, detail: str | None = None) -> str:
    return f"{code}:{detail}" if detail else code


def user_message(error: str) -> str:
    """A plain message for the user; never a traceback or an exception class name."""
    return USER_MESSAGES.get(error) or USER_MESSAGES.get(
        error.split(":")[0], "Something went wrong. Please try again."
    )


class MiloError(Exception):
    code = "error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail

    @property
    def error(self) -> str:
        return format_error(self.code, self.detail)


class InvalidInput(MiloError):
    code = INVALID_INPUT


class ProviderFailure(MiloError):
    code = PROVIDER_ERROR


class ReasoningRequired(ProviderFailure):
    """The endpoint answered HTTP 400 "reasoning is mandatory" (SPEC D159). Permanent: never retried.

    The error string is `provider_error:reasoning_required:<model>`, so the first field is still `provider_error`.
    """

    def __init__(self, model: str) -> None:
        super().__init__(f"reasoning_required:{model}")
        self.model = model


class PolicyDenied(MiloError):
    code = POLICY_DENIED


class NotFound(MiloError):
    code = NOT_FOUND


class SpendRefused(MiloError):
    """The run would take the OpenRouter balance below the reserve (SPEC D18)."""

    code = "spend_refused"


class ProdGuardError(RuntimeError):
    """A test or eval process was given production settings (SPEC D76, D127)."""

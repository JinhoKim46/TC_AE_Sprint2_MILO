"""The two-stage input gate of discovery mode (SPEC D103 to D118, D128, D145, D157, D158; milestone M5G).

`stage1` (deterministic, lexicon), `jev` (stage 2, one Jev `noul` call through `llm/cloud.py`), `route` (thresholds
and routing). Contract mode never calls the gate.
"""

from milo.gate.route import (
    GREETING_REPLY,
    SCOPE_REPLY,
    THRESHOLDS,
    Gate,
    GateDecision,
    Thresholds,
    build_gate,
    compute_thresholds,
    load_thresholds,
    offline_gate,
    sr_gate,
)

__all__ = [
    "GREETING_REPLY",
    "SCOPE_REPLY",
    "THRESHOLDS",
    "Gate",
    "GateDecision",
    "Thresholds",
    "build_gate",
    "compute_thresholds",
    "load_thresholds",
    "offline_gate",
    "sr_gate",
]

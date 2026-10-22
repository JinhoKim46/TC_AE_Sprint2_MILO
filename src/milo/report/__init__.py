"""M5c: the canonical report JSON from a stored specification snapshot (SPEC section 7, D40). M6 renders it."""

from milo.report.build import (
    REPORT_VERSION,
    MissingRateCardKey,
    ReportError,
    SnapshotError,
    attach_fields,
    build_report,
    make_snapshot,
    resolve_source,
)

__all__ = [
    "REPORT_VERSION",
    "MissingRateCardKey",
    "ReportError",
    "SnapshotError",
    "attach_fields",
    "build_report",
    "make_snapshot",
    "resolve_source",
]

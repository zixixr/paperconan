"""Block-level numeric detectors.

The public detector import surface lives here. Implementations are currently
kept in `_audit` for compatibility with older tests/imports while the scanner
orchestrator is being slimmed down.
"""
from __future__ import annotations

from ._audit import (
    RELATION_FLOOD_CAP,
    benjamini_hochberg,
    benign_reason,
    detect_arithmetic_progression,
    detect_equal_pairs,
    detect_grim_grimmer,
    detect_identical_after_rounding,
    detect_last_digit,
    detect_relations,
    detect_repeated_decimals,
    detect_within_column_patterns,
    grim_consistent,
    grimmer_consistent,
)
from ._prefilter import make_finding as prefilter_relation_finding

__all__ = [
    "RELATION_FLOOD_CAP",
    "benjamini_hochberg",
    "benign_reason",
    "detect_arithmetic_progression",
    "detect_equal_pairs",
    "detect_grim_grimmer",
    "detect_identical_after_rounding",
    "detect_last_digit",
    "detect_relations",
    "detect_repeated_decimals",
    "detect_within_column_patterns",
    "grim_consistent",
    "grimmer_consistent",
    "prefilter_relation_finding",
]

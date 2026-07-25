"""Canonical registry of the per-block finding groups carried by a scan.

Single source of truth for the keys under which `relations_blocks[*]` stores its
findings. This lives in a leaf module with no paperconan imports so every
consumer — the scanner, the default HTML renderer, the adjudicated renderer and
the review packet — can read the same set without an import cycle.

Consumers must recognise every registered key. A consumer may then apply its own
explicit eligibility filter (a high-only packet, a profile projection), but it
must not silently drop a whole group: that is how `block_dups` stayed out of the
default HTML report while being present in scan.json and the Markdown summary.
"""
from __future__ import annotations

BLOCK_FINDING_GROUPS: tuple[str, ...] = (
    "relations", "equal_pairs", "progressions", "row_pairs", "row_relations",
    "within_col", "identical_after_rounding", "grim", "block_dups",
)

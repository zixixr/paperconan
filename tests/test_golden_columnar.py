"""Golden snapshot lock for the audit engine's findings.

This test pins the SUBSTANCE of `scan_dir`'s output (the detector findings)
against committed golden JSON, computed on the CURRENT engine. The columnar
rewrite (later tasks) must reproduce these findings byte-identically; this test
is the guard that proves it.

Only finding substance is pinned. Volatile metadata — tool_version, timestamps,
absolute input paths, provenance, and all wall-clock timings in scan_stats — is
stripped, and every list of findings is sorted by a stable key, so the
comparison is order- and environment-independent.

Regenerate the golden (only when an intentional behavior change lands) with:

    PAPERCONAN_GEN_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden_columnar.py -q
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

# Make tests/build_fixture importable when running pytest from repo root.
sys.path.insert(0, os.path.dirname(__file__))
from build_fixture import build as build_tiny_paper  # noqa: E402

from paperconan._audit import scan_dir  # noqa: E402

HERE = pathlib.Path(__file__).parent
GOLD = HERE / "golden"

# Each case is (name, builder). The builder writes the fixture files into a
# fresh dir and returns nothing meaningful; we then scan that dir. Fixtures are
# built from the COMMITTED, version-controlled tests/build_fixture.py rather than
# committed binary workbooks (the repo .gitignores *.xlsx), so the golden stays
# self-contained and deterministic. tiny_paper trips identical_column,
# arithmetic_progression, rounded_to_half_or_int, grim_inconsistent, and a
# cross_sheet_position_identical collision.
CASES = [("tiny_paper", build_tiny_paper)]

# Per-finding "group" lists carried inside each relations_blocks entry.
_FINDING_GROUPS = (
    "relations",
    "progressions",
    "equal_pairs",
    "within_col",
    "identical_after_rounding",
    "grim",
)


def _sortkey(obj) -> str:
    """Order-independent, deterministic sort key for any finding dict."""
    return json.dumps(obj, sort_keys=True, default=str)


# Display-only fields dropped from the golden substance: the evidence blob plus the
# per-finding value-peek samples (col_a_sample/col_b_sample) fed to downstream LLM
# triage. Like `evidence`, these are presentation data, not finding substance.
_DISPLAY_ONLY = frozenset({"evidence", "col_a_sample", "col_b_sample"})


def _drop_evidence(finding: dict) -> dict:
    """A finding without its (large, rendering-only) evidence/sample display blobs."""
    return {k: v for k, v in finding.items() if k not in _DISPLAY_ONLY}


def _stable(scan: dict) -> dict:
    """Reduce a scan result to its deterministic finding substance.

    Drops volatile top-level metadata (tool/version/timestamp/paths/provenance/
    timings), drops per-finding evidence blobs, and sorts every finding list by
    a stable key so the result is order- and environment-independent.
    """
    block_findings = []
    for blk in scan.get("relations_blocks") or []:
        block_meta = blk.get("block") or {}
        location = {
            "file": blk.get("file"),
            "sheet": blk.get("sheet"),
            "block_rows": block_meta.get("rows"),
            "block_cols": block_meta.get("cols"),
            "header": block_meta.get("header"),
        }
        for group in _FINDING_GROUPS:
            for finding in blk.get(group, []) or []:
                block_findings.append(
                    {"group": group, **location, "finding": _drop_evidence(finding)}
                )

    cross = [_drop_evidence(cf) for cf in (scan.get("cross_sheet_findings") or [])]
    digits = [_drop_evidence(d) for d in (scan.get("digit_distribution") or [])]
    decimals = [_drop_evidence(d) for d in (scan.get("decimal_endings") or [])]

    # scan_errors may carry absolute paths; keep only basename-level fields.
    errors = [
        {k: v for k, v in e.items() if k != "path"}
        for e in (scan.get("scan_errors") or [])
    ]

    return {
        "profile": scan.get("profile"),
        "n_files": scan.get("n_files"),
        "n_blocks_with_findings": scan.get("n_blocks_with_findings"),
        "scan_errors": sorted(errors, key=_sortkey),
        "block_findings": sorted(block_findings, key=_sortkey),
        "cross_sheet_findings": sorted(cross, key=_sortkey),
        "digit_distribution": sorted(digits, key=_sortkey),
        "decimal_endings": sorted(decimals, key=_sortkey),
    }


@pytest.mark.parametrize("name,builder", CASES, ids=[c[0] for c in CASES])
def test_golden(tmp_path, name, builder):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    builder(str(in_dir))
    scan = scan_dir(str(in_dir), str(out_dir), write_md=False, write_html=False)
    stable = _stable(scan)
    gold_path = GOLD / f"{name}.json"

    if os.environ.get("PAPERCONAN_GEN_GOLDEN"):
        GOLD.mkdir(exist_ok=True)
        gold_path.write_text(
            json.dumps(stable, indent=2, sort_keys=True, default=str) + "\n"
        )
        pytest.skip(f"generated {gold_path}")

    assert gold_path.exists(), (
        f"missing golden {gold_path}; regenerate with PAPERCONAN_GEN_GOLDEN=1"
    )
    expected = json.loads(gold_path.read_text())
    # Round-trip `stable` through JSON so tuples become lists, matching the
    # golden's on-disk types exactly (e.g. example_cells / failed_rows).
    actual = json.loads(json.dumps(stable, sort_keys=True, default=str))
    assert actual == expected

"""Report-chain consistency for the canonical per-block finding groups.

Every consumer of a scan — default HTML, the adjudicated report, and the review
packet — must recognise the same registered group keys. Each may then apply its
own explicit eligibility filter, but none may silently drop a whole group.

This is the regression that `block_dups` needed: it was persisted in scan output
and summarised in Markdown, yet the default HTML renderer kept its own private
tuple of groups and so never selected it.
"""
from __future__ import annotations

import pytest

from paperconan import BLOCK_FINDING_GROUPS
from paperconan._adjudicated_html import render_adjudicated_report
from paperconan._audit import write_markdown_report
from paperconan._html import write_html_report


def _rule_for(group: str) -> str:
    return f"synthetic {group} signal"


def _scan_with_group(group: str) -> dict:
    """A minimal scan carrying exactly one finding, stored under `group`."""
    return {
        "tool_version": "0.test",
        "profile": "review",
        "input_dir": "synthetic",
        "n_files": 1,
        "n_blocks_with_findings": 1,
        "relations_blocks": [{
            "file": "synthetic.xlsx",
            "sheet": "Panel",
            "block": {"rows": "2-6", "cols": "1-3", "header": ["a", "b", "c"]},
            group: [{
                "kind": f"synthetic_{group}",
                "severity": "high",
                "rule": _rule_for(group),
                "profile_action": "kept",
            }],
        }],
        "cross_sheet_findings": [],
        "digit_distribution": [],
        "decimal_endings": [],
        "decimal_tail_clusters": [],
    }


@pytest.mark.parametrize("group", BLOCK_FINDING_GROUPS)
def test_default_html_surfaces_every_canonical_block_group(group, tmp_path):
    report = tmp_path / "report.html"

    write_html_report(_scan_with_group(group), str(report))

    assert _rule_for(group) in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("group", BLOCK_FINDING_GROUPS)
def test_adjudicated_report_resolves_a_ref_into_every_canonical_block_group(group):
    verdict = {
        "verdict": "NEEDS_HUMAN",
        "findings": [{
            "title": "Cited synthetic signal",
            "finding_ref": {"kind": f"synthetic_{group}"},
            "report_md": "This signal requires contextual review.",
        }],
    }

    html = render_adjudicated_report(_scan_with_group(group), verdict)

    assert _rule_for(group) in html
    assert "无匹配证据（finding_ref 未命中扫描结果）" not in html


@pytest.mark.parametrize("group", BLOCK_FINDING_GROUPS)
def test_the_markdown_summary_surfaces_every_canonical_block_group(group, tmp_path):
    """The third consumer, which had gone stale unnoticed.

    `write_markdown_report` kept its own hand-maintained list of groups, and
    `row_relations` was never added to it -- so `paperconan --md` reported no
    "row B = row A * k" signal at all while scan.json and the HTML report both
    carried them. Parametrising over the registry is what stops a fourth list.
    """
    report = tmp_path / "REPORT.md"

    write_markdown_report(_scan_with_group(group), str(report))

    assert _rule_for(group) in report.read_text(encoding="utf-8")


def test_the_scanner_emits_exactly_the_registered_groups(tmp_path):
    """Closes the loop on the producer side.

    The parametrised tests above iterate *over* the registry, so they can only
    check consumers — deleting an entry deletes its own test case. This asserts
    the other direction against a real scan: the block keys `scan_dir` writes
    are exactly the registered ones, so a producer that invents a key without
    registering it (which is how `block_dups` went missing) fails here.
    """
    from paperconan import scan_dir

    data = tmp_path / "data"
    data.mkdir()
    rows = ["a,b,c"] + [f"{i + 1},{(i + 1) * 2},{round((i + 1) * 1.5, 4)}" for i in range(12)]
    (data / "panel.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    blocks = scan.get("relations_blocks") or []
    assert blocks, "fixture produced no blocks"

    structural = {"file", "sheet", "block", "findings_omitted"}
    for block in blocks:
        assert set(block) - structural == set(BLOCK_FINDING_GROUPS)

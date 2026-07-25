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


def test_html_does_not_keep_a_private_copy_of_the_canonical_group_set():
    """A newly registered group must reach HTML without editing the renderer."""
    from paperconan import _html

    assert set(_html.canonical_block_groups()) == set(BLOCK_FINDING_GROUPS)

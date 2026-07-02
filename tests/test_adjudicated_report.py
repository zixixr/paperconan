from __future__ import annotations

import json
import subprocess
import sys

from build_fixture import build

from paperconan import scan_dir, write_adjudicated_report
from paperconan._adjudicated_html import _render_md, render_adjudicated_report


def _verdict() -> dict:
    return {
        "verdict": "KEEP",
        "suspicion_tier": 1,
        "impact_scope": "supporting",
        "tier_why": "synthetic independent columns are identical",
        "drop_reason": None,
        "innocent_explanation": "source-data assembly error remains possible",
        "needs_author_data": "raw values and figure mapping",
        "review_status": "confirmed",
        "report_md": (
            "## Synthetic paper\n\n"
            "### 论文主结论\n"
            "This synthetic fixture tests report rendering.\n\n"
            "### 异常位置\n"
            "`ED_Fig1.xlsx` Sheet1 has an identical numeric column pair.\n\n"
            "### 标签含义\n"
            "The fixture labels two columns as separate measurements.\n\n"
            "### 为什么这是问题\n"
            "If independent, identical values need clarification.\n\n"
            "### 影响判断\n"
            "This is supporting evidence in a synthetic test.\n\n"
            "### 无辜解释的层次\n"
            "A duplicate export remains possible.\n\n"
            "### 需要作者澄清\n"
            "Please provide the raw source mapping.\n\n"
            "### 证据\n"
            "paperconan synthetic fixture, identical_column finding.\n"
        ),
    }


def test_write_adjudicated_report_renders_verdict_and_scan_evidence(tmp_path):
    data = tmp_path / "data"
    build(str(data))
    audit = tmp_path / "audit"
    scan = scan_dir(str(data), str(audit), write_html=False)
    out = tmp_path / "adjudication.html"

    write_adjudicated_report(scan, _verdict(), str(out))

    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "KEEP" in html
    assert "Tier 1" in html
    assert "supporting" in html
    assert "confirmed" in html
    assert "论文主结论" in html
    assert "异常位置" in html
    assert "identical_column" in html
    assert "ED_Fig1.xlsx" in html
    assert "signal, not verdict" in html
    assert "fabrication" not in html.lower()


def test_report_subcommand_writes_adjudicated_html(tmp_path):
    data = tmp_path / "data"
    build(str(data))
    audit = tmp_path / "audit"
    scan_dir(str(data), str(audit), write_html=False)
    verdict_path = tmp_path / "verdict.json"
    verdict_path.write_text(json.dumps(_verdict(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "adjudication.html"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            "report",
            str(audit / "scan.json"),
            "--verdict",
            str(verdict_path),
            "--out",
            str(out),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "Synthetic paper" in html
    assert "关键证据" in html
    assert "identical_column" in html
    assert str(out) in proc.stdout


def test_render_md_sections_are_balanced_not_nested():
    md = (
        "## Title\n\n### 论文主结论\nA.\n\n### 异常位置\n- one\n- two\n\n"
        "### 证据\nC.\n"
    )
    out = _render_md(md)
    # every opened report section must be closed, so sections are siblings
    assert out.count("<section") == out.count("</section>") == 3


def test_profile_hidden_findings_do_not_surface_as_key_evidence():
    scan = {
        "relations_blocks": [
            {
                "file": "F.xlsx",
                "sheet": "S1",
                "block": {"rows": "1-4", "cols": "A-B", "header": ["a", "b"]},
                "within_col": [
                    {
                        "kind": "within_col_value_duplication",
                        "severity": "high",
                        "rule": "r",
                        "profile_action": "hidden",
                        "evidence": {"rows": [{"row_idx": 1, "values": [1]}], "headers": ["a"]},
                    }
                ],
            }
        ],
        "cross_sheet_findings": [],
    }
    html = render_adjudicated_report(scan, {"verdict": "DROP", "report_md": "## x"})
    assert "within_col_value_duplication" not in html


def _scan_two_findings() -> dict:
    return {
        "relations_blocks": [
            {
                "file": "A.xlsx",
                "sheet": "Alpha",
                "block": {"rows": "5-39", "cols": "A-B", "header": ["x", "y"]},
                "relations": [
                    {
                        "kind": "constant_offset",
                        "severity": "high",
                        "rule": "col[1] = col[2] + 0.3",
                        "n": 35,
                        "evidence": {"headers": ["x", "y"], "rows": [{"row_idx": 5, "values": [1, 2]}]},
                    }
                ],
            },
            {
                "file": "B.xlsx",
                "sheet": "Beta",
                "block": {"rows": "1-9", "cols": "C-D", "header": ["p", "q"]},
                "within_col": [
                    {
                        "kind": "within_col_value_duplication",
                        "severity": "medium",
                        "rule": "dup",
                        "n": 9,
                        "evidence": {"headers": ["p"], "rows": [{"row_idx": 1, "values": [9]}]},
                    }
                ],
            },
        ],
        "cross_sheet_findings": [],
    }


def test_finding_refs_scope_key_evidence_to_the_selected_finding():
    scan = _scan_two_findings()
    verdict = {
        "verdict": "KEEP",
        "suspicion_tier": 1,
        "report_md": "## t",
        "finding_refs": [{"sheet": "Alpha", "kind": "constant_offset"}],
    }
    html = render_adjudicated_report(scan, verdict)
    # only the selected finding is rendered as a full evidence card
    assert html.count('class="finding-card"') == 1
    assert "constant_offset" in html
    # the other signal is demoted, not presented as part of the verdict
    assert "未纳入本次判定" in html


def test_without_finding_refs_shows_top_findings_as_before():
    scan = _scan_two_findings()
    html = render_adjudicated_report(scan, {"verdict": "KEEP", "report_md": "## t"})
    assert html.count('class="finding-card"') == 2
    assert "未纳入本次判定" not in html


def test_finding_refs_with_no_match_falls_back_to_top_findings():
    scan = _scan_two_findings()
    verdict = {"verdict": "KEEP", "report_md": "## t", "finding_refs": [{"sheet": "Nonexistent"}]}
    html = render_adjudicated_report(scan, verdict)
    assert html.count('class="finding-card"') == 2
    assert "未纳入本次判定" not in html


def _multi_finding_verdict() -> dict:
    return {
        "title": "Paper X",
        "verdict": "KEEP",
        "paper_conclusion": "Main claim under review.",
        "overall_impact": "core",
        "findings": [
            {
                "title": "Finding one",
                "finding_ref": {"sheet": "Alpha", "kind": "constant_offset"},
                "suspicion_tier": 3,
                "impact_scope": "core",
                "review_status": "confirmed",
                "report_md": "**位置** alpha loc.",
            },
            {
                "title": "Finding two",
                "finding_ref": {"sheet": "Beta", "kind": "within_col_value_duplication"},
                "suspicion_tier": 2,
                "impact_scope": "supporting",
                "review_status": "needs_human",
                "report_md": "**位置** beta loc.",
            },
        ],
    }


def test_findings_array_renders_per_finding_blocks_with_own_evidence():
    scan = _scan_two_findings()
    html = render_adjudicated_report(scan, _multi_finding_verdict())
    # each finding is its own self-contained block
    assert html.count('class="finding-block"') == 2
    assert "Finding one" in html and "Finding two" in html
    # each block carries its own status badge
    assert "confirmed" in html and "needs_human" in html
    # each finding's evidence table is rendered adjacent to its block
    assert html.count('class="ev"') == 2
    # a findings index summarises them
    assert "findings-index" in html


def test_hero_shows_highest_tier_across_findings():
    scan = _scan_two_findings()
    html = render_adjudicated_report(scan, _multi_finding_verdict())  # tiers 3 and 2
    hero = html.split("</section>")[0]  # the hero is the first <section>
    assert "Tier 2" in hero  # highest severity across findings
    assert "Tier 3" not in hero


def test_legacy_single_finding_format_still_renders():
    scan = _scan_two_findings()
    verdict = {
        "verdict": "KEEP",
        "report_md": "## t",
        "finding_refs": [{"sheet": "Alpha", "kind": "constant_offset"}],
    }
    html = render_adjudicated_report(scan, verdict)
    assert 'class="finding-block"' not in html
    assert html.count('class="finding-card"') == 1
    assert "未纳入本次判定" in html

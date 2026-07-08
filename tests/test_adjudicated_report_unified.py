"""Unified adjudicated report: single-finding verdicts render in the SAME
high-fidelity layout (finding card + evidence heatmap) as multi-finding ones."""
from paperconan._adjudicated_html import render_adjudicated_report

SCAN = {
    "tool_version": "0.8.2", "profile": "review",
    "paper": {"title": "T", "doi": "10.0/x", "input_dir": "d"},
    "relations_blocks": [{
        "file": "f.xlsx", "sheet": "S", "block": {"rows": "2-40", "cols": "3-3", "header": ["v"]},
        "relations": [], "progressions": [], "equal_pairs": [], "row_pairs": [],
        "within_col": [{
            "kind": "within_col_dispersed_repeats", "col": "v", "col_idx": 2,
            "n": 30, "severity": "medium", "rule": "col[2]: dispersed repeats",
            "evidence": {"headers": ["v"], "col_offset": 2, "highlight_cols": [2],
                         "highlight_rows": [5, 12, 20],
                         "rows": [{"row_idx": 1, "is_context": True, "values": ["v"]},
                                  {"row_idx": 5, "is_context": False, "values": [1.23]}]},
        }],
        "identical_after_rounding": [], "grim": [], "findings_omitted": 0,
    }],
    "digit_distribution": [], "decimal_endings": [], "cross_sheet_findings": [],
}

SINGLE_VERDICT = {
    "title": "T", "verdict": "KEEP", "suspicion_tier": 1, "impact_scope": "core",
    "tier_why": "why-1", "innocent_explanation": "checked", "needs_author_data": "raw",
    "report_md": "### 1. 论文主结论\n结论。\n\n**为什么** …",
    "finding_refs": [{"file": "f.xlsx", "sheet": "S", "rows": "2-40",
                      "kind": "within_col_dispersed_repeats"}],
    "review_status": "confirmed",
}


def test_single_verdict_renders_rich_layout():
    html = render_adjudicated_report(SCAN, SINGLE_VERDICT)
    # rich per-finding card + evidence heatmap present
    assert "finding-block" in html
    assert "hi-col" in html            # evidence heatmap cells
    # old two-column plain layout is GONE
    assert 'class="panel side"' not in html
    assert 'class="panel report"' not in html
    # verdict-level judgment fields preserved somewhere in the page
    assert "why-1" in html and "raw" in html


def test_single_finding_hides_findings_index():
    html = render_adjudicated_report(SCAN, SINGLE_VERDICT)
    assert "findings-index" not in html   # 1 finding -> no silly 1-row index


def test_drop_verdict_renders_without_crash():
    v = {"title": "T", "verdict": "DROP", "drop_reason": "fixed_denominator",
         "innocent_explanation": "percentages from a common denominator",
         "report_md": None, "review_status": "unreviewed"}
    html = render_adjudicated_report(SCAN, v)
    assert "DROP" in html
    assert "finding-block" in html          # falls back to strongest scan finding evidence
    assert 'class="panel side"' not in html


def test_needs_human_verdict_renders_without_crash():
    v = {"title": "T", "verdict": "NEEDS_HUMAN",
         "tier_why": "sample provenance missing", "report_md": None,
         "review_status": "unreviewed"}
    html = render_adjudicated_report(SCAN, v)
    assert "NEEDS_HUMAN" in html
    assert "sample provenance missing" in html

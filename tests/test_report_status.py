from paperconan._audit import write_markdown_report
from paperconan._html import write_html_report


def _scan(status, limitations=None):
    return {
        "tool": "paperconan",
        "tool_version": "test",
        "profile": "review",
        "input_dir": "data",
        "n_files": 1,
        "n_blocks_with_findings": 0,
        "findings_omitted": 0,
        "scan_errors": [],
        "scan_stats": {"files": [], "sheets": [], "elapsed_ms": None},
        "relations_blocks": [],
        "digit_distribution": [],
        "decimal_endings": [],
        "cross_sheet_findings": [],
        "scan_status": status,
        "coverage": {
            "files_discovered": 1,
            "files_succeeded": 1 if status != "failed" else 0,
            "files_failed": 1 if status == "failed" else 0,
            "sheets_succeeded": 1 if status != "failed" else 0,
            "sheets_skipped": 0,
            "blocks_analyzed": 1 if status != "failed" else 0,
            "blocks_skipped": 0,
            "truncated": bool(limitations),
            "limitations": limitations or [],
        },
    }


def _add_finding(scan):
    scan["n_blocks_with_findings"] = 1
    scan["relations_blocks"] = [{
        "file": "good.xlsx",
        "sheet": "Data",
        "block": {"rows": "2-5", "cols": "A-B", "header": ["a", "b"]},
        "relations": [{
            "kind": "constant_offset",
            "severity": "medium",
            "rule": "col[1] = col[0] + 1",
            "n": 4,
        }],
        "progressions": [],
        "equal_pairs": [],
        "row_pairs": [],
        "within_col": [],
        "identical_after_rounding": [],
        "grim": [],
    }]


def _render_html(tmp_path, scan, name="report.html"):
    out = tmp_path / name
    write_html_report(scan, str(out))
    return out.read_text(encoding="utf-8")


def _render_markdown(tmp_path, scan, name="REPORT.md"):
    out = tmp_path / name
    write_markdown_report(scan, str(out))
    return out.read_text(encoding="utf-8")


def test_complete_html_reports_status_before_findings(tmp_path):
    html = _render_html(tmp_path, _scan("complete"))

    assert "scan complete" in html.lower()
    assert html.lower().index("scan complete") < html.index("How to read this")
    assert "nothing flagged in this dataset" in html


def test_failed_html_does_not_claim_no_findings(tmp_path):
    html = _render_html(tmp_path, _scan("failed"))

    assert "scan failed" in html.lower()
    assert "no input table reached numeric scanning" in html.lower()
    assert "nothing flagged in this dataset" not in html


def test_partial_html_lists_escaped_limit_and_retains_findings(tmp_path):
    scan = _scan(
        "partial",
        [{
            "scope": "file<script>",
            "reason": "parse_<error>",
            "file": 'bad<&".xlsx',
        }],
    )
    _add_finding(scan)

    html = _render_html(tmp_path, scan)

    assert "scan partial" in html.lower()
    assert "parse_&lt;error&gt;" in html
    assert "bad&lt;&amp;&quot;.xlsx" in html
    assert "file&lt;script&gt;" in html
    assert "parse_<error>" not in html
    assert 'bad<&".xlsx' not in html
    assert (
        html.index('<section class="scan-status')
        < html.index('<details class="finding"')
    )
    assert "constant_offset" in html


def test_legacy_html_reports_unknown_detailed_coverage(tmp_path):
    scan = _scan("complete")
    scan.pop("scan_status")
    scan.pop("coverage")

    html = _render_html(tmp_path, scan)

    assert "legacy scan" in html.lower()
    assert "detailed coverage status is unavailable" in html.lower()
    assert "nothing flagged in this dataset" not in html
    assert "no findings" in html.lower()


def test_complete_markdown_reports_status_and_completed_empty_state(tmp_path):
    markdown = _render_markdown(tmp_path, _scan("complete"))

    assert "## Scan status" in markdown
    assert "scan complete" in markdown.lower()
    assert "nothing flagged in this dataset" in markdown.lower()


def test_failed_markdown_uses_dedicated_state_not_completed_empty_claim(tmp_path):
    markdown = _render_markdown(tmp_path, _scan("failed"))

    assert "## Scan status" in markdown
    assert "scan failed" in markdown.lower()
    assert "no input table reached numeric scanning" in markdown.lower()
    assert "nothing flagged in this dataset" not in markdown.lower()


def test_partial_markdown_lists_limit_and_retains_findings(tmp_path):
    scan = _scan(
        "partial",
        [{"scope": "file", "reason": "parse_error", "file": "bad.xlsx"}],
    )
    _add_finding(scan)

    markdown = _render_markdown(tmp_path, scan)

    assert "scan partial" in markdown.lower()
    assert "parse_error" in markdown
    assert "bad.xlsx" in markdown
    assert markdown.lower().index("scan partial") < markdown.index("constant_offset")
    assert "constant_offset" in markdown


def test_legacy_markdown_reports_unknown_detailed_coverage(tmp_path):
    scan = _scan("complete")
    scan.pop("scan_status")
    scan.pop("coverage")

    markdown = _render_markdown(tmp_path, scan)

    assert "legacy scan" in markdown.lower()
    assert "detailed coverage status is unavailable" in markdown.lower()
    assert "nothing flagged in this dataset" not in markdown.lower()

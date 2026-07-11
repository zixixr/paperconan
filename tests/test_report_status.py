import pytest

from paperconan._audit import _markdown_inline_code, write_markdown_report
from paperconan._html import write_html_report


_MISSING = object()


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


def _add_finding(scan, severity="medium"):
    scan["n_blocks_with_findings"] = 1
    scan["relations_blocks"] = [{
        "file": "good.xlsx",
        "sheet": "Data",
        "block": {"rows": "2-5", "cols": "A-B", "header": ["a", "b"]},
        "relations": [{
            "kind": "constant_offset",
            "severity": severity,
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


def _nested_limitations(reordered=False):
    if reordered:
        first_reason = {
            "a": "alpha",
            "z": ["reason`one", {
                "a": "first",
                "line\nkey": "<value>",
                "line\rkey": "other",
            }],
        }
        first_meta = {
            "a": "<tag>",
            "outer": [{"a": "line\nvalue", "z": "last"}],
        }
        second_detail = {
            "x": ["one", {"a": None, "b": False}],
            "y": "two",
        }
        return [
            {
                "meta`key\n": first_meta,
                "reason": first_reason,
                "scope": "block",
            },
            {
                "detail": second_detail,
                "scope": "file",
                "reason": ["second", {"a": 1, "b": 2}],
            },
        ]
    return [
        {
            "scope": "block",
            "reason": {
                "z": ["reason`one", {
                    "line\rkey": "other",
                    "line\nkey": "<value>",
                    "a": "first",
                }],
                "a": "alpha",
            },
            "meta`key\n": {
                "outer": [{"z": "last", "a": "line\nvalue"}],
                "a": "<tag>",
            },
        },
        {
            "reason": ["second", {"b": 2, "a": 1}],
            "scope": "file",
            "detail": {
                "y": "two",
                "x": ["one", {"b": False, "a": None}],
            },
        },
    ]


def _add_reportable_signal(scan, signal):
    if signal in {"high_block", "low_block"}:
        _add_finding(scan, severity=signal.removesuffix("_block"))
    elif signal == "cross_sheet":
        scan["cross_sheet_findings"] = [{
            "kind": "cross_sheet_position_identical",
            "severity": "high",
            "file": "good.xlsx",
            "sheet_a": "Data",
            "sheet_b": "Repeat",
            "rule": "matching numeric positions require review",
        }]
    elif signal == "digit":
        scan["digit_distribution"] = [{
            "label": "good.xlsx::Data",
            "n": 90,
            "chi2": 45.0,
            "p": 1e-8,
            "p_adj": 1e-7,
            "fdr_significant": True,
            "counts": {str(digit): 9 for digit in range(10)},
            "top": [["1", 18]],
        }]
    elif signal == "decimal":
        scan["decimal_endings"] = [{
            "label": "good.xlsx::Data",
            "n": 40,
            "n_unique": 7,
            "top": [["12", 11]],
        }]


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


def test_nested_limitations_render_canonically_and_escape_html(tmp_path):
    first = _scan("partial", _nested_limitations())
    reordered = _scan("partial", _nested_limitations(reordered=True))

    first_html = _render_html(tmp_path, first, "first.html")
    reordered_html = _render_html(tmp_path, reordered, "reordered.html")

    assert first_html == reordered_html
    assert "&lt;value&gt;" in first_html
    assert "&lt;tag&gt;" in first_html
    assert "<value>" not in first_html
    assert "<tag>" not in first_html
    assert "line\nkey" not in first_html
    assert "line key" in first_html
    assert first_html.index("alpha") < first_html.index("reason`one")
    assert first_html.index("reason`one") < first_html.index("second")


def test_nested_limitations_render_canonically_and_sanitize_markdown(tmp_path):
    first = _scan("partial", _nested_limitations())
    reordered = _scan("partial", _nested_limitations(reordered=True))

    first_md = _render_markdown(tmp_path, first, "first.md")
    reordered_md = _render_markdown(tmp_path, reordered, "reordered.md")
    limitations = first_md.split("### Coverage limitations\n", 1)[1].split("\n\n", 1)[0]

    assert first_md == reordered_md
    assert limitations.strip().splitlines() == [
        "- ``{a: alpha, z: [reason`one, "
        "{a: first, line key: <value>, line key: other}]}`` "
        "· `scope`: `block` · ``meta`key``: "
        "`{a: <tag>, outer: [{a: line value, z: last}]}`",
        "- `[second, {a: 1, b: 2}]` · `scope`: `file` "
        "· `detail`: `{x: [one, {a: None, b: False}], y: two}`",
    ]
    assert "line\nkey" not in limitations


@pytest.mark.parametrize(
    "signal",
    ["high_block", "low_block", "cross_sheet", "digit", "decimal"],
)
def test_reportable_signals_never_render_completed_empty_claim(
    tmp_path, signal
):
    scan = _scan("complete")
    _add_reportable_signal(scan, signal)

    html = _render_html(tmp_path, scan)
    markdown = _render_markdown(tmp_path, scan)

    assert "nothing flagged in this dataset" not in html.lower()
    assert "nothing flagged in this dataset" not in markdown.lower()


def test_unknown_html_status_never_uses_completed_empty_claim(tmp_path):
    html = _render_html(tmp_path, _scan("cancelled"))

    assert "scan cancelled" in html.lower()
    assert "detailed coverage status is unavailable" in html.lower()
    assert "nothing flagged in this dataset" not in html.lower()
    assert "no findings recorded" in html.lower()


def test_unknown_markdown_status_never_uses_completed_empty_claim(tmp_path):
    markdown = _render_markdown(tmp_path, _scan("cancelled"))

    assert "**scan status:** `cancelled`." in markdown.lower()
    assert "detailed coverage status is unavailable" in markdown.lower()
    assert "nothing flagged in this dataset" not in markdown.lower()
    assert "no findings were recorded" in markdown.lower()


def test_hostile_markdown_status_and_limitations_stay_in_code_spans(tmp_path):
    status = (
        "BROKEN <details open><script>*em*[link](x)`tick`\r\nnext"
    )
    hostile_key = (
        "<details open><script>*label*[link](x)`key`\r\nnext"
    )
    hostile_value = (
        "<details open><script>*value*[link](x)`value`\r\nnext"
    )
    limitation = {
        hostile_key: hostile_value,
        "scope": "file",
        "reason": (
            "<details open><script>*reason*[link](x)`reason`\r\nnext"
        ),
    }
    reordered = {
        "reason": limitation["reason"],
        "scope": "file",
        hostile_key: hostile_value,
    }

    first = _render_markdown(
        tmp_path,
        _scan(status, [limitation]),
        "hostile-first.md",
    )
    second = _render_markdown(
        tmp_path,
        _scan(status, [reordered]),
        "hostile-reordered.md",
    )
    status_line = next(
        line for line in first.splitlines() if line.startswith("**Scan status:**")
    )
    limitation_line = next(
        line for line in first.splitlines() if line.startswith("- ")
    )

    assert first == second
    assert status_line == (
        "**Scan status:** ``broken <details open><script>*em*[link](x)"
        "`tick` next``. Detailed coverage status is unavailable for this scan."
    )
    assert limitation_line == (
        "- ``<details open><script>*reason*[link](x)`reason` next`` "
        "· `scope`: `file` "
        "· ``<details open><script>*label*[link](x)`key` next``: "
        "``<details open><script>*value*[link](x)`value` next``"
    )
    assert not any(
        line.startswith(("<details", "<script>", "*em*", "[link]"))
        for line in first.splitlines()
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(" ", "` `", id="one-space"),
        pytest.param("  ", "`  `", id="two-spaces"),
        pytest.param(" lead", "`  lead `", id="leading-space"),
        pytest.param("trail ", "` trail  `", id="trailing-space"),
        pytest.param(" both ", "`  both  `", id="boundary-spaces"),
        pytest.param("`tick", "`` `tick ``", id="leading-backtick"),
        pytest.param("tick`", "`` tick` ``", id="trailing-backtick"),
        pytest.param("``tick", "``` ``tick ```", id="leading-backtick-run"),
        pytest.param("tick``", "``` tick`` ```", id="trailing-backtick-run"),
    ],
)
def test_markdown_inline_code_preserves_boundary_spaces_and_backticks(
    tmp_path, value, expected
):
    assert _markdown_inline_code(value) == expected

    markdown = _render_markdown(
        tmp_path,
        _scan("partial", [{"scope": "file", "reason": value}]),
    )

    assert f"- {expected} · `scope`: `file`" in markdown


@pytest.mark.parametrize(
    ("reason", "expected_html", "expected_markdown"),
    [
        pytest.param(_MISSING, "unspecified", "unspecified", id="absent"),
        pytest.param(None, "unspecified", "unspecified", id="none"),
        pytest.param({}, "{}", "{}", id="empty-dict"),
        pytest.param([], "[]", "[]", id="empty-list"),
        pytest.param(False, "False", "False", id="false"),
        pytest.param(0, "0", "0", id="zero"),
        pytest.param("", "&quot;&quot;", '""', id="empty-string"),
    ],
)
def test_falsey_reason_values_are_preserved(
    tmp_path, reason, expected_html, expected_markdown
):
    limitation = {"scope": "file"}
    if reason is not _MISSING:
        limitation["reason"] = reason
    scan = _scan("partial", [limitation])

    html = _render_html(tmp_path, scan)
    markdown = _render_markdown(tmp_path, scan)

    assert f"<li><code>{expected_html}</code>" in html
    assert f"- `{expected_markdown}` · `scope`: `file`" in markdown
    if expected_markdown != "unspecified":
        assert "unspecified" not in html
        assert "unspecified" not in markdown

import pytest

from paperconan._audit import _markdown_inline_code, write_markdown_report
from paperconan._html import (
    _fmt_cell,
    _render_cross_sheet_examples,
    write_html_report,
)


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


def _omission_warning(html):
    warnings = [
        part.split("</div>", 1)[0]
        for part in html.split('<div class="warn">')[1:]
    ]
    return next(warning for warning in warnings if "omitted" in warning)


def _cross_finding(**overrides):
    finding = {
        "kind": "cross_sheet_position_identical",
        "severity": "high",
        "file": "good.xlsx",
        "file_a": "good.xlsx",
        "file_b": "good.xlsx",
        "same_file": True,
        "sheet_a": "Data",
        "sheet_b": "Repeat",
        "rule": "cross-table statistical signal requires review",
        "examples": [0],
    }
    finding.update(overrides)
    return finding


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(0.0, "0", id="zero"),
        pytest.param(1e-13, "1e-13", id="positive-tiny"),
        pytest.param(-1e-13, "-1e-13", id="negative-tiny"),
        pytest.param(float("nan"), "", id="nan"),
        pytest.param(float("inf"), "", id="positive-infinity"),
        pytest.param(float("-inf"), "", id="negative-infinity"),
    ],
)
def test_float_cell_formatting_is_truthful(value, expected):
    assert _fmt_cell(value) == expected


@pytest.mark.parametrize(
    ("examples", "headings", "values"),
    [
        pytest.param(
            [{"row": 0, "col": 2, "value": "<same>"}],
            ["row", "col", "value"],
            ["0", "2", "&lt;same&gt;"],
            id="same-position",
        ),
        pytest.param(
            [{
                "row_a": 0,
                "col_a": 1,
                "value_a": 0,
                "row_b": 3,
                "col_b": 4,
                "value_b": "<changed>",
                "decimal_tail": "01234",
            }],
            [
                "row A",
                "col A",
                "value A",
                "row B",
                "col B",
                "value B",
                "decimal tail",
            ],
            ["0", "1", "3", "4", "&lt;changed&gt;", "01234"],
            id="decimal-tail",
        ),
        pytest.param(
            [{"value": 0}, {"value": "<duplicate>"}],
            ["value"],
            ["0", "&lt;duplicate&gt;"],
            id="value-only",
        ),
        pytest.param(
            [{
                "file": "a<&.xlsx",
                "sheet": "S<script>",
                "row": 0,
                "start_col": 0,
                "values": [0, "<vector>"],
            }],
            ["file", "sheet", "row", "start col", "values"],
            [
                "a&lt;&amp;.xlsx",
                "S&lt;script&gt;",
                "0",
                "[0, &lt;vector&gt;]",
            ],
            id="recurring-location",
        ),
        pytest.param(
            [{"mystery": 0, "hostile": "<script>"}],
            ["hostile", "mystery"],
            ["0", "&lt;script&gt;"],
            id="unknown-dict",
        ),
        pytest.param(
            [{}],
            [],
            ["empty example object"],
            id="empty-unknown-dict",
        ),
    ],
)
def test_cross_sheet_example_renderer_is_shape_aware_and_escaped(
    examples, headings, values
):
    rendered = _render_cross_sheet_examples({"examples": examples})

    assert rendered
    for heading in headings:
        assert f"<th>{heading}</th>" in rendered
    for value in values:
        assert value in rendered
    assert "<script>" not in rendered


def test_cross_sheet_scalar_examples_preserve_falsey_and_hostile_values():
    rendered = _render_cross_sheet_examples({
        "examples": [0, False, "<shared>"],
    })

    assert ">0<" in rendered
    assert ">FALSE<" in rendered
    assert "&lt;shared&gt;" in rendered
    assert "<shared>" not in rendered


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


@pytest.mark.parametrize(
    ("limitations", "expected_control", "unexpected_control"),
    [
        pytest.param(
            [{"scope": "block", "reason": "finding_limit"}],
            "PAPERCONAN_MAX_FINDINGS_PER_BLOCK",
            "PAPERCONAN_MAX_TOTAL_FINDINGS",
            id="block",
        ),
        pytest.param(
            [{"scope": "scan", "reason": "global_finding_limit"}],
            "PAPERCONAN_MAX_TOTAL_FINDINGS",
            "PAPERCONAN_MAX_FINDINGS_PER_BLOCK",
            id="global",
        ),
        pytest.param(
            [{
                "scope": "scan",
                "reason": "recurring_row_vector_finding_limit",
            }],
            "recurring_row_vector_finding_limit",
            "PAPERCONAN_MAX_FINDINGS_PER_BLOCK",
            id="recurring",
        ),
        pytest.param(
            [{
                "scope": "block",
                "reason": "row_pair_finding_limit",
            }],
            "row_pair_finding_limit",
            "PAPERCONAN_MAX_FINDINGS_PER_BLOCK",
            id="row-pair-only",
        ),
        pytest.param(
            [{
                "scope": "scan",
                "reason": (
                    "within_row_repeated_segment_candidate_limit"
                ),
            }],
            "within_row_repeated_segment_candidate_limit",
            "PAPERCONAN_MAX_TOTAL_FINDINGS",
            id="within-row-candidate",
        ),
    ],
)
def test_omission_warning_uses_coverage_reason_and_neutral_severity(
    tmp_path, limitations, expected_control, unexpected_control
):
    scan = _scan("partial", limitations)
    scan["findings_omitted"] = 2

    html = _render_html(tmp_path, scan)
    warning = _omission_warning(html)

    assert "2 findings were omitted" in warning
    assert "lower-severity" not in warning
    assert expected_control in warning
    assert unexpected_control not in warning


def test_legacy_omission_warning_stays_generic_and_neutral(tmp_path):
    scan = _scan("complete")
    scan["findings_omitted"] = 1
    scan.pop("coverage")

    html = _render_html(tmp_path, scan)
    warning = _omission_warning(html)

    assert "1 finding was omitted" in warning
    assert "lower-severity" not in warning
    assert "PAPERCONAN_MAX_FINDINGS_PER_BLOCK" not in warning
    assert "PAPERCONAN_MAX_TOTAL_FINDINGS" not in warning


def test_lower_bounded_omission_warning_is_explicit(tmp_path):
    scan = _scan("partial", [{
        "scope": "scan",
        "reason": "recurring_row_vector_finalization_limit",
        "omitted_findings_lower_bound": 2,
    }])
    scan["findings_omitted"] = 2
    scan["findings_omitted_is_lower_bound"] = True

    html = _render_html(tmp_path, scan)
    warning = _omission_warning(html)

    assert "At least 2 findings were omitted" in warning
    assert "recurring_row_vector_finalization_limit" in warning


def test_raw_html_evidence_keeps_tiny_nonzero_float_signs(tmp_path):
    scan = _scan("complete")
    _add_finding(scan)
    scan["relations_blocks"][0]["relations"][0]["evidence"] = {
        "headers": ["positive", "negative", "zero"],
        "col_offset": 0,
        "highlight_cols": [0, 1, 2],
        "highlight_rows": [1],
        "rows": [{
            "row_idx": 1,
            "is_context": False,
            "values": [1e-13, -1e-13, 0.0],
        }],
    }

    html = _render_html(tmp_path, scan)

    assert ">1e-13<" in html
    assert ">-1e-13<" in html
    assert ">0<" in html


def test_raw_html_renders_all_truncated_evidence_windows(tmp_path):
    scan = _scan("partial", [{
        "scope": "block",
        "reason": "evidence_limit",
    }])
    _add_finding(scan)
    scan["relations_blocks"][0]["relations"][0]["evidence"] = {
        "truncated": True,
        "windows": [
            {
                "headers": ["first"],
                "col_offset": 0,
                "col_indices": [0],
                "highlight_cols": [0],
                "highlight_rows": [1],
                "rows": [{
                    "row_idx": 1,
                    "is_context": False,
                    "values": ["window-one"],
                }],
            },
            {
                "headers": ["last"],
                "col_offset": 9,
                "col_indices": [9],
                "highlight_cols": [9],
                "highlight_rows": [10],
                "rows": [{
                    "row_idx": 10,
                    "is_context": False,
                    "values": ["window-two"],
                }],
            },
        ],
    }

    html = _render_html(tmp_path, scan)

    assert html.count('<table class="ev">') == 2
    assert "window-one" in html
    assert "window-two" in html


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


def test_cross_table_section_uses_generic_heading_and_hint(tmp_path):
    scan = _scan("complete")
    scan["cross_sheet_findings"] = [_cross_finding()]

    html = _render_html(tmp_path, scan)
    markdown = _render_markdown(tmp_path, scan)

    assert "Cross-table statistical signals" in html
    assert "Cross-table statistical signals" in markdown
    assert "Cross-sheet bit-identical collisions" not in html
    assert "Cross-sheet bit-identical collisions" not in markdown
    assert "同位置出现高度一致" not in html


def test_raw_html_renders_decimal_tail_cross_table_evidence(tmp_path):
    scan = _scan("complete")
    scan["cross_sheet_findings"] = [_cross_finding(
        kind="cross_sheet_decimal_tail_reuse",
        examples=[{
            "row_a": 0,
            "col_a": 1,
            "value_a": 0,
            "row_b": 2,
            "col_b": 3,
            "value_b": "<changed>",
            "decimal_tail": "01234",
        }],
    )]

    html = _render_html(tmp_path, scan)

    assert "<th>row A</th>" in html
    assert "<th>value B</th>" in html
    assert "<th>decimal tail</th>" in html
    assert ">0<" in html
    assert "&lt;changed&gt;" in html
    assert "<changed>" not in html


@pytest.mark.parametrize(
    ("blocks", "cross_findings", "expected"),
    [
        pytest.param(True, [], 1, id="block-only"),
        pytest.param(
            False,
            [_cross_finding()],
            2,
            id="cross-table-only",
        ),
        pytest.param(
            True,
            [_cross_finding()],
            2,
            id="mixed-deduplicated",
        ),
        pytest.param(
            False,
            [_cross_finding(), _cross_finding(kind="another_signal")],
            2,
            id="duplicate-cross-table-locations",
        ),
        pytest.param(
            False,
            [_cross_finding(
                file="a.xlsx + b.xlsx",
                file_a="a.xlsx",
                file_b="b.xlsx",
                same_file=False,
                sheet_a="a.xlsx::Panel A",
                sheet_b="b.xlsx::Panel B",
            )],
            2,
            id="cross-file",
        ),
        pytest.param(
            False,
            [_cross_finding(
                file="legacy.xlsx",
                file_a=None,
                file_b=None,
                same_file=True,
                sheet_a="A",
                sheet_b="B",
            )],
            2,
            id="legacy-same-file",
        ),
        pytest.param(
            False,
            [_cross_finding(
                file="a.xlsx + b.xlsx",
                file_a=None,
                file_b=None,
                same_file=False,
                sheet_a="A",
                sheet_b="B",
            )],
            0,
            id="legacy-ambiguous-cross-file",
        ),
        pytest.param(
            False,
            [_cross_finding(
                kind="recurring_row_vector",
                file="A.xlsx; Z.xlsx",
                file_a="A.xlsx",
                file_b="Z.xlsx",
                same_file=False,
                sheet_a="Figure 002a",
                sheet_b="Figure 115a",
                examples=[{"value": 1.25}],
            )],
            0,
            id="recurring-cross-file-extrema-are-not-paired",
        ),
        pytest.param(
            False,
            [_cross_finding(
                kind="recurring_row_vector",
                file="A.xlsx; Z.xlsx",
                file_a="A.xlsx",
                file_b="Z.xlsx",
                same_file=False,
                sheet_a="Figure 002a",
                sheet_b="Figure 115a",
                examples=[
                    {
                        "file": "A.xlsx",
                        "sheet": "Figure 115a",
                        "row": 4,
                        "start_col": 2,
                        "value": 1.25,
                    },
                    {
                        "file": "Z.xlsx",
                        "sheet": "Figure 002a",
                        "row": 7,
                        "start_col": 3,
                        "value": 1.25,
                    },
                ],
            )],
            2,
            id="recurring-explicit-occurrence-locations",
        ),
    ],
)
def test_html_location_count_unions_identifiable_physical_locations(
    tmp_path, blocks, cross_findings, expected
):
    scan = _scan("complete")
    if blocks:
        _add_finding(scan)
    scan["cross_sheet_findings"] = cross_findings

    html = _render_html(tmp_path, scan)

    assert (
        f'<span class="stat"><strong>{expected}</strong> '
        "sheets w/ findings</span>"
    ) in html


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

"""The first page and the false-positive filter's verdict.

`overview` ranks on the detector's severity, frozen before the profile runs, so a
location whose every finding the filter had already demoted could take a first-page
slot on the strength of findings the scan itself had marked as likely false positives.

The filter returns two verdicts and they must not be treated alike. "drop" says the
pattern is usually derived or structural; "downweight" says worth less, not nothing.
Only the first is grounds for moving a location, and only to the back of its own
severity band: the verdict is compared after severity, because the filter can be
wrong and a verdict that can be wrong must not outweigh the detector's severity.

Every fixture uses one finding kind, so family interleaving -- which round-robins
across kinds -- cannot reorder what these tests look at.
"""
from __future__ import annotations

import re

from paperconan._drill import drill, overview
from paperconan._drill_render import render_overview

DROP = "deterministic_relation_prefilter"
DOWNWEIGHT = "deterministic_relation_downweight"


def _finding(i, severity="high", action="kept", context=()):
    return {
        "kind": "identical_column",
        "severity": severity if action == "kept" else "low",
        "raw_severity": severity, "n": 6, "rule": f"col[{i}] == col[{i + 1}]",
        "profile_action": action, "false_positive_context": list(context),
    }


def _outright(count):
    return [_finding(i, action="demoted", context=[DROP]) for i in range(count)]


def _block(sheet, findings):
    return {"file": "s.xlsx", "sheet": sheet,
            "block": {"rows": "2-9", "cols": "1-12", "header": []},
            "relations": findings}


def _scan(*blocks, cross=()):
    return {"tool": "paperconan", "schema_version": 1, "n_files": 1,
            "relations_blocks": list(blocks), "cross_sheet_findings": list(cross)}


def _sheet(loc):
    return loc["location"].split(" :: ", 1)[1].split(" rows ", 1)[0]


def _order(scan):
    return [_sheet(loc) for loc in overview(scan)["locations"]]


def test_a_location_demoted_outright_sorts_after_its_band_mates():
    order = _order(_scan(_block("Flagged", _outright(5)),
                         _block("Kept", [_finding(0)])))

    assert order.index("Kept") < order.index("Flagged"), (
        f"five high findings the filter demoted outright outranked one it kept: {order}"
    )


def test_the_verdict_is_compared_after_severity_not_before():
    order = _order(_scan(_block("Flagged", _outright(5)),
                         _block("Weaker", [_finding(0, severity="medium")])))

    assert order.index("Flagged") < order.index("Weaker"), (
        f"the filter's verdict moved a high location behind a medium one: {order}"
    )


def test_a_downweight_verdict_does_not_move_a_location():
    downweighted = [_finding(i, action="demoted", context=[DOWNWEIGHT]) for i in range(5)]
    order = _order(_scan(_block("Downweighted", downweighted),
                         _block("Kept", [_finding(0)])))

    assert order.index("Downweighted") < order.index("Kept"), (
        f"a downweight was treated as a drop and moved the location back: {order}"
    )


def test_one_finding_the_filter_left_alone_keeps_the_location_in_place():
    order = _order(_scan(_block("Mixed", _outright(4) + [_finding(9)]),
                         _block("Kept", [_finding(0)])))

    assert order.index("Mixed") < order.index("Kept"), (
        f"a location with a kept finding was moved back as if wholly demoted: {order}"
    )


def test_a_demotion_with_no_recorded_reason_does_not_move_a_location():
    unexplained = [_finding(i, action="demoted") for i in range(5)]
    order = _order(_scan(_block("Unexplained", unexplained),
                         _block("Kept", [_finding(0)])))

    assert order.index("Unexplained") < order.index("Kept"), (
        f"a demotion nobody can read the reason for moved the location back: {order}"
    )


def _cross(a, b, i, action="kept", context=()):
    return {
        "kind": "cross_sheet_position_identical",
        "severity": "high" if action == "kept" else "low", "raw_severity": "high",
        "rule": f"{a} row {i} == {b} row {i}", "file_a": "s.xlsx", "file_b": "s.xlsx",
        "sheet_a": a, "sheet_b": b, "row_a": i, "row_b": i, "same_position_count": 6,
        "profile_action": action, "false_positive_context": list(context),
    }


def test_a_cross_sheet_location_carries_the_verdict_too():
    flagged = [_cross("F1", "F2", i, action="demoted",
                      context=["same_data_replot_or_duplicate_upload"]) for i in range(5)]
    view = overview(_scan(cross=flagged + [_cross("K1", "K2", 0)]))
    order = [loc["location"] for loc in view["locations"]]

    assert order.index("s.xlsx :: K1 ↔ K2") < order.index("s.xlsx :: F1 ↔ F2"), (
        f"cross-sheet seeds do not carry the verdict: {order}"
    )


def _lines_under(text, n):
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(rf"^\s*{n}\s{{2}}\S", ln))
    under = []
    for ln in lines[start + 1:]:
        if not ln.startswith("      "):
            break
        under.append(ln)
    return "\n".join(under)


def test_the_page_says_when_every_signal_at_a_location_was_demoted_outright():
    mixed = [_finding(0, action="demoted", context=[DROP]), _finding(1)]
    view = overview(_scan(_block("Flagged", _outright(3)), _block("Mixed", mixed)))
    loc = {_sheet(x): x for x in view["locations"]}

    assert loc["Flagged"]["demoted_outright"] == 3
    assert loc["Mixed"]["demoted_outright"] == 1

    text = render_overview(view)
    assert "filter: all 3 demoted outright" in _lines_under(text, loc["Flagged"]["n"]), text
    assert "filter:" not in _lines_under(text, loc["Mixed"]["n"]), text


def test_drill_opens_the_location_overview_numbered():
    scan = _scan(_block("Flagged", _outright(5)), _block("Kept", [_finding(0)]))

    for loc in overview(scan)["locations"]:
        assert drill(scan, loc["n"])["cluster_id"] == loc["cluster_id"], (
            f"overview #{loc['n']} and drill #{loc['n']} name different locations"
        )

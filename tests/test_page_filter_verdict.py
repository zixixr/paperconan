"""The first page and the prefilter's drop verdict.

`overview` ranks on the detector's severity, frozen before the profile runs, so a
location whose every finding the filter had already demoted could take a first-page
slot on the strength of findings the scan itself had marked as likely false positives.

Of the profile's demotions, only a prefilter's drop rule is acted on here. The
prefilters distinguish "drop" from "downweight" and the profile then demotes both
alike, so the context tag is the record of which it was; the other guards record no
strength, and two of them decide on words in headers and sheet names. A downweight
means worth less, not nothing, and a location holding one is left alone.

Within its severity band a location that matched sorts after its band-mates, and
severity is compared first because the filter can be wrong. Family interleaving then
round-robins kinds, so most fixtures here use one kind and the page order is the sort
order; `test_with_several_kinds_the_move_is_among_its_leading_kind` pins what still
holds when kinds differ.
"""
from __future__ import annotations

import re

import pytest

from paperconan._drill import drill, explain, overview
from paperconan._drill_render import render_explain, render_overview

DROP = "deterministic_relation_prefilter"
WC_DROP = "within_col_structural_filter"
DOWNWEIGHT = "deterministic_relation_downweight"
WC_DOWNWEIGHT = "within_col_downweight"
GUARD = "derived_or_unit_conversion"


def _finding(i, severity="high", action="kept", context=(), kind="identical_column"):
    return {
        "kind": kind,
        "severity": severity if action == "kept" else "low",
        "raw_severity": severity, "n": 6, "rule": f"{kind} col[{i}] == col[{i + 1}]",
        "profile_action": action, "false_positive_context": list(context),
    }


def _dropped(count, tag=DROP, kind="identical_column", action="demoted"):
    return [_finding(i, action=action, context=[tag], kind=kind) for i in range(count)]


def _block(sheet, findings, cols="1-12", group="relations"):
    return {"file": "s.xlsx", "sheet": sheet,
            "block": {"rows": "2-9", "cols": cols, "header": []},
            group: findings}


def _scan(*blocks):
    return {"tool": "paperconan", "schema_version": 1, "n_files": 1,
            "relations_blocks": list(blocks), "cross_sheet_findings": []}


def _sheet(loc):
    return loc["location"].split(" :: ", 1)[1].split(" rows ", 1)[0]


def _order(scan):
    return [_sheet(loc) for loc in overview(scan)["locations"]]


def _before(order, first, second):
    return order.index(first) < order.index(second)


def test_a_location_matching_a_drop_rule_sorts_after_its_band_mates():
    order = _order(_scan(_block("Flagged", _dropped(5)), _block("Kept", [_finding(0)])))

    assert _before(order, "Kept", "Flagged"), (
        f"five high findings a drop rule matched outranked one the filter kept: {order}"
    )


def test_the_within_column_drop_rule_counts_too():
    kind = "within_col_value_duplication"
    order = _order(_scan(
        _block("Flagged", _dropped(5, tag=WC_DROP, kind=kind), group="within_col"),
        _block("Kept", [_finding(0, kind=kind)], group="within_col")))

    assert _before(order, "Kept", "Flagged"), (
        f"the within-column drop rule was not acted on: {order}"
    )


def test_severity_is_compared_before_the_verdict():
    order = _order(_scan(_block("Flagged", _dropped(5)),
                         _block("Weaker", [_finding(0, severity="medium")])))

    assert _before(order, "Flagged", "Weaker"), (
        f"the verdict outweighed severity in the sort: {order}"
    )


@pytest.mark.parametrize("tag", [DOWNWEIGHT, WC_DOWNWEIGHT])
def test_a_downweight_does_not_move_a_location(tag):
    order = _order(_scan(_block("Downweighted", _dropped(5, tag=tag)),
                         _block("Kept", [_finding(0)])))

    assert _before(order, "Downweighted", "Kept"), (
        f"a downweight ({tag}) moved the location back as if it were a drop: {order}"
    )


def test_a_demotion_by_another_guard_does_not_move_a_location():
    order = _order(_scan(_block("Guarded", _dropped(5, tag=GUARD)),
                         _block("Kept", [_finding(0)])))

    assert _before(order, "Guarded", "Kept"), (
        f"a guard that records no strength moved the location back: {order}"
    )


def test_one_finding_outside_the_rule_keeps_the_location_in_place():
    order = _order(_scan(_block("Mixed", _dropped(4) + [_finding(9)]),
                         _block("Kept", [_finding(0)])))

    assert _before(order, "Mixed", "Kept"), (
        f"a location with a kept finding was moved back as if every finding matched: {order}"
    )


def test_a_demotion_with_no_recorded_reason_does_not_move_a_location():
    unexplained = [_finding(i, action="demoted") for i in range(5)]
    order = _order(_scan(_block("Unexplained", unexplained), _block("Kept", [_finding(0)])))

    assert _before(order, "Unexplained", "Kept"), (
        f"a demotion nobody can read the reason for moved the location back: {order}"
    )


def test_a_hidden_finding_counts_like_a_demoted_one():
    order = _order(_scan(_block("Hidden", _dropped(5, action="hidden")),
                         _block("Kept", [_finding(0)])))

    assert _before(order, "Kept", "Hidden"), (
        f"the triage profile's hidden findings were not acted on: {order}"
    )


def test_a_merged_panel_is_judged_on_all_its_findings():
    """One panel, two column spans. `_merge_panels` builds the panel from its first
    member and appends the rest, so a verdict read off that member alone would miss
    the kept finding in the second span."""
    left = _block("Panel", _dropped(3), cols="1-6")
    right = _block("Panel", [_finding(9)], cols="7-12")
    order = _order(_scan(left, right, _block("Kept", [_finding(0)])))

    assert _before(order, "Panel", "Kept"), (
        f"a merged panel holding a kept finding was moved back on its first member: {order}"
    )


def test_with_several_kinds_the_move_is_among_its_leading_kind():
    """Interleaving files each location under the first kind it lists and round-robins
    those, so a location that matched can still print ahead of a same-strength location
    of another kind, or behind a weaker one. What holds is its place among the
    locations filed under the same leading kind."""
    order = _order(_scan(
        _block("A flagged", _dropped(5)),
        _block("A kept", [_finding(0)]),
        _block("B kept", [_finding(i, kind="constant_offset") for i in range(3)])))

    assert _before(order, "A kept", "A flagged"), (
        f"among its own kind, the location that matched was not moved back: {order}"
    )


@pytest.mark.parametrize("value", [
    [{"ctx": DROP}],            # an entry that is not a string
    DOWNWEIGHT,                 # a bare string, not a list
    {DROP: 1},                  # a mapping whose key is a drop tag
    1, True, 2.5,               # scalars
])
def test_a_malformed_context_is_ignored_not_trusted_or_fatal(value):
    """Hand-edited or foreign scans: a context that is not a list of strings must
    neither make the check raise nor be read as a drop rule."""
    odd = _dropped(2)
    odd[0]["false_positive_context"] = value
    scan = _scan(_block("Odd", odd), _block("Kept", [_finding(0)]))

    loc = {_sheet(x): x for x in overview(scan)["locations"]}
    assert loc["Odd"]["matched_drop_rule"] == 1, (value, loc["Odd"])
    assert _before(_order(scan), "Odd", "Kept"), value


def test_explain_shows_the_drop_tag_when_detector_severity_was_already_low():
    """The page's filter line sends the reader to explain. A finding the detector
    already rated low can still be demoted, and explain used to print its reasons
    only when the displayed severity differed from the detector's."""
    low = [_finding(i, severity="low", action="demoted", context=[DROP]) for i in range(2)]
    scan = _scan(_block("Low", low))
    fid = drill(scan, 1, kind="identical_column")["findings"][0]["finding_id"]

    text = render_explain(explain(scan, fid))
    assert DROP in text, text


def _lines_under(text, n):
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(rf"^\s*{n}\s{{2}}\S", ln))
    under = []
    for ln in lines[start + 1:]:
        if not ln.startswith("      "):
            break
        under.append(ln)
    return "\n".join(under)


def test_the_page_says_when_every_signal_matched_a_drop_rule():
    mixed = [_finding(0, action="demoted", context=[DROP]), _finding(1)]
    view = overview(_scan(_block("Flagged", _dropped(3)), _block("Mixed", mixed)))
    loc = {_sheet(x): x for x in view["locations"]}

    assert loc["Flagged"]["matched_drop_rule"] == 3
    assert loc["Mixed"]["matched_drop_rule"] == 1

    text = render_overview(view)
    assert ("filter: 3/3 matched a prefilter drop rule"
            in _lines_under(text, loc["Flagged"]["n"])), text
    assert "filter:" not in _lines_under(text, loc["Mixed"]["n"]), text


def test_drill_opens_the_location_overview_numbered():
    scan = _scan(_block("Flagged", _dropped(5)), _block("Kept", [_finding(0)]),
                 _block("B kept", [_finding(i, kind="constant_offset") for i in range(3)]))

    for loc in overview(scan)["locations"]:
        assert drill(scan, loc["n"])["cluster_id"] == loc["cluster_id"], (
            f"overview #{loc['n']} and drill #{loc['n']} name different locations"
        )

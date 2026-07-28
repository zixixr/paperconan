"""Layered read-only views over a scan: overview → drill → explain.

A single paper's supplement can produce thousands of findings and a multi-MB
scan.json — far past what an agent can hold, and past what a human wants to read
in one go. Tightening the detectors to shrink that would drop real signals, so
the layering happens at presentation instead: nothing is filtered out, it is
just reached in stages.

The contract these tests pin is reachability. Every finding in the scan must be
reachable through the three layers, or be named in `coverage` as not shown.
A signal that is silently unreachable is the same defect as a signal that was
never detected.
"""
from __future__ import annotations

import json
import re

import pytest

from paperconan import BLOCK_FINDING_GROUPS, scan_dir
from paperconan._drill import drill, explain, overview


@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    """One synthetic paper with several panels and a cross-file duplicate."""
    d = tmp_path_factory.mktemp("data")
    for f in range(3):
        cols = [f"c{j}" for j in range(8)]
        rows = [",".join(cols)]
        for i in range(30):
            vals = [round((i + 1) * (j + 1) * (f + 1) * 1.017, 6) for j in range(8)]
            vals[5] = vals[2]                      # identical column
            vals[6] = round(vals[3] * 1.13, 6)     # constant ratio
            rows.append(",".join(str(v) for v in vals))
        (d / f"supp{f}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return scan_dir(str(d), str(d.parent / "out"), write_html=False)


# Structural keys — scan bookkeeping, not findings.
_NON_FINDING_KEYS = {
    "relations_blocks", "image_assets", "scan_errors", "scan_stats", "paper",
    "coverage", "tool", "tool_version", "schema_version", "profile", "input_dir",
    "scanned_at", "scan_status",
}


def _seeded_finding_count(scan):
    """What the layers are expected to route."""
    n = sum(len(b.get(g) or []) for b in scan.get("relations_blocks") or []
            for g in BLOCK_FINDING_GROUPS)
    return n + len(scan.get("cross_sheet_findings") or [])


def _families_present(scan):
    """Every finding-bearing family in this scan, discovered from its own shape.

    Deliberately not a constant. A previous version listed the families by hand
    and the list turned out to equal `_workflow._UNSEEDED_FAMILIES`, so the test
    could only ever find families the implementation already declared — the same
    scoping error it was written to replace, one constant over. Detecting the key
    structurally means a newly added family fails this test on the day it lands.
    """
    present = {}
    for key, value in scan.items():
        if key in _NON_FINDING_KEYS or not isinstance(value, list) or not value:
            continue
        if all(isinstance(item, dict) for item in value):
            present[key] = len(value)
    blocks = sum(len(b.get(g) or []) for b in scan.get("relations_blocks") or []
                 for g in BLOCK_FINDING_GROUPS)
    if blocks:
        present["relations_blocks"] = blocks
    return present


# ---------- L1 overview ----------

def test_overview_fits_in_a_glance(scan):
    view = overview(scan)

    assert len(json.dumps(view)) < 4096, "overview must stay small enough to read at once"
    assert view["locations"], "expected at least one location"
    assert view["signals_total"] == _seeded_finding_count(scan)


def test_overview_names_the_families_at_each_location(scan):
    """Lets an agent judge at the cheapest layer whether a location is worth opening."""
    view = overview(scan)

    for loc in view["locations"]:
        assert loc["families"], f"{loc['location']} lists no families"
        assert loc["signals"] > 0
        assert loc["strongest"] in ("high", "medium", "low")


def test_overview_is_read_only(scan):
    before = json.dumps(scan, sort_keys=True)

    overview(scan)

    assert json.dumps(scan, sort_keys=True) == before


# ---------- L2 / L3 drill ----------

def test_drill_groups_a_location_by_kind(scan):
    view = overview(scan)

    detail = drill(scan, view["locations"][0]["n"])

    assert detail["by_kind"], "expected kind groups"
    assert sum(k["n"] for k in detail["by_kind"]) == view["locations"][0]["signals"]
    for group in detail["by_kind"]:
        assert group["example"], "each kind needs a concrete example to judge from"


def test_drill_into_a_kind_lists_its_findings(scan):
    view = overview(scan)
    first = view["locations"][0]
    kind = drill(scan, first["n"])["by_kind"][0]["kind"]

    listing = drill(scan, first["n"], kind=kind)

    assert listing["findings"]
    assert all(f["finding_id"] for f in listing["findings"])
    assert all(f["kind"] == kind for f in listing["findings"])


def test_drill_accepts_a_cluster_id_as_well_as_an_ordinal(scan):
    view = overview(scan)
    loc = view["locations"][0]

    assert drill(scan, loc["n"]) == drill(scan, loc["cluster_id"])


def test_drill_rejects_an_unknown_location(scan):
    with pytest.raises(ValueError, match="no such location"):
        drill(scan, 9999)


# ---------- L4 explain ----------

def test_explain_returns_the_full_evidence_for_one_finding(scan):
    view = overview(scan)
    first = view["locations"][0]
    kind = drill(scan, first["n"])["by_kind"][0]["kind"]
    fid = drill(scan, first["n"], kind=kind)["findings"][0]["finding_id"]

    detail = explain(scan, fid)

    assert detail["finding_id"] == fid
    assert detail["rule"]
    assert "location" in detail
    assert "evidence" in detail


def test_explain_separates_raw_severity_from_the_projected_one(scan):
    """The whole point of freezing raw_severity: a demoted finding must still be
    recognisable as something the detector rated highly, with the reason shown,
    so a profile decision never silently buries a real signal."""
    view = overview(scan)
    fids = [
        f["finding_id"]
        for loc in view["locations"]
        for group in drill(scan, loc["n"])["by_kind"]
        for f in drill(scan, loc["n"], kind=group["kind"])["findings"]
    ]

    details = [explain(scan, fid) for fid in fids]

    assert details, "fixture produced no findings"
    for d in details:
        assert "raw" in d["severity"] and "effective" in d["severity"]
        assert "profile_action" in d["severity"]
        if d["severity"]["raw"] != d["severity"]["effective"]:
            assert d["severity"]["context"], (
                "a demoted finding must say why it was demoted"
            )


def test_explain_never_serves_another_findings_evidence(scan):
    """`explain` is where a reviewer decides whether to act, so the evidence has
    to belong to the id asked for.

    The lookup must key on the same tuple `seed_id` is hashed from. Anything
    weaker is not injective where the id is: several detectors build `rule` from
    row labels or omit the column index, so two side-by-side panels can produce
    byte-identical rule strings.
    """
    view = overview(scan)
    pairs = []
    for loc in view["locations"]:
        for group in drill(scan, loc["n"])["by_kind"]:
            for f in drill(scan, loc["n"], kind=group["kind"])["findings"]:
                detail = explain(scan, f["finding_id"])
                pairs.append((f["finding_id"], detail))

    assert pairs, "fixture produced no findings"
    for fid, detail in pairs:
        assert detail["finding_id"] == fid
        # the evidence must come from the block the heading names
        if detail.get("evidence") and detail["location"]:
            assert detail["kind"], "a finding with evidence must name its kind"

    # Distinct ids must resolve to distinct raw findings. Checked against the
    # rule text rather than the evidence blob, since two genuinely different
    # findings can legitimately share an evidence window over the same block.
    from paperconan._drill import _match_raw_finding
    from paperconan._workflow import _build_clusters

    clusters, _ = _build_clusters(scan, max_clusters=10**9)
    resolved = {}
    for cluster in clusters:
        for seed in cluster["seeds"]:
            raw = _match_raw_finding(scan, seed)
            if raw is None:
                continue
            key = id(raw)
            assert key not in resolved, (
                f"{seed['seed_id']} and {resolved[key]} both resolved to the same "
                "raw finding; explain would serve one of them the other's evidence"
            )
            resolved[key] = seed["seed_id"]


def test_match_is_keyed_on_the_same_tuple_as_the_seed_id():
    """Unit-level: two blocks differing only in columns must not cross-match."""
    from paperconan._drill import _match_raw_finding
    from paperconan._workflow import _block_seed

    finding = {"kind": "block_value_duplication", "rule": "same rule text",
               "severity": "high", "raw_severity": "high", "evidence": {"rows": [1]}}
    other = dict(finding, evidence={"rows": [2]})
    scan = {"relations_blocks": [
        {"file": "f.csv", "sheet": "s", "block": {"rows": "2-41", "cols": "1-3"},
         "block_dups": [finding]},
        {"file": "f.csv", "sheet": "s", "block": {"rows": "2-41", "cols": "5-7"},
         "block_dups": [other]},
    ]}

    seed_b = _block_seed(scan["relations_blocks"][1], "block_dups", other)
    matched = _match_raw_finding(scan, seed_b)

    assert matched is not None
    assert matched["evidence"] == {"rows": [2]}, (
        "panel 2's id resolved to panel 1's evidence"
    )


def test_explain_renders_the_actual_evidence_numbers(scan):
    """C1: the evidence payload had no test at all.

    `"evidence" in detail` is satisfied by None, and `"evidence:" in stdout` is
    the heading, printed unconditionally. Neither notices if the table is empty,
    truncated to one row, or never rendered — in the layer whose whole purpose is
    letting a reviewer compare exact digits against the paper.
    """
    from paperconan._drill_render import render_explain

    view = overview(scan)
    detailed = None
    for loc in view["locations"]:
        for group in drill(scan, loc["n"])["by_kind"]:
            for f in drill(scan, loc["n"], kind=group["kind"])["findings"]:
                d = explain(scan, f["finding_id"])
                if (d.get("evidence") or {}).get("rows"):
                    detailed = d
                    break
            if detailed:
                break
        if detailed:
            break
    assert detailed, "fixture produced no finding with an evidence table"

    rows = detailed["evidence"]["rows"]
    text = render_explain(detailed)

    rendered_rows = [ln for ln in text.splitlines() if "│" in ln]
    assert len(rendered_rows) >= min(len(rows), 5) + 1, (
        "evidence table rendered fewer rows than it holds"
    )
    # actual recorded values must appear, not just the heading
    checked = 0
    for row in rows[:3]:
        for value in (row.get("values") or [])[:3]:
            if isinstance(value, float):
                assert f"{value:.10g}"[:8] in text or repr(value)[:8] in text, (
                    f"recorded value {value} does not appear in the rendered table"
                )
                checked += 1
    assert checked, "no numeric cells were checked; fixture is unsuitable"


def test_evidence_values_are_never_silently_shortened():
    """A clipped number is worse than a missing one: this table exists so exact
    digits can be compared against the paper."""
    from paperconan._drill_render import _render_evidence

    ev = {
        "headers": ["a", "b"],
        "col_offset": 0,
        "rows": [{"row_idx": 2, "values": [-1.2345678e-100, 98765432.123456]}],
    }

    text = "\n".join(_render_evidence(ev))

    assert "-1.2345678e-100" in text, "a tiny magnitude was clipped"
    assert "98765432.123456" in text, "a long value was clipped"


def test_each_value_lands_under_its_own_header_in_its_own_row():
    """The evidence table's job is to let a reviewer point at a cell.

    Asserting that a number appears "somewhere on the page" leaves column order,
    dropped trailing columns, row numbering and the highlight markers all
    unguarded — a reviewer could read the right digits against the wrong header.
    """
    from paperconan._drill_render import _render_evidence

    # five columns, not three: a three-column fixture cannot tell "render all"
    # from "render the first three", which is the realistic refactor slip
    labels = ["alpha", "beta", "gamma", "delta", "epsilon"]
    ev = {
        "headers": labels,
        "col_offset": 0,
        "highlight_cols": [1],
        "highlight_rows": [7],
        "rows": [{"row_idx": 7, "values": [1.5, 2.5, 3.5, 4.5, 5.5]},
                 {"row_idx": 8, "values": [6.5, 7.5, 8.5, 9.5, 10.5]}],
    }

    lines = _render_evidence(ev)
    head = lines[0]
    body = [ln for ln in lines if "│" in ln][1:]

    assert len(body) == 2, "one line per row"
    # Each value sits under its own header, so column order cannot silently
    # flip. Columns are right-aligned, so it is the right edges that line up.
    for label, value in zip(labels, ["1.5", "2.5", "3.5", "4.5", "5.5"]):
        label_end = head.index(label) + len(label)
        value_end = body[0].index(value) + len(value)
        assert abs(label_end - value_end) <= 1, f"{value} is not under {label}"
    # trailing columns are rendered, not dropped
    assert "9.5" in body[1] and "10.5" in body[1]
    # spreadsheet row numbers survive, so the reviewer can find the row again
    assert "7" in body[0] and "8" in body[1]
    # the markers that say which cell matters
    assert body[0].lstrip().startswith("▸"), "highlighted row is not marked"
    assert body[1].lstrip()[0] != "▸", "a non-highlighted row was marked"
    assert "beta*" in head.replace(" ", "") or head.count("*") == 1


def test_evidence_says_how_many_rows_it_did_not_print():
    """The scan keeps up to 50 rows; the renderer shows 20 — live, not theoretical."""
    from paperconan._drill_render import _render_evidence

    ev = {"headers": ["a"], "col_offset": 0,
          "rows": [{"row_idx": i, "values": [float(i)]} for i in range(2, 33)]}

    text = "\n".join(_render_evidence(ev))

    assert "11 more rows in this window" in text


def test_evidence_handles_ragged_rows_without_crashing():
    """The widths pass indexed past a short row; the old zip() truncated instead.

    Not reachable from a scan paperconan writes — its windows are rectangular —
    but a foreign or older scan.json would surface as a raw traceback.
    """
    from paperconan._drill_render import _render_evidence

    short_then_long = {"headers": ["a", "b"], "rows": [
        {"row_idx": 2, "values": [1.0]},
        {"row_idx": 3, "values": [2.0, 3.0, 4.0]},
    ]}
    long_then_short = {"headers": ["a", "b"], "rows": [
        {"row_idx": 2, "values": [2.0, 3.0, 4.0]},
        {"row_idx": 3, "values": [1.0]},
    ]}

    for ev in (short_then_long, long_then_short):
        text = "\n".join(_render_evidence(ev))
        assert "4.0" in text, "the widest row lost a column"


@pytest.mark.parametrize("ev", [
    {"headers": [], "rows": [{"row_idx": 2, "values": [1.0]}]},
    {"headers": ["a"], "rows": [{"row_idx": 2, "values": [None, None]}]},
    {"headers": ["a"], "rows": [{"row_idx": 2, "values": ["text", 1]}]},
    {"headers": ["a"], "rows": [{"row_idx": 2, "values": []}]},
])
def test_evidence_renders_edge_shapes_without_raising(ev):
    from paperconan._drill_render import _render_evidence

    assert _render_evidence(ev)


def test_evidence_says_when_the_scan_itself_trimmed_the_window():
    from paperconan._drill_render import _render_evidence

    ev = {"headers": ["a"], "rows": [{"row_idx": 2, "values": [1.0]}], "truncated": True}

    assert "trimmed by the scan" in "\n".join(_render_evidence(ev))


def test_evidence_shows_columns_that_have_no_header():
    """zip(values, headers) silently dropped the tail; whole columns vanished."""
    from paperconan._drill_render import _render_evidence

    ev = {"headers": ["a"], "rows": [{"row_idx": 2, "values": [1.0, 42.5, 7.25]}]}

    text = "\n".join(_render_evidence(ev))

    assert "42.5" in text and "7.25" in text


def test_a_cross_sheet_finding_resolves_to_its_parameters(scan):
    """The cross-sheet lookup could be disabled entirely without a test noticing."""
    view = overview(scan)
    cross = [loc for loc in view["locations"] if loc["scope"] == "cross_sheet"]
    assert cross, "fixture has no cross-sheet location"

    loc = cross[0]
    kind = drill(scan, loc["n"])["by_kind"][0]["kind"]
    fid = drill(scan, loc["n"], kind=kind)["findings"][0]["finding_id"]

    detail = explain(scan, fid)

    assert detail["parameters"], (
        "cross-sheet finding resolved to nothing; explain would show an empty shell"
    )


def test_the_projected_severity_comes_from_the_scan_not_the_seed(scan):
    """I4: forcing effective = raw left the suite green, though most of the
    fixture's findings are genuinely demoted."""
    view = overview(scan)
    details = [
        explain(scan, f["finding_id"])
        for loc in view["locations"]
        for group in drill(scan, loc["n"])["by_kind"]
        for f in drill(scan, loc["n"], kind=group["kind"])["findings"]
    ]

    demoted = [d for d in details if d["severity"]["raw"] != d["severity"]["effective"]]

    assert demoted, (
        "no finding shows a raw/effective split; either the fixture stopped "
        "producing demotions or the two values are being read from one source"
    )
    for d in demoted:
        assert d["severity"]["context"]


def test_explain_rejects_an_unknown_finding(scan):
    with pytest.raises(ValueError, match="no such finding"):
        explain(scan, "seed:doesnotexist")


# ---------- the reachability contract ----------

def test_every_finding_is_reachable_or_declared(scan):
    """No signal may be lost between the layers.

    Walk overview → drill → drill(kind) and collect everything reachable. The
    total must account for every finding in the scan: either reachable, or
    counted in the coverage the view itself reports.
    """
    view = overview(scan)

    reached = set()
    declared = view["coverage"]["signals_not_shown"]
    for loc in view["locations"]:
        for group in drill(scan, loc["n"])["by_kind"]:
            listing = drill(scan, loc["n"], kind=group["kind"])
            reached.update(f["finding_id"] for f in listing["findings"])
            hidden = listing["coverage"]["total"] - listing["coverage"]["shown"]
            if hidden:
                # a truncated listing must say so, and counts as declared
                assert listing["coverage"]["limitations"]
                declared += hidden

    assert len(reached) + declared == view["signals_total"], (
        f"{view['signals_total'] - len(reached) - declared} findings are neither "
        "reachable nor declared missing"
    )


def test_raising_the_limit_actually_reaches_the_declared_remainder(scan):
    """"Declared but not shown" is only acceptable if it is genuinely reachable."""
    view = overview(scan)
    loc = view["locations"][0]
    biggest = max(drill(scan, loc["n"])["by_kind"], key=lambda g: g["n"])

    capped = drill(scan, loc["n"], kind=biggest["kind"], max_findings=1)
    full = drill(scan, loc["n"], kind=biggest["kind"], max_findings=10**6)

    assert capped["coverage"]["shown"] == 1
    assert full["coverage"]["shown"] == full["coverage"]["total"] == biggest["n"]
    # Positive first: a negative assertion is trivially satisfied if the notice
    # is deleted outright, which is how this survived a passing mutation.
    assert any("raise max_findings" in x for x in capped["coverage"]["limitations"]), (
        "a truncated listing did not say it was truncated"
    )
    # gone once nothing is held back...
    assert not any("raise max_findings" in x for x in full["coverage"]["limitations"])
    # ...but scan-wide caveats are not this layer's to drop. The fixture always
    # carries unrouted families, so that is the standing representative here.
    assert any("does not route" in x for x in full["coverage"]["limitations"]), (
        f"L3 dropped the scan-wide caveats: {full['coverage']['limitations']}"
    )


def test_a_family_present_but_unreachable_is_named_in_coverage(scan):
    """The reachability claim covers the whole scan, not just what gets seeded.

    Some families (digit_distribution, decimal_endings, …) are not routed by the
    layers at all. Counting them out of the expected total hides that; they have
    to be named instead, or a reviewer has no way to learn they exist.
    """
    view = overview(scan)
    present = _families_present(scan)

    reachable_families = {"relations_blocks", "cross_sheet_findings"}
    unreachable = {k: n for k, n in present.items() if k not in reachable_families}
    assert unreachable, "fixture no longer exercises an unrouted family"

    declared = json.dumps(view["coverage"], ensure_ascii=False)
    for family, count in unreachable.items():
        assert family in declared, (
            f"{count} {family} are in the scan but no coverage field mentions them"
        )


def test_overview_carries_the_limitations_the_seeding_layer_recorded(scan,
                                                                    monkeypatch):
    """overview must not drop what _build_clusters already knew it missed."""
    import paperconan._audit as audit
    monkeypatch.setattr(audit, "_MAX_FINDINGS_PER_BLOCK", 1)

    from paperconan import scan_dir
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = __import__("pathlib").Path(td) / "d"
        d.mkdir()
        rows = ["a,b,c,d"] + [
            ",".join(str(round((i + 1) * (j + 1) * 1.017, 6)) for j in range(4))
            for i in range(20)
        ]
        (d / "p.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        capped = scan_dir(str(d), str(d.parent / "out"), write_html=False)

    view = overview(capped)

    assert capped.get("findings_omitted"), "fixture did not trip the finding cap"
    assert any("finding caps" in x for x in view["coverage"]["limitations"]), (
        "overview dropped the upstream cap the seeding layer reported"
    )
    assert view["coverage"]["complete"] is False


def test_a_truncated_overview_declares_what_it_left_out(scan):
    view = overview(scan, max_locations=1)

    assert len(view["locations"]) == 1
    assert view["coverage"]["locations_not_shown"] > 0
    assert view["coverage"]["signals_not_shown"] > 0
    # positive, not `assert limitations` — the always-present detector-caps line
    # satisfies that on its own, so it says nothing about truncation
    assert any("max_locations" in x for x in view["coverage"]["limitations"]), (
        "a truncated overview did not say which locations it dropped"
    )


def test_an_all_locations_hidden_overview_does_not_read_as_a_clean_paper(scan):
    """--max-locations 0 empties the page but not the scan; the all-clear text
    would then contradict the header and the coverage line on one screen."""
    from paperconan._drill_render import render_overview

    text = render_overview(overview(scan, max_locations=0))

    assert "no locations carry signal" not in text
    assert "free of problems" not in text
    assert "max_locations" in text


def test_the_views_are_deterministic(scan):
    assert overview(scan) == overview(scan)
    assert drill(scan, 1) == drill(scan, 1)


def test_a_demotion_from_any_source_carries_its_reason(scan):
    """Demotions are recorded in four different places by four code paths.

    Reading only `false_positive_context` would show three of them as demoted
    with no reason — and a strong signal shown as 'low' with no explanation is
    how a real finding gets dismissed.
    """
    from paperconan._drill import _demotion_reasons

    assert _demotion_reasons({"false_positive_context": ["axis_or_scan_column"]})
    assert _demotion_reasons({"dense_block": True})
    assert _demotion_reasons({"reused_progression": True})
    assert _demotion_reasons({"within_col_flood_sheet": True})
    assert _demotion_reasons({"prefilter_reason": "within_col_sheet_flood"})
    assert _demotion_reasons({}) == []


# ---------- CLI ----------

def _cli(*args, cwd=None):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-m", "paperconan", *args],
                          text=True, capture_output=True, cwd=cwd)


@pytest.fixture(scope="module")
def scan_path(scan, tmp_path_factory):
    p = tmp_path_factory.mktemp("cli") / "scan.json"
    p.write_text(json.dumps(scan), encoding="utf-8")
    return str(p)


def test_cli_walks_the_three_layers(scan_path):
    ov = _cli("overview", scan_path)
    assert ov.returncode == 0, ov.stderr
    assert "next: paperconan drill" in ov.stdout

    dr = _cli("drill", scan_path, "1")
    assert dr.returncode == 0, dr.stderr
    assert "next: paperconan drill" in dr.stdout and "--kind" in dr.stdout

    kind = drill(json.loads(open(scan_path).read()), 1)["by_kind"][0]["kind"]
    lst = _cli("drill", scan_path, "1", "--kind", kind)
    assert lst.returncode == 0, lst.stderr
    assert "next: paperconan explain" in lst.stdout

    fid = [ln.strip() for ln in lst.stdout.splitlines() if ln.strip().startswith("seed:")][0]
    ex = _cli("explain", scan_path, fid)
    assert ex.returncode == 0, ex.stderr
    assert "evidence:" in ex.stdout


def test_cli_explain_states_that_a_signal_is_not_a_conclusion(scan_path):
    kind = drill(json.loads(open(scan_path).read()), 1)["by_kind"][0]["kind"]
    lst = _cli("drill", scan_path, "1", "--kind", kind)
    fid = [ln.strip() for ln in lst.stdout.splitlines() if ln.strip().startswith("seed:")][0]

    out = _cli("explain", scan_path, fid).stdout

    assert "statistical signal, not a conclusion" in out


def test_cli_json_mode_emits_the_structure(scan_path):
    res = _cli("overview", scan_path, "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["locations"]


@pytest.mark.parametrize("args", [
    ("drill", "9999"),
    ("explain", "seed:nope"),
])
def test_cli_unknown_reference_exits_with_a_message_not_a_traceback(scan_path, args):
    res = _cli(args[0], scan_path, args[1])

    assert res.returncode != 0
    assert "Traceback" not in res.stderr
    assert "no such" in (res.stderr + res.stdout)


def test_an_unknown_kind_is_an_error_not_an_empty_list(scan):
    """Silence would read as "this location has none of those", which is a
    different claim from "you typed a kind that does not exist here"."""
    with pytest.raises(ValueError, match="no such kind"):
        drill(scan, 1, kind="totally_made_up_kind")


def test_cli_unknown_kind_lists_what_is_available(scan_path):
    res = _cli("drill", scan_path, "1", "--kind", "totally_made_up_kind")

    assert res.returncode != 0
    assert "Traceback" not in res.stderr
    assert "no such kind" in (res.stderr + res.stdout)


@pytest.mark.parametrize("content", ["[]", "null", '{"a": 1}', '"text"'])
def test_cli_rejects_json_that_is_not_a_scan(tmp_path, content):
    """Valid JSON that is not a scan must not render as "0 signals" — that reads
    as a clean paper when it actually means the wrong file was opened."""
    bad = tmp_path / f"bad{abs(hash(content))}.json"
    bad.write_text(content, encoding="utf-8")

    res = _cli("overview", str(bad))

    assert res.returncode != 0
    assert "Traceback" not in res.stderr
    assert "does not look like" in (res.stderr + res.stdout)


def test_cli_rejects_the_workflow_artifacts_that_sit_beside_a_scan(tmp_path):
    """The realistic wrong-file case, not a synthetic one.

    Every workflow artifact carries `schema_version`, so a one-of gate let
    `overview states/s000.json` render "0 signals" — and the empty-overview text
    then asserts the detectors found nothing about a file never scanned.
    """
    from paperconan._workflow import start_workflow

    data = tmp_path / "data"
    data.mkdir()
    rows = ["a,b"] + [f"{i + 1},{(i + 1) * 2}" for i in range(12)]
    (data / "p.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = tmp_path / "wf"
    start_workflow(str(data), str(out))

    for rel in ("workflow_state.json", "states/s000.json",
                "steps/t000/candidate_packet.json"):
        res = _cli("overview", str(out / rel))
        assert res.returncode != 0, f"{rel} was accepted as a scan"
        assert "does not look like" in (res.stderr + res.stdout), rel


def test_cli_explain_names_parameters_it_cannot_render(scan_path):
    """Sample values live in list parameters — the numbers a reviewer checks
    against the paper. Dropping them silently from the text is the same defect
    the family list had at L1."""
    with open(scan_path, encoding="utf-8") as fh:
        s = json.load(fh)
    fid = None
    for loc in overview(s)["locations"]:
        for group in drill(s, loc["n"])["by_kind"]:
            for f in drill(s, loc["n"], kind=group["kind"])["findings"]:
                params = explain(s, f["finding_id"])["parameters"]
                if any(isinstance(v, (list, dict)) for v in params.values()):
                    fid = f["finding_id"]
                    break
            if fid:
                break
        if fid:
            break
    assert fid, "fixture has no finding with structured parameters"

    out = _cli("explain", scan_path, fid).stdout

    assert "structured parameter" in out, (
        "list/dict parameters were dropped from the text with no notice"
    )


def test_cli_empty_overview_says_that_quiet_is_not_clean(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({
        "tool": "paperconan", "schema_version": 1, "n_files": 1,
        "relations_blocks": [], "cross_sheet_findings": [],
    }), encoding="utf-8")

    res = _cli("overview", str(empty))

    assert res.returncode == 0, res.stderr
    assert "not that the paper is free of problems" in res.stdout


def test_cli_overview_renders_the_high_count_value(scan_path):
    """strongest alone cannot distinguish 1 high among 200 from 60 among 60.

    Asserts the rendered number, not the column label — an earlier version
    checked only the header and stayed green with the value removed.
    """
    with open(scan_path, encoding="utf-8") as fh:
        expected = overview(json.load(fh))["locations"][0]

    res = _cli("overview", scan_path)

    assert res.returncode == 0, res.stderr
    row = next(ln for ln in res.stdout.splitlines() if ln.strip().startswith("1 "))
    trailing = [int(x) for x in re.findall(r"\d+", row)][-2:]
    assert trailing == [expected["signals"], expected["high"]], (
        f"row ends with {trailing}, expected signals={expected['signals']} "
        f"then high={expected['high']}"
    )


def test_cli_overview_marks_a_truncated_family_list(tmp_path):
    """L1 must not silently drop the tail of a long family list."""
    from paperconan._drill_render import render_overview

    view = {
        "files": 1, "signals_total": 9,
        "locations": [{
            "n": 1, "cluster_id": "cluster:x", "scope": "block",
            "location": "f.csv :: s", "strongest": "high", "signals": 9, "high": 9,
            "families": ["a", "b", "c", "d", "e", "f"],
        }],
        "coverage": {"locations_total": 1, "locations_not_shown": 0,
                     "signals_not_shown": 0, "limitations": []},
    }

    text = render_overview(view)

    assert "2 more" in text, "families were truncated without saying so"


def test_cli_drill_prints_coverage_at_the_kind_grouping_layer(scan_path):
    """SKILL.md tells the agent to read coverage at every layer."""
    res = _cli("drill", scan_path, "1")

    assert res.returncode == 0, res.stderr
    assert "!" in res.stdout, "no coverage line at the grouping layer"


def test_cli_rejects_a_scan_that_is_not_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    res = _cli("overview", str(bad))

    assert res.returncode != 0
    assert "Traceback" not in res.stderr
    assert "not valid JSON" in (res.stderr + res.stdout)


# ---------- collisions and ordering, both found by running on real papers ----------

def _saturated_scan(noisy_blocks=30, per_block=150, real_blocks=6):
    """Shaped like a real supplement.

    `noisy_blocks` blocks where one family saturates the per-block cap, and a few
    carrying a single strong signal. Deliberately more noisy blocks than the
    default page holds — with fewer, a diversity assertion passes for free.
    """
    blocks = []
    for b in range(noisy_blocks):
        blocks.append({
            "file": "big.xlsx", "sheet": f"Supplementary Figure {b}",
            "block": {"rows": "3-53", "cols": f"{30 + b}-53", "header": []},
            "row_pairs": [
                {"kind": "integer_diff_shared_fraction", "severity": "high",
                 "raw_severity": "high", "n": 20,
                 "rule": f"col[{i}] and col[{i + 1}] share the same decimal fraction"}
                for i in range(per_block)
            ],
        })
    for b in range(real_blocks):
        blocks.append({
            "file": "big.xlsx", "sheet": "Supplementary Figure 8e",
            "block": {"rows": "4-9", "cols": f"{15 + b * 22}-{21 + b * 22}", "header": []},
            "relations": [
                {"kind": "identical_column", "severity": "high", "raw_severity": "high",
                 "n": 6, "rule": f"col[{16 + b * 22}] == col[{18 + b * 22}]"}
            ],
        })
    return {"tool": "paperconan", "schema_version": 1, "n_files": 1,
            "relations_blocks": blocks, "cross_sheet_findings": []}


def test_a_saturating_family_cannot_fill_the_default_page():
    """Measured on a real paper: the duplicated columns the report was about
    landed at rank 25-47, past the default page, because three blocks where one
    family hit the per-block cap outranked them on high-count.

    A family saturating one block is evidence about that family, not about the
    paper. Thirty such blocks must not take all twenty slots.
    """
    view = overview(_saturated_scan(), max_locations=20)
    first_families = [loc["families"][0] for loc in view["locations"]]

    assert "identical_column" in first_families, (
        "a 6-row exactly-duplicated column never reached the default page; "
        f"it was filled with {set(first_families)}"
    )


def test_the_default_page_shows_more_than_one_family():
    view = overview(_saturated_scan(), max_locations=20)

    shown = {loc["families"][0] for loc in view["locations"]}
    assert len(shown) >= 2, f"the whole page is one family: {shown}"


def test_ordering_stays_deterministic_under_diversity():
    scan = _saturated_scan()
    assert overview(scan) == overview(scan)


def test_colliding_seed_ids_are_disambiguated_not_refused():
    """Real supplements collide. Refusing the packet denies the reader every
    other finding in the paper over a gap in one locator — measured: a real
    Nature Metabolism supplement produced 10 collisions and no output at all.
    """
    from paperconan._workflow import _build_clusters

    dup = {"kind": "identical_column", "severity": "high", "raw_severity": "high",
           "n": 6, "rule": "col[2] == col[5]"}
    scan = {"relations_blocks": [{
        "file": "f.xlsx", "sheet": "S", "block": {"rows": "2-9", "cols": "1-9", "header": []},
        "relations": [dict(dup), dict(dup), dict(dup)],
    }], "cross_sheet_findings": []}

    clusters, coverage = _build_clusters(scan, max_clusters=10)

    seeds = [s for c in clusters for s in c["seeds"]]
    ids = [s["seed_id"] for s in seeds]
    assert len(ids) == 3, "findings were dropped instead of disambiguated"
    assert len(set(ids)) == 3, f"ids still collide: {ids}"
    assert coverage["seed_ids_disambiguated"] == 2
    assert coverage["coverage_complete"] is False
    assert any("shared a locator" in x for x in coverage["limitations"])


# ---------- the invariants, run against a scan big enough to merge and interleave ----------

def test_every_overview_number_opens_that_location_on_a_saturated_scan():
    """L1's ordinal is the only handle the reader is given.

    overview merges panels and interleaves families; drill used to resolve
    against the raw cluster list, so the number printed by one layer opened a
    different location in the next.

    Not a large-scan effect: _merge_panels set block_cols=None on every panel
    including single-member ones, so the labels diverged at every fixture size
    in this file. What was missing was any test that compared the two layers at
    all. This one does it on a scan big enough that the reordering also bites.
    """
    scan = _saturated_scan()
    view = overview(scan, max_locations=20)

    assert len(view["locations"]) == 20, "fixture no longer fills a page"
    mismatched = [
        (loc["n"], loc["location"], drill(scan, loc["n"])["location"])
        for loc in view["locations"]
        if drill(scan, loc["n"])["location"] != loc["location"]
    ]
    assert not mismatched, (
        f"{len(mismatched)} overview numbers open a different location: {mismatched[:3]}"
    )


def test_a_merged_panel_opens_with_every_finding_it_was_counted_for():
    """The count on L1 and the findings on L2 have to be the same set.

    _merge_panels folds several column spans into one panel and kept only the
    first member's cluster_id, so drilling it reached one span and reported
    `kinds_total == kinds_shown` -- a truncated view declaring itself complete,
    which is this tool's worst failure.
    """
    scan = _saturated_scan()
    view = overview(scan, max_locations=20)

    # Compared against the unmerged clusters, not against drill: both layers read
    # one list now, so a panel that dropped its members' seeds would report the
    # same reduced count at every layer and any consistency check would hold.
    # The property that matters is that merging loses nothing.
    from paperconan._drill import _clusters_of, _merge_panels

    raw, _seeding = _clusters_of(scan)
    before = sum(len(c["seeds"]) for c in raw)
    after = sum(len(p["seeds"]) for p in _merge_panels(raw))
    assert after == before, (
        f"merging panels dropped {before - after} of {before} findings"
    )

    merged = [loc for loc in view["locations"] if loc["signals"] > 1]
    assert merged, "fixture no longer produces a location with several findings"
    for loc in merged[:6]:
        opened = drill(scan, loc["n"])
        shown = sum(group["n"] for group in opened["by_kind"])
        assert shown == loc["signals"], (
            f"location #{loc['n']} was counted for {loc['signals']} findings and "
            f"opens with {shown}"
        )


def test_a_merged_panel_still_names_where_its_evidence_is():
    """Merging must not cost the reader the column span.

    The panel used to set block_cols=None so the label dropped the span
    entirely, leaving a row range and no way to find the cells.
    """
    scan = _saturated_scan()
    view = overview(scan, max_locations=20)

    spanned = [loc for loc in view["locations"] if "rows" in loc["location"]]
    assert spanned, "fixture no longer produces a block-scoped location"
    assert all("cols" in loc["location"] for loc in spanned), (
        f"a block location names no column span: "
        f"{[loc['location'] for loc in spanned if 'cols' not in loc['location']][:3]}"
    )
    # A panel built from several column groups has to say so, or the reader is
    # sent to one group's cells for evidence that is spread across four.
    multi = [loc for loc in view["locations"] if loc["signals"] > 1
             and "more" in loc["location"]]
    assert multi, (
        f"no merged panel discloses its extra column groups: "
        f"{[l['location'] for l in view['locations'][:5]]}"
    )


def test_merging_re_ranks_so_the_strongest_panel_leads():
    """Merging changes the counts the ranking is built on.

    Without a re-rank a panel whose evidence arrived split across several column
    groups sorts on its first fragment's count, so a panel with more high
    findings ends up behind one with fewer.
    """
    def block(sheet, cols, n_high):
        return {
            "file": "split.xlsx", "sheet": sheet,
            "block": {"rows": "3-40", "cols": cols, "header": []},
            "relations": [
                {"kind": "identical_column", "severity": "high",
                 "raw_severity": "high", "n": 6,
                 "rule": f"col[{cols}] == col[{i}]"}
                for i in range(n_high)
            ],
        }

    # One panel, seven high findings arriving split across four column groups,
    # none of which alone beats the rival.
    blocks = [block("Fig 1a", "0-2", 2), block("Fig 1a", "3-5", 2),
              block("Fig 1a", "6-8", 2), block("Fig 1a", "9-11", 1)]
    # A rival that arrives whole with six.
    blocks.append(block("Fig 2b", "0-9", 6))
    scan = {"relations_blocks": blocks, "cross_sheet_findings": []}

    leader = overview(scan, max_locations=10)["locations"][0]

    assert leader["high"] == 7, (
        f"the merged seven-high panel did not lead; got {leader['location']} "
        f"with {leader['high']} high"
    )


def test_explain_serves_the_right_finding_for_a_disambiguated_id():
    """L4 has to answer for the finding the id names, not the first one like it.

    Colliding seed ids get a `#N` suffix. _match_raw_finding recomputed the bare
    hash and compared it to the suffixed id, so it never matched: explain
    returned an empty evidence table and the profile defaults -- reporting a
    finding as kept when the profile had demoted it. The suffix is assigned over
    the whole seed list, so recovering the finding means replaying that
    enumeration, not parsing the suffix as an index into one block.
    """
    twin = {
        "kind": "identical_column", "severity": "high", "raw_severity": "high",
        "n": 6, "rule": "col[1] == col[3]",
    }
    first = dict(twin, profile_action="kept",
                 evidence={"rows": ["r1"], "cols": ["c1"], "cells": [[1.5]]})
    second = dict(twin, profile_action="demoted",
                  false_positive_context=["shared axis"],
                  evidence={"rows": ["r9"], "cols": ["c9"], "cells": [[9.5]]})
    scan = {
        "relations_blocks": [{
            "file": "t.xlsx", "sheet": "Fig 1a",
            "block": {"rows": "3-9", "cols": "1-4", "header": []},
            "relations": [first, second],
        }],
        "cross_sheet_findings": [],
    }

    from paperconan._drill import _clusters_of

    clusters, _seeding = _clusters_of(scan)
    ids = [s["seed_id"] for c in clusters for s in c["seeds"]]
    assert any("#" in i for i in ids), (
        f"fixture no longer produces colliding ids: {ids}"
    )

    bare = next(i for i in ids if "#" not in i)
    suffixed = next(i for i in ids if "#" in i)

    assert explain(scan, bare)["evidence"] == first["evidence"]
    got = explain(scan, suffixed)
    assert got["evidence"] == second["evidence"], (
        f"the suffixed id served the wrong finding's evidence: {got['evidence']}"
    )
    # The falsehood this fixes: with no raw match, profile_action fell back to
    # its "kept" default, so a demoted finding was reported as kept.
    assert got["severity"]["profile_action"] == "demoted", (
        f"explain reported {got['severity']['profile_action']!r} for a demoted finding"
    )
    assert "shared axis" in str(got["severity"]["context"]), (
        f"the demotion reason did not reach the reader: {got['severity']}"
    )


def test_a_member_cluster_id_opens_the_panel_that_absorbed_it():
    """Ids outlive the merge that hid them.

    explain reports the member cluster's own id, and the workflow packet cites
    raw cluster ids -- neither of which appears in overview's listing once the
    panel absorbs them. If _resolve did not accept a member id, those ids would
    reference nothing. Deleting that branch left the whole suite green.
    """
    scan = _saturated_scan()
    view = overview(scan, max_locations=20)
    panel = next(loc for loc in view["locations"] if "more" in loc["location"])

    from paperconan._drill import _clusters_of, _merge_panels

    raw, _seeding = _clusters_of(scan)
    merged = next(p for p in _merge_panels(raw) if p["cluster_id"] == panel["cluster_id"])
    members = [m for m in merged["member_ids"] if m != merged["cluster_id"]]
    assert members, "fixture no longer produces a panel with absorbed members"

    opened = drill(scan, members[0])
    assert opened["cluster_id"] == panel["cluster_id"], (
        f"a member id opened {opened['cluster_id']} instead of its panel "
        f"{panel['cluster_id']}"
    )

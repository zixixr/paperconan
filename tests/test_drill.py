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


# Every finding-bearing list a scan can carry. Deliberately enumerated from the
# scan's own shape rather than from the seeding layer's BLOCK_FINDING_GROUPS: an
# earlier version counted only what the implementation seeds, so families it
# skips were absent from both the reachable set and the expected total, and the
# reachability test was green while six findings were unaccounted for.
_SCAN_FINDING_KEYS = (
    "cross_sheet_findings", "image_findings",
    "digit_distribution", "decimal_endings", "decimal_tail_clusters",
)


def _seeded_finding_count(scan):
    """What the layers are expected to route."""
    n = sum(len(b.get(g) or []) for b in scan.get("relations_blocks") or []
            for g in BLOCK_FINDING_GROUPS)
    return n + len(scan.get("cross_sheet_findings") or [])


def _families_present(scan):
    """Every finding-bearing family actually in this scan, with its count."""
    present = {k: len(scan.get(k) or []) for k in _SCAN_FINDING_KEYS if scan.get(k)}
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

    # distinct ids must not collapse onto one evidence table
    by_evidence = {}
    for fid, detail in pairs:
        ev = json.dumps(detail.get("evidence"), sort_keys=True)
        by_evidence.setdefault((detail["location"], detail["kind"], ev), []).append(fid)
    for key, ids in by_evidence.items():
        if len(ids) > 1:
            rules = {explain(scan, i)["rule"] for i in ids}
            assert len(rules) == len(ids) or len(rules) == 1, (
                f"{len(ids)} ids share one evidence table at {key[0]}"
            )


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
    assert not full["coverage"]["limitations"]


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
    assert view["coverage"]["limitations"]


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


def test_cli_empty_overview_says_that_quiet_is_not_clean(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({
        "tool": "paperconan", "schema_version": 1, "n_files": 1,
        "relations_blocks": [], "cross_sheet_findings": [],
    }), encoding="utf-8")

    res = _cli("overview", str(empty))

    assert res.returncode == 0, res.stderr
    assert "not that the paper is free of problems" in res.stdout


def test_cli_overview_shows_the_high_count_per_location(scan_path):
    """strongest alone cannot distinguish 1 high among 200 from 60 among 60."""
    res = _cli("overview", scan_path)

    assert "high" in res.stdout.splitlines()[2], "no high column in the header"


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

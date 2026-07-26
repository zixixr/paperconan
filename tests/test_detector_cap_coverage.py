"""Detector-level caps have to reach the scan's coverage.

Several detectors stop at their own `max_findings` or compute budget. Until now
they reported that to stderr at best, and for some paths to nothing at all — so
`scan_status` stayed "complete" and every consumer downstream, including the
layered views and the Agent workflow, reported full coverage over a block whose
enumeration had been cut short.

For a tool used to decide whether a paper's numbers need author clarification,
a silently shortened search is the worst failure mode available: it looks
exactly like a clean result.
"""
from __future__ import annotations

import pytest

from paperconan import scan_dir
from paperconan._coverage import ScanCoverage


def _panel(path, rows=40, cols=14):
    """A block dense enough that the relation detectors have plenty to chew on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ",".join(f"c{j}" for j in range(cols))
    lines = [header]
    for i in range(rows):
        vals = [round((i + 1) * (j + 1) * 1.017, 6) for j in range(cols)]
        if cols > 5:
            vals[5] = vals[2]
        if cols > 9:
            vals[9] = round(vals[3] * 1.13, 6)
        lines.append(",".join(str(v) for v in vals))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _shared_tail_within_row():
    """Cells in one row sharing a >=6-digit fractional tail, integer parts apart.

    _WITHIN_ROW_FRAC_MIN_DIGITS is 6, so a shorter tail is not evidence and the
    detector rejects it. Six such cells per row give the detector more than one
    finding to report, which is what a result cap needs in order to bite.
    """
    rows = [[f"c{j}" for j in range(12)]]
    tails = (0.316768, 0.847215, 0.529437, 0.163094)
    for i in range(8):
        tail = tails[i % len(tails)]
        row = [round((j + 1) * 7 + tail, 6) for j in range(6)]
        row += [round(1.0173 * (i + 1) * (j + 1), 6) for j in range(6)]
        rows.append(row)
    return rows


def _shared_tail_across_rows():
    """Row pairs sharing a fractional tail over >=_ROW_PAIR_MIN_RUN aligned columns."""
    rows = [[f"c{j}" for j in range(12)]]
    for i in range(8):
        base = [round(0.1234 + j * 0.0917 + i * 0.0031, 6) for j in range(12)]
        rows.append(base)
        rows.append([round(v + 10 * (j + 1), 6) for j, v in enumerate(base)])
    return rows


def _repeated_short_rows():
    """Short high-precision rows repeated verbatim, one repeat per band.

    detect_short_row_reuse wants 3..11 columns at >=5 significant figures, and
    _short_row_candidates drops any row _vector_is_patterned accepts -- so the
    values have to be irregular, not a progression. Each pair is separated by a
    label row so the two rows sit in different bands.
    """
    vectors = (
        (13.40712, 27.91834, 8.52619, 41.06375, 19.73408, 33.28951),
        (22.61483, 9.34026, 37.15792, 14.80613, 29.47158, 6.92374),
        (31.08627, 17.25913, 44.63081, 11.39754, 26.81420, 38.54269),
        (7.83415, 35.60298, 20.14763, 42.97531, 15.28640, 28.36107),
    )
    rows = [[f"c{j}" for j in range(6)]]
    for i, vec in enumerate(vectors):
        rows.append(list(vec))
        rows.append([f"panel {i}", None, None, None, None, None])
        rows.append(list(vec))
    return rows


_DETECTOR_GRIDS = {
    "detect_within_row_shared_fraction": _shared_tail_within_row,
    "detect_row_pair_shared_fraction": _shared_tail_across_rows,
    "detect_short_row_reuse": _repeated_short_rows,
}


def _grids_for(name):
    """The fixture that drives `name` past its cap.

    Most detectors are driven by the shared _two_sheets() block. The two
    shared-fraction detectors gate on fractional-digit agreement, which that
    block does not produce, so they get a grid built to their own contract.
    """
    from paperconan._sheet import Sheet

    build = _DETECTOR_GRIDS.get(name)
    if build is None:
        return _two_sheets()
    return {(f"{s}.csv", s): Sheet.from_rows(build()) for s in ("Figure 1a", "Figure 2b")}


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(",".join("" if v is None else str(v) for v in r) for r in rows) + "\n",
        encoding="utf-8")


def _grid_for_pool_cap(detector):
    """A grid with more candidate rows than the pool cap under test.

    Each detector builds its candidate list through its own gates, so a grid has
    to be chosen per detector: _shared_tail_across_rows only overflows the
    row-pair cap, while _repeated_short_rows happens to overflow both.
    """
    if detector == "detect_row_pair_shared_fraction":
        return _shared_tail_across_rows()
    return _repeated_short_rows()


def _reasons(scan):
    return [item.get("reason") for item in (scan.get("coverage") or {}).get("limitations") or []]


# ---------- the compute budgets ----------

def test_an_exhausted_row_relation_budget_reaches_coverage(tmp_path, monkeypatch):
    """Previously printed "coverage bounded" to stderr and nowhere else."""
    import paperconan._audit as audit
    monkeypatch.setattr(audit, "_ROW_REL_BUDGET", 1)

    _panel(tmp_path / "d" / "p.csv")
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    assert "detector_compute_budget_limit" in _reasons(scan), _reasons(scan)
    assert scan["scan_status"] != "complete"
    assert scan["coverage"]["truncated"] is True


# ---------- the per-detector result caps ----------

_CAPPED_DETECTORS = (
    "detect_recurring_row_vectors",
    "detect_scaled_row_reuse",
    "detect_short_row_reuse",
    "detect_within_row_shared_fraction",
    "detect_row_pair_shared_fraction",
)


def _two_sheets():
    from paperconan._sheet import Sheet

    grids = {}
    # figure-shaped sheet names: the cross-figure pass is skipped entirely
    # unless two distinct figure keys are present, which also means a fixture
    # named "a"/"b" can only ever exercise the within-row pass.
    for sheet in ("Figure 1a", "Figure 2b"):
        rows = [[f"c{j}" for j in range(12)]]
        base = [round(0.1234567 + j * 0.0173219, 7) for j in range(12)]
        for i in range(24):
            if i % 4 == 0:
                rows.append(list(base))                          # exact repeat
            elif i % 4 == 1:
                rows.append([round(v * 1.13, 7) for v in base])   # constant ratio
            elif i % 4 == 2:
                rows.append([round(v + 100, 7) for v in base])    # shared tail
            else:
                rows.append([round(v * (i + 1) * 1.017, 7) for v in base])
        grids[(f"{sheet}.csv", sheet)] = Sheet.from_rows(rows)
    return grids


@pytest.mark.parametrize("name", _CAPPED_DETECTORS)
def test_each_capped_detector_reports_reaching_its_finding_limit(name):
    """Every capped detector is wired to the coverage object.

    Behavioural for all five: each gets a fixture built to its own gates and is
    driven past its cap for real. An earlier version fell back to asserting on
    inspect.getsource() where the shared fixture produced nothing, which made an
    unwired detector look the same as an un-driven one -- and passed for a call
    carrying the wrong reason string and for one made unreachable.
    """
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)

    grids = _grids_for(name)
    natural = len(getattr(audit, name)(grids, profile="review", max_findings=10**6))
    # Every capped detector gets a fixture that actually drives it past its cap.
    # An earlier version fell back to asserting on inspect.getsource() when the
    # shared fixture produced nothing; that check passed for a call with the
    # wrong reason string and for one made provably unreachable, so two unwired
    # detectors survived it.
    assert natural >= 2, (
        f"{name}'s fixture no longer produces enough findings to reach a cap "
        f"(got {natural}); fix the fixture rather than weakening the assertion"
    )

    getattr(audit, name)(grids, profile="review", max_findings=1, coverage=coverage)
    reasons = [item["reason"] for item in coverage.to_dict()["limitations"]]
    assert "detector_finding_limit" in reasons, (
        f"{name} was cut short without recording it: {reasons}"
    )


def test_a_detector_that_actually_fills_its_cap_records_it():
    """The end-to-end half: real findings, a real cap, a real limitation."""
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)

    found = audit.detect_scaled_row_reuse(_two_sheets(), profile="review",
                                          max_findings=1, coverage=coverage)

    assert len(found) >= 1, "fixture no longer produces findings for this detector"
    reasons = [item["reason"] for item in coverage.to_dict()["limitations"]]
    assert "detector_finding_limit" in reasons


@pytest.mark.parametrize("name", _CAPPED_DETECTORS)
def test_a_detector_below_its_cap_records_nothing(name):
    """The notice has to mean something: no cap reached, no limitation."""
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)

    # _grids_for, not _two_sheets: that fixture yields nothing for three of the
    # five detectors, so `_capped` was unreachable and this assertion held by
    # construction. Making a detector report its finding limit whenever it found
    # anything then left the suite green -- the exact bug named above.
    grids = _grids_for(name)
    found = getattr(audit, name)(grids, profile="review", max_findings=10**6,
                                 coverage=coverage)

    assert found, f"{name}'s fixture produces nothing, so 'below its cap' is vacuous"
    assert not coverage.to_dict()["limitations"], (
        f"{name} reported a cap it never reached"
    )


def test_an_uncapped_scan_records_no_detector_limitation(tmp_path):
    """The notice must mean something: a normal scan must stay clean."""
    _panel(tmp_path / "d" / "p.csv", rows=12, cols=4)

    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    detector_reasons = [r for r in _reasons(scan)
                        if "detector" in (r or "") or "cap" in (r or "")]
    assert not detector_reasons, detector_reasons
    assert scan["scan_status"] == "complete"


# ---------- the workflow caveat can now be retired ----------

def test_the_unreported_cap_caveat_stays_until_every_cap_is_wired():
    """Result caps are wired; many resource caps are not.

    _MAX_BLOCK_COLS drops detect_relations entirely on a wide block, and
    detect_row_relations' row ceiling drops an exact relation at 61 rows -- both
    while the scan calls itself complete. Flipping this flag on partial wiring
    replaced an over-broad but true caveat with a false all-clear, which is
    strictly worse for a tool whose job is to not miss things. (No count is
    cited: an earlier version gave one that no reader could verify.)
    """
    from paperconan._workflow import DETECTOR_CAPS_REPORTED

    assert DETECTOR_CAPS_REPORTED is False


def test_a_block_wider_than_the_column_cap_is_still_an_unreported_gap():
    """Pins the gap the caveat exists for, so it cannot be quietly forgotten.

    A planted identical column is found at 110 columns and lost at 130, and the
    scan reports complete either way. When this starts failing, the cap has been
    wired and DETECTOR_CAPS_REPORTED can be revisited.
    """
    import tempfile
    from pathlib import Path

    from paperconan import BLOCK_FINDING_GROUPS

    def kinds_at(width):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "d"
            d.mkdir()
            header = ",".join(f"c{j}" for j in range(width))
            lines = [header]
            for i in range(15):
                vals = [round((i + 1) * (j + 1) * 1.017, 6) for j in range(width)]
                vals[3] = vals[1]          # an exactly duplicated column
                lines.append(",".join(str(v) for v in vals))
            (d / "p.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            scan = scan_dir(str(d), str(Path(td) / "out"), write_html=False)
        found = {f.get("kind") for b in scan["relations_blocks"]
                 for g in BLOCK_FINDING_GROUPS for f in (b.get(g) or [])}
        return found, scan["scan_status"]

    narrow_kinds, narrow_status = kinds_at(110)
    wide_kinds, wide_status = kinds_at(130)

    assert "identical_column" in narrow_kinds, "fixture no longer plants a signal"
    assert "identical_column" not in wide_kinds, (
        "the wide-block cap appears to have been wired — revisit "
        "DETECTOR_CAPS_REPORTED and this test"
    )
    assert narrow_status == "complete"
    assert wide_status == "complete", (
        "the wide-block skip now reaches coverage; update DETECTOR_CAPS_REPORTED"
    )


def test_a_scan_limitation_reads_as_a_sentence_not_a_python_repr():
    """These land in a terminal; a dict repr is not something to hand a reader."""
    from paperconan._workflow import _describe_scan_limitation

    text = _describe_scan_limitation({
        "scope": "detector", "reason": "detector_compute_budget_limit",
        "detector": "detect_row_relations", "rows": 60, "cols": 14,
    })

    assert "{" not in text and "'" not in text
    assert "detector compute budget limit" in text
    assert "detect_row_relations" in text
    assert "rows=60" in text


def test_an_ordinary_table_is_not_reported_as_truncated(tmp_path):
    """The commonest supplementary shape must not read as a partial scan.

    A branch added and then withdrawn here fired on 61x14 purely for being tall,
    which would have made "partial" the near-universal state and taught readers
    to skip the coverage line entirely.
    """
    import random
    random.seed(3)
    d = tmp_path / "d"
    d.mkdir(parents=True, exist_ok=True)
    rows = [",".join(f"c{j}" for j in range(14))]
    for _ in range(200):
        rows.append(",".join(str(round(random.uniform(1, 999), 4)) for _ in range(14)))
    (d / "p.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    scan = scan_dir(str(d), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "complete", _reasons(scan)
    assert scan["coverage"]["truncated"] is False


def test_a_dense_table_that_really_fills_a_cap_is_reported(tmp_path):
    """The other side of the same coin: when a cap genuinely bites, say so."""
    _panel(tmp_path / "d" / "p.csv", rows=200, cols=14)

    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    assert "detector_finding_limit" in _reasons(scan), _reasons(scan)
    assert scan["scan_status"] == "partial"


def test_a_result_cap_equal_to_the_natural_output_still_reports(tmp_path):
    """Documents a known boundary rather than leaving it to be rediscovered.

    Reaching the cap on the final iteration is indistinguishable, at the break
    site, from being cut short. It errs toward "there may be more", which is the
    safe direction here — a false all-clear is the failure this tool cannot
    afford. Narrowing it needs per-break knowledge of what remained unexamined.
    """
    import paperconan._audit as audit

    natural = len(audit.detect_scaled_row_reuse(_two_sheets(), profile="review",
                                                max_findings=10**6))
    coverage = ScanCoverage(files_discovered=1)

    audit.detect_scaled_row_reuse(_two_sheets(), profile="review",
                                  max_findings=natural, coverage=coverage)

    reasons = [i["reason"] for i in coverage.to_dict()["limitations"]]
    assert "detector_finding_limit" in reasons, (
        "if this stops firing the boundary was narrowed — update the docstring "
        "on _note_detector_cap"
    )


# ---------- the three claims that outran their coverage ----------

# (detector, budget constant, expected reason). detect_recurring_row_vectors has
# two budgets with distinct reasons: sharing one would collapse them in the
# coverage dedup key and the survivor would imply the other pass ran to
# completion.
_BUDGETS = (
    ("detect_recurring_row_vectors", "_RECURRING_VEC_BUDGET",
     "detector_cross_figure_budget_limit"),
    ("detect_recurring_row_vectors", "_WITHIN_ROW_VEC_BUDGET",
     "detector_within_row_budget_limit"),
    ("detect_scaled_row_reuse", "_SCALED_ROW_BUDGET", "detector_compute_budget_limit"),
    ("detect_short_row_reuse", "_SHORT_ROW_BUDGET", "detector_compute_budget_limit"),
    ("detect_within_row_shared_fraction", "_WITHIN_ROW_FRAC_BUDGET", "detector_compute_budget_limit"),
    ("detect_row_pair_shared_fraction", "_ROW_PAIR_FRAC_BUDGET", "detector_compute_budget_limit"),
)


@pytest.mark.parametrize("detector,budget_const,reason", _BUDGETS)
def test_a_spent_budget_is_not_reported_as_a_result_cap(detector, budget_const,
                                                        reason, monkeypatch):
    """C1': a scripted rewrite folded two break conditions into one flag.

    A detector that exhausts its compute budget and returns nothing then
    reported "detector finding limit (limit=1000000)" — a claim about a cap it
    never approached. Saying the wrong thing is worse than saying nothing, and
    this was a regression: checking at function exit never did it.
    """
    import paperconan._audit as audit
    monkeypatch.setattr(audit, budget_const, 1)

    coverage = ScanCoverage(files_discovered=1)
    found = getattr(audit, detector)(_two_sheets(), profile="review",
                                     max_findings=10**6, coverage=coverage)

    reasons = [i["reason"] for i in coverage.to_dict()["limitations"]]
    # Some detectors have more than one budget, so starving one need not empty
    # the result list. The invariant is the same either way: a spent budget is
    # not a result cap, and must never be reported as one.
    assert reason in reasons, f"{detector} spent its budget without saying so: {reasons}"
    if not found:
        assert "detector_finding_limit" not in reasons, (
            f"{detector} produced nothing but reported a result cap: {reasons}"
        )


@pytest.mark.parametrize("detector,budget_const,reason", _BUDGETS)
def test_each_detector_reports_its_own_exhausted_budget(detector, budget_const,
                                                        reason, monkeypatch):
    """Pins the budget wiring itself — untestable while these were literals."""
    import paperconan._audit as audit
    monkeypatch.setattr(audit, budget_const, 1)

    coverage = ScanCoverage(files_discovered=1)
    getattr(audit, detector)(_two_sheets(), profile="review", coverage=coverage)

    named = [(i.get("detector"), i["reason"]) for i in coverage.to_dict()["limitations"]]
    assert (detector, reason) in named, named


def test_a_truncated_candidate_pool_is_not_called_a_budget_limit():
    """Two causes, two names. The stderr line prints budget_exhausted=False while
    the structured record used to say the budget was spent — sending a reader to
    a knob that never moved."""
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)
    audit.detect_scaled_row_reuse(_two_sheets(), profile="review",
                                  max_candidates=3, max_findings=1,
                                  coverage=coverage)

    reasons = [i["reason"] for i in coverage.to_dict()["limitations"]]
    # Asserted, not skipped: a fixture that stops exercising the path is a test
    # failure. Skipping here would pass just as happily if the wiring were
    # deleted, which is the defect this test exists to catch.
    assert "detector_candidate_pool_limit" in reasons, (
        f"the truncated candidate pool was not recorded under its own name: {reasons}"
    )
    assert "detector_compute_budget_limit" not in reasons, (
        f"a full compute budget was reported as spent: {reasons}"
    )


def test_two_detectors_capped_at_once_are_both_named():
    """C2': detector records carry no file or sheet, so a dedup key without the
    detector name kept only the first and dropped the rest — while the caveat
    told the reader result caps were reported."""
    coverage = ScanCoverage(files_discovered=1)

    coverage.add_limitation("detector", "detector_finding_limit",
                            detector="detect_short_row_reuse", limit=60)
    coverage.add_limitation("detector", "detector_finding_limit",
                            detector="detect_row_pair_shared_fraction", limit=60)

    named = {i.get("detector") for i in coverage.to_dict()["limitations"]}
    assert named == {"detect_short_row_reuse", "detect_row_pair_shared_fraction"}, named


def test_the_unreported_cap_caveat_reaches_the_packet(tmp_path):
    """C3': the caveat was restored but its guard was not.

    Asserting the constant is False does not keep the sentence in front of a
    reader — deleting the block that appends it left the suite fully green,
    which is one edit away from the gap the caveat exists to cover.
    """
    from paperconan._workflow import start_workflow

    data = tmp_path / "data"
    data.mkdir()
    rows = ["a,b"] + [f"{i + 1},{(i + 1) * 2}" for i in range(12)]
    (data / "p.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    start_workflow(str(data), str(tmp_path / "out"))
    import json
    packet = json.loads(
        (tmp_path / "out" / "steps" / "t000" / "candidate_packet.json").read_text()
    )

    assert any("still not reported" in x for x in packet["coverage"]["limitations"]), (
        packet["coverage"]["limitations"]
    )
    # Not asserting coverage_complete: the blanket caveat is appended whenever
    # DETECTOR_CAPS_REPORTED is False, which is hardcoded, so that flag is False
    # on a wholly clean scan too and cannot discriminate.


# ---------- the record cap itself ----------

def test_limitations_dropped_by_the_record_cap_are_disclosed(tmp_path, monkeypatch):
    """The list is capped, so the packet must not imply it is exhaustive.

    ScanCoverage stops retaining records past PAPERCONAN_MAX_LIMITATIONS and
    counts the rest in `limitations_omitted`. The HTML report renders that
    count; the workflow packet used to iterate `limitations` alone, so on a
    scan with more limitations than the cap it silently showed a short list.
    A reader deciding whether a paper's numbers are fully enumerated has no way
    to tell a complete list from a truncated one.
    """
    monkeypatch.setenv("PAPERCONAN_MAX_LIMITATIONS", "3")
    from paperconan._workflow import _coverage_for

    coverage = ScanCoverage(files_discovered=1)
    for i in range(10):
        coverage.add_limitation("sheet", "sheet_unreadable_limit", file=f"f{i}.xlsx",
                                sheet=f"S{i}")
    scan = {"scan_status": "partial", "coverage": coverage.to_dict()}

    cov = _coverage_for(scan, [], [], [], 100)
    blob = " ".join(cov["limitations"])

    assert coverage.to_dict()["limitations_omitted"] == 7, "fixture no longer overflows the cap"
    assert "7" in blob and "cap" in blob.lower(), (
        f"7 dropped limitations were not disclosed: {cov['limitations']}"
    )


def test_the_caveat_makes_no_claim_the_list_is_exhaustive():
    """A hedge is honest; an unhedged positive claim about coverage is not.

    Every other clause in the caveat says what *may* be missing. Any sentence
    asserting that what is reported is all there is has to be true of the
    capped, deduped list — and it is not.
    """
    from paperconan._workflow import _coverage_for

    scan = {"scan_status": "partial", "coverage": ScanCoverage(files_discovered=1).to_dict()}
    blob = " ".join(_coverage_for(scan, [], [], [], 100)["limitations"]).lower()

    # Pinned exactly rather than screened against a list of phrasings. A
    # blacklist only catches the three wordings someone thought of: rewriting the
    # closing sentence into a fresh, unhedged completeness claim passed it. An
    # exact match cannot be worded around, and forces a deliberate decision on
    # every edit to text whose whole job is to not overstate coverage.
    expected = (
        "some detector caps are still not reported: a detector that skipped a "
        "block for being too wide or too tall, or stopped at an internal limit, "
        "may not appear above. This line does not enumerate what is not reported."
    )
    assert expected.lower() in blob, (
        "the unreported-cap caveat changed. Re-read it before updating this "
        "string: every clause must say what MAY be missing, and none may assert "
        f"that the list is complete.\nnow: {blob!r}"
    )


# ---------- a known gap, held honestly ----------

def test_a_block_past_the_row_cap_loses_relations_and_says_nothing(tmp_path):
    """Pins a real blind spot so it cannot widen unnoticed.

    detect_row_relations returns early above _ROW_REL_MAX_ROWS. That bound is a
    cost cap, not a scope rule: a 61x14 block is well inside the detector's
    orientation (14 >= _ROW_REL_MIN_COLS), and an exact relation there is lost
    while scan_status stays "complete". The behaviour is deliberate for now --
    the shape is common enough that reporting it would train readers to skip
    the coverage line -- but it is a gap, and the workflow's caveat is what
    currently covers it. If this test starts failing because the relation is
    found, the cap was raised: delete the test.
    """
    import csv

    def kinds_at(n_rows):
        d = tmp_path / f"r{n_rows}"
        d.mkdir(parents=True, exist_ok=True)
        grid = [[round(3.7 * i + 1.13 * j, 4) for j in range(14)] for i in range(n_rows)]
        grid[1] = [round(3.0 * v, 6) for v in grid[0]]
        with open(d / "t.csv", "w", newline="") as fh:
            csv.writer(fh).writerows(grid)
        scan = scan_dir(str(d), str(d / "out"), write_html=False)
        return {f.get("kind") for b in scan["relations_blocks"]
                for f in (b.get("row_relations") or [])}, scan

    under, _ = kinds_at(60)
    over, scan_over = kinds_at(61)

    assert "constant_ratio_row" in under, "fixture no longer plants a detectable relation"
    assert "constant_ratio_row" not in over, "the row cap no longer drops it -- delete this test"
    assert scan_over["scan_status"] == "complete", "the gap is now reported; update this test"


# ---------- the production wiring, not just the detector ----------

_WIRED_AT_SCAN_DIR = (
    "detect_row_relations",
    "detect_recurring_row_vectors",
    "detect_scaled_row_reuse",
    "detect_short_row_reuse",
    "detect_within_row_shared_fraction",
    "detect_row_pair_shared_fraction",
)


@pytest.mark.parametrize("name", _WIRED_AT_SCAN_DIR)
def test_scan_dir_hands_each_wired_detector_its_coverage(tmp_path, monkeypatch, name):
    """The kwarg at the call site, not the parameter in the signature.

    Every other test in this file calls the detectors directly with an explicit
    coverage=, so all of them stayed green when `, coverage=coverage` was deleted
    from four of the six scan_dir call sites. A detector that accepts coverage
    and is never handed one reports nothing, which is the defect this file
    exists to prevent -- so assert on what scan_dir actually passes.
    """
    import paperconan._audit as audit

    seen = {}
    original = getattr(audit, name)

    def spy(*args, **kwargs):
        seen["coverage"] = kwargs.get("coverage", "NOT PASSED")
        return original(*args, **kwargs)

    monkeypatch.setattr(audit, name, spy)
    _panel(tmp_path / "d" / "p.csv")
    scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    assert seen, f"{name} was never called by scan_dir; this test cannot see its wiring"
    assert isinstance(seen["coverage"], ScanCoverage), (
        f"scan_dir called {name} with coverage={seen['coverage']!r}; a cap it hits "
        f"would reach no channel"
    )


def test_the_packet_renders_limitations_as_sentences_not_dict_reprs(tmp_path):
    """_describe_scan_limitation is only reachable through _coverage_for.

    Tested in isolation it stays green while the packet goes back to emitting
    `{'scope': 'detector', ...}` at the reader.
    """
    from paperconan._workflow import _coverage_for

    coverage = ScanCoverage(files_discovered=1)
    coverage.add_limitation("detector", "detector_finding_limit",
                            detector="detect_short_row_reuse", limit=60)
    scan = {"scan_status": "partial", "coverage": coverage.to_dict()}

    blob = " ".join(_coverage_for(scan, [], [], [], 100)["limitations"])

    assert "detect_short_row_reuse" in blob, "the detector name did not reach the packet"
    for repr_marker in ("{", "'scope'", "'reason'"):
        assert repr_marker not in blob, f"a Python repr reached the reader: {blob!r}"


def test_a_tall_band_loses_scaled_row_reuse_and_says_nothing():
    """The second site gated on _ROW_REL_MAX_ROWS, pinned like the first.

    _scaled_row_candidates drops any band taller than the constant, so at 61
    rows this detector goes silent entirely -- including identical_row_reuse,
    verbatim row copies across sheets. A band's height says nothing about
    whether one of its rows is a scalar multiple of a row in another sheet, so
    this is a cost cap, not orientation. Deliberate for now and covered only by
    the workflow's over-broad caveat; if it is ever raised or reported, this
    test fails loudly and should be updated.
    """
    import paperconan._audit as audit
    from paperconan._sheet import Sheet

    vec = (13.40712, 27.91834, 8.52619, 41.06375, 19.73408, 33.28951, 22.61483,
           9.34026, 37.15792, 14.80613, 29.47158, 6.92374, 31.08627, 17.25913)

    def sheets(n_rows):
        grids = {}
        for idx, name in enumerate(("Figure 1a", "Figure 2b")):
            rows = [[f"c{j}" for j in range(len(vec))]]
            for i in range(n_rows):
                rows.append([round(v * (1 + 0.031 * i) + 0.7 * i, 5) for v in vec])
            if idx == 1:
                rows[1] = [round(3.0 * v, 6) for v in rows[1]]
            grids[(f"{name}.csv", name)] = Sheet.from_rows(rows)
        return grids

    under = audit.detect_scaled_row_reuse(sheets(60), profile="review", max_findings=10**6)
    over = audit.detect_scaled_row_reuse(sheets(61), profile="review", max_findings=10**6)

    assert under, "fixture no longer produces cross-sheet row reuse at 60 rows"
    assert not over, "the band ceiling no longer silences this detector -- update this test"


def test_the_html_coverage_row_distinguishes_one_capped_detector_from_another(tmp_path):
    """Detector records carry no file or sheet, so the name is all there is.

    The dedup key was widened to keep each capped detector as its own record;
    the renderer then dropped the field that made them distinguishable, so three
    capped detectors rendered as three identical rows that read as a display
    bug. Asserted on the rendered markup because the scan.json side was already
    green while this was broken.
    """
    from paperconan._html import _render_scan_status

    coverage = ScanCoverage(files_discovered=1)
    coverage.add_limitation("detector", "detector_finding_limit",
                            detector="detect_short_row_reuse", limit=60)
    coverage.add_limitation("detector", "detector_finding_limit",
                            detector="detect_scaled_row_reuse", limit=60)

    html = _render_scan_status({"scan_status": "partial", "coverage": coverage.to_dict()})

    assert "detect_short_row_reuse" in html and "detect_scaled_row_reuse" in html, (
        "the reader cannot tell which detector was capped"
    )
    assert "limit=60" in html, "the cap value did not reach the report"

    # A pool record separately: `candidates` is the quantity that says how much
    # was dropped, and rendering only `limit` shows the reader the cap back at
    # themselves. Only detect_scaled_row_reuse emits it, so the two-record
    # fixture above cannot cover it.
    pool = ScanCoverage(files_discovered=1)
    pool.add_limitation("detector", "detector_candidate_pool_limit",
                        detector="detect_scaled_row_reuse", candidates=1800, limit=1500)
    pool_html = _render_scan_status({"scan_status": "partial", "coverage": pool.to_dict()})
    assert "1800" in pool_html, (
        f"the pool size did not reach the report: {pool_html}"
    )


def test_a_truncated_candidate_pool_reports_the_pool_not_the_cap(tmp_path, capsys):
    """The one number on the record has to say how much was dropped.

    len(cands) is read after the slice, so reporting it prints max_candidates
    back at the reader -- the same value in every scan, and no way to recover
    how large the pool actually was.
    """
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)
    grids = _grids_for("detect_scaled_row_reuse")
    pool = len(audit._scaled_row_candidates(grids))

    audit.detect_scaled_row_reuse(grids, profile="review", max_candidates=3,
                                  max_findings=10**6, coverage=coverage)

    assert pool > 3, "fixture no longer overflows the candidate cap"
    record = next(i for i in coverage.to_dict()["limitations"]
                  if i["reason"] == "detector_candidate_pool_limit")
    assert record["candidates"] == pool, (
        f"reported {record['candidates']} candidates for a pool of {pool}"
    )
    assert record["limit"] == 3

    # The stderr line is a peer reporting channel, not a lesser one -- it had the
    # same post-slice bug and no test, so fixing only the structured record left
    # the human watching the run reading the cap back at themselves.
    err = capsys.readouterr().err
    assert f"candidates={pool}" in err, (
        f"stderr reported the cap instead of the pool: {err.strip()!r}"
    )


@pytest.mark.parametrize("detector,knob,reason", [
    ("detect_short_row_reuse", "_SHORT_ROW_MAX_ROWS_PER_SHEET",
     "detector_candidate_pool_limit"),
    ("detect_row_pair_shared_fraction", "_ROW_PAIR_MAX_ROWS_PER_SHEET",
     "detector_candidate_pool_limit"),
])
def test_sibling_candidate_pool_caps_reach_coverage(tmp_path, monkeypatch,
                                                    detector, knob, reason):
    """The same construct as max_candidates, in the detectors that also have it.

    One of these reported on stderr only and the other was silent everywhere,
    so an identical event flipped scan_status in one detector and vanished in
    two others.
    """
    import paperconan._audit as audit
    monkeypatch.setattr(audit, knob, 2)

    _write_csv(tmp_path / "d" / "p.csv", _grid_for_pool_cap(detector))
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    records = [i for i in (scan.get("coverage") or {}).get("limitations") or []
               if i.get("detector") == detector and i.get("reason") == reason]
    assert records, (
        f"{detector}'s candidate pool was cut at {knob} without recording it: "
        f"{_reasons(scan)}"
    )
    assert scan["scan_status"] != "complete"


def test_the_row_pair_digit_coupling_block_cap_reaches_coverage(tmp_path):
    """A result cap that truncated before _cap_block_findings ever saw it.

    detect_row_pair_digit_coupling ended in `findings[:25]`. That is a result cap
    like any other, but it ran ahead of the block-level cap, so the drop reached
    neither findings_omitted nor scan_status -- the one category the workflow's
    blind-spot note had been rewritten to claim was empty.

    No monkeypatching: 14 rows of 14 columns whose pairwise differences are
    multiples of 10 produce all C(14,2)=91 pairs and hit the default cap of 25.
    That shape is synthetic -- random blocks of the same size yield nothing --
    but the cap it reaches is the real default.
    """
    cols = 14
    base = [round(12.34 + j * 7.91, 2) for j in range(cols)]
    rows = [[f"c{j}" for j in range(cols)]]
    for i in range(14):
        rows.append([round(v + 10 * (i + 1), 2) for v in base])
    _write_csv(tmp_path / "d" / "p.csv", rows)

    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    records = [i for i in (scan.get("coverage") or {}).get("limitations") or []
               if i.get("detector") == "detect_row_pair_digit_coupling"
               and i.get("reason") == "detector_finding_limit"]
    assert records, (
        f"the per-block result cap recorded nothing under its own reason: "
        f"{[(i.get('detector'), i.get('reason')) for i in (scan.get('coverage') or {}).get('limitations') or []]}"
    )
    assert records[0]["limit"] == 25, records[0]
    assert "found" not in records[0], (
        "a per-block count on a record that collapses scan-wide reads as a total"
    )
    assert scan["scan_status"] != "complete"


def test_every_scan_line_quoted_in_the_skill_is_one_the_code_can_emit():
    """SKILL.md is what an agent reads to interpret a scan; its examples must be real.

    Hand-written example strings in that file have been wrong repeatedly -- a
    detail field the record does not carry, a shape the code cannot produce, a
    remediation knob that does not exist. Each is invisible until an agent acts
    on it. This renders the real thing through the real formatter and requires
    every `scan: ...` line quoted in the skill to match exactly.
    """
    import re
    from pathlib import Path

    import paperconan._audit as audit
    from paperconan._workflow import _coverage_for

    coverage = ScanCoverage(files_discovered=2)
    coverage.mark_file_failed("big.xlsx", "file_too_large")
    # Read from production, not hardcoded: with both sides self-supplied, moving
    # a default left SKILL.md quoting a limit the code no longer emits while this
    # test stayed green.
    coverage.add_limitation("detector", "detector_candidate_pool_limit",
                            detector="detect_short_row_reuse",
                            limit=audit._SHORT_ROW_MAX_ROWS_PER_SHEET)
    coverage.add_limitation("detector", "detector_finding_limit",
                            detector="detect_row_pair_digit_coupling",
                            limit=audit._ROW_PAIR_MAX_FINDINGS_PER_BLOCK)
    coverage.add_limitation("detector", "detector_compute_budget_limit",
                            detector="detect_row_relations")
    scan = {"scan_status": "partial", "coverage": coverage.to_dict()}
    emitted = {line for line in _coverage_for(scan, [], [], [], 100)["limitations"]
               if line.startswith("scan:")}

    skill = Path(__file__).resolve().parents[1] / "skills" / "paperconan" / "SKILL.md"
    quoted = re.findall(r"`(scan: [^`]+)`", skill.read_text(encoding="utf-8"))

    assert quoted, "no scan: examples found in SKILL.md; did the section move?"
    unemittable = [q for q in quoted if q not in emitted]
    assert not unemittable, (
        f"SKILL.md quotes {len(unemittable)} scan: line(s) the code does not emit: "
        f"{unemittable}\nemitted here: {sorted(emitted)}"
    )


# Reasons the skill deliberately does not quote. Listing them here rather than
# leaving the check one-directional: an agent that meets an unfamiliar `scan:`
# line is likelier to skim it as noise than to be misled by an invented one, so
# omission is the more dangerous direction. Adding a reason to the code without
# deciding which side it belongs on now fails the test below.
_SCAN_REASONS_NOT_IN_SKILL = {
    # Renders exactly like the quoted file line with the sheet name appended, and
    # the bullet says so in prose.
    "sheet_too_large",
    # Provenance of a formula sidecar, not a bound on what was searched: no
    # numeric coverage is lost, so it does not change how a result is read.
    "formula_cache_missing",
    "formula_cache_unreadable",
    # Two of detect_recurring_row_vectors' budgets. They read as
    # "detector cross figure budget limit ..." — recognisable from the quoted
    # compute-budget example, and quoting every budget variant would bloat the
    # bullet without teaching the agent anything new.
    "detector_cross_figure_budget_limit",
    "detector_within_row_budget_limit",
}


def test_every_limitation_reason_is_either_quoted_in_the_skill_or_listed_as_unquoted():
    """The reverse direction: a reason the code can emit and nobody decided about.

    The forward check stops SKILL.md inventing shapes. On its own it says nothing
    about the ones the code emits and the skill omits -- and under a heading that
    reads as an enumeration, an unlisted line is read as noise. This forces a
    decision per reason rather than letting the set drift.
    """
    import ast
    from pathlib import Path

    # Extracted from the call sites, not by pattern-matching identifiers: the
    # reason is a positional literal whose index depends on the helper, and a
    # loose regex picks up unrelated names like a test's "file_a".
    reason_arg = {"add_limitation": 1, "_note_detector_cap": 2,
                  "mark_file_failed": 1, "mark_sheet_skipped": 2}
    src = Path(__file__).resolve().parents[1] / "src" / "paperconan"
    reasons = set()
    for path in sorted(src.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            idx = reason_arg.get(name)
            if idx is None or len(node.args) <= idx:
                continue
            arg = node.args[idx]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                reasons.add(arg.value)

    assert reasons, "no limitation reasons found; did the helpers get renamed?"

    skill = (Path(__file__).resolve().parents[1] / "skills" / "paperconan"
             / "SKILL.md").read_text(encoding="utf-8")

    def shown(reason):
        # _describe_scan_limitation renders a reason by stripping the _limit
        # suffix and swapping underscores for spaces.
        return reason.replace("_limit", "").replace("_", " ") in skill

    undecided = sorted(r for r in reasons
                       if r not in _SCAN_REASONS_NOT_IN_SKILL and not shown(r))
    assert not undecided, (
        f"{len(undecided)} limitation reason(s) are neither shown in SKILL.md nor "
        f"listed as deliberately unquoted: {undecided}. Add an example to the skill, "
        f"or add the reason to _SCAN_REASONS_NOT_IN_SKILL with a note saying why."
    )


def test_one_detector_hitting_two_limits_records_both():
    """The `reason` half of the dedup key, which had no test.

    `detector` is pinned by test_two_detectors_capped_at_once_are_both_named, so
    dropping it turns the suite red. Dropping `reason` did not: nothing recorded
    two different limits for the same detector, and detectors really do reach
    several -- detect_recurring_row_vectors has two budgets, and a detector can
    exhaust a budget and fill its result cap in one scan. Merging them leaves a
    survivor that implies the other pass ran to completion, which is the silent
    shortening this file exists to prevent.
    """
    coverage = ScanCoverage(files_discovered=1)
    coverage.add_limitation("detector", "detector_compute_budget_limit",
                            detector="detect_recurring_row_vectors")
    coverage.add_limitation("detector", "detector_finding_limit",
                            detector="detect_recurring_row_vectors", limit=60)
    coverage.add_limitation("detector", "detector_candidate_pool_limit",
                            detector="detect_recurring_row_vectors", limit=400)

    reasons = {i["reason"] for i in coverage.to_dict()["limitations"]}

    assert reasons == {"detector_compute_budget_limit", "detector_finding_limit",
                       "detector_candidate_pool_limit"}, (
        f"one detector's distinct limits collapsed into {reasons}; the survivor "
        f"implies the other passes ran to completion"
    )


def test_two_budgets_in_one_detector_survive_a_real_scan(tmp_path, monkeypatch):
    """The same property end-to-end, since the unit test above builds records by hand.

    detect_recurring_row_vectors has a cross-figure budget and a within-row
    budget. Starving both is the production shape that a `reason`-less dedup key
    would collapse.
    """
    import paperconan._audit as audit
    monkeypatch.setattr(audit, "_RECURRING_VEC_BUDGET", 1)
    monkeypatch.setattr(audit, "_WITHIN_ROW_VEC_BUDGET", 1)

    # Two figure-named files: the cross-figure pass is skipped outright unless two
    # distinct figure keys are present, so a single panel can only ever starve the
    # within-row budget and the test would pin half the property.
    _panel(tmp_path / "d" / "Figure 1a.csv")
    _panel(tmp_path / "d" / "Figure 2b.csv")
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    reasons = {i["reason"] for i in (scan.get("coverage") or {}).get("limitations") or []
               if i.get("detector") == "detect_recurring_row_vectors"}

    assert len(reasons) >= 2, (
        f"both starved budgets collapsed to {reasons}; a reader is told one pass "
        f"was bounded and cannot learn the other was too"
    )

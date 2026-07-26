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

    Behavioural where the fixture can drive the detector past its cap;
    structural where it cannot. Skipping the latter would make an unwired
    detector look the same as an un-driven one, which is the gap this whole
    change exists to close.
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

    getattr(audit, name)(_two_sheets(), profile="review", max_findings=10**6,
                         coverage=coverage)

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
    assert packet["coverage"]["coverage_complete"] is False


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

    for claim in ("what is reported appears in this list",
                  "all limitations are listed",
                  "this list is complete"):
        assert claim not in blob, f"the caveat asserts completeness it does not have: {claim!r}"


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

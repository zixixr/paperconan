"""The rarity gate trims a matched run; it no longer rejects one whole.

A value shared by many rows is a quantized grid or a fitted-curve plateau, not a
distinctive duplicate, so a run built on such values is not evidence. The gate that
enforced that was all-or-nothing: one common value anywhere in a run rejected all of
it, including the stretch either side that was as distinctive as ever.

That coupled the gate to `_SHORT_ROW_MIN_FRAC_DIGITS`, which decides only which cells
are precise enough to be looked at. Lowering the floor lets an ordinary round number
join a run it had previously broken in two; being ordinary it is common, and a finding
the narrower floor had reported from the rare part alone disappeared. Admission must
not set the standard of proof for evidence it had nothing to do with.

Everything here is synthetic.
"""
from __future__ import annotations

import collections
import random

import numpy as np

import pytest

from paperconan import _audit
from paperconan._audit import detect_short_row_reuse
from paperconan._sheet import Sheet

# Three ordinary values, one per arm, so a common cell can be planted inside a run
# WITHOUT changing the relation that run expresses. Planting the same value in both
# rows leaves an identical run identical, but turns a scaled run's ratio to 1 and an
# offset run's difference to 0 -- which breaks the run instead of merely making a cell
# unremarkable, and a fixture built to exercise trimming then never trims anything.
_COMMON = 0.5001                      # identical arm: same value both sides
_COMMON_SCALED = round(_COMMON * 1.37, 6)     # scaled arm: the ratio still holds
_COMMON_OFFSET = round(_COMMON + 12.5, 6)     # offset arm: the difference still holds


def _carriers(count: int, width: int, tag: int) -> list[list[object]]:
    """Rows that make `_COMMON` ordinary, and that relate to nothing else.

    Pseudo-random rather than arithmetic: a row that reads as a progression is filtered
    as patterned before it can join the candidate pool, so it would never reach the
    frequency counter -- and rows built from a shared formula relate to each other,
    which floods the fixture with findings that have nothing to do with the test.
    """
    rows: list[list[object]] = []
    for index in range(count):
        generator = random.Random(tag * 1000 + index)
        values = [round(generator.uniform(5, 400), 4) for _ in range(width)]
        # Every planted value has to be made common, or the arm whose plant it is
        # never has anything trimmed.
        for offset, value in enumerate((_COMMON, _COMMON_SCALED, _COMMON_OFFSET)):
            values[(width // 2 + offset) % width] = value
        rows.append([f"carrier {tag}.{index}", *values])
    return rows


def _sheet(run: list[float], width: int, carriers: int = 6) -> Sheet:
    """Two panels repeating `run`, separated, with the carriers around them."""
    return Sheet.from_rows(
        [["", *[f"c{j}" for j in range(width)]],
         ["copy a", *run], *_carriers(carriers, width, 1),
         [None] * (width + 1),
         ["copy b", *run], *_carriers(carriers, width, 2)])


def _findings(sheet: Sheet) -> list[dict]:
    return list(detect_short_row_reuse({("book.xlsx", "S"): sheet}))


# --- preconditions, measured rather than asserted as literals ---------------------

def test_the_carriers_really_make_the_value_common() -> None:
    """Measured on the fixture, not pinned as arithmetic on a constant.

    A precondition that restates a constant guards nothing: it passes whether or not
    the mechanism it describes is present in the fixture at all.
    """
    width = 5
    sheet = _sheet([11.1111, 22.2222, _COMMON, 33.3333, 44.4444], width)
    seen = sum(1 for row in range(sheet.nrows) for column in range(sheet.ncols)
               if sheet.numeric[row, column] == _COMMON)

    assert seen > _audit._SHORT_ROW_MAX_VALUE_FREQ, (
        f"{seen} occurrences does not clear the rarity bar of "
        f"{_audit._SHORT_ROW_MAX_VALUE_FREQ}; the gate never engages")


def test_every_planted_value_is_common_on_the_planted_sheets() -> None:
    """One per arm, and each must clear the bar or that arm trims nothing."""
    for arm, length, common_at, sheet in _planted_sheets():
        if not common_at:
            continue
        for value in (_COMMON, _COMMON_SCALED, _COMMON_OFFSET):
            seen = sum(1 for row in range(sheet.nrows)
                       for column in range(sheet.ncols)
                       if sheet.numeric[row, column] == value)
            assert seen > _audit._SHORT_ROW_MAX_VALUE_FREQ, (arm, length, value, seen)
        break


# --- what trimming must do -------------------------------------------------------

def _reported_cells(finding: dict) -> list[float]:
    return [example["value"] for example in finding["examples"]]


def test_the_rare_part_either_side_of_a_common_value_is_still_reported() -> None:
    """The whole point: a common cell costs its own place in the run, not the run.

    The cells are asserted, not only their count. A trim that reports the right LENGTH
    from the wrong OFFSET hands the reviewer a window that still contains the common
    value -- the one cell the gate exists to exclude -- while the finding's own
    `run_length` says it was excluded. Length alone cannot see that.
    """
    run = [11.1111, 22.2222, 33.3333, _COMMON, 44.4444, 55.5555, 66.6666]
    findings = _findings(_sheet(run, len(run)))

    assert len(findings) == 1, [f["rule"] for f in findings]
    assert findings[0]["run_length"] == 3
    # Two stretches of equal length; the first is the one kept.
    assert _reported_cells(findings[0]) == [11.1111, 22.2222, 33.3333]


def test_the_reported_cells_are_the_longest_stretch_not_the_first() -> None:
    """Asymmetric on purpose: here the two differ.

    With one rare cell before the common value and four after, a trim that keeps its
    length but not its offset returns four cells starting at the beginning -- and the
    second of those is the common value itself.
    """
    run = [11.1111, _COMMON, 22.2222, 33.3333, 44.4444, 55.5555]
    findings = _findings(_sheet(run, len(run)))

    assert len(findings) == 1, [f["rule"] for f in findings]
    assert findings[0]["run_length"] == 4
    assert _reported_cells(findings[0]) == [22.2222, 33.3333, 44.4444, 55.5555]
    assert _COMMON not in _reported_cells(findings[0])


def test_a_rare_stretch_shorter_than_the_floor_is_not_reported() -> None:
    """Trimming does not smuggle a run past the detector's own column floor."""
    run = [11.1111, 22.2222, _COMMON, 33.3333, 44.4444]

    assert _findings(_sheet(run, len(run))) == []


def _pair_sheet(source, target, width):
    return Sheet.from_rows(
        [["", *[f"c{j}" for j in range(width)]],
         ["copy a", *source], *_carriers(6, width, 1),
         [None] * (width + 1),
         ["copy b", *target], *_carriers(6, width, 2)])


def _planted_sheets():
    """Each relation arm, at several lengths, with common values planted in the run.

    Generated rather than hand-built. The premise is checked by the test below: an
    earlier hand-built fixture opened with a whole number and continued as a
    progression, so the row never became a candidate, nothing was reported, and an
    assertion of absence held without the rule it named ever running.
    """
    for arm in ("identical", "scaled", "offset"):
        for length in (6, 9, 12, 13, 16):   # 12 is the hand-off boundary itself
            for common_at in ((), (2,), (2, 3), (1, 2, 3, 4),
                              tuple(range(1, max(2, length - 2)))):
                generator = random.Random(len(arm) * 7919 + length * 97
                                          + sum(common_at) * 13 + len(common_at))
                # Four decimals with a non-zero last digit, always. `round(uniform, 4)`
                # yields a trailing zero often enough that a run breaks at a cell that
                # is not high-precision after all, so a sixteen-column fixture produced
                # an eight-column run and the hand-off case was never reached.
                source = [generator.randrange(5, 400)
                          + generator.randrange(1000, 9999, 1) / 10000.0
                          for _ in range(length)]
                source = [value if round(value * 10000) % 10 else value + 0.0003
                          for value in source]
                if arm == "identical":
                    target = list(source)
                elif arm == "scaled":
                    target = [round(value * 1.37, 6) for value in source]
                else:
                    target = [round(value + 12.5, 6) for value in source]
                planted = {"identical": (_COMMON, _COMMON),
                           "scaled": (_COMMON, _COMMON_SCALED),
                           "offset": (_COMMON, _COMMON_OFFSET)}[arm]
                for index in common_at:
                    source[index], target[index] = planted
                yield arm, length, common_at, _pair_sheet(source, target, length)


def test_the_planted_sheets_reach_every_arm() -> None:
    """Without this the invariants below could hold over an empty result set."""
    seen = {f["kind"] for _, _, _, sheet in _planted_sheets() for f in _findings(sheet)}

    assert seen >= {"identical_row_reuse", "scaled_row_reuse",
                    "offset_row_reuse"}, seen


def test_every_cell_of_a_planted_sheet_is_high_precision() -> None:
    """The second premise, and the second way a fixture here quietly failed to run.

    A run breaks at any cell below the precision floor, so one trailing zero turns a
    sixteen-column plant into an eight-column run -- under the hand-off bound, which is
    then never exercised by a fixture built to exercise it.
    """
    for arm, length, common_at, sheet in _planted_sheets():
        row = [sheet.numeric[1, c] for c in range(1, length + 1)]

        assert all(_audit._is_short_hp(value) for value in row), (
            arm, length, common_at, [v for v in row if not _audit._is_short_hp(v)])


def _longest_gap(length: int, common_at: tuple) -> int:
    """The longest stretch of indices the plant leaves untouched."""
    best = current = 0
    for index in range(length):
        current = 0 if index in common_at else current + 1
        best = max(best, current)
    return best


def test_every_arm_reports_the_trimmed_length_not_the_whole_run() -> None:
    """The scaled and offset arms had no trimming test at all.

    Switching `_rare_span` off for either of them, or reporting the untrimmed run,
    left the suite green -- this branch's whole feature disabled on two of three arms.
    """
    checked = collections.Counter()
    for arm, length, common_at, sheet in _planted_sheets():
        if not common_at:
            continue
        for finding in _findings(sheet):
            if {finding["row_a"], finding["row_b"]} != {"copy a", "copy b"}:
                continue
            checked[arm] += 1

            assert finding["run_length"] == _longest_gap(length, common_at), (
                arm, length, common_at, finding["kind"], finding["run_length"])
            assert _COMMON not in _reported_cells(finding), (arm, length, common_at)

    assert set(checked) == {"identical", "scaled", "offset"}, dict(checked)


def test_a_stretch_with_too_few_distinct_values_is_not_reported() -> None:
    """Clears the column floor, fails the distinct-value floor.

    The two were only ever exercised together, so either could be deleted on any arm
    with the suite green -- and at the shipped constants the column floor is implied by
    the distinct one, which makes the distinct one the load-bearing half.
    """
    repeated = 77.7777
    run = [repeated, repeated, repeated, repeated, _COMMON, 11.1111, 22.2222]

    assert _findings(_sheet(run, len(run))) == []


def test_no_reported_run_is_shorter_than_the_column_floor() -> None:
    """Gating on the untrimmed run while reporting the trimmed one emits a finding
    whose evidence is one cell long -- its own numbers contradicting the rule it
    states."""
    for arm, length, common_at, sheet in _planted_sheets():
        for finding in _findings(sheet):
            assert finding["run_length"] >= _audit._SHORT_ROW_MIN_COLS, (
                arm, length, common_at, finding["kind"], finding["run_length"])


# --- which relation is reported ---------------------------------------------------

def test_the_arms_are_compared_on_what_each_would_report() -> None:
    """Preferring the arm with the longer FULL run reports the weaker relation.

    Here the identical stretch is the longer of the two before trimming and the shorter
    after. Comparing full lengths picks it, and the reviewer is shown three identical
    cells instead of the six-cell scaling that is actually there.
    """
    width = 12
    shared = [11.1111, 22.2222, 33.3333, _COMMON, 44.4444, 55.5555]
    # Four decimals: at the current floor a two-decimal cell is not high-precision
    # and no ratio run can form over it, so the fixture would exercise one arm.
    scaled_source = [70.1373, 76.2841, 82.9165, 88.4027, 94.6538, 100.2719]
    row_a = [*shared, *scaled_source]
    row_b = [*shared, *[round(v * 1.37, 6) for v in scaled_source]]

    sheet = Sheet.from_rows(
        [["", *[f"c{j}" for j in range(width)]],
         ["copy a", *row_a], *_carriers(6, width, 1),
         [None] * (width + 1),
         ["copy b", *row_b], *_carriers(6, width, 2)])

    findings = [f for f in _findings(sheet)
                if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert len(findings) == 1, [(f["kind"], f["run_length"]) for f in findings]
    assert findings[0]["kind"] == "scaled_row_reuse"
    assert findings[0]["run_length"] == 6


def test_the_longer_of_the_two_one_parameter_arms_is_reported() -> None:
    """Unequal on purpose. At a tie the comparison reads the same reversed, so a tie
    fixture cannot tell `>=` from `<=` -- the offset arm wins either way and the
    preference is untested. Here the ratio run is longer and must win."""
    width = 10
    base = [70.1373, 76.2841, 82.9165, 88.4027, 94.6538,
            100.2719, 107.4318, 113.8256, 121.5074, 128.9631]
    row_a = list(base)
    row_b = [round(v + 12.5, 6) for v in base[:4]]
    row_b += [round(v * 1.37, 6) for v in base[4:]]

    sheet = Sheet.from_rows(
        [["", *[f"c{j}" for j in range(width)]],
         ["copy a", *row_a], *_carriers(6, width, 1),
         [None] * (width + 1),
         ["copy b", *row_b], *_carriers(6, width, 2)])

    findings = [f for f in _findings(sheet)
                if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert len(findings) == 1, [(f["kind"], f["run_length"]) for f in findings]
    assert findings[0]["kind"] == "scaled_row_reuse", findings[0]["kind"]
    assert findings[0]["run_length"] == 6


@pytest.mark.parametrize("arm", ["identical", "scaled", "offset"])
def test_the_column_floor_is_read_on_every_arm_when_raised(arm, monkeypatch) -> None:
    """At the shipped constants this floor is dead code, on all three arms.

    `len(np.unique(run)) >= 3` implies `len(run) >= 3`, and `_SHORT_ROW_MIN_COLS`
    defaults to 3, so the length check can be deleted from any arm without changing a
    verdict. It only does anything when raised -- a supported configuration, the
    constant being env-tunable -- so that is where it has to be exercised, and on each
    arm separately, since each carries its own copy of the check.
    """
    source = [11.1111, 22.2222, 33.3333, _COMMON, 44.4444, 55.5555, 66.6666]
    if arm == "identical":
        target = list(source)
    elif arm == "scaled":
        target = [round(v * 1.37, 6) for v in source]
        target[3] = _COMMON_SCALED
    else:
        target = [round(v + 12.5, 6) for v in source]
        target[3] = _COMMON_OFFSET

    sheet = _pair_sheet(source, target, len(source))
    monkeypatch.setattr(_audit, "_SHORT_ROW_MIN_COLS", 3)
    before = [f for f in _findings(sheet)
              if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert [f["run_length"] for f in before] == [3], (
        arm, [(f["kind"], f["run_length"]) for f in before])

    monkeypatch.setattr(_audit, "_SHORT_ROW_MIN_COLS", 4)
    after = [f for f in _findings(sheet)
             if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert after == [], (arm, [(f["kind"], f["run_length"]) for f in after])


def test_when_two_one_parameter_arms_tie_the_offset_arm_is_preferred() -> None:
    """The tie-break, which no fixture reached: both comparisons used strict fixtures.

    An offset run and a ratio run of the same trimmed length describe the same pair
    equally well, and the code states a preference. Left untested, reversing it -- or
    letting either always win -- changed which relation a reviewer is shown.
    """
    width = 12
    base = [70.1373, 76.2841, 82.9165, 88.4027, 94.6538, 100.2719]
    row_a = [*base, *[round(v + 200.0, 4) for v in base]]
    # First half: a constant offset. Second half: the same values again, so the ratio
    # arm finds a run of the same length over a different window.
    row_b = [*[round(v + 12.5, 6) for v in base],
             *[round((v + 200.0) * 1.37, 6) for v in base]]

    sheet = Sheet.from_rows(
        [["", *[f"c{j}" for j in range(width)]],
         ["copy a", *row_a], *_carriers(6, width, 1),
         [None] * (width + 1),
         ["copy b", *row_b], *_carriers(6, width, 2)])

    findings = [f for f in _findings(sheet)
                if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert len(findings) == 1, [(f["kind"], f["run_length"]) for f in findings]
    assert findings[0]["kind"] == "offset_row_reuse", findings[0]["kind"]


def _carriers_of(value, count, width, tag):
    """`count` rows each carrying `value` once, otherwise unique."""
    rows = []
    for index in range(count):
        generator = random.Random(tag * 5000 + index)
        values = [round(generator.uniform(5, 400), 4) for _ in range(width)]
        values[width // 2] = value
        rows.append([f"c{tag}.{index}", *values])
    return rows


def test_a_value_exactly_at_the_rarity_bar_is_still_part_of_the_run() -> None:
    """The boundary itself: `<=` keeps it, `<` would trim there.

    The frozen bench pins exact counts but holds no value sitting on the bar, so the
    comparison could be moved a step in either direction unnoticed.
    """
    bar = _audit._SHORT_ROW_MAX_VALUE_FREQ
    marker = 0.7007
    run = [11.1111, 22.2222, marker, 33.3333, 44.4444]

    def sheet_with(occurrences: int) -> Sheet:
        # Two of the occurrences are the copied rows themselves.
        extra = occurrences - 2
        return Sheet.from_rows(
            [["", *[f"c{i}" for i in range(5)]],
             ["copy a", *run], *_carriers_of(marker, extra, 5, 1),
             [None] * 6,
             ["copy b", *run]])

    # Pinned exactly on both sides. On the bar the whole five-cell run stands; one
    # occurrence more splits it into stretches of two and two, and neither clears the
    # column floor, so nothing is reported.
    assert [f["run_length"] for f in _findings(sheet_with(bar - 1))] == [5]
    assert [f["run_length"] for f in _findings(sheet_with(bar))] == [5]
    assert _findings(sheet_with(bar + 1)) == []


# --- the language rule -----------------------------------------------------------

@pytest.mark.parametrize("banned", ["fraud", "fabricat", "falsif", "misconduct"])
def test_the_finding_text_makes_no_accusation(banned: str) -> None:
    run = [11.1111, 22.2222, 33.3333, _COMMON, 44.4444, 55.5555, 66.6666]
    finding = _findings(_sheet(run, len(run)))[0]

    assert banned not in " ".join(str(v) for v in finding.values()).casefold()


def test_a_long_run_stays_measured_on_the_full_run_not_the_trimmed_one() -> None:
    """The hand-off bound reads the FULL run, and trimming must not reach it.

    A run of `_ROW_REL_MIN_COLS` columns or more belongs to `detect_row_relations`.
    Applying the bound to the trimmed run instead would let a long match trim its way
    back under the boundary and be reported in both places -- the trim is about which
    cells are evidence, not about whose subject the pair is.
    """
    checked = 0
    for arm, length, common_at, sheet in _planted_sheets():
        if length < _audit._ROW_REL_MIN_COLS or not common_at:
            continue
        row_a = sheet.numeric[1, 1:length + 1]
        row_b = sheet.numeric[1 + 6 + 1 + 1, 1:length + 1]
        scan = {"identical": _audit._longest_hp_identical_run,
                "scaled": _audit._longest_hp_ratio_run,
                "offset": _audit._longest_hp_offset_run}[arm]
        full = scan(row_a, row_b)
        if full is None:
            continue
        full_len = full[0] if arm == "identical" else full[1]
        # The plant has to leave the FULL run long and the trimmed one short, or the
        # two readings of the bound cannot be told apart.
        if full_len < _audit._ROW_REL_MIN_COLS:
            continue
        trimmed = _longest_gap(length, common_at)
        if trimmed >= _audit._ROW_REL_MIN_COLS:
            continue
        checked += 1
        reported = [f for f in _findings(sheet)
                    if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

        assert reported == [], (arm, length, common_at, full_len, trimmed,
                                [(f["kind"], f["run_length"]) for f in reported])

    assert checked >= 2, f"only {checked} sheets separate the two readings of the bound"


@pytest.mark.parametrize("arm", ["identical", "scaled", "offset"])
def test_a_plateau_stretch_is_not_reported_on_any_arm(arm) -> None:
    """The distinct-value floor reads the TRIMMED run, on every arm.

    Moving that floor from the full run to the trimmed one is new here, and on two of
    the three arms nothing held it: reverting either to the full run left the whole
    suite green. What it lets through is a finding whose three reported cells are the
    same number -- the plateau the frequency gate exists to exclude, presented as a
    distinctive duplicate.
    """
    plateau = 77.7777
    source = [plateau, plateau, plateau, _COMMON, 11.1111, 22.2222]
    if arm == "identical":
        target = list(source)
    elif arm == "scaled":
        target = [round(v * 1.37, 6) for v in source]
        target[3] = _COMMON_SCALED
    else:
        target = [round(v + 12.5, 6) for v in source]
        target[3] = _COMMON_OFFSET

    reported = [f for f in _findings(_pair_sheet(source, target, len(source)))
                if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert reported == [], (arm, [(f["kind"], f["run_length"],
                                   [e["value"] for e in f["examples"]])
                                  for f in reported])


def test_the_reported_constant_describes_the_cells_the_finding_shows() -> None:
    """A trimmed finding's equation must come from the cells it displays.

    `_longest_hp_offset_run` takes its constant as a mean over the whole run, and its
    membership tolerance scales per cell -- so one large cell can join a run of small
    ones with a very different difference and pull the mean away from all of them.
    Trimming that cell out while keeping the mean printed a rule of "+34.375" over three
    cells that were each "+12.5", with the cell that produced 34.375 no longer shown and
    `run_length` asserting only three existed.
    """
    # The rare stretch must NOT start at index 0, or `partner[start:start + length]`
    # and `partner[:length]` are the same cells and the alignment is untested.
    big = 900000.1234
    small = [0.0011, 0.0037, 0.0092]
    source = [big] + small
    target = [big + 100] + [round(v + 12.5, 6) for v in small]

    rows: list[list[object]] = [["", *[f"c{i}" for i in range(4)]], ["copy a", *source]]
    for index in range(12):
        rows.append([f"carrier {index}", 11.1111 + index, 22.2222 + index,
                     33.3333 + index, big])
    rows.append([None] * 5)
    rows.append(["copy b", *target])

    reported = [f for f in _findings(Sheet.from_rows(rows))
                if f["kind"] == "offset_row_reuse"
                and {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert len(reported) == 1, [(f["kind"], f["run_length"]) for f in reported]
    finding = reported[0]
    assert finding["run_length"] == 3
    shown = [example["value"] for example in finding["examples"]]
    assert shown == small
    assert finding["offset"] == pytest.approx(12.5)


def test_the_ratio_arm_states_the_ratio_of_the_cells_it_shows() -> None:
    """The ratio arm's constant is recomputed too, and that is observable.

    An earlier version of this test called the recomputation equivalent by
    construction, reasoning that `_scan_ratio_run` admits only cells within
    `_SHORT_ROW_RTOL` of one ratio so every sub-slice has the same mean. The reasoning
    is wrong: the scan anchors on the run's FIRST cell and holds the others near THAT,
    so a sub-slice mean can sit up to twice the tolerance away -- visible in the sixth
    significant figure the rule prints. The assertion below was written at `rel=1e-4`,
    exactly the width that let the difference through.
    """
    trimmed = [11.1111, 22.2222, 33.3333]
    source = trimmed + [_COMMON, 44.4444, 55.5555]
    # The trimmed cells hold one ratio; the cell that will be trimmed away holds another,
    # far enough from it that a full-run mean cannot be mistaken for the trimmed one.
    target = [round(v * 1.37, 6) for v in trimmed]
    target += [round(_COMMON * 9.5, 6),
               round(44.4444 * 1.37, 6), round(55.5555 * 1.37, 6)]

    reported = [f for f in _findings(_pair_sheet(source, target, len(source)))
                if f["kind"] == "scaled_row_reuse"
                and {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert len(reported) == 1, [(f["kind"], f["run_length"]) for f in reported]
    finding = reported[0]
    shown = [example["value"] for example in finding["examples"]]

    assert finding["ratio"] == pytest.approx(1.37, rel=1e-9), finding["ratio"]
    assert all(value in source for value in shown)
    assert _COMMON not in shown


def test_the_one_parameter_arms_are_compared_on_their_trimmed_lengths() -> None:
    """Which of offset and ratio wins is decided by what each would report.

    The existing tie test plants no common value, so full length equals trimmed length
    in it and it cannot tell the two readings apart. Here the offset run is longer
    before trimming and shorter after, so comparing full lengths reports a three-cell
    offset where a six-cell scaling is what the pair actually holds -- the same failure
    the identical-vs-one-parameter comparison was changed to avoid.
    """
    width = 12
    ratio_source = [70.1373, 76.2841, 82.9165, 88.4027, 94.6538, 100.2719]
    offset_source = [11.1111, 22.2222, _COMMON, 33.3333, 44.4444, 55.5555]
    source = offset_source + ratio_source
    target = [round(v + 12.5, 6) for v in offset_source[:2]]
    target += [_COMMON_OFFSET]
    target += [round(v + 12.5, 6) for v in offset_source[3:]]
    target += [round(v * 1.37, 6) for v in ratio_source]

    sheet = Sheet.from_rows(
        [["", *[f"c{j}" for j in range(width)]],
         ["copy a", *source], *_carriers(6, width, 1),
         [None] * (width + 1),
         ["copy b", *target], *_carriers(6, width, 2)])

    reported = [f for f in _findings(sheet)
                if {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert len(reported) == 1, [(f["kind"], f["run_length"]) for f in reported]
    assert reported[0]["kind"] == "scaled_row_reuse", reported[0]["kind"]
    assert reported[0]["run_length"] == 6


def test_the_ratio_reported_is_the_trimmed_slices_own_not_the_full_runs() -> None:
    """The two means genuinely differ, so the recomputation is observable.

    `_scan_ratio_run` anchors on the run's FIRST cell and admits the rest within
    `_SHORT_ROW_RTOL` of that, so ratios drift across a run and a sub-slice mean is not
    the full-run mean. An earlier fixture here used cells whose ratios were identical,
    where the two are the same number and nothing could be told apart.
    """
    base = [70.1373, 76.2841, 82.9165, 88.4027, 94.6538, 100.2719]
    drifting = [1.37, 1.37, 1.37, 1.37, 1.370118, 1.370118, 1.370118]
    source = base[:3] + [_COMMON] + base[3:]
    target = [round(value * ratio, 9)
              for value, ratio in zip(source, drifting)]
    target[3] = _COMMON_SCALED

    sheet = _pair_sheet(source, target, len(source))
    full = _audit._longest_hp_ratio_run(
        sheet.numeric[1, 1:len(source) + 1],
        sheet.numeric[1 + 6 + 1 + 1, 1:len(source) + 1])

    assert full is not None and full[1] == len(source), full
    assert abs(full[0] - 1.37) / 1.37 > 1e-6, (
        "the fixture must make the full-run mean differ from the trimmed one")

    reported = [f for f in _findings(sheet) if f["kind"] == "scaled_row_reuse"
                and {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert len(reported) == 1, [(f["kind"], f["run_length"]) for f in reported]
    assert reported[0]["ratio"] == pytest.approx(1.37, rel=1e-9), reported[0]["ratio"]


def test_a_trimmed_slice_that_is_not_itself_one_run_is_not_reported() -> None:
    """Re-validating the slice means the WHOLE slice, not some run inside it.

    The offset scan's tolerance scales with each cell's own magnitude, so a run anchored
    on a large cell holds together differences that a smaller anchor would not. Trim the
    large cell away and the remainder is no longer one run -- here four cells whose
    differences are 0.5133, 0.7337, 0.9618 and 1.0488, of which a rescan accepts only
    two. Reporting the four while the relation holds over two is the same disagreement
    between the stated constant and the shown cells that re-validation exists to
    prevent, one level down.

    The values come from a search over magnitudes rather than from reasoning about the
    code: 18% of trimmed slices whose rescan accepts anything at all accept less than
    the whole slice, so this is a common shape, not a contrived one.
    """
    source = [0.0243, 775227.2176, 149774.399, 3382.5221, 9971.5716]
    target = [0.866, 775227.7309, 149775.1327, 3383.4839, 9972.6204]

    rows: list[list[object]] = [["", *[f"c{i}" for i in range(5)]],
                                ["copy a", *source]]
    for index in range(12):
        rows.append([f"carrier {index}", 11.1111 + index, 22.2222 + index,
                     33.3333 + index, 44.4444 + index, 0.0243])
    rows.append([None] * 6)
    rows.append(["copy b", *target])
    sheet = Sheet.from_rows(rows)

    full = _audit._longest_hp_offset_run(sheet.numeric[1, 1:6],
                                         sheet.numeric[sheet.nrows - 1, 1:6])
    assert full is not None and full[1] == 5, full
    rescan = _audit._longest_hp_offset_run(
        np.asarray(full[2][1:], dtype=float), np.asarray(full[3][1:], dtype=float))
    assert rescan is not None and rescan[1] < 4, (
        "the fixture must be a slice whose rescan accepts less than all of it")

    reported = [f for f in _findings(sheet) if f["kind"] == "offset_row_reuse"
                and {f["row_a"], f["row_b"]} == {"copy a", "copy b"}]

    assert reported == [], [(f["offset"], f["run_length"],
                             [e["value"] for e in f["examples"]]) for f in reported]

"""The column-pair row floor is a detection boundary, so it has to be visible.

Every other floor in this engine is a named constant with an environment override,
which is what let them be measured against the false-positive bench. This one was a
bare `4` inside a loop, so it could not be swept and its cost was never quantified --
while the review corpus put small panels under it repeatedly.
"""
from __future__ import annotations

import os
import subprocess
import sys


def test_the_floor_is_named_and_defaults_to_four() -> None:
    from paperconan._audit import _COLUMN_PAIR_MIN_ROWS

    assert _COLUMN_PAIR_MIN_ROWS == 4


def test_the_floor_can_be_overridden_from_the_environment() -> None:
    """Read at import, like its siblings, so a sweep runs one setting per process."""
    code = "from paperconan._audit import _COLUMN_PAIR_MIN_ROWS; print(_COLUMN_PAIR_MIN_ROWS)"
    out = subprocess.check_output(
        [sys.executable, "-c", code],
        env={**os.environ, "PAPERCONAN_COLUMN_PAIR_MIN_ROWS": "3"},
        text=True,
    )

    assert out.strip() == "3"


# --- the floor is graded by what a relation has to estimate, not by row count alone ---

def _pair_sheet(left, right):
    """Two columns side by side, with a header row so `find_numeric_blocks` sees a block."""
    from paperconan._sheet import Sheet

    rows = [["a", "b"]]
    rows.extend([x, y] for x, y in zip(left, right))
    return Sheet.from_rows(rows)


def _relations(left, right):
    from paperconan._audit import detect_relations, find_numeric_blocks, header_for

    sheet = _pair_sheet(left, right)
    out = []
    for (r0, r1, c0, c1) in find_numeric_blocks(sheet):
        out.extend(detect_relations(sheet, r0, r1, c0, c1,
                                    header_for(sheet, r0, c0, c1)))
    return out


def test_three_rows_are_enough_for_an_equality() -> None:
    """Equality estimates nothing from the data.

    Three rows agreeing to the last recorded digit is three independent agreements. The
    review corpus put three-replicate panels under the old floor -- a repeated column in a
    three-mouse group is exactly the shape a reader wants flagged, and it was unreachable.
    """
    values = [4.176382, 9.028415, 1.593047]

    kinds = [f["kind"] for f in _relations(values, list(values))]

    assert kinds == ["identical_column"], kinds


def test_three_rows_are_not_enough_for_a_fitted_relation() -> None:
    """The control that makes this about degrees of freedom rather than a smaller number.

    A relation that estimates a constant from the same points it then checks keeps the
    ordinary floor. If these fired too, the change would be "lower the floor", which is
    the thing measured to cost more than it returns. Values are deliberately not evenly
    spaced: an arithmetic column reads as an axis and is dropped for that instead, which
    would make this pass without testing anything.
    """
    left = [1.5, 4.1, 2.7]

    assert _relations(left, [x + 3.75 for x in left]) == []

    # `exact_linear` is deliberately NOT asserted here. It carries its own `n >= 5` floor
    # that predates this change, so it stays silent at three rows whether this guard exists
    # or not -- an assertion on it reads as a second check and is passed by nothing. That
    # shape, an assertion about two gates that goes vacuous, is one this repo has shipped
    # before. `test_the_fitted_relations_still_fire_at_their_own_floors` covers it where it
    # can actually discriminate.


def test_the_fitted_relations_still_fire_at_their_own_floors() -> None:
    """And they are not disabled -- each still reports once it has the rows it asks for.

    Those floors differ from each other and always did: an offset needs four rows and a
    line needs five, which is the same grading by what a relation has to estimate, one
    rung further up. This change extends that ladder downward rather than introducing it.
    """
    left = [1.5, 4.1, 2.7, 9.3, 6.8]

    offset = {f["kind"] for f in _relations(left[:4], [x + 3.75 for x in left[:4]])}
    linear = {f["kind"] for f in _relations(left, [2.5 * x + 0.25 for x in left])}

    assert "constant_offset" in offset, offset
    assert "exact_linear" in linear, linear


def test_the_exact_floor_is_named_and_overridable() -> None:
    """Named like its siblings so its cost can be swept rather than argued."""
    from paperconan._audit import _COLUMN_PAIR_EXACT_MIN_ROWS

    assert _COLUMN_PAIR_EXACT_MIN_ROWS == 3

    code = ("from paperconan._audit import _COLUMN_PAIR_EXACT_MIN_ROWS as v; print(v)")
    out = subprocess.check_output(
        [sys.executable, "-c", code],
        env={**os.environ, "PAPERCONAN_COLUMN_PAIR_EXACT_MIN_ROWS": "4"},
        text=True,
    )

    assert out.strip() == "4"


def test_raising_the_exact_floor_silences_an_equality_the_default_reports() -> None:
    """The knob has to work in both directions, or "its cost can be swept" is not true.

    Entry is gated on the lower of the two floors so an equality can be reached at all;
    with only that, setting this one ABOVE the ordinary floor changed nothing, and a sweep
    upward would have measured its own no-op.
    """
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "from paperconan._audit import detect_relations, find_numeric_blocks, header_for;"
        "from paperconan._sheet import Sheet;"
        "v = [4.176382, 9.028415, 1.593047, 6.702914];"
        "rows = [['a', 'b']] + [[x, x] for x in v];"
        "sheet = Sheet.from_rows(rows);"
        "out = [f for b in find_numeric_blocks(sheet)"
        " for f in detect_relations(sheet, b[0], b[1], b[2], b[3],"
        " header_for(sheet, b[0], b[2], b[3]))];"
        "print(len(out))"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    raised = subprocess.check_output(
        [sys.executable, "-c", code],
        env={**os.environ, "PAPERCONAN_COLUMN_PAIR_EXACT_MIN_ROWS": "5"},
        text=True, cwd=root)
    default = subprocess.check_output([sys.executable, "-c", code], text=True, cwd=root)

    assert default.strip() == "1", default
    assert raised.strip() == "0", raised


def test_a_flood_of_short_equalities_does_not_demote_its_neighbours() -> None:
    """A sheet is called dense on the evidence that always decided it.

    The flood cap exists for correlated matrices, where identical columns are expected by
    construction. A wide three-row panel of repeated columns is a different thing, and
    letting its equalities vote turned a sheet dense on their evidence: a genuine four-row
    offset in a neighbouring block dropped to low severity because of findings that had
    nothing to do with it. They are still demoted themselves -- a flood is a flood -- they
    just no longer take anything else down with them.
    """
    from paperconan._audit import (_demote_dense_sheets, detect_relations,
                                   find_numeric_blocks, header_for)
    from paperconan._sheet import Sheet

    values = [4.176382, 9.028415, 1.593047]
    rows = [["h"] + [f"c{i}" for i in range(10)] + ["", "g1", "g2"]]
    for k in range(3):
        rows.append([f"r{k}"] + [values[k]] * 10 + ["", 1.5 + k * 2.3, 1.5 + k * 2.3 + 3.75])
    rows.append(["r3"] + [None] * 10 + ["", 9.9, 13.65])
    sheet = Sheet.from_rows(rows)
    blocks = [{"file": "f.xlsx", "sheet": "S", "block": {"r0": b[0]},
               "equal_pairs": [], "within_col": [],
               "relations": detect_relations(sheet, b[0], b[1], b[2], b[3],
                                             header_for(sheet, b[0], b[2], b[3]))}
              for b in find_numeric_blocks(sheet)]

    _demote_dense_sheets(blocks)

    every = [f for b in blocks for f in b["relations"]]
    offsets = [f for f in every if f["kind"] == "constant_offset"]
    shorts = [f for f in every if f["kind"] == "identical_column"]
    assert offsets and all(f["severity"] == "high" for f in offsets), offsets
    assert len(shorts) > 40 and all(f["severity"] == "low" for f in shorts), len(shorts)


def test_two_classes_under_the_cap_do_not_add_up_to_an_undemoted_flood() -> None:
    """Weighing each class against the full cap on its own opens a side door.

    The cap exists because a sheet dense with high-severity relations drowns the genuine
    signal. Judging the two admission classes separately let a sheet carry nearly twice the
    cap with nothing demoted at all -- the same flood, arriving as two halves. The short
    ones now go low once the sheet is crowded by any measure; the ordinary ones still
    answer only to their own count, which is what keeps them safe from the newcomers.
    """
    from paperconan._audit import RELATION_FLOOD_CAP, _demote_dense_relations

    cap = RELATION_FLOOD_CAP
    relations = ([{"kind": "constant_offset", "n": 4, "severity": "high"} for _ in range(cap)]
                 + [{"kind": "identical_column", "n": 3, "severity": "high"} for _ in range(cap)])

    _demote_dense_relations(relations)

    ordinary = [r for r in relations if r["n"] == 4]
    short = [r for r in relations if r["n"] == 3]
    assert all(r["severity"] == "high" for r in ordinary), "the pre-existing class is unchanged"
    assert all(r["severity"] == "low" for r in short), "the newcomers carry what they add"


def test_an_equality_below_its_floor_is_stopped_rather_than_left_to_the_other_branches() -> None:
    """Silence here has to be a decision, not a coincidence of three suppressions.

    Raising this floor above the ordinary one lets an equality reach the fitted branches.
    They all decline an identity -- a slope of one with a zero intercept is the same
    relationship restated worse -- so the pair goes quiet either way today. That quiet
    rests on every one of those suppressions staying in place; relaxing any would surface
    an equality its own floor had just excluded, wearing a line's clothes. The stop is
    explicit so the invariant is the floor's, not theirs.
    """
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "from paperconan._audit import detect_relations, find_numeric_blocks, header_for;"
        "from paperconan._sheet import Sheet;"
        "v = [4.176382, 9.028415, 1.593047, 6.702914, 2.481937];"
        "sheet = Sheet.from_rows([['a', 'b']] + [[x, x] for x in v]);"
        "out = [f['kind'] for b in find_numeric_blocks(sheet)"
        " for f in detect_relations(sheet, b[0], b[1], b[2], b[3],"
        " header_for(sheet, b[0], b[2], b[3]))];"
        "print(sorted(out))"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    above = subprocess.check_output(
        [sys.executable, "-c", code],
        env={**os.environ, "PAPERCONAN_COLUMN_PAIR_EXACT_MIN_ROWS": "6"},
        text=True, cwd=root)
    default = subprocess.check_output([sys.executable, "-c", code], text=True, cwd=root)

    assert default.strip() == "['identical_column']", default
    # Not "some other kind instead": nothing at all, and by this floor's decision.
    assert above.strip() == "[]", above

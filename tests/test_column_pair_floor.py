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

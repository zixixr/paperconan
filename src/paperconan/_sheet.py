"""Columnar substrate for the audit engine.

A Sheet replaces the legacy {sheet: list[list]} representation. Numeric cells
live in a dense float64 array (NaN = empty-or-non-numeric); non-numeric cells
(text, dates, bools) live in a sparse dict; integer-typed cells are tracked in a
sparse set so evidence keeps int-vs-float fidelity. The reconstruction rule in
`cell()` reproduces the original value exactly for every accessor and the
evidence builder.
"""
from __future__ import annotations
import math
import numpy as np


def _coerce_text_number(s):
    """A numeral held as text -> int/float; anything else back unchanged.

    Mirrors paperconan._audit._coerce_cell WITHOUT importing it (avoid a cycle),
    including its refusal of non-finite values: `numeric` spells "no number here"
    as NaN and `cell()` promises the source value back, so "NaN"/"Infinity" stay
    text rather than becoming a value the Sheet cannot represent."""
    t = s.strip()
    if not t:
        return s
    try:
        return int(t)
    except ValueError:
        pass
    try:
        v = float(t)
    except ValueError:
        return s
    return v if math.isfinite(v) else s


def _is_num(x):
    # mirror paperconan._audit.is_num WITHOUT importing it (avoid a cycle):
    # bool is NOT numeric; NaN/inf are NOT numeric.
    if x is None or isinstance(x, bool):
        return False
    if isinstance(x, (int, float)):
        return not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))
    return False


class Sheet:
    __slots__ = ("nrows", "ncols", "numeric", "_text", "_ints")

    def __init__(self, nrows, ncols, numeric, text, ints):
        self.nrows = nrows
        self.ncols = ncols
        self.numeric = numeric
        self._text = text
        self._ints = ints

    @classmethod
    def from_rows(cls, rows):
        rows = list(rows)
        nrows = len(rows)
        ncols = max((len(r) for r in rows), default=0)
        numeric = np.full((nrows, ncols), np.nan, dtype=float)
        text = {}
        ints = set()
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if isinstance(v, str):
                    # Mirrors _audit._fill_sheet_from_rows: a numeral held as text is
                    # a number. Kept in step deliberately -- the loaders' parity tests
                    # compare these two implementations, so a split here would make
                    # that comparison stop meaning anything.
                    v = _coerce_text_number(v)
                if _is_num(v):
                    numeric[r, c] = float(v)
                    if isinstance(v, int):
                        ints.add((r, c))
                elif v is not None:
                    text[(r, c)] = v
        return cls(nrows, ncols, numeric, text, ints)

    def cell(self, r, c):
        """Original value at (r, c): number (int/float fidelity), text/date/bool, or None."""
        if 0 <= r < self.nrows and 0 <= c < self.ncols:
            v = self.numeric[r, c]
            if not math.isnan(v):
                # Return built-in int/float, never numpy scalars: evidence cells
                # are JSON-serialized, and np.float64 would either fail json.dump
                # or (with default=str) drift to a quoted string vs the legacy
                # Python-float output. int_mask preserves int-vs-float fidelity.
                return int(v) if (r, c) in self._ints else float(v)
        return self._text.get((r, c))

    def block(self, r0, r1, c0, c1):
        """float64 sub-array (NaN for non-numeric) — the equal-pairs block matrix."""
        return self.numeric[r0:r1, c0:c1].copy()

    def numeric_values(self):
        """Flat list of all numeric cell values (order unspecified)."""
        col = self.numeric[~np.isnan(self.numeric)]
        return col.tolist()

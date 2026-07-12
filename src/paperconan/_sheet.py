"""Columnar substrate for the audit engine.

A Sheet replaces the legacy {sheet: list[list]} representation. Numeric cells
representable as float64 live in a dense array (NaN = empty-or-non-numeric);
wide integers and non-numeric cells live in sparse mappings. Integer-typed
dense cells are tracked in a sparse set so evidence keeps int-vs-float
fidelity. The reconstruction rule in `cell()` reproduces the original value
exactly for every accessor and the evidence builder.
"""
from __future__ import annotations
import math
import numpy as np


_MAX_EXACT_FLOAT_INT = 2**53


def _is_num(x):
    # mirror paperconan._audit.is_num WITHOUT importing it (avoid a cycle):
    # bool is NOT numeric; NaN/inf are NOT numeric.
    if x is None or isinstance(x, bool):
        return False
    if isinstance(x, (int, float)):
        return not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))
    return False


class Sheet:
    __slots__ = ("nrows", "ncols", "numeric", "_text", "_ints", "_wide_ints")

    def __init__(self, nrows, ncols, numeric, text, ints, wide_ints=None):
        self.nrows = nrows
        self.ncols = ncols
        self.numeric = numeric
        self._text = text
        self._ints = ints
        self._wide_ints = wide_ints or {}

    @classmethod
    def from_rows(cls, rows):
        rows = list(rows)
        nrows = len(rows)
        ncols = max((len(r) for r in rows), default=0)
        numeric = np.full((nrows, ncols), np.nan, dtype=float)
        text = {}
        ints = set()
        wide_ints = {}
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if _is_num(v):
                    if isinstance(v, int) and abs(v) > _MAX_EXACT_FLOAT_INT:
                        wide_ints[(r, c)] = v
                    else:
                        numeric[r, c] = float(v)
                        if isinstance(v, int):
                            ints.add((r, c))
                elif v is not None:
                    text[(r, c)] = v
        return cls(nrows, ncols, numeric, text, ints, wide_ints)

    def cell(self, r, c):
        """Original value at (r, c): number (int/float fidelity), text/date/bool, or None."""
        if 0 <= r < self.nrows and 0 <= c < self.ncols:
            if (r, c) in self._wide_ints:
                return self._wide_ints[(r, c)]
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

    def numeric_mask(self):
        mask = ~np.isnan(self.numeric)
        for r, c in self._wide_ints:
            mask[r, c] = True
        return mask

    def exact_numeric(self, r, c):
        if (r, c) in self._wide_ints:
            return self._wide_ints[(r, c)]
        value = self.cell(r, c)
        return value if _is_num(value) else None

    def iter_numeric_values(self):
        """Yield numeric cell values row-major with exact integer fidelity."""
        for r in range(self.nrows):
            for c in range(self.ncols):
                value = self.exact_numeric(r, c)
                if value is not None:
                    yield value

    def numeric_values(self):
        """Row-major list compatibility wrapper for callers that need materialization."""
        return list(self.iter_numeric_values())

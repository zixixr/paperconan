"""Columnar substrate and bounded streaming builder for the audit engine."""
from __future__ import annotations

import ctypes
import datetime
import math

import numpy as np


_MAX_EXACT_FLOAT_INT = 2**53


def _is_num(x):
    # Mirror paperconan._audit.is_num without importing it (avoid a cycle).
    if x is None or isinstance(x, bool):
        return False
    if isinstance(x, (int, float)):
        return not (
            isinstance(x, float)
            and (math.isnan(x) or math.isinf(x))
        )
    return False


def _sparse_payload_bytes(value):
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="surrogatepass"))
    if isinstance(value, bytes):
        return len(value)
    if isinstance(
        value,
        (datetime.date, datetime.time, datetime.datetime),
    ):
        return len(value.isoformat().encode("ascii"))
    if isinstance(value, bool):
        return 1
    if isinstance(value, int):
        return len(str(value).encode("ascii"))
    rendered = f"{type(value).__module__}.{type(value).__qualname__}:{value}"
    return len(rendered.encode("utf-8", errors="backslashreplace"))


class SheetBuildLimit(ValueError):
    def __init__(
        self,
        reason,
        *,
        cells,
        observed_sparse_cells,
        observed_sparse_bytes,
        max_sparse_cells,
        max_sparse_bytes,
        pending_value=None,
    ):
        self.reason = reason
        self.cells = cells
        self.observed_sparse_cells = observed_sparse_cells
        self.observed_sparse_bytes = observed_sparse_bytes
        self.max_sparse_cells = max_sparse_cells
        self.max_sparse_bytes = max_sparse_bytes
        self.pending_value = pending_value
        super().__init__(reason)

    def limitation_details(self):
        if self.reason == "cell_limit":
            return {"cells": self.cells}
        return {
            "max_sparse_bytes": self.max_sparse_bytes,
            "max_sparse_cells": self.max_sparse_cells,
            "observed_sparse_bytes": self.observed_sparse_bytes,
            "observed_sparse_cells": self.observed_sparse_cells,
        }


def _move_rows_in_place(array, old_cols, new_cols, used_rows, used_cols):
    if not used_rows or not used_cols or old_cols == new_cols:
        return
    rows = (
        range(used_rows)
        if new_cols < old_cols
        else range(used_rows - 1, -1, -1)
    )
    address = array.ctypes.data
    row_bytes = used_cols * array.itemsize
    for row in rows:
        source = address + row * old_cols * array.itemsize
        target = address + row * new_cols * array.itemsize
        if source != target:
            ctypes.memmove(target, source, row_bytes)


def _resize_in_place(
    array,
    target_rows,
    target_cols,
    used_rows,
    used_cols,
    fill_value,
):
    old_rows, old_cols = array.shape
    if (old_rows, old_cols) == (target_rows, target_cols):
        return
    if target_cols < old_cols:
        _move_rows_in_place(
            array, old_cols, target_cols, used_rows, used_cols
        )
    array.resize((target_rows, target_cols), refcheck=False)
    if target_cols > old_cols:
        _move_rows_in_place(
            array, old_cols, target_cols, used_rows, used_cols
        )
    if used_rows and target_cols > used_cols:
        array[:used_rows, used_cols:target_cols] = fill_value
    if target_rows > used_rows:
        array[used_rows:target_rows, :] = fill_value


class SheetBuilder:
    """Build one sheet without retaining source rows or unbounded sparse state."""

    __slots__ = (
        "_loaded_cells",
        "_max_cells",
        "_max_sparse_bytes",
        "_max_sparse_cells",
        "_sparse_bytes",
        "_sparse_cells",
        "_text",
        "_wide_ints",
        "int_mask",
        "max_width",
        "nrows",
        "numeric",
    )

    def __init__(
        self,
        *,
        declared_rows=0,
        declared_cols=0,
        loaded_cells=0,
        max_cells=None,
        max_sparse_cells=None,
        max_sparse_bytes=None,
    ):
        self._loaded_cells = max(0, int(loaded_cells))
        self._max_cells = (
            None if max_cells is None else max(0, int(max_cells))
        )
        self._max_sparse_cells = (
            None
            if max_sparse_cells is None
            else max(0, int(max_sparse_cells))
        )
        self._max_sparse_bytes = (
            None
            if max_sparse_bytes is None
            else max(0, int(max_sparse_bytes))
        )
        rows = max(0, int(declared_rows))
        cols = max(0, int(declared_cols))
        self.nrows = 0
        self.max_width = 0
        self._sparse_cells = 0
        self._sparse_bytes = 0
        self._text = {}
        self._wide_ints = {}
        self._check_dense(rows, cols)
        if rows and cols:
            self.numeric = np.full(
                (rows, cols), np.nan, dtype=float
            )
            self.int_mask = np.zeros((rows, cols), dtype=np.bool_)
        else:
            self.numeric = np.empty((0, 0))
            self.int_mask = np.empty((0, 0), dtype=np.bool_)

    @property
    def cells(self):
        return self.nrows * self.max_width

    def _raise_limit(self, reason, *, cells=None, sparse_value=None):
        observed_cells = self._sparse_cells
        observed_bytes = self._sparse_bytes
        if sparse_value is not None:
            observed_cells += 1
            observed_bytes += _sparse_payload_bytes(sparse_value)
        raise SheetBuildLimit(
            reason,
            cells=self.cells if cells is None else cells,
            observed_sparse_cells=observed_cells,
            observed_sparse_bytes=observed_bytes,
            max_sparse_cells=self._max_sparse_cells,
            max_sparse_bytes=self._max_sparse_bytes,
        )

    def _check_dense(self, rows, cols):
        cells = rows * cols
        if (
            self._max_cells is not None
            and self._loaded_cells + cells > self._max_cells
        ):
            self._raise_limit("cell_limit", cells=cells)

    def _ensure_geometry(self, rows, cols):
        self._check_dense(rows, cols)
        if (
            rows <= self.numeric.shape[0]
            and cols <= self.numeric.shape[1]
        ):
            return
        current_rows, current_cols = self.numeric.shape
        target_rows = (
            max(
                rows,
                1 if current_rows == 0 else current_rows * 2,
            )
            if rows > current_rows
            else current_rows
        )
        target_cols = (
            max(
                cols,
                1 if current_cols == 0 else current_cols * 2,
            )
            if cols > current_cols
            else current_cols
        )
        try:
            self._check_dense(target_rows, target_cols)
        except SheetBuildLimit:
            proposed_rows = target_rows
            proposed_cols = target_cols
            target_rows, target_cols = rows, cols
            remaining = (
                self._max_cells - self._loaded_cells
                if self._max_cells is not None
                else None
            )
            if remaining is not None:
                if target_cols:
                    target_rows = max(
                        rows,
                        min(
                            proposed_rows,
                            remaining // target_cols,
                        ),
                    )
                elif rows > current_rows:
                    target_rows = proposed_rows
                if target_rows:
                    target_cols = max(
                        cols,
                        min(
                            proposed_cols,
                            remaining // target_rows,
                        ),
                    )
            self._check_dense(target_rows, target_cols)
        _resize_in_place(
            self.numeric,
            target_rows,
            target_cols,
            self.nrows,
            self.max_width,
            np.nan,
        )
        _resize_in_place(
            self.int_mask,
            target_rows,
            target_cols,
            self.nrows,
            self.max_width,
            False,
        )

    def _retain_sparse(self, value):
        payload_bytes = _sparse_payload_bytes(value)
        observed_cells = self._sparse_cells + 1
        observed_bytes = self._sparse_bytes + payload_bytes
        if (
            self._max_sparse_cells is not None
            and observed_cells > self._max_sparse_cells
        ):
            self._raise_limit(
                "sparse_cell_limit", sparse_value=value
            )
        if (
            self._max_sparse_bytes is not None
            and observed_bytes > self._max_sparse_bytes
        ):
            self._raise_limit(
                "sparse_payload_limit", sparse_value=value
            )
        self._sparse_cells = observed_cells
        self._sparse_bytes = observed_bytes

    def append_row(self, row, *, transform=None):
        row_number = self.nrows
        projected_rows = row_number + 1
        try:
            width = len(row)
        except TypeError:
            width = None
        if width is not None:
            projected_width = max(self.max_width, width)
            self._ensure_geometry(projected_rows, projected_width)
            iterator = enumerate(row)
        else:
            self._ensure_geometry(projected_rows, self.max_width)
            iterator = enumerate(iter(row))

        row_width = 0
        for col, raw_value in iterator:
            row_width = col + 1
            projected_width = max(self.max_width, row_width)
            if projected_width > self.numeric.shape[1]:
                try:
                    self._ensure_geometry(
                        projected_rows, projected_width
                    )
                except SheetBuildLimit as error:
                    error.pending_value = raw_value
                    raise
            value = (
                transform(raw_value)
                if transform is not None
                else raw_value
            )
            if _is_num(value):
                if (
                    isinstance(value, int)
                    and abs(value) > _MAX_EXACT_FLOAT_INT
                ):
                    self._retain_sparse(value)
                    self._wide_ints[(row_number, col)] = value
                else:
                    self.numeric[row_number, col] = float(value)
                    if isinstance(value, int):
                        self.int_mask[row_number, col] = True
            elif value is not None:
                self._retain_sparse(value)
                self._text[(row_number, col)] = value
        self.max_width = max(
            self.max_width,
            row_width if width is None else width,
        )
        self.nrows = projected_rows

    def finish(self):
        _resize_in_place(
            self.numeric,
            self.nrows,
            self.max_width,
            self.nrows,
            self.max_width,
            np.nan,
        )
        _resize_in_place(
            self.int_mask,
            self.nrows,
            self.max_width,
            self.nrows,
            self.max_width,
            False,
        )
        return Sheet(
            self.nrows,
            self.max_width,
            self.numeric,
            self._text,
            self.int_mask,
            self._wide_ints,
        )


class Sheet:
    __slots__ = (
        "nrows",
        "ncols",
        "numeric",
        "_text",
        "_ints",
        "_wide_ints",
    )

    def __init__(
        self,
        nrows,
        ncols,
        numeric,
        text,
        ints,
        wide_ints=None,
    ):
        self.nrows = nrows
        self.ncols = ncols
        self.numeric = numeric
        self._text = text
        if isinstance(ints, np.ndarray):
            self._ints = ints
        else:
            mask = np.zeros((nrows, ncols), dtype=np.bool_)
            for row, col in ints:
                if 0 <= row < nrows and 0 <= col < ncols:
                    mask[row, col] = True
            self._ints = mask
        self._wide_ints = wide_ints or {}

    @classmethod
    def from_rows(
        cls,
        rows,
        *,
        max_cells=None,
        max_sparse_cells=None,
        max_sparse_bytes=None,
    ):
        builder = SheetBuilder(
            max_cells=max_cells,
            max_sparse_cells=max_sparse_cells,
            max_sparse_bytes=max_sparse_bytes,
        )
        iterator = iter(rows)
        while True:
            try:
                row = next(iterator)
            except StopIteration:
                break
            builder.append_row(row)
            del row
        sheet = builder.finish()
        if cls is Sheet:
            return sheet
        return cls(
            sheet.nrows,
            sheet.ncols,
            sheet.numeric,
            sheet._text,
            sheet._ints,
            sheet._wide_ints,
        )

    def cell(self, r, c):
        """Original value at (r, c), preserving int-vs-float fidelity."""
        if 0 <= r < self.nrows and 0 <= c < self.ncols:
            if (r, c) in self._wide_ints:
                return self._wide_ints[(r, c)]
            value = self.numeric[r, c]
            if not math.isnan(value):
                return int(value) if self._ints[r, c] else float(value)
        return self._text.get((r, c))

    def block(self, r0, r1, c0, c1):
        return self.numeric[r0:r1, c0:c1].copy()

    def numeric_mask(self):
        mask = ~np.isnan(self.numeric)
        for row, col in self._wide_ints:
            mask[row, col] = True
        return mask

    def exact_numeric(self, r, c):
        if (r, c) in self._wide_ints:
            return self._wide_ints[(r, c)]
        value = self.cell(r, c)
        return value if _is_num(value) else None

    def iter_numeric_values(self):
        for row in range(self.nrows):
            for col in range(self.ncols):
                value = self.exact_numeric(row, col)
                if value is not None:
                    yield value

    def numeric_values(self):
        return list(self.iter_numeric_values())

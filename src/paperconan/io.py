"""Table input and numeric-block helpers.

This module is the stable import surface for loading supported tabular inputs.
The current implementation re-exports the battle-tested functions used by the
legacy `_audit` orchestrator; future refactors should move implementation here
without changing callers.
"""
from __future__ import annotations

from ._audit import (
    _coerce_cell,
    col_array,
    find_numeric_blocks,
    header_for,
    is_num,
    load_csv_rows,
    load_table,
    load_workbook_rows,
    to_float,
)

__all__ = [
    "_coerce_cell",
    "col_array",
    "find_numeric_blocks",
    "header_for",
    "is_num",
    "load_csv_rows",
    "load_table",
    "load_workbook_rows",
    "to_float",
]


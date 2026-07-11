"""Extract tabular data locked inside supplementary PDF / Word files.

A great deal of fabrication is visible in numbers that are presented *in the
paper itself* — supplementary PDF tables, Word appendix tables — rather than in
a downloadable .xlsx source-data file. This module pulls those real tables out
and normalizes them into the same ``{sheet_name: rows}`` shape the rest of
paperconan already consumes, so every existing numeric detector applies with no
change.

Scope: real ruled/structured tables only. It does NOT digitize data points off
bar charts or curves (pixel digitization introduces error that would itself trip
the arithmetic/duplication detectors), and it does not OCR scanned images.

The heavy parsers (pdfplumber, python-docx) are optional extras, imported lazily
so the base install (xlsx/csv/tsv) never depends on them.
"""
from __future__ import annotations

import os

from ._audit import _coerce_cell
from ._input import ExtractedTableResult, InputLimitation


def tables_to_sheets(
    stem, labeled_tables, max_cells=None, with_metadata=False
):
    """Normalize extracted tables into ``{sheet_name: rows}``.

    ``labeled_tables`` yields ``(label, table)`` pairs where each ``table``
    yields rows of raw string/None cells. Each table becomes one sheet named
    ``"<stem>!<label>"`` (e.g. ``"supp!p3_t1"``), so cross-sheet detectors
    stay meaningful and every finding is traceable back to the page/table it
    came from.

    Cells are coerced to int/float/text via the same conservative parser used
    for CSV input; ragged rows are padded to the widest row. Tables with no
    content at all are dropped. When ``max_cells`` is set, successful tables
    share one cumulative dense-cell budget.
    """
    sheets = {}
    limitations = []
    loaded = 0
    for label, table in labeled_tables:
        sheet_name = f"{stem}!{label}"
        rows = []
        row_count = 0
        max_width = 0
        has_content = False
        rejected = False
        for row in table or ():
            normalized = [_coerce_cell(_as_text(c)) for c in row]
            row_count += 1
            max_width = max(max_width, len(normalized))
            table_cells = row_count * max_width
            if max_cells is not None and loaded + table_cells > max_cells:
                sheets[sheet_name] = None
                limitations.append(InputLimitation(
                    scope="sheet",
                    reason="cell_limit",
                    sheet=sheet_name,
                    details={
                        "cells": table_cells,
                        "max_cells": max_cells,
                    },
                ))
                rejected = True
                break
            has_content = has_content or any(
                cell is not None for cell in normalized
            )
            rows.append(normalized)
        if rejected:
            continue
        if not has_content:
            continue  # nothing in this table — drop it rather than emit noise
        for r in rows:
            if len(r) < max_width:
                r.extend([None] * (max_width - len(r)))
        sheets[sheet_name] = rows
        loaded += row_count * max_width
    if with_metadata:
        return ExtractedTableResult(
            tables=sheets,
            limitations=limitations,
        )
    return sheets


def _as_text(cell):
    """Adapters hand us str or None; make that explicit for _coerce_cell."""
    if cell is None:
        return None
    return cell if isinstance(cell, str) else str(cell)


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def load_pdf_tables(path, *, max_cells=None, with_metadata=False):
    """Extract every table from a PDF as ``{sheet_name: rows}``.

    Sheets are named ``<stem>!p<page>_t<table>`` (1-based page and table index).
    """
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - exercised via message only
        raise ImportError(
            "reading .pdf tables needs the optional extra: "
            "pip install 'paperconan[pdf]'"
        ) from e

    with pdfplumber.open(path) as pdf:
        def labeled_tables():
            for pi, page in enumerate(pdf.pages, start=1):
                for ti, table in enumerate(page.extract_tables(), start=1):
                    yield f"p{pi}_t{ti}", table

        return tables_to_sheets(
            _stem(path),
            labeled_tables(),
            max_cells=max_cells,
            with_metadata=with_metadata,
        )


def load_docx_tables(path, *, max_cells=None, with_metadata=False):
    """Extract every table from a Word .docx as ``{sheet_name: rows}``.

    Sheets are named ``<stem>!t<table>`` (1-based table index).
    """
    try:
        import docx
    except ImportError as e:  # pragma: no cover - exercised via message only
        raise ImportError(
            "reading .docx tables needs the optional extra: "
            "pip install 'paperconan[docx]'"
        ) from e

    doc = docx.Document(path)

    def table_rows(table):
        seen = set()
        for row in table.rows:
            values = []
            for cell in row.cells:
                identity = cell._tc
                if identity in seen:
                    values.append(None)
                else:
                    seen.add(identity)
                    values.append(cell.text)
            yield values

    labeled_tables = (
        (f"t{ti}", table_rows(table))
        for ti, table in enumerate(doc.tables, start=1)
    )
    return tables_to_sheets(
        _stem(path),
        labeled_tables,
        max_cells=max_cells,
        with_metadata=with_metadata,
    )

"""Extract tabular data locked inside supplementary PDF / Word files.

Statistical signals and data inconsistencies can appear in numbers presented
inside supplementary PDF tables and Word appendix tables rather than in a
downloadable .xlsx source-data file. This module pulls those tables out and
normalizes them into the same bounded ``Sheet`` substrate the rest of
paperconan consumes, so every existing numeric detector applies unchanged.

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
from ._sheet import SheetBuilder, SheetBuildLimit


def _close_iterator(iterator):
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _build_limitation(error, sheet_name, max_cells):
    details = error.limitation_details()
    if error.reason == "cell_limit":
        details["max_cells"] = max_cells
    return InputLimitation(
        scope="sheet",
        reason=error.reason,
        sheet=sheet_name,
        details=details,
    )


def _consume_until_content(row_iter):
    for cell in row_iter:
        if _coerce_cell(_as_text(cell)) is not None:
            return True
    return False


def _raw_cell_has_content(cell):
    if cell is None:
        return False
    if isinstance(cell, str):
        return bool(cell.strip())
    return bool(str(cell).strip())


def iter_tables_to_sheets(
    stem,
    labeled_tables,
    *,
    max_cells=None,
    max_sparse_cells=None,
    max_sparse_bytes=None,
):
    """Yield one normalized, bounded Sheet result at a time."""
    loaded = 0
    for label, table in labeled_tables:
        sheet_name = f"{stem}!{label}"
        table_iter = iter(table if table is not None else ())
        builder = SheetBuilder(
            loaded_cells=loaded,
            max_cells=max_cells,
            max_sparse_cells=max_sparse_cells,
            max_sparse_bytes=max_sparse_bytes,
        )
        has_content = False
        empty_overflow = None
        rejected = None
        for row in table_iter:
            row_iter = iter(row)

            def normalize_cell(cell):
                nonlocal has_content
                value = _coerce_cell(_as_text(cell))
                if value is not None:
                    has_content = True
                return value

            if empty_overflow is not None:
                if _consume_until_content(row_iter):
                    has_content = True
                    rejected = empty_overflow
                    _close_iterator(row_iter)
                    _close_iterator(table_iter)
                    break
                continue
            try:
                builder.append_row(
                    row_iter, transform=normalize_cell
                )
            except SheetBuildLimit as error:
                if error.reason == "cell_limit" and not has_content:
                    if (
                        _raw_cell_has_content(error.pending_value)
                        or _consume_until_content(row_iter)
                    ):
                        has_content = True
                        rejected = error
                        _close_iterator(row_iter)
                        _close_iterator(table_iter)
                        break
                    empty_overflow = error
                    builder = None
                    continue
                rejected = error
                _close_iterator(row_iter)
                _close_iterator(table_iter)
                break
        if rejected is not None:
            yield (
                sheet_name,
                None,
                [_build_limitation(rejected, sheet_name, max_cells)],
            )
            continue
        if not has_content:
            continue
        sheet = builder.finish()
        loaded += sheet.nrows * sheet.ncols
        yield sheet_name, sheet, []
        del sheet
        del builder
        del table_iter
        del table


def tables_to_sheets(
    stem,
    labeled_tables,
    max_cells=None,
    max_sparse_cells=None,
    max_sparse_bytes=None,
    with_metadata=False,
):
    """Normalize extracted tables into ``{sheet_name: Sheet}``.

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
    for sheet_name, sheet, sheet_limitations in iter_tables_to_sheets(
        stem,
        labeled_tables,
        max_cells=max_cells,
        max_sparse_cells=max_sparse_cells,
        max_sparse_bytes=max_sparse_bytes,
    ):
        sheets[sheet_name] = sheet
        limitations.extend(sheet_limitations)
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


def iter_pdf_tables(
    path,
    *,
    max_cells=None,
    max_sparse_cells=None,
    max_sparse_bytes=None,
):
    """Yield bounded PDF table Sheets in page/table order."""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - exercised via message only
        raise ImportError(
            "reading .pdf tables needs the optional extra: "
            "pip install 'paperconan[pdf]'"
        ) from e

    with pdfplumber.open(path) as pdf:
        def labeled_tables():
            for page_index, page in enumerate(pdf.pages, start=1):
                for table_index, table in enumerate(
                    page.find_tables(), start=1
                ):
                    yield (
                        f"p{page_index}_t{table_index}",
                        table.extract(),
                    )

        yield from iter_tables_to_sheets(
            _stem(path),
            labeled_tables(),
            max_cells=max_cells,
            max_sparse_cells=max_sparse_cells,
            max_sparse_bytes=max_sparse_bytes,
        )


def load_pdf_tables(
    path,
    *,
    max_cells=None,
    max_sparse_cells=None,
    max_sparse_bytes=None,
    with_metadata=False,
):
    """Extract every table from a PDF as ``{sheet_name: Sheet}``.

    Sheets are named ``<stem>!p<page>_t<table>`` (1-based page and table index).
    """
    sheets = {}
    limitations = []
    for sheet_name, sheet, sheet_limitations in iter_pdf_tables(
        path,
        max_cells=max_cells,
        max_sparse_cells=max_sparse_cells,
        max_sparse_bytes=max_sparse_bytes,
    ):
        sheets[sheet_name] = sheet
        limitations.extend(sheet_limitations)
    if with_metadata:
        return ExtractedTableResult(
            tables=sheets,
            limitations=limitations,
        )
    return sheets


def iter_docx_tables(
    path,
    *,
    max_cells=None,
    max_sparse_cells=None,
    max_sparse_bytes=None,
):
    """Yield bounded Word table Sheets in document order."""
    try:
        import docx
    except ImportError as e:  # pragma: no cover - exercised via message only
        raise ImportError(
            "reading .docx tables needs the optional extra: "
            "pip install 'paperconan[docx]'"
        ) from e

    document = docx.Document(path)

    def table_rows(table):
        previous_row = ()
        for row in table.rows:
            values = []
            current_row = []
            for column, cell in enumerate(row.cells):
                identity = cell._tc
                repeated = (
                    (
                        current_row
                        and current_row[-1] is identity
                    )
                    or (
                        column < len(previous_row)
                        and previous_row[column] is identity
                    )
                )
                if repeated:
                    values.append(None)
                else:
                    values.append(cell.text)
                current_row.append(identity)
            yield values
            previous_row = tuple(current_row)

    labeled_tables = (
        (f"t{ti}", table_rows(table))
        for ti, table in enumerate(document.tables, start=1)
    )
    yield from iter_tables_to_sheets(
        _stem(path),
        labeled_tables,
        max_cells=max_cells,
        max_sparse_cells=max_sparse_cells,
        max_sparse_bytes=max_sparse_bytes,
    )


def load_docx_tables(
    path,
    *,
    max_cells=None,
    max_sparse_cells=None,
    max_sparse_bytes=None,
    with_metadata=False,
):
    sheets = {}
    limitations = []
    for sheet_name, sheet, sheet_limitations in iter_docx_tables(
        path,
        max_cells=max_cells,
        max_sparse_cells=max_sparse_cells,
        max_sparse_bytes=max_sparse_bytes,
    ):
        sheets[sheet_name] = sheet
        limitations.extend(sheet_limitations)
    if with_metadata:
        return ExtractedTableResult(
            tables=sheets,
            limitations=limitations,
        )
    return sheets

"""Bounded inspection of OOXML (.xlsx/.xlsm) formula-result caches.

A spreadsheet formula cell stores both the formula (``<f>``) and its cached
computed value (``<v>``). paperconan reads spreadsheets through calamine, which
surfaces the *cached value* — so a formula cell that carries **no** cached value
is invisible to the numeric audit even though it holds a computed number. That
is a silent under-read, not a data problem.

This module reads the raw worksheet XML and reports, per sheet, how many formula
cells lack a cached value (with a few example cell references). The scan records
those as coverage limitations so a partially-read sheet is not mistaken for a
fully-examined one. It is purely a statement about scanner reach — never a
judgement about the data or its authors.

All reads are bounded (per-member byte limit and sheet-count limit, both
env-tunable) so a crafted or oversized package cannot exhaust memory.
"""

from __future__ import annotations

import os
import posixpath
import zipfile
from xml.etree import ElementTree as ET


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_TAG = f"{{{_MAIN_NS}}}c"
_SHEET_TAG = f"{{{_MAIN_NS}}}sheet"
_RELATIONSHIP_TAG = f"{{{_PKG_REL_NS}}}Relationship"


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _formula_metadata_bytes() -> int:
    return _int_env("PAPERCONAN_OOXML_FORMULA_METADATA_BYTES", 8 * 1024 * 1024)


def _formula_sheet_limit() -> int:
    return _int_env("PAPERCONAN_OOXML_FORMULA_SHEET_LIMIT", 10000)


class OoxmlFormulaInspectionLimit(ValueError):
    """A bound (byte or sheet count) was hit while inspecting formula metadata."""

    def __init__(self, reason: str, **details: object) -> None:
        self.reason = reason
        self.details = details
        super().__init__(reason)


class _BoundedXmlReader:
    """Wrap a stream so an XML member cannot read past ``byte_limit`` bytes."""

    def __init__(self, stream, *, member: str, byte_limit: int) -> None:
        self._stream = stream
        self._member = member
        self._limit = max(0, int(byte_limit))
        self._read = 0

    def read(self, size=-1):
        requested = 64 * 1024 if size is None or size < 0 else max(0, int(size))
        allowed = min(requested, self._limit - self._read + 1)
        data = self._stream.read(max(0, allowed))
        self._read += len(data)
        if self._read > self._limit:
            raise OoxmlFormulaInspectionLimit(
                "formula_metadata_byte_limit",
                limit=self._limit,
                member=self._member,
            )
        return data

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _bounded_accepted_sheets(values, *, max_sheets):
    if values is None:
        return None
    limit = max(0, int(max_sheets))
    accepted = set()
    for value in values:
        name = str(value)
        if name in accepted:
            continue
        if len(accepted) >= limit:
            raise OoxmlFormulaInspectionLimit(
                "formula_metadata_sheet_limit",
                limit=limit,
                selected_sheets=len(accepted) + 1,
            )
        accepted.add(name)
    return frozenset(accepted)


def _worksheet_paths(zf, *, accepted_sheets=None) -> list[tuple[str, str]]:
    sheet_limit = _formula_sheet_limit()
    byte_limit = _formula_metadata_bytes()
    accepted = _bounded_accepted_sheets(accepted_sheets, max_sheets=sheet_limit)
    if accepted == frozenset():
        return []

    workbook_sheets = []
    with zf.open("xl/workbook.xml") as stream:
        bounded = _BoundedXmlReader(
            stream, member="xl/workbook.xml", byte_limit=byte_limit)
        for _event, sheet in ET.iterparse(bounded, events=("end",)):
            if sheet.tag == _SHEET_TAG:
                sheet_name = sheet.attrib["name"]
                if accepted is None or sheet_name in accepted:
                    if len(workbook_sheets) >= max(0, int(sheet_limit)):
                        raise OoxmlFormulaInspectionLimit(
                            "formula_metadata_sheet_limit",
                            limit=max(0, int(sheet_limit)),
                            selected_sheets=len(workbook_sheets) + 1,
                        )
                    workbook_sheets.append(
                        (sheet_name, sheet.attrib[f"{{{_REL_NS}}}id"]))
            sheet.clear()
    if not workbook_sheets:
        return []

    needed_ids = {rel_id for _name, rel_id in workbook_sheets}
    targets = {}
    with zf.open("xl/_rels/workbook.xml.rels") as stream:
        bounded = _BoundedXmlReader(
            stream, member="xl/_rels/workbook.xml.rels", byte_limit=byte_limit)
        for _event, relationship in ET.iterparse(bounded, events=("end",)):
            if (
                relationship.tag == _RELATIONSHIP_TAG
                and relationship.attrib["Id"] in needed_ids
            ):
                targets[relationship.attrib["Id"]] = (
                    relationship.attrib["Target"],
                    relationship.attrib.get("TargetMode"),
                )
            relationship.clear()

    out = []
    for sheet_name, rel_id in workbook_sheets:
        target, target_mode = targets[rel_id]
        if target_mode == "External":
            raise ValueError(f"worksheet target is external: {target!r}")
        target = target.replace("\\", "/")
        if target.startswith("/"):
            member = posixpath.normpath(target.lstrip("/"))
        else:
            member = posixpath.normpath(posixpath.join("xl", target))
        if member in {"", ".", ".."} or member.startswith("../"):
            raise ValueError(f"worksheet target leaves package: {target!r}")
        out.append((sheet_name, member))
    return out


def inspect_ooxml_formula_cache(
    path, *, max_examples=20, accepted_sheets=None
) -> dict[str, dict[str, object]]:
    """Per-sheet count of formula cells with no cached value.

    Returns ``{sheet_name: {"count": int, "cells": [ref, ...]}}`` for sheets that
    have at least one such cell (empty dict when the package is clean or the path
    is not an .xlsx/.xlsm). ``accepted_sheets`` restricts inspection to the sheets
    the loader actually read; ``max_examples`` bounds the example refs per sheet.

    Raises ``OoxmlFormulaInspectionLimit`` when a bound is hit. Structural errors
    (bad zip / XML / package layout) propagate as ``ValueError``-family; callers
    should treat inspection as best-effort and degrade to a limitation.
    """
    if not str(path).lower().endswith((".xlsx", ".xlsm")):
        return {}

    example_limit = max(0, int(max_examples))
    gaps = {}
    with zipfile.ZipFile(path) as zf:
        for sheet_name, member in _worksheet_paths(
            zf, accepted_sheets=accepted_sheets
        ):
            count = 0
            cells = []
            with zf.open(member) as stream:
                stack = []
                for event, elem in ET.iterparse(
                    stream, events=("start", "end")
                ):
                    if event == "start":
                        stack.append(elem)
                        continue
                    parent = stack[-2] if len(stack) > 1 else None
                    if elem.tag == _CELL_TAG:
                        formula = elem.find(f"{{{_MAIN_NS}}}f")
                        value = elem.find(f"{{{_MAIN_NS}}}v")
                        if formula is not None and (
                            value is None
                            or value.text is None
                            or not value.text.strip()
                        ):
                            count += 1
                            if len(cells) < example_limit:
                                cells.append(elem.attrib.get("r", "?"))
                    # Detach finished elements to bound memory, but keep a cell's
                    # children until the cell end event so its formula and value
                    # nodes remain available for inspection.
                    if parent is not None and parent.tag != _CELL_TAG:
                        parent.remove(elem)
                        elem.clear()
                    stack.pop()
            if count:
                gaps[sheet_name] = {"count": count, "cells": cells}
    return gaps

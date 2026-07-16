from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import posixpath
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET
import zipfile

from .schema import PaperconanInputError

if TYPE_CHECKING:
    from ._sheet import Sheet


SUPPORTED_INPUT_EXTS = (
    "xlsx", "xls", "xlsm", "xlsb",
    "csv", "tsv", "pdf", "docx",
)


def ext_of(name):
    return Path(name or "").suffix.lstrip(".").lower()


def is_supported_input(name):
    return ext_of(name) in SUPPORTED_INPUT_EXTS


def discover_supported_inputs(in_dir):
    root = Path(in_dir)
    if not root.exists():
        raise PaperconanInputError(
            f"input directory does not exist: {root}"
        )
    if not root.is_dir():
        raise PaperconanInputError(
            f"input path is not a directory: {root}"
        )
    try:
        return sorted(
            str(path)
            for path in root.iterdir()
            if path.is_file() and is_supported_input(path.name)
        )
    except OSError as exc:
        raise PaperconanInputError(
            f"could not enumerate input directory {root}: {exc}"
        ) from exc


_RESERVED_LIMITATION_KEYS = ("scope", "reason", "sheet")


@dataclass(frozen=True)
class InputLimitation:
    scope: str
    reason: str
    sheet: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate_details()

    def _validate_details(self) -> None:
        for key in _RESERVED_LIMITATION_KEYS:
            if key in self.details:
                raise ValueError(f"details contains reserved key: {key}")

    def to_dict(self) -> dict[str, Any]:
        self._validate_details()
        out = {"scope": self.scope, "reason": self.reason}
        if self.sheet is not None:
            out["sheet"] = self.sheet
        for key in sorted(self.details):
            out[key] = self.details[key]
        return out


@dataclass
class TableLoadResult:
    sheets: dict[str, Sheet | None]
    limitations: list[InputLimitation] = field(default_factory=list)


@dataclass
class ExtractedTableResult:
    tables: dict[str, list[list[Any]] | None]
    limitations: list[InputLimitation] = field(default_factory=list)


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_TAG = f"{{{_MAIN_NS}}}c"
_SHEET_TAG = f"{{{_MAIN_NS}}}sheet"
_RELATIONSHIP_TAG = f"{{{_PKG_REL_NS}}}Relationship"
_OOXML_FORMULA_METADATA_BYTES = int(os.environ.get(
    "PAPERCONAN_OOXML_FORMULA_METADATA_BYTES",
    str(8 * 1024 * 1024),
))
_OOXML_FORMULA_SHEET_LIMIT = int(os.environ.get(
    "PAPERCONAN_OOXML_FORMULA_SHEET_LIMIT",
    "10000",
))


class OoxmlFormulaInspectionLimit(ValueError):
    def __init__(self, reason, **details):
        self.reason = reason
        self.details = details
        super().__init__(reason)


class _BoundedXmlReader:
    def __init__(self, stream, *, member, byte_limit):
        self._stream = stream
        self._member = member
        self._limit = max(0, int(byte_limit))
        self._read = 0

    def read(self, size=-1):
        requested = (
            64 * 1024
            if size is None or size < 0
            else max(0, int(size))
        )
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


def _worksheet_paths(
    zf,
    *,
    accepted_sheets=None,
    max_sheets=None,
    metadata_byte_limit=None,
) -> list[tuple[str, str]]:
    sheet_limit = (
        _OOXML_FORMULA_SHEET_LIMIT
        if max_sheets is None
        else max_sheets
    )
    byte_limit = (
        _OOXML_FORMULA_METADATA_BYTES
        if metadata_byte_limit is None
        else metadata_byte_limit
    )
    accepted = _bounded_accepted_sheets(
        accepted_sheets,
        max_sheets=sheet_limit,
    )
    if accepted == frozenset():
        return []

    workbook_sheets = []
    with zf.open("xl/workbook.xml") as stream:
        bounded = _BoundedXmlReader(
            stream,
            member="xl/workbook.xml",
            byte_limit=byte_limit,
        )
        for _event, sheet in ET.iterparse(bounded, events=("end",)):
            if sheet.tag == _SHEET_TAG:
                sheet_name = sheet.attrib["name"]
                if accepted is None or sheet_name in accepted:
                    if len(workbook_sheets) >= max(
                        0, int(sheet_limit)
                    ):
                        raise OoxmlFormulaInspectionLimit(
                            "formula_metadata_sheet_limit",
                            limit=max(0, int(sheet_limit)),
                            selected_sheets=(
                                len(workbook_sheets) + 1
                            ),
                        )
                    workbook_sheets.append((
                        sheet_name,
                        sheet.attrib[f"{{{_REL_NS}}}id"],
                    ))
            sheet.clear()
    if not workbook_sheets:
        return []

    needed_ids = {rel_id for _name, rel_id in workbook_sheets}
    targets = {}
    with zf.open("xl/_rels/workbook.xml.rels") as stream:
        bounded = _BoundedXmlReader(
            stream,
            member="xl/_rels/workbook.xml.rels",
            byte_limit=byte_limit,
        )
        for _event, relationship in ET.iterparse(
            bounded, events=("end",)
        ):
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
    if not str(path).lower().endswith((".xlsx", ".xlsm")):
        return {}

    example_limit = max(0, int(max_examples))
    gaps = {}
    with zipfile.ZipFile(path) as zf:
        for sheet_name, member in _worksheet_paths(
            zf,
            accepted_sheets=accepted_sheets,
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
                        if (
                            formula is not None
                            and (
                                value is None
                                or value.text is None
                                or not value.text.strip()
                            )
                        ):
                            count += 1
                            if len(cells) < example_limit:
                                cells.append(elem.attrib.get("r", "?"))

                    # Keep cell children until the cell end event so formula and
                    # cached-value nodes remain available for inspection.
                    if parent is not None and parent.tag != _CELL_TAG:
                        parent.remove(elem)
                        elem.clear()
                    stack.pop()
            if count:
                gaps[sheet_name] = {"count": count, "cells": cells}
    return gaps

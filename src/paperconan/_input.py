from __future__ import annotations

from dataclasses import dataclass, field
import posixpath
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET
import zipfile

if TYPE_CHECKING:
    from ._sheet import Sheet


@dataclass(frozen=True)
class InputLimitation:
    scope: str
    reason: str
    sheet: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
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


def _worksheet_paths(zf) -> list[tuple[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    relationships = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {}
    for relationship in relationships.findall(
        f"{{{_PKG_REL_NS}}}Relationship"
    ):
        targets[relationship.attrib["Id"]] = (
            relationship.attrib["Target"],
            relationship.attrib.get("TargetMode"),
        )

    out = []
    sheets = workbook.find(f"{{{_MAIN_NS}}}sheets")
    for sheet in list(sheets) if sheets is not None else []:
        rel_id = sheet.attrib[f"{{{_REL_NS}}}id"]
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
        out.append((sheet.attrib["name"], member))
    return out


def inspect_ooxml_formula_cache(
    path, *, max_examples=20
) -> dict[str, dict[str, object]]:
    if not str(path).lower().endswith((".xlsx", ".xlsm")):
        return {}

    example_limit = max(0, int(max_examples))
    gaps = {}
    with zipfile.ZipFile(path) as zf:
        for sheet_name, member in _worksheet_paths(zf):
            count = 0
            cells = []
            with zf.open(member) as stream:
                for _event, elem in ET.iterparse(stream, events=("end",)):
                    if elem.tag != f"{{{_MAIN_NS}}}c":
                        continue
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
                    elem.clear()
            if count:
                gaps[sheet_name] = {"count": count, "cells": cells}
    return gaps

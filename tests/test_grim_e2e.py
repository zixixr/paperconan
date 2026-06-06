"""End-to-end: GRIM/GRIMMER surfaces through scan_dir, and continuous data does not."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from build_fixture import build  # noqa: E402

from paperconan import scan_dir  # noqa: E402


def _grim_findings(scan):
    out = []
    for blk in scan.get("relations_blocks", []) or []:
        out.extend(blk.get("grim", []) or [])
    return out


def test_scan_dir_surfaces_grim(tmp_path):
    d = tmp_path / "paper"
    d.mkdir()
    build(str(d))
    res = scan_dir(str(d), str(tmp_path / "out"), write_html=True)
    kinds = {f["kind"] for f in _grim_findings(res)}
    assert "grim_inconsistent" in kinds, f"expected grim_inconsistent, got {kinds}"
    # It must also render into the HTML report.
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "grim_inconsistent" in html


def test_continuous_data_yields_no_grim(tmp_path):
    data = tmp_path / "cont"
    data.mkdir()
    # mean/sd/n columns but a continuous-measure header (no integer keyword).
    csv = "group,concentration mean,sd,n\nA,3.45,1.10,10\nB,3.51,1.20,10\nC,3.49,1.05,10\n"
    (data / "cont.csv").write_text(csv, encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out2"), write_html=False)
    assert _grim_findings(res) == []

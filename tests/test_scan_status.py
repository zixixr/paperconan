"""End-to-end coverage/status accounting through ``scan_dir`` and reports.

Drives real inputs (no loader stubs) so the wiring between the orchestration
control flow and ``ScanCoverage`` is exercised as shipped.
"""

import json

from paperconan._audit import scan_dir
from paperconan._html import write_html_report


_GOOD = "a,b,c\n1.11,2.22,3.33\n4.44,5.55,6.66\n7.77,8.88,9.99\n10.1,11.2,12.3\n"


def _scan(tmp_path, **kwargs):
    out = tmp_path / "audit"
    res = scan_dir(str(tmp_path), str(out), write_html=False, write_json=True, **kwargs)
    on_disk = json.loads((out / "scan.json").read_text())
    return res, on_disk


def test_clean_scan_is_complete(tmp_path):
    (tmp_path / "data.csv").write_text(_GOOD)
    res, disk = _scan(tmp_path)
    assert res["schema_version"] == 2
    assert res["scan_status"] == "complete"
    cov = res["coverage"]
    assert cov["files_discovered"] == 1
    assert cov["files_succeeded"] == 1
    assert cov["files_failed"] == 0
    assert cov["sheets_skipped"] == 0
    assert cov["truncated"] is False
    assert cov["limitations"] == []
    # persisted identically
    assert disk["scan_status"] == "complete"
    assert disk["coverage"] == cov


def test_unreadable_file_is_partial_with_limitation(tmp_path):
    (tmp_path / "data.csv").write_text(_GOOD)
    # A .xlsx that is not a valid workbook fails the loader, not discovery.
    (tmp_path / "broken.xlsx").write_text("this is not a real xlsx")
    res, _ = _scan(tmp_path)
    assert res["scan_status"] == "partial"
    cov = res["coverage"]
    assert cov["files_discovered"] == 2
    assert cov["files_succeeded"] == 1
    assert cov["files_failed"] == 1
    reasons = {(l["scope"], l["reason"]) for l in cov["limitations"]}
    assert ("file", "unreadable") in reasons
    # no raw exception text leaks into the (deterministic) limitation payload
    assert all("error" not in l and "detail" not in l for l in cov["limitations"])


def test_oversized_file_is_recorded(tmp_path, monkeypatch):
    import paperconan._audit as audit
    # Every non-empty file is now "too large" (restored at teardown, no reload).
    monkeypatch.setattr(audit, "_MAX_FILE_BYTES", 0)
    (tmp_path / "data.csv").write_text(_GOOD)
    out = tmp_path / "audit"
    res = audit.scan_dir(str(tmp_path), str(out), write_html=False, write_json=True)
    assert res["scan_status"] == "failed"
    cov = res["coverage"]
    assert cov["files_failed"] == 1
    assert any(l["reason"] == "file_too_large" for l in cov["limitations"])


def test_status_persisted_and_deterministic(tmp_path):
    (tmp_path / "data.csv").write_text(_GOOD)
    _, disk1 = _scan(tmp_path)
    _, disk2 = _scan(tmp_path)
    assert json.dumps(disk1["coverage"]) == json.dumps(disk2["coverage"])


def test_html_report_shows_partial_banner(tmp_path):
    (tmp_path / "data.csv").write_text(_GOOD)
    (tmp_path / "broken.xlsx").write_text("nope")
    res, _ = _scan(tmp_path)
    html_path = tmp_path / "report.html"
    write_html_report(res, str(html_path))
    html = html_path.read_text()
    assert "Scan coverage" in html
    assert "unreadable" in html


def test_html_report_hides_banner_when_complete(tmp_path):
    (tmp_path / "data.csv").write_text(_GOOD)
    res, _ = _scan(tmp_path)
    html_path = tmp_path / "report.html"
    write_html_report(res, str(html_path))
    assert "Scan coverage" not in html_path.read_text()

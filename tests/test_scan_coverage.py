"""Unit coverage for the ``ScanCoverage`` accounting model."""

import pytest

from paperconan._coverage import ScanCoverage


def test_all_files_succeed_is_complete():
    cov = ScanCoverage(files_discovered=2)
    cov.mark_file_succeeded()
    cov.mark_sheet_succeeded()
    cov.mark_block_analyzed(3)
    cov.mark_file_succeeded()
    cov.mark_sheet_succeeded()
    assert cov.status == "complete"
    d = cov.to_dict()
    assert d["files_succeeded"] == 2
    assert d["blocks_analyzed"] == 3
    assert d["truncated"] is False
    assert d["limitations"] == []


def test_any_skip_is_partial():
    cov = ScanCoverage(files_discovered=1)
    cov.mark_file_succeeded()
    cov.mark_sheet_succeeded()
    cov.mark_sheet_skipped("book.xlsx", "huge", "sheet_too_large")
    assert cov.status == "partial"
    d = cov.to_dict()
    assert d["sheets_skipped"] == 1
    assert d["limitations"] == [
        {"scope": "sheet", "reason": "sheet_too_large",
         "file": "book.xlsx", "sheet": "huge"}
    ]


def test_no_file_succeeds_is_failed():
    cov = ScanCoverage(files_discovered=1)
    cov.mark_file_failed("bad.xlsx", "unreadable", detail="boom")
    assert cov.status == "failed"
    assert cov.to_dict()["files_failed"] == 1


def test_empty_input_is_complete_not_failed():
    # No discovered files at all is a caller-level condition, not a failed scan.
    assert ScanCoverage(files_discovered=0).status == "complete"


def test_block_truncation_sets_truncated_flag():
    cov = ScanCoverage(files_discovered=1)
    cov.mark_file_succeeded()
    cov.mark_sheet_succeeded()
    cov.mark_block_analyzed(10)
    cov.mark_blocks_skipped(
        5, scope="sheet", reason="report_block_limit",
        file="book.xlsx", sheet="s1",
    )
    d = cov.to_dict()
    assert d["blocks_skipped"] == 5
    assert d["truncated"] is True


def test_duplicate_limitations_are_deduped():
    cov = ScanCoverage(files_discovered=1)
    cov.mark_file_succeeded()
    cov.mark_sheet_succeeded()
    for _ in range(4):
        cov.mark_sheet_skipped("b.xlsx", "s", "sheet_too_large")
    assert len(cov.to_dict()["limitations"]) == 1
    # but the counter still reflects every skip event
    assert cov.sheets_skipped == 4


def test_limitations_are_capped(monkeypatch):
    monkeypatch.setenv("PAPERCONAN_MAX_LIMITATIONS", "3")
    cov = ScanCoverage(files_discovered=1)
    cov.mark_file_succeeded()
    cov.mark_sheet_succeeded()
    for i in range(10):
        cov.mark_sheet_skipped("b.xlsx", f"sheet{i}", "sheet_too_large")
    d = cov.to_dict()
    assert len(d["limitations"]) == 3
    assert d["limitations_omitted"] == 7
    assert cov.sheets_skipped == 10


def test_bad_cap_env_falls_back(monkeypatch):
    monkeypatch.setenv("PAPERCONAN_MAX_LIMITATIONS", "not-an-int")
    cov = ScanCoverage(files_discovered=1)
    cov.mark_file_succeeded()
    cov.mark_sheet_succeeded()
    cov.mark_sheet_skipped("b.xlsx", "s", "sheet_too_large")
    assert len(cov.to_dict()["limitations"]) == 1

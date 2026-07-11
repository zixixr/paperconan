from paperconan._coverage import ScanCoverage


def test_complete_coverage_has_no_limitations():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_file_succeeded()
    coverage.mark_sheet_succeeded()
    coverage.mark_block_analyzed()
    assert coverage.status == "complete"
    assert coverage.to_dict()["truncated"] is False


def test_partial_coverage_requires_some_success_and_a_limitation():
    coverage = ScanCoverage(files_discovered=2)
    coverage.mark_file_succeeded()
    coverage.mark_sheet_succeeded()
    coverage.mark_file_failed("bad.xlsx", "parse_error")
    assert coverage.status == "partial"
    out = coverage.to_dict()
    assert out["files_failed"] == 1
    assert out["limitations"][0]["reason"] == "parse_error"


def test_failed_coverage_has_no_successful_sheet():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_file_failed("bad.xlsx", "parse_error")
    assert coverage.status == "failed"


def test_skipped_blocks_are_counted_and_mark_truncation():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_file_succeeded()
    coverage.mark_sheet_succeeded()
    coverage.mark_blocks_skipped(
        4, scope="sheet", reason="report_block_limit", file="a.csv", sheet="S"
    )
    out = coverage.to_dict()
    assert out["blocks_skipped"] == 4
    assert out["truncated"] is True
    assert coverage.status == "partial"


def test_skipped_sheet_records_location_and_makes_coverage_partial():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_sheet_succeeded()
    coverage.mark_sheet_skipped("a.xlsx", "Summary", "unsupported_layout")

    assert coverage.sheets_skipped == 1
    assert coverage.limitations == [
        {
            "scope": "sheet",
            "reason": "unsupported_layout",
            "file": "a.xlsx",
            "sheet": "Summary",
        }
    ]
    assert coverage.status == "partial"


def test_standalone_limitation_has_deterministic_fields_and_omits_none():
    coverage = ScanCoverage(files_discovered=1)
    coverage.add_limitation(
        "scan", "memory_limit", file="a.xlsx", sheet=None, threshold=100
    )

    limitation = coverage.limitations[0]
    assert list(limitation) == ["scope", "reason", "file", "threshold"]
    assert limitation == {
        "scope": "scan",
        "reason": "memory_limit",
        "file": "a.xlsx",
        "threshold": 100,
    }


def test_nonpositive_skipped_block_counts_are_ignored():
    coverage = ScanCoverage(files_discovered=1)
    coverage.mark_blocks_skipped(0, scope="sheet", reason="report_block_limit")
    coverage.mark_blocks_skipped(-2, scope="sheet", reason="report_block_limit")

    assert coverage.blocks_skipped == 0
    assert coverage.limitations == []


def test_to_dict_has_exact_key_order():
    coverage = ScanCoverage(files_discovered=1)

    assert list(coverage.to_dict()) == [
        "files_discovered",
        "files_succeeded",
        "files_failed",
        "sheets_succeeded",
        "sheets_skipped",
        "blocks_analyzed",
        "blocks_skipped",
        "truncated",
        "limitations",
    ]

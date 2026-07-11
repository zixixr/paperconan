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

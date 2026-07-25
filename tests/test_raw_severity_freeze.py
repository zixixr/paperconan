"""`raw_severity` is frozen before any profile rewrite touches `severity`.

The review/triage profiles demote a finding by overwriting `severity` in place,
which loses what the detector actually produced. The Agent workflow has to seed
from the pre-profile raw stream, so the detector's own severity must survive
the projection. Freezing it here — the single entry point every profile path
goes through — keeps one value authoritative instead of asking each consumer to
guess whether it is looking at a raw or a projected severity.
"""
from __future__ import annotations

import pytest

from paperconan._profiles import apply_profile_to_findings


def _boundary_dup_finding() -> dict:
    """A finding the review profile is known to demote (a 0/1/100 boundary dup)."""
    return {
        "kind": "within_col_value_duplication",
        "severity": "high",
        "dup_value": 1.0,
        "col": "pvalue",
        "n": 12,
    }


@pytest.mark.parametrize("profile", ["review", "triage"])
def test_demoting_profile_keeps_the_detector_severity_in_raw_severity(profile):
    findings = [_boundary_dup_finding()]

    apply_profile_to_findings(findings, profile)

    assert findings[0]["severity"] == "low", "expected this fixture to be demoted"
    assert findings[0]["raw_severity"] == "high"


def test_forensic_profile_also_records_raw_severity():
    """forensic returns early, before any demotion — it must still be frozen."""
    findings = [_boundary_dup_finding()]

    apply_profile_to_findings(findings, "forensic")

    assert findings[0]["severity"] == "high"
    assert findings[0]["raw_severity"] == "high"


def test_reapplying_a_profile_does_not_overwrite_the_frozen_value():
    """Idempotent: a second pass must not capture the already-demoted severity."""
    findings = [_boundary_dup_finding()]

    apply_profile_to_findings(findings, "review")
    apply_profile_to_findings(findings, "review")

    assert findings[0]["severity"] == "low"
    assert findings[0]["raw_severity"] == "high"


def test_hidden_findings_still_carry_raw_severity():
    findings = [_boundary_dup_finding()]

    apply_profile_to_findings(findings, "triage")

    assert findings[0]["profile_action"] == "hidden"
    assert findings[0]["raw_severity"] == "high"


def test_undemoted_findings_report_the_same_raw_and_effective_severity():
    findings = [{"kind": "identical_column", "severity": "high", "n": 20}]

    apply_profile_to_findings(findings, "review")

    assert findings[0]["severity"] == "high"
    assert findings[0]["raw_severity"] == "high"

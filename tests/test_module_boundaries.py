from __future__ import annotations


def test_engineering_modules_expose_scanner_boundaries():
    from paperconan import collisions, detectors, io, schema

    assert callable(io.load_table)
    assert callable(detectors.detect_relations)
    assert callable(collisions.detect_collisions)
    assert schema.VALID_PROFILES == ("review", "forensic", "triage")


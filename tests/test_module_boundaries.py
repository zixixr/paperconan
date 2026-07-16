from __future__ import annotations


def test_engineering_modules_expose_scanner_boundaries():
    from paperconan import collisions, detectors, io, schema

    assert callable(io.load_table)
    assert callable(detectors.detect_relations)
    assert callable(detectors.prefilter_relation_finding)
    assert callable(collisions.detect_collisions)
    assert schema.VALID_PROFILES == ("review", "forensic", "triage")


def test_image_modules_expose_report_integration_boundaries():
    from paperconan.image import ImageDependencyError
    from paperconan.image._evidence import EvidenceBudget, registered_preview_data_uri

    assert issubclass(ImageDependencyError, RuntimeError)
    assert EvidenceBudget(1).consume(1)
    assert callable(registered_preview_data_uri)

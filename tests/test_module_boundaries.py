from __future__ import annotations


def test_engineering_modules_expose_scanner_boundaries():
    from paperconan import collisions, detectors, io, schema

    assert callable(io.load_table)
    assert callable(detectors.detect_relations)
    assert callable(detectors.prefilter_relation_finding)
    assert callable(collisions.detect_collisions)
    assert schema.VALID_PROFILES == ("review", "forensic", "triage")


def test_resource_bounded_paths_have_no_superseded_helpers():
    from paperconan.fetch import _download

    obsolete = {
        "_bounded_sidecar_managed_names",
        "_pax_path_value_lengths",
        "_ReplayFile",
        "_with_replayed_tar_payload",
    }

    assert obsolete.isdisjoint(vars(_download))

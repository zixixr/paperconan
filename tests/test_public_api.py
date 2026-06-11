import os
import paperconan


def test_audit_dir_is_public():
    assert hasattr(paperconan, "audit_dir")


def test_audit_dir_runs_on_examples(tmp_path):
    here = os.path.dirname(os.path.dirname(__file__))
    in_dir = os.path.join(here, "examples", "demo_paper")
    if not os.path.isdir(in_dir):
        import pytest
        pytest.skip("examples/demo_paper not present")
    scan = paperconan.audit_dir(in_dir, str(tmp_path), write_html=False)
    assert scan["tool"] == "paperconan"
    assert "relations_blocks" in scan

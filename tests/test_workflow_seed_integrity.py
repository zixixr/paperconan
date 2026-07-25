"""Regressions for the workflow seed layer, from the post-merge review of #45.

Each test here corresponds to a claim the module makes about itself that turned
out to be false: that fixed inputs replay, that `coverage` is honest about what
was dropped, that lineage is immutable, and that ids are stable.
"""
from __future__ import annotations

import json
import os

import pytest

from paperconan._workflow import (
    REQUIRED_ENVELOPE_FIELDS,
    WorkflowError,
    start_workflow,
    workflow_status,
)


def _panel(path, *, cols=("a", "b"), scale=1):
    rows = [",".join(cols)]
    for i in range(12):
        rows.append(",".join(str(round((i + 1) * scale * (j + 1), 4)) for j in range(len(cols))))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _packet(out):
    return _read(out / "steps" / "t000" / "candidate_packet.json")


# ---------- C1: replay ----------

def test_a_nested_output_directory_is_refused(tmp_path):
    """It would re-read its own artifacts as source data on the next run."""
    data = tmp_path / "data"
    _panel(data / "p.csv")

    with pytest.raises(WorkflowError, match="outside the source directory"):
        start_workflow(str(data), str(data / "audit"))


def test_run_id_ignores_files_the_scan_never_reads(tmp_path):
    """A stray sidecar must not change the identity of an identical scan."""
    data = tmp_path / "data"
    _panel(data / "p.csv")

    first = start_workflow(str(data), str(tmp_path / "a"))["run_id"]
    (data / ".DS_Store").write_bytes(b"\x00\x01")
    (data / "notes.txt").write_text("scratch", encoding="utf-8")
    second = start_workflow(str(data), str(tmp_path / "b"))["run_id"]

    assert first == second


def test_repeated_runs_converge_on_one_run_id(tmp_path):
    data = tmp_path / "data"
    _panel(data / "p.csv")

    ids = [
        start_workflow(str(data), str(tmp_path / f"o{i}"))["run_id"]
        for i in range(3)
    ]

    assert len(set(ids)) == 1, ids


# ---------- C2 / C3: honest coverage ----------

def test_scan_side_finding_caps_are_carried_into_coverage(tmp_path, monkeypatch):
    """The scan's own caps cut seeds before the workflow sees them."""
    # the cap is read into a module constant at import time, so setenv is too late
    import paperconan._audit as audit
    monkeypatch.setattr(audit, "_MAX_FINDINGS_PER_BLOCK", 1)

    data = tmp_path / "data"
    _panel(data / "p.csv", cols=("a", "b", "c", "d"))

    start_workflow(str(data), str(tmp_path / "out"))
    coverage = _packet(tmp_path / "out")["coverage"]

    # unconditional: the cap of 1 must actually have dropped findings here
    assert coverage["upstream_findings_omitted"] > 0, "cap did not bite; fixture is stale"
    assert coverage["coverage_complete"] is False
    assert any("finding caps" in x for x in coverage["limitations"])


def _duplicated_across_files(data, n=20):
    """Two files sharing a high-precision column — trips cross-sheet detection."""
    rows = ["sample,value"] + [
        f"s{i},{round(0.1234567 + i * 0.0173219, 7)}" for i in range(n)
    ]
    data.mkdir(parents=True, exist_ok=True)
    for name in ("a.csv", "b.csv"):
        (data / name).write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_cross_sheet_findings_reach_the_seed_stream(tmp_path):
    """The highest-yield family in this project's funnel must be routable.

    Before this fix `_iter_raw_findings` walked `relations_blocks` only, so a
    HIGH cross-sheet duplication contributed no seed at all while `coverage`
    still reported the packet as complete.
    """
    data = tmp_path / "data"
    _duplicated_across_files(data)

    start_workflow(str(data), str(tmp_path / "out"))
    scan = _read(tmp_path / "out" / "scan.json")
    packet = _packet(tmp_path / "out")

    # unconditional: if the fixture stops producing these, this test must fail
    assert scan["cross_sheet_findings"], "fixture no longer trips cross-sheet detection"

    scopes = {s["scope"] for c in packet["clusters"] for s in c["seeds"]}
    assert "cross_sheet" in scopes, "cross-sheet findings produced no seed"


def test_coverage_names_families_it_does_not_seed(tmp_path):
    data = tmp_path / "data"
    _panel(data / "p.csv")

    start_workflow(str(data), str(tmp_path / "out"))
    coverage = _packet(tmp_path / "out")["coverage"]

    assert "families_not_seeded" in coverage
    assert "coverage_complete" in coverage


# ---------- I7 / I8: stable, well-separated identity ----------

def test_seed_ids_do_not_shift_when_an_unrelated_file_is_added(tmp_path):
    """P1d's routing request cites seeds by id; positional ids would renumber."""
    data = tmp_path / "data"
    _panel(data / "zebra.csv")

    start_workflow(str(data), str(tmp_path / "a"))
    before = {s["seed_id"] for c in _packet(tmp_path / "a")["clusters"] for s in c["seeds"]}

    _panel(data / "alpha.csv", scale=3)  # sorts first, would shift every index
    start_workflow(str(data), str(tmp_path / "b"))
    after = {s["seed_id"] for c in _packet(tmp_path / "b")["clusters"] for s in c["seeds"]}

    assert before <= after, "existing seed ids changed when a new file was added"


def test_side_by_side_panels_are_separate_clusters(tmp_path):
    """Two panels on one sheet are independent evidence, not one cluster."""
    data = tmp_path / "data"
    # two numeric blocks separated by a blank column
    rows = ["a,b,,c,d"]
    for i in range(12):
        rows.append(f"{i + 1},{(i + 1) * 2},,{(i + 1) * 5},{(i + 1) * 10}")
    (data / "two.csv").parent.mkdir(parents=True, exist_ok=True)
    (data / "two.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    start_workflow(str(data), str(tmp_path / "out"))
    clusters = _packet(tmp_path / "out")["clusters"]

    for cluster in clusters:
        cols = {s["block_cols"] for s in cluster["seeds"]}
        assert len(cols) == 1, f"cluster merged distinct panels: {cols}"
        assert cluster["block_cols"] is not None or cluster["scope"] != "block"


# ---------- I2 / I6: envelope and lineage ----------

@pytest.mark.parametrize("rel", ["states/s000.json", "steps/t000/candidate_packet.json"])
def test_envelope_carries_every_required_field(tmp_path, rel):
    """Driven off the constant, so the contract and the check cannot drift."""
    out = tmp_path / "out"
    _panel(tmp_path / "data" / "p.csv")
    start_workflow(str(tmp_path / "data"), str(out))

    art = _read(out / rel)

    for field in REQUIRED_ENVELOPE_FIELDS:
        assert field in art, f"{rel} missing {field}"


def test_an_edited_artifact_is_rejected(tmp_path):
    """Content-addressed lineage only means something if it is verified."""
    out = tmp_path / "out"
    _panel(tmp_path / "data" / "p.csv")
    start_workflow(str(tmp_path / "data"), str(out))

    state_path = out / "states" / "s000.json"
    state = _read(state_path)
    state["budget_remaining"]["clusters"] = 999999
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(WorkflowError, match="artifact_id"):
        workflow_status(str(out))


def test_restarting_over_an_existing_workflow_is_refused(tmp_path):
    data = tmp_path / "data"
    _panel(data / "p.csv")
    out = tmp_path / "out"
    start_workflow(str(data), str(out))

    with pytest.raises(WorkflowError, match="already holds a workflow"):
        start_workflow(str(data), str(out))

    start_workflow(str(data), str(out), force=True)  # explicit opt-in still works


# ---------- I5: damaged directories ----------

@pytest.mark.parametrize("damage", ["truncate_index", "truncate_state", "state_is_a_dir",
                                    "index_without_path", "path_escapes"])
def test_a_damaged_workflow_directory_reports_a_workflow_error(tmp_path, damage):
    out = tmp_path / "out"
    _panel(tmp_path / "data" / "p.csv")
    start_workflow(str(tmp_path / "data"), str(out))
    index_path = out / "workflow_state.json"

    if damage == "truncate_index":
        index_path.write_text('{"schema_version": 1, "curr', encoding="utf-8")
    elif damage == "truncate_state":
        (out / "states" / "s000.json").write_text('{"schema_ver', encoding="utf-8")
    elif damage == "state_is_a_dir":
        os.remove(out / "states" / "s000.json")
        (out / "states" / "s000.json").mkdir()
    elif damage == "index_without_path":
        index_path.write_text('{"schema_version": 1}', encoding="utf-8")
    else:
        index_path.write_text(
            '{"schema_version": 1, "current_state_path": "../../escape.json"}',
            encoding="utf-8",
        )

    with pytest.raises(WorkflowError):
        workflow_status(str(out))

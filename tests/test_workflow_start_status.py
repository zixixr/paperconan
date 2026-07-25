"""`workflow start` / `workflow status` — the DISCOVER stage.

Phase 1 is a skeleton: no new detector maths runs here. `start` reuses the
existing scan, freezes a bounded seed stream from the pre-profile raw findings,
and writes the immutable state and candidate-packet artifacts the Agent reads.

The two properties worth pinning are that the seed stream is taken before any
profile projection (so a triage run does not starve the workflow of candidates),
and that fixed inputs replay to identical artifacts.
"""
from __future__ import annotations

import json

import pytest

from paperconan._workflow import start_workflow, workflow_status


def _paper(d, *, boundary=True):
    """A tiny synthetic sheet. `boundary` seeds a value the review profile demotes."""
    d.mkdir(parents=True, exist_ok=True)
    rows = ["gene,pvalue,logFC"]
    for i in range(12):
        p = 1.0 if (boundary and i < 9) else round(0.01 + i * 0.003, 4)
        rows.append(f"g{i},{p},{round(-2 + i * 0.31, 4)}")
    (d / "panel.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return d


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- artifacts and envelope ----------

def test_start_writes_the_discover_artifacts(tmp_path):
    out = tmp_path / "agent"

    start_workflow(str(_paper(tmp_path / "data")), str(out))

    assert (out / "scan.json").exists()
    assert (out / "states" / "s000.json").exists()
    assert (out / "steps" / "t000" / "candidate_packet.json").exists()


@pytest.mark.parametrize("rel", [
    "states/s000.json",
    "steps/t000/candidate_packet.json",
])
def test_every_artifact_carries_the_common_envelope(tmp_path, rel):
    out = tmp_path / "agent"
    start_workflow(str(_paper(tmp_path / "data")), str(out))

    art = _read(out / rel)

    for field in ("schema_version", "run_id", "artifact_id", "parent_refs",
                  "config_digest", "coverage", "created_by_stage"):
        assert field in art, f"{rel} missing envelope field {field}"


def test_initial_state_is_route_with_a_next_action(tmp_path):
    out = tmp_path / "agent"
    start_workflow(str(_paper(tmp_path / "data")), str(out))

    state = _read(out / "states" / "s000.json")

    assert state["workflow_stage"] == "ROUTE"
    assert state["next_action"] == "write_routing_request"
    assert state["next_artifact_path"] == "steps/t000/routing_request.json"
    assert state["route_step"] == 0
    assert state["expansion_round"] == 0
    assert state["max_expansion_rounds"] == 2
    assert set(state["allowed_decisions"]) == {"expand", "explained", "needs_context", "defer"}


def test_budget_is_declared_up_front(tmp_path):
    out = tmp_path / "agent"
    start_workflow(str(_paper(tmp_path / "data")), str(out))

    budget = _read(out / "states" / "s000.json")["budget_remaining"]

    for k in ("clusters", "evidence_cells", "context_requests"):
        assert isinstance(budget[k], int) and budget[k] > 0


# ---------- the seed stream is raw, not profile-projected ----------

def test_seeds_survive_a_profile_that_would_demote_them(tmp_path):
    """spec 14.3 exit criterion: seeds must not vanish under a CLI profile.

    The fixture's repeated 1.0 p-values are demoted by review and hidden by
    triage. Seeding from the raw stream means all three profiles agree.
    """
    counts = {}
    for profile in ("forensic", "review", "triage"):
        out = tmp_path / profile
        start_workflow(str(_paper(tmp_path / "data")), str(out), profile=profile)
        packet = _read(out / "steps" / "t000" / "candidate_packet.json")
        counts[profile] = len(packet["clusters"])

    assert counts["review"] == counts["forensic"], counts
    assert counts["triage"] == counts["forensic"], counts
    assert counts["forensic"] > 0, "fixture produced no candidates at all"


def test_seed_records_the_raw_severity_not_the_projected_one(tmp_path):
    out = tmp_path / "agent"
    start_workflow(str(_paper(tmp_path / "data")), str(out), profile="triage")

    packet = _read(out / "steps" / "t000" / "candidate_packet.json")
    seeds = [s for c in packet["clusters"] for s in c["seeds"]]

    assert seeds, "expected at least one seed"
    assert any(s["raw_severity"] == "high" for s in seeds)


# ---------- coverage and determinism ----------

def test_packet_records_coverage_for_what_it_left_out(tmp_path):
    """Truncation must be stated, never silent."""
    data = _paper(tmp_path / "data")
    # a second sibling file so there is genuinely more than one cluster to drop
    # (scan_dir does not recurse, so it has to live beside the first)
    (data / "panel2.csv").write_text(
        (data / "panel.csv").read_text(encoding="utf-8"), encoding="utf-8"
    )

    uncapped = tmp_path / "uncapped"
    start_workflow(str(data), str(uncapped))
    total = len(_read(uncapped / "steps" / "t000" / "candidate_packet.json")["clusters"])
    assert total > 1, "fixture must yield >1 cluster for this to test anything"

    out = tmp_path / "agent"
    start_workflow(str(data), str(out), max_clusters=1)
    packet = _read(out / "steps" / "t000" / "candidate_packet.json")

    assert len(packet["clusters"]) == 1
    assert packet["coverage"]["clusters_omitted"] == total - 1
    assert packet["coverage"]["truncated"] is True
    assert packet["coverage"]["coverage_complete"] is False
    assert any("cluster budget" in x for x in packet["coverage"]["limitations"])


def test_fixed_input_replays_to_identical_artifacts(tmp_path):
    data = _paper(tmp_path / "data")

    start_workflow(str(data), str(tmp_path / "a"))
    start_workflow(str(data), str(tmp_path / "b"))

    for rel in ("states/s000.json", "steps/t000/candidate_packet.json"):
        assert (tmp_path / "a" / rel).read_text() == (tmp_path / "b" / rel).read_text(), rel


# ---------- status ----------

def test_status_reports_the_current_stage_without_mutating(tmp_path):
    out = tmp_path / "agent"
    start_workflow(str(_paper(tmp_path / "data")), str(out))
    before = (out / "states" / "s000.json").read_text()

    status = workflow_status(str(out))

    assert status["workflow_stage"] == "ROUTE"
    assert status["next_action"] == "write_routing_request"
    assert (out / "states" / "s000.json").read_text() == before


def test_status_on_a_directory_that_is_not_a_workflow_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        workflow_status(str(tmp_path))


def test_status_rejects_an_artifact_from_an_unsupported_schema(tmp_path):
    """Schema mismatch must be refused outright, not guessed at."""
    out = tmp_path / "agent"
    start_workflow(str(_paper(tmp_path / "data")), str(out))
    state_path = out / "states" / "s000.json"
    state = _read(state_path)
    state["schema_version"] = 999
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        workflow_status(str(out))


# ---------- CLI surface ----------

def test_cli_start_then_status(tmp_path):
    import subprocess
    import sys

    data = _paper(tmp_path / "data")
    out = tmp_path / "agent"

    started = subprocess.run(
        [sys.executable, "-m", "paperconan", "workflow", "start", str(data), "--out", str(out)],
        text=True, capture_output=True,
    )
    assert started.returncode == 0, started.stderr

    status = subprocess.run(
        [sys.executable, "-m", "paperconan", "workflow", "status", str(out)],
        text=True, capture_output=True,
    )
    assert status.returncode == 0, status.stderr
    assert "ROUTE" in status.stdout


def test_cli_start_prints_coverage_limitations_when_it_truncates(tmp_path):
    """The honesty message has to survive the path it was written for.

    An earlier revision renamed the coverage field and left this print statement
    reading the old key, so `workflow start` raised KeyError on exactly the runs
    that had something to disclose. The one-file fixture above never truncates,
    so nothing caught it.
    """
    import subprocess
    import sys

    data = tmp_path / "data"
    data.mkdir()
    for k in range(12):  # more clusters than the default budget of 8
        rows = ["a,b"] + [f"{i + 1},{(i + 1) * (k + 2)}" for i in range(12)]
        (data / f"f{k}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "paperconan", "workflow", "start",
         str(data), "--out", str(tmp_path / "agent")],
        text=True, capture_output=True,
    )

    assert res.returncode == 0, res.stderr
    assert "Traceback" not in res.stderr
    assert "coverage:" in res.stdout
    assert "cluster budget" in res.stdout


def test_cli_force_flag_is_wired_through(tmp_path):
    """A one-line wire a refactor can cut silently: without it `--force` fails
    with "Use force=True (CLI --force)", which reads as a tool bug."""
    import subprocess
    import sys

    data = _paper(tmp_path / "data")
    out = tmp_path / "agent"
    cmd = [sys.executable, "-m", "paperconan", "workflow", "start",
           str(data), "--out", str(out)]

    assert subprocess.run(cmd, text=True, capture_output=True).returncode == 0

    # second run without --force is refused
    again = subprocess.run(cmd, text=True, capture_output=True)
    assert again.returncode != 0
    assert "already holds a workflow" in (again.stderr + again.stdout)

    forced = subprocess.run(cmd + ["--force"], text=True, capture_output=True)
    assert forced.returncode == 0, forced.stderr
    assert "Traceback" not in forced.stderr


def test_cli_status_on_a_plain_directory_exits_with_a_message(tmp_path):
    import subprocess
    import sys

    res = subprocess.run(
        [sys.executable, "-m", "paperconan", "workflow", "status", str(tmp_path)],
        text=True, capture_output=True,
    )

    assert res.returncode != 0
    assert "workflow" in (res.stderr + res.stdout).lower()

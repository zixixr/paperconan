"""The deterministic half of the Agent workflow: DISCOVER (`start`) and `status`.

paperconan never calls a model. It runs the scan, freezes a bounded candidate
stream, and writes the artifacts an Agent reads and answers. The Agent decides
only whether to spend expansion budget; every number here is produced by the
existing deterministic detectors.

Two properties this module is responsible for:

* **Seeds come from the raw stream.** Candidates are built from `raw_severity`,
  frozen before any profile projection, so running the CLI with `--profile
  triage` cannot starve the workflow of candidates it would otherwise route.
* **Fixed inputs replay.** Artifacts are content-addressed and carry no
  wall-clock or machine-specific values, so the same source data yields the same
  bytes. Agent free text is not covered by that guarantee and is not an input
  to any digest.

Phase 1 is a skeleton: no new detector maths lives here.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from ._finding_groups import BLOCK_FINDING_GROUPS

SCHEMA_VERSION = 1
MAX_EXPANSION_ROUNDS = 2
MAX_ROUTE_STEPS = 5

ALLOWED_DECISIONS = ("expand", "explained", "needs_context", "defer")

DEFAULT_BUDGET = {
    "clusters": 8,
    "evidence_cells": 2000,
    "context_requests": 4,
}

# Severity order for deterministic candidate ranking (raw, never projected).
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


class WorkflowError(ValueError):
    """A workflow directory or artifact is missing, stale or schema-incompatible."""


# ---------- digests and envelope ----------

def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _source_manifest(in_dir: str) -> list[dict[str, str]]:
    """Normalized relative paths + content digests. No absolute machine paths.

    Keeping the manifest content-addressed rather than path-addressed is what
    lets the same source data replay to the same run_id on another machine.
    """
    entries = []
    for root, _dirs, files in os.walk(in_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, in_dir).replace(os.sep, "/")
            try:
                with open(path, "rb") as fh:
                    sha = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
            entries.append({"path": rel, "sha256": sha})
    return sorted(entries, key=lambda e: e["path"])


def _envelope(*, run_id: str, artifact_type: str, stage: str,
              parent_refs: list[str], config_digest: str,
              coverage: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    art = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "artifact_type": artifact_type,
        "created_by_stage": stage,
        "parent_refs": parent_refs,
        "config_digest": config_digest,
        "coverage": coverage,
        **payload,
    }
    # artifact_id is derived last so it covers the whole artifact.
    art["artifact_id"] = _digest(art)
    return art


def require_envelope(art: dict[str, Any], *, expected_type: str | None = None) -> None:
    """Reject anything this version cannot interpret, rather than guessing."""
    version = art.get("schema_version")
    if version != SCHEMA_VERSION:
        raise WorkflowError(
            f"artifact schema_version {version!r} is not supported "
            f"(this build reads {SCHEMA_VERSION})"
        )
    if expected_type is not None and art.get("artifact_type") != expected_type:
        raise WorkflowError(
            f"expected a {expected_type} artifact, got {art.get('artifact_type')!r}"
        )


# ---------- seeds ----------

def _iter_raw_findings(scan: dict[str, Any]):
    """Every per-block finding, read through the canonical group registry."""
    for blk in scan.get("relations_blocks", []) or []:
        for group in BLOCK_FINDING_GROUPS:
            for f in blk.get(group, []) or []:
                yield blk, group, f


def _seed_from(blk: dict[str, Any], group: str, f: dict[str, Any], index: int) -> dict[str, Any]:
    # raw_severity is frozen before profile projection; `severity` may already
    # have been rewritten to "low" and must not drive routing.
    raw = f.get("raw_severity") or f.get("severity") or "low"
    return {
        "seed_id": f"seed:{index:04d}",
        "kind": f.get("kind"),
        "group": group,
        "file": blk.get("file"),
        "sheet": blk.get("sheet"),
        "block_rows": (blk.get("block") or {}).get("rows"),
        "block_cols": (blk.get("block") or {}).get("cols"),
        "raw_severity": raw,
        "rule": f.get("rule"),
        "n": f.get("n"),
    }


def _cluster_key(seed: dict[str, Any]) -> tuple:
    return (seed["file"] or "", seed["sheet"] or "", seed["block_rows"] or "")


def _build_clusters(scan: dict[str, Any], max_clusters: int) -> tuple[list[dict], dict]:
    seeds = [
        _seed_from(blk, group, f, i)
        for i, (blk, group, f) in enumerate(_iter_raw_findings(scan))
    ]

    grouped: dict[tuple, list[dict]] = {}
    for seed in seeds:
        grouped.setdefault(_cluster_key(seed), []).append(seed)

    clusters = []
    for key, members in grouped.items():
        members = sorted(
            members,
            key=lambda s: (_SEVERITY_RANK.get(s["raw_severity"], 3), s["seed_id"]),
        )
        clusters.append({
            "cluster_id": "cluster:" + hashlib.sha256(
                _canonical_json(list(key)).encode("utf-8")
            ).hexdigest()[:16],
            "file": key[0],
            "sheet": key[1],
            "block_rows": key[2],
            "strongest_raw_severity": members[0]["raw_severity"],
            "seeds": members,
        })

    # Deterministic order: strongest first, then by stable id.
    clusters.sort(key=lambda c: (
        _SEVERITY_RANK.get(c["strongest_raw_severity"], 3), c["cluster_id"]
    ))

    kept, omitted = clusters[:max_clusters], clusters[max_clusters:]
    coverage = {
        "seeds_total": len(seeds),
        "clusters_total": len(clusters),
        "clusters_omitted": len(omitted),
        "truncated": bool(omitted),
    }
    if omitted:
        # Never silently truncate: say what was dropped and why.
        coverage["omitted_reason"] = (
            f"cluster budget {max_clusters} reached; "
            f"{len(omitted)} lower-ranked clusters were not routed"
        )
    return kept, coverage


# ---------- start ----------

def start_workflow(in_dir: str, out_dir: str, *, profile: str = "review",
                   max_clusters: int | None = None) -> dict[str, Any]:
    """Run DISCOVER: scan, seed, and write the immutable step-0 artifacts."""
    from ._audit import scan_dir

    max_clusters = DEFAULT_BUDGET["clusters"] if max_clusters is None else max_clusters
    os.makedirs(out_dir, exist_ok=True)
    scan = scan_dir(in_dir, out_dir, write_md=False, write_html=False, profile=profile)

    manifest = _source_manifest(in_dir)
    config = {
        "schema_version": SCHEMA_VERSION,
        "max_clusters": max_clusters,
        "max_expansion_rounds": MAX_EXPANSION_ROUNDS,
        "max_route_steps": MAX_ROUTE_STEPS,
    }
    config_digest = _digest(config)
    # Semantic run id: same sources + same config replay to the same run.
    run_id = "wf:" + hashlib.sha256(
        _canonical_json({"manifest": manifest, "config": config}).encode("utf-8")
    ).hexdigest()[:16]

    clusters, coverage = _build_clusters(scan, max_clusters)

    packet = _envelope(
        run_id=run_id, artifact_type="candidate_packet", stage="DISCOVER",
        parent_refs=[], config_digest=config_digest, coverage=coverage,
        payload={
            "route_step": 0,
            "source_manifest": manifest,
            "clusters": clusters,
        },
    )
    state = _envelope(
        run_id=run_id, artifact_type="workflow_state", stage="DISCOVER",
        parent_refs=[packet["artifact_id"]], config_digest=config_digest,
        coverage=coverage,
        payload={
            "workflow_stage": "ROUTE",
            "next_action": "write_routing_request",
            "next_artifact_path": "steps/t000/routing_request.json",
            "allowed_decisions": list(ALLOWED_DECISIONS),
            "allowed_recipes": [],
            "route_step": 0,
            "max_route_steps": MAX_ROUTE_STEPS,
            "expansion_round": 0,
            "max_expansion_rounds": MAX_EXPANSION_ROUNDS,
            "budget_remaining": dict(DEFAULT_BUDGET, clusters=max_clusters),
        },
    )

    _write(os.path.join(out_dir, "steps", "t000", "candidate_packet.json"), packet)
    _write(os.path.join(out_dir, "states", "s000.json"), state)
    _write(os.path.join(out_dir, "workflow_state.json"),
           {"schema_version": SCHEMA_VERSION, "run_id": run_id,
            "current_state_path": "states/s000.json",
            "current_state_id": state["artifact_id"]})
    return state


def _write(path: str, art: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(art, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


# ---------- status ----------

def workflow_status(out_dir: str) -> dict[str, Any]:
    """Read-only view of where the workflow stands. Never mutates."""
    index_path = os.path.join(out_dir, "workflow_state.json")
    if not os.path.exists(index_path):
        raise WorkflowError(
            f"{out_dir} is not a paperconan workflow directory "
            "(no workflow_state.json); run `paperconan workflow start` first"
        )
    with open(index_path, encoding="utf-8") as fh:
        index = json.load(fh)
    state_path = os.path.join(out_dir, index.get("current_state_path", ""))
    if not os.path.exists(state_path):
        raise WorkflowError(f"workflow index points at a missing state: {state_path}")
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    require_envelope(state, expected_type="workflow_state")
    return state

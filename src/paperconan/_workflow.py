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
import shutil
from typing import Any

from ._finding_groups import BLOCK_FINDING_GROUPS

# 2: envelope requires source_finding_refs; coverage replaced `omitted_reason`
#    with `limitations` and gained scan_status / scan_incomplete / coverage_complete.
#    A v1 directory is not readable by this build, and says so rather than
#    failing later with a missing-field error.
SCHEMA_VERSION = 2
# Bumped whenever seeding, clustering or coverage semantics change, so artifacts
# from an older policy are never silently compared against a newer one.
WORKFLOW_POLICY_VERSION = 1
NUMERIC_CANONICALIZATION_VERSION = 1
# Detector *result* caps (max_findings) and the row-relation compute budget now
# record into ScanCoverage. Many resource caps still do not. Two that anyone can
# check from the source, both whole-detector skips rather than result caps:
# scan_dir's `wide` gate drops detect_relations, detect_equal_pairs and
# detect_row_pair_digit_coupling outright past
# _MAX_BLOCK_COLS, and _ROW_REL_MAX_ROWS silently disables two detectors at 61
# rows -- detect_row_relations loses an exact relation, and _scaled_row_candidates
# drops the band entirely, taking identical_row_reuse with it. All while the scan
# reports itself complete. So this stays
# False: the caveat below is over-broad, but it is true, and a wrong all-clear
# is worse than a broad warning. No count is given here on purpose; an earlier
# version cited one that no reader could verify and that would drift silently
# as caps get wired.
DETECTOR_CAPS_REPORTED = False

MAX_EXPANSION_ROUNDS = 2
# Route steps are bounded so a context-only loop cannot spin forever; the cap is
# generous relative to the two expansion rounds it has to accommodate.
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


def _file_digest(path: str) -> str:
    """Chunked so a large supplement cannot blow the memory caps CLAUDE.md sets."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


#  scan_dir reads this sidecar for provenance and embeds it as scan["paper"], so
#  it changes scan output and has to be part of the identity.
_PROVENANCE_SIDECAR = "paperconan_source.json"


def _source_manifest(in_dir: str, *, images: bool = False) -> list[dict[str, str]]:
    """Digests of exactly the inputs this workflow's scan reads.

    "Exactly" is load-bearing in both directions, and both were wrong before:

    * Over-covering moves `run_id` for files that never touched the output. The
      original walked the whole tree, so a stray `.DS_Store` changed the id while
      the scan stayed byte-identical — and with `out_dir` nested under `in_dir` it
      hashed the run's own artifacts and the id never reached a fixed point.
    * Under-covering is worse: two materially different runs collapse onto one
      identity. `paperconan_source.json` is not a table file, but the scan embeds
      it as `paper` provenance, so it belongs here.

    Images are only included when the scan is actually inventorying them.
    """
    from ._audit import find_local_images, find_table_files

    paths = set(find_table_files(in_dir))
    if images:
        paths |= set(find_local_images(in_dir))
    sidecar = os.path.join(in_dir, _PROVENANCE_SIDECAR)
    if os.path.isfile(sidecar):
        paths.add(sidecar)

    entries = []
    for path in sorted(paths):
        rel = os.path.relpath(path, in_dir).replace(os.sep, "/")
        try:
            entries.append({"path": rel, "sha256": _file_digest(path)})
        except OSError:
            continue
    return sorted(entries, key=lambda e: e["path"])


REQUIRED_ENVELOPE_FIELDS = (
    "schema_version",
    "run_id",
    "artifact_id",
    "parent_refs",
    "config_digest",
    "source_finding_refs",
    "coverage",
    "created_by_stage",
)


def _envelope(*, run_id: str, artifact_type: str, stage: str,
              parent_refs: list[str], config_digest: str,
              coverage: dict[str, Any], payload: dict[str, Any],
              source_finding_refs: list[str] | None = None) -> dict[str, Any]:
    art = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "artifact_type": artifact_type,
        "created_by_stage": stage,
        "parent_refs": parent_refs,
        "config_digest": config_digest,
        "source_finding_refs": source_finding_refs or [],
        "coverage": coverage,
    }
    collisions = set(payload) & set(art)
    if collisions:
        raise WorkflowError(f"payload may not shadow envelope fields: {sorted(collisions)}")
    art.update(payload)
    # artifact_id is derived last so it covers the whole artifact.
    art["artifact_id"] = _digest(art)
    return art


def verify_artifact_id(art: dict[str, Any]) -> None:
    """Recompute the content digest: lineage is only immutable if it's checked."""
    claimed = art.get("artifact_id")
    body = {k: v for k, v in art.items() if k != "artifact_id"}
    if claimed != _digest(body):
        raise WorkflowError(
            f"artifact_id does not match the artifact contents "
            f"({art.get('artifact_type')} in run {art.get('run_id')}); "
            "it was edited after it was written"
        )


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
    missing = [f for f in REQUIRED_ENVELOPE_FIELDS if f not in art]
    if missing:
        raise WorkflowError(f"artifact is missing envelope fields: {missing}")
    verify_artifact_id(art)


# ---------- seeds ----------

def _reject_nested_out_dir(in_dir: str, out_dir: str) -> None:
    """Keep the workflow's own artifacts out of the directory it treats as source.

    The manifest is non-recursive and matches no artifact name, so this is
    defence in depth rather than the sole guard — but a workflow directory living
    inside the source tree still invites confusion about what is input.
    realpath, not abspath, so a symlinked out_dir cannot slip past.
    """
    in_abs = os.path.realpath(in_dir)
    out_abs = os.path.realpath(out_dir)
    if in_abs == out_abs or out_abs.startswith(in_abs + os.sep):
        raise WorkflowError(
            f"workflow --out ({out_dir}) must live outside the source directory "
            f"({in_dir}); keep workflow artifacts separate from the data they describe"
        )


def _stable_id(prefix: str, parts: list[Any]) -> str:
    """Content-derived id: stable under unrelated additions elsewhere in the scan."""
    return prefix + ":" + hashlib.sha256(
        _canonical_json(parts).encode("utf-8")
    ).hexdigest()[:16]


def _iter_raw_findings(scan: dict[str, Any]):
    """Per-block findings, read through the canonical group registry."""
    for blk in scan.get("relations_blocks", []) or []:
        for group in BLOCK_FINDING_GROUPS:
            for f in blk.get(group, []) or []:
                yield blk, group, f


def _raw_severity_of(f: dict[str, Any]) -> str:
    # raw_severity is frozen before profile projection; `severity` may already
    # have been rewritten to "low" and must not drive routing.
    return f.get("raw_severity") or f.get("severity") or "low"


def _block_seed(blk: dict[str, Any], group: str, f: dict[str, Any]) -> dict[str, Any]:
    block = blk.get("block") or {}
    locator = {
        "scope": "block",
        "file": blk.get("file"),
        "sheet": blk.get("sheet"),
        "block_rows": block.get("rows"),
        "block_cols": block.get("cols"),
    }
    return {
        # Derived from content, never from enumeration order: adding an unrelated
        # file must not renumber the seeds a routing request already cites.
        "seed_id": _stable_id("seed", [locator, group, f.get("kind"), f.get("rule")]),
        "kind": f.get("kind"),
        "group": group,
        **locator,
        "raw_severity": _raw_severity_of(f),
        "rule": f.get("rule"),
        "n": f.get("n"),
    }


# Fields that pin down *which* cross-sheet finding this is. The locator alone is
# not enough: several findings can share one sheet pair, and repeated row labels
# make `rule` repeat too, so hashing only (locator, kind, rule) is not injective.
_CROSS_SHEET_LOCATOR_FIELDS = (
    "file_a", "file_b", "sheet_a", "sheet_b",
    "row_a", "row_b", "col_a", "col_b", "block_a", "block_b",
    "figure_a", "figure_b", "same_position_count", "size_a", "size_b",
)


def _cross_sheet_seed(f: dict[str, Any]) -> dict[str, Any]:
    locator = {
        "scope": "cross_sheet",
        "file": f.get("file") or f.get("file_a"),
        "sheet": f"{f.get('sheet_a', '?')} ↔ {f.get('sheet_b', '?')}",
        "block_rows": None,
        "block_cols": None,
    }
    detail = {k: f.get(k) for k in _CROSS_SHEET_LOCATOR_FIELDS if f.get(k) is not None}
    return {
        "seed_id": _stable_id(
            "seed", [locator, "cross_sheet", f.get("kind"), f.get("rule"), detail]
        ),
        "kind": f.get("kind"),
        "group": "cross_sheet_findings",
        **locator,
        "cross_sheet_locator": detail,
        "raw_severity": _raw_severity_of(f),
        "rule": f.get("rule"),
        "n": f.get("same_position_count") or f.get("size_a"),
    }


def _cluster_key(seed: dict[str, Any]) -> tuple:
    # block_cols is part of the key: two panels side by side on one sheet are
    # independent evidence and must not share a cluster (or a routing vote).
    return (
        seed["scope"],
        seed["file"] or "",
        seed["sheet"] or "",
        seed["block_rows"] or "",
        seed["block_cols"] or "",
    )


def _build_clusters(scan: dict[str, Any], max_clusters: int) -> tuple[list[dict], dict]:
    seeds = [_block_seed(blk, group, f) for blk, group, f in _iter_raw_findings(scan)]
    seeds += [_cross_sheet_seed(f) for f in scan.get("cross_sheet_findings", []) or []]

    # A routing request cites seeds by id, so a content-derived id is only an
    # identifier if the hashed tuple is a key. Two findings sharing an id would
    # make a routing decision ambiguous and inflate seeds_total; catch it here
    # rather than letting the packet ship indistinguishable entries.
    ids = [s["seed_id"] for s in seeds]
    if len(set(ids)) != len(ids):
        from collections import Counter
        dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
        raise WorkflowError(
            f"seed ids are not unique ({len(ids) - len(set(ids))} collisions, "
            f"first: {dupes[0]}); the seed locator does not distinguish these findings"
        )

    grouped: dict[tuple, list[dict]] = {}
    for seed in seeds:
        grouped.setdefault(_cluster_key(seed), []).append(seed)

    clusters = []
    for key, members in grouped.items():
        members = sorted(
            members,
            key=lambda s: (_SEVERITY_RANK.get(s["raw_severity"], 3), s["seed_id"]),
        )
        n_high = sum(1 for s in members if s["raw_severity"] == "high")
        clusters.append({
            "cluster_id": _stable_id("cluster", list(key)),
            "scope": key[0],
            "file": key[1],
            "sheet": key[2],
            "block_rows": key[3],
            "block_cols": key[4],
            "strongest_raw_severity": members[0]["raw_severity"],
            "n_high_seeds": n_high,
            "seeds": members,
        })

    # Deterministic and content-ranked: strongest severity first, then the
    # cluster carrying more high seeds, then the stable id. Ranking on the id
    # alone would drop clusters by hash order, i.e. arbitrarily.
    clusters.sort(key=lambda c: (
        _SEVERITY_RANK.get(c["strongest_raw_severity"], 3),
        -c["n_high_seeds"],
        c["cluster_id"],
    ))

    kept, omitted = clusters[:max_clusters], clusters[max_clusters:]
    coverage = _coverage_for(scan, seeds, clusters, omitted, max_clusters)
    return kept, coverage


# Finding families carried in scan.json that the Phase 1 seed layer does not route
# yet. Named explicitly so `coverage_complete` can be false rather than letting the
# packet imply it covered everything.
_UNSEEDED_FAMILIES = (
    "image_findings",
    "digit_distribution",
    "decimal_endings",
    "decimal_tail_clusters",
)


def _describe_scan_limitation(entry: Any) -> str:
    """A sentence, not a Python repr — these reach a reader's terminal."""
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return str(entry)
    reason = str(entry.get("reason") or "unspecified").replace("_", " ")
    where = " ".join(
        str(entry[k]) for k in ("detector", "file", "sheet") if entry.get(k)
    )
    extra = ", ".join(
        f"{k}={entry[k]}" for k in sorted(entry)
        if k not in ("scope", "reason", "detector", "file", "sheet")
    )
    text = reason
    if where:
        text += f" in {where}"
    if extra:
        text += f" ({extra})"
    return text


def _coverage_for(scan, seeds, clusters, omitted, max_clusters) -> dict[str, Any]:
    limitations: list[str] = []

    # The scan's own caps run before the workflow ever sees a finding. There are
    # two distinct channels and the packet has to carry both: the finding cap
    # (counted in findings_omitted) and everything the scan itself recorded as
    # incomplete — blocks dropped by the report-block limit, files that failed to
    # parse — which never reaches findings_omitted at all.
    upstream_omitted = int(scan.get("findings_omitted") or 0)
    if upstream_omitted:
        limitations.append(
            f"{upstream_omitted} findings were dropped by the scan's finding caps "
            "before seeding (see scan.json findings_omitted)"
        )

    scan_status = scan.get("scan_status")
    scan_coverage = scan.get("coverage") or {}
    scan_incomplete = bool(scan_coverage.get("truncated")) or (
        scan_status not in (None, "complete")
    )
    if scan_incomplete:
        limitations.append(
            f"the underlying scan was not complete (scan_status={scan_status!r}); "
            "seeds cover only what it analysed"
        )
        for entry in scan_coverage.get("limitations") or []:
            limitations.append("scan: " + _describe_scan_limitation(entry))
        # The record list is itself capped. Reporting the retained records
        # without the dropped count shows a short list that reads as a full one.
        dropped = int(scan_coverage.get("limitations_omitted") or 0)
        if dropped:
            limitations.append(
                f"scan: {dropped} further limitation records were dropped by the record "
                f"cap (PAPERCONAN_MAX_LIMITATIONS); the scan lines above are a sample, "
                f"not the whole set. Records, not distinct causes: repeats of one cause "
                f"past the cap are each counted, so this bounds what was lost from above"
            )

    unseeded = {
        family: len(scan.get(family) or [])
        for family in _UNSEEDED_FAMILIES
        if scan.get(family)
    }
    for family, count in unseeded.items():
        limitations.append(
            f"{count} {family} are present in the scan but this view does not route "
            "them; read them from scan.json directly"
        )

    if omitted:
        limitations.append(
            f"cluster budget {max_clusters} reached; "
            f"{len(omitted)} lower-ranked clusters were not routed"
        )

    # Result caps, compute budgets and candidate-pool caps now record through
    # ScanCoverage. What still reaches no channel is the whole-detector skips:
    # a block dropped for being too wide (the `wide` gate) or too tall
    # (_ROW_REL_MAX_ROWS, a cost bound that drops real relations at 61 rows).
    # Until those are wired
    # the workflow cannot know whether a block was fully enumerated, so it must
    # not claim it was. Stating it here rather than only in a side field means
    # `coverage_complete` is false by construction and the caveat reaches the
    # CLI, which prints `limitations`.
    #
    # Deliberately not an enumeration: a list here goes stale the moment a cap
    # is wired, which is how an earlier version of this text came to name
    # coverage the code did not have.
    if not DETECTOR_CAPS_REPORTED:
        limitations.append(
            "some detector caps are still not reported: a detector that skipped a "
            "block for being too wide or too tall, or stopped at an internal "
            "limit, may not appear above. This line does not enumerate what is "
            "not reported."
        )

    return {
        "seeds_total": len(seeds),
        "clusters_total": len(clusters),
        "clusters_omitted": len(omitted),
        "truncated": bool(omitted),
        "upstream_findings_omitted": upstream_omitted,
        "scan_status": scan_status,
        "scan_incomplete": scan_incomplete,
        "families_not_seeded": unseeded,
        "coverage_complete": not limitations,
        "limitations": limitations,
        # Known blind spot, stated rather than implied. Every detector result cap
        # and compute budget now records through ScanCoverage, as do the candidate
        # pool caps; whole-detector skips (the `wide` gate, _ROW_REL_MAX_ROWS) do
        # not, so `coverage_complete` means
        # "complete as far as the scan reported", not "every finding was seeded".
        # See the caveat assembled above for what that leaves out.
        "detector_caps_reported": DETECTOR_CAPS_REPORTED,
    }


# ---------- start ----------

def start_workflow(in_dir: str, out_dir: str, *, profile: str = "review",
                   max_clusters: int | None = None,
                   force: bool = False) -> dict[str, Any]:
    """Run DISCOVER: scan, seed, and write the immutable step-0 artifacts."""
    from ._audit import scan_dir

    max_clusters = DEFAULT_BUDGET["clusters"] if max_clusters is None else max_clusters
    _reject_nested_out_dir(in_dir, out_dir)
    restarting = os.path.exists(os.path.join(out_dir, "workflow_state.json"))
    if restarting and not force:
        raise WorkflowError(
            f"{out_dir} already holds a workflow; restarting would rewind its "
            "index and orphan later states. Use force=True (CLI --force)."
        )

    # Manifest first: hashing after the scan would fold this run's own artifacts
    # into the digest whenever out_dir sits under in_dir, and run_id would never
    # settle.
    manifest = _source_manifest(in_dir)

    os.makedirs(out_dir, exist_ok=True)
    scan = scan_dir(in_dir, out_dir, write_md=False, write_html=False, profile=profile)

    config = {
        "schema_version": SCHEMA_VERSION,
        "workflow_policy_version": WORKFLOW_POLICY_VERSION,
        "numeric_canonicalization_version": NUMERIC_CANONICALIZATION_VERSION,
        # The packet is deliberately profile-invariant (seeds come from the raw
        # stream), but config_digest describes what produced the run, and profile
        # is the one knob the CLI exposes.
        "profile": profile,
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

    # Last possible moment: everything that can fail — the rescan, seeding,
    # clustering, envelope construction — has already run. Clearing any earlier
    # leaves an index pointing at deleted states if one of them raises, which is
    # the unreadable directory this guard exists to prevent.
    if restarting:
        _clear_previous_run(out_dir)

    _write(os.path.join(out_dir, "steps", "t000", "candidate_packet.json"), packet)
    _write(os.path.join(out_dir, "states", "s000.json"), state)
    _write(os.path.join(out_dir, "workflow_state.json"),
           {"schema_version": SCHEMA_VERSION, "run_id": run_id,
            "current_state_path": "states/s000.json",
            "current_state_id": state["artifact_id"]})
    return state


def _clear_previous_run(out_dir: str) -> None:
    """Drop the prior run's artifacts. No ignore_errors — a half-cleaned
    directory is the mixed-run state this is meant to prevent, and it must
    surface as a WorkflowError rather than a bare OSError the CLI won't catch.
    """
    for stale in ("states", "steps"):
        path = os.path.join(out_dir, stale)
        if not os.path.exists(path):
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise WorkflowError(
                f"could not clear the previous run's {stale}/ in {out_dir}: {exc}. "
                "The directory now holds artifacts from more than one run; remove "
                "it and re-run `paperconan workflow start`."
            ) from None


def _write(path: str, art: dict[str, Any]) -> None:
    """Atomic: a killed run must not leave a half-written artifact behind."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(art, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _read_json(path: str, *, what: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise WorkflowError(f"{what} is missing: {path}") from None
    except IsADirectoryError:
        raise WorkflowError(f"{what} is not a file: {path}") from None
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"{what} is not valid JSON ({path}): {exc}") from None
    except OSError as exc:
        raise WorkflowError(f"{what} could not be read ({path}): {exc}") from None


# ---------- status ----------

def workflow_status(out_dir: str) -> dict[str, Any]:
    """Read-only view of where the workflow stands. Never mutates."""
    index_path = os.path.join(out_dir, "workflow_state.json")
    if not os.path.exists(index_path):
        raise WorkflowError(
            f"{out_dir} is not a paperconan workflow directory "
            "(no workflow_state.json); run `paperconan workflow start` first"
        )
    index = _read_json(index_path, what="workflow index")
    if index.get("schema_version") != SCHEMA_VERSION:
        raise WorkflowError(
            f"workflow index schema_version {index.get('schema_version')!r} is not "
            f"supported (this build reads {SCHEMA_VERSION}); "
            "re-run `paperconan workflow start --force`"
        )

    rel = index.get("current_state_path")
    if not rel or not isinstance(rel, str):
        raise WorkflowError("workflow index has no current_state_path")
    state_path = os.path.join(out_dir, rel)
    # These artifacts are exchanged between tool and Agent; keep a crafted index
    # from reaching outside the workflow directory.
    if os.path.commonpath([os.path.abspath(out_dir),
                           os.path.abspath(state_path)]) != os.path.abspath(out_dir):
        raise WorkflowError(f"workflow index points outside the workflow directory: {rel}")

    state = _read_json(state_path, what="workflow state")
    require_envelope(state, expected_type="workflow_state")

    # Per-artifact digests cannot catch a mixed directory: a state left behind by
    # an earlier run verifies fine on its own. Cross-check it against the index.
    if index.get("run_id") != state.get("run_id"):
        raise WorkflowError(
            f"workflow index belongs to run {index.get('run_id')} but its current "
            f"state belongs to {state.get('run_id')}; the directory mixes runs"
        )
    if index.get("current_state_id") != state.get("artifact_id"):
        raise WorkflowError(
            "workflow index does not point at the state it claims "
            f"({index.get('current_state_id')} vs {state.get('artifact_id')})"
        )
    return state

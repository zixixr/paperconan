"""Layered read-only views over a scan, for reading a paper one layer at a time.

A single supplement can yield thousands of findings and a multi-MB scan.json.
Handing that to a reviewer — human or agent — buries the few signals worth
acting on. The tempting fix is to tighten the detectors, but that trades a
presentation problem for a detection problem: the real signals go too.

So nothing is filtered here. The same findings are reached in stages, each
costing about a screenful:

    overview(scan)                        which locations carry signal, how strong
    drill(scan, location)                 that location, grouped by finding kind
    drill(scan, location, kind=...)       the individual findings of one kind
    explain(scan, finding_id)             one finding, with its evidence table

The contract that makes this safe is reachability: every finding is reachable
through those layers, or named in the view's own `coverage`. A signal that is
silently unreachable is no better than one that was never detected.

These functions are pure reads — they never write, and never mutate the scan.
"""
from __future__ import annotations

from typing import Any

from ._workflow import _build_clusters

# Kept generous: these are read straight into a reviewer's context, and the
# limit exists to bound a pathological scan, not to curate.
DEFAULT_MAX_LOCATIONS = 20
DEFAULT_MAX_FINDINGS = 50


def _location_label(cluster: dict[str, Any]) -> str:
    if cluster["scope"] == "cross_sheet":
        return f'{cluster["file"]} :: {cluster["sheet"]}'
    where = f'{cluster["file"]} :: {cluster["sheet"]}'
    if cluster.get("block_rows"):
        where += f' rows {cluster["block_rows"]}'
    if cluster.get("block_cols"):
        where += f' cols {cluster["block_cols"]}'
    return where


def _clusters_of(scan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every cluster, unbounded. Truncation is a presentation choice made by the
    caller, so it can be reported — not something baked into the grouping."""
    clusters, _ = _build_clusters(scan, max_clusters=10**9)
    return clusters


def _families(cluster: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for seed in cluster["seeds"]:
        if seed["kind"] and seed["kind"] not in seen:
            seen.append(seed["kind"])
    return seen


# ---------- L1 ----------

def overview(scan: dict[str, Any], *,
             max_locations: int = DEFAULT_MAX_LOCATIONS) -> dict[str, Any]:
    """Which locations carry signal, how strong, and of what kinds."""
    clusters = _clusters_of(scan)
    shown, hidden = clusters[:max_locations], clusters[max_locations:]

    locations = []
    for i, cluster in enumerate(shown, 1):
        locations.append({
            "n": i,
            "cluster_id": cluster["cluster_id"],
            "scope": cluster["scope"],
            "location": _location_label(cluster),
            "strongest": cluster["strongest_raw_severity"],
            "signals": len(cluster["seeds"]),
            "high": cluster["n_high_seeds"],
            "families": _families(cluster),
        })

    hidden_signals = sum(len(c["seeds"]) for c in hidden)
    limitations = []
    if hidden:
        limitations.append(
            f"{len(hidden)} lower-ranked locations ({hidden_signals} signals) are not "
            f"listed; raise max_locations above {max_locations} to see them"
        )

    return {
        "files": scan.get("n_files"),
        "locations": locations,
        "signals_total": sum(len(c["seeds"]) for c in clusters),
        "coverage": {
            "locations_total": len(clusters),
            "locations_not_shown": len(hidden),
            "signals_not_shown": hidden_signals,
            "limitations": limitations,
        },
    }


# ---------- L2 / L3 ----------

def _resolve(clusters: list[dict[str, Any]], location: int | str) -> dict[str, Any]:
    if isinstance(location, int):
        if 1 <= location <= len(clusters):
            return clusters[location - 1]
    else:
        for cluster in clusters:
            if cluster["cluster_id"] == location:
                return cluster
    raise ValueError(
        f"no such location: {location!r}; run overview() to list them"
    )


def drill(scan: dict[str, Any], location: int | str, *, kind: str | None = None,
          max_findings: int = DEFAULT_MAX_FINDINGS) -> dict[str, Any]:
    """One location — grouped by kind, or listing the findings of a single kind."""
    cluster = _resolve(_clusters_of(scan), location)
    label = _location_label(cluster)

    if kind is None:
        groups: dict[str, list[dict[str, Any]]] = {}
        for seed in cluster["seeds"]:
            groups.setdefault(seed["kind"] or "?", []).append(seed)
        by_kind = [
            {
                "kind": k,
                "n": len(v),
                "high": sum(1 for s in v if s["raw_severity"] == "high"),
                # a concrete instance, so the group can be judged without opening it
                "example": v[0].get("rule") or "",
            }
            for k, v in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ]
        return {
            "cluster_id": cluster["cluster_id"],
            "location": label,
            "scope": cluster["scope"],
            "signals": len(cluster["seeds"]),
            "by_kind": by_kind,
        }

    matching = [s for s in cluster["seeds"] if (s["kind"] or "?") == kind]
    shown = matching[:max_findings]
    limitations = []
    if len(shown) < len(matching):
        limitations.append(
            f"showing {len(shown)} of {len(matching)}; raise max_findings to see the rest"
        )
    return {
        "cluster_id": cluster["cluster_id"],
        "location": label,
        "kind": kind,
        "findings": [
            {
                "finding_id": s["seed_id"],
                "kind": s["kind"],
                "severity": s["raw_severity"],
                "rule": s.get("rule"),
                "n": s.get("n"),
            }
            for s in shown
        ],
        "coverage": {
            "total": len(matching),
            "shown": len(shown),
            "limitations": limitations,
        },
    }


# ---------- L4 ----------

def _iter_findings_with_seeds(scan: dict[str, Any]):
    for cluster in _clusters_of(scan):
        for seed in cluster["seeds"]:
            yield cluster, seed


def _match_raw_finding(scan: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any] | None:
    """Find the scan finding a seed was derived from, to recover its evidence."""
    from ._finding_groups import BLOCK_FINDING_GROUPS

    if seed["scope"] == "cross_sheet":
        for f in scan.get("cross_sheet_findings") or []:
            if f.get("kind") == seed["kind"] and f.get("rule") == seed.get("rule"):
                return f
        return None
    for blk in scan.get("relations_blocks") or []:
        if blk.get("file") != seed["file"] or blk.get("sheet") != seed["sheet"]:
            continue
        if (blk.get("block") or {}).get("rows") != seed["block_rows"]:
            continue
        for group in BLOCK_FINDING_GROUPS:
            for f in blk.get(group) or []:
                if f.get("kind") == seed["kind"] and f.get("rule") == seed.get("rule"):
                    return f
    return None


# Fields that are presentation or bookkeeping rather than the finding's own
# numbers; everything else is surfaced as a parameter so nothing is hidden.
_NON_PARAMETER_KEYS = {
    "kind", "rule", "severity", "raw_severity", "profile_action",
    "false_positive_context", "evidence", "likely_benign",
}

# A demotion can come from four places and each leaves a different trace:
# the profile guards write false_positive_context, two flood demoters write
# prefilter_reason, and the dense-relation demoter writes only a bare flag.
# Reading one field would silently lose three of them — and a finding shown as
# demoted with no reason is exactly the shape a real signal disappears in.
_FLAG_REASONS = {
    "dense_block": "one of many pairwise relations in a dense block; "
                   "down-weighted so a dense matrix does not dominate",
    "reused_progression": "this progression repeats elsewhere, so it reads as a "
                          "shared axis rather than measured data",
    "within_col_flood_sheet": "one of many within-column findings on this sheet",
}


def _demotion_reasons(raw: dict[str, Any]) -> list[str]:
    """Every recorded reason this finding was down-weighted, from any source."""
    reasons: list[str] = []
    for ctx in raw.get("false_positive_context") or []:
        reasons.append(str(ctx))
    for flag, text in _FLAG_REASONS.items():
        if raw.get(flag):
            reasons.append(text)
    reason = raw.get("prefilter_reason")
    if reason and str(reason) not in reasons:
        reasons.append(str(reason))
    return reasons


def explain(scan: dict[str, Any], finding_id: str) -> dict[str, Any]:
    """One finding in full: parameters, evidence, and how a profile treated it."""
    for cluster, seed in _iter_findings_with_seeds(scan):
        if seed["seed_id"] != finding_id:
            continue
        raw = _match_raw_finding(scan, seed) or {}
        return {
            "finding_id": finding_id,
            "kind": seed["kind"],
            "location": _location_label(cluster),
            "cluster_id": cluster["cluster_id"],
            "rule": seed.get("rule") or raw.get("rule"),
            "severity": {
                # raw is what the detector produced; effective is what the
                # display profile projected. Showing both means a demotion can
                # be reviewed rather than silently taken on trust.
                "raw": seed["raw_severity"],
                "effective": raw.get("severity", seed["raw_severity"]),
                "profile_action": raw.get("profile_action", "kept"),
                "context": _demotion_reasons(raw),
                "likely_benign": raw.get("likely_benign"),
            },
            "parameters": {
                k: v for k, v in raw.items() if k not in _NON_PARAMETER_KEYS
            },
            "evidence": raw.get("evidence"),
        }
    raise ValueError(f"no such finding: {finding_id!r}; run drill() to list them")

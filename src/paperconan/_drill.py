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

import os
from typing import Any

from ._workflow import (_SEVERITY_RANK, _block_seed, _build_clusters,
                        _cross_sheet_seed, _iter_raw_findings)

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
    merged_spans = cluster.get("block_cols_merged") or []
    spans = [c for c in merged_spans if c]
    if len(merged_spans) > 1:
        # A merged panel spans several column groups. Naming the first would send
        # the reader to a third of the evidence; naming none loses the location
        # entirely, which is what setting block_cols=None used to do.
        # Counted from members, not from non-empty spans: a member whose span
        # is missing still holds evidence, and dropping it from the count told
        # the reader a three-group panel had two.
        first = spans[0] if spans else "?"
        where += f' cols {first} +{len(merged_spans) - 1} more'
    elif cluster.get("block_cols"):
        where += f' cols {cluster["block_cols"]}'
    return where


def _clusters_of(scan: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every cluster plus what the seeding layer already knows it could not cover.

    That second value is not optional bookkeeping. It carries the upstream
    finding caps, an incomplete scan, the families this layer does not route, and
    the detector-level caps still reaching no channel — i.e. most of the ways a real signal
    fails to reach the reader. Discarding it here made `overview` print an empty
    `limitations` list on a scan where thousands of findings had been dropped.
    """
    return _build_clusters(scan, max_clusters=10**9)


def _families(cluster: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for seed in cluster["seeds"]:
        if seed["kind"] and seed["kind"] not in seen:
            seen.append(seed["kind"])
    return seen


# ---------- L1 ----------


def _merge_panels(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the ranked clusters by panel for the overview.

    Clusters key on the column span, which is right for routing — two panels
    side by side are independent evidence. But one panel's findings often sit in
    several column groups, and listing each separately makes a single finding
    compete with itself for space: on a real paper, six exactly-duplicated
    column pairs from one panel occupied six ranks instead of one, and none
    reached the first page. The column span is what `drill` is for.
    """
    merged: dict[tuple, dict[str, Any]] = {}
    for cluster in clusters:
        key = (cluster["scope"], cluster["file"], cluster["sheet"],
               cluster.get("block_rows"))
        panel = merged.get(key)
        if panel is None:
            merged[key] = dict(cluster, member_ids=[cluster["cluster_id"]],
                               block_cols_merged=[cluster.get("block_cols")])
            continue
        panel["seeds"] = panel["seeds"] + cluster["seeds"]
        panel["block_cols_merged"].append(cluster.get("block_cols"))
        panel["n_high_seeds"] += cluster["n_high_seeds"]
        panel["member_ids"].append(cluster["cluster_id"])
        if _SEVERITY_RANK.get(cluster["strongest_raw_severity"], 3) < \
           _SEVERITY_RANK.get(panel["strongest_raw_severity"], 3):
            panel["strongest_raw_severity"] = cluster["strongest_raw_severity"]

    # Re-rank: merging changed the counts the ranking is built on. Skipping this
    # left a panel with seven high findings behind one with six, purely because
    # its evidence had arrived split across six column groups.
    panels = list(merged.values())
    panels.sort(key=lambda c: (
        _SEVERITY_RANK.get(c["strongest_raw_severity"], 3),
        -c["n_high_seeds"],
        -len(c["seeds"]),
        c["cluster_id"],
    ))
    return panels


def _interleave_by_family(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round-robin the ranked list across finding families.

    Ranking on severity then high-count alone lets one family own the page: a
    block where a single kind hits the per-block cap contributes 150 high
    findings, which outranks a six-row exactly-duplicated column. Measured on a
    real paper, that pushed the duplicated columns the report was about to rank
    25-47, past the default page.

    A family saturating one block is evidence about that family, not about the
    paper. Taking one block from each family in turn keeps the strongest of every
    kind on the first page while preserving the existing order within a family.
    Deterministic: families are visited in the order they first appear in the
    already-deterministic ranking.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for cluster in clusters:
        families = _families(cluster)
        buckets.setdefault(families[0] if families else "?", []).append(cluster)

    out: list[dict[str, Any]] = []
    while any(buckets.values()):
        for queue in buckets.values():
            if queue:
                out.append(queue.pop(0))
    return out



def _ranked_panels(scan: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The list `overview` and `drill` share, so an ordinal means one thing.

    Not `explain`: L4 answers about one finding and reports the member cluster's
    exact column span, which is the span the reader needs to find the cells --
    deliberately narrower than the panel label above it. _resolve accepts a
    member id, so that id still opens the panel.

    overview used to merge and interleave while drill resolved against the raw
    cluster list, so `overview` printed a number and `drill <that number>` opened
    a different location -- and a merged panel opened to only its first member,
    dropping the rest while its own coverage reported the location complete.
    Both layers now walk the same ordering, so an ordinal means one thing.
    """
    clusters, seeding = _clusters_of(scan)
    return _interleave_by_family(_merge_panels(clusters)), seeding


def overview(scan: dict[str, Any], *,
             max_locations: int = DEFAULT_MAX_LOCATIONS) -> dict[str, Any]:
    """Which locations carry signal, how strong, and of what kinds."""
    clusters, seeding = _ranked_panels(scan)
    max_locations = max(0, max_locations)
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
    # Everything the seeding layer already declared, then what this layer itself
    # holds back. Both have to reach the reader, or "read coverage" is advice
    # about a field that does not say anything.
    limitations = list(seeding.get("limitations") or [])
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
            "families_not_seeded": seeding.get("families_not_seeded") or {},
            "upstream_findings_omitted": seeding.get("upstream_findings_omitted", 0),
            "scan_incomplete": seeding.get("scan_incomplete", False),
            "detector_caps_reported": seeding.get("detector_caps_reported", False),
            "complete": not limitations,
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
            # member_ids too: merging keeps the first member's cluster_id, so an
            # id taken from an earlier run or from a member cluster still has to
            # open the panel that now carries its seeds.
            if cluster["cluster_id"] == location or location in (
                    cluster.get("member_ids") or ()):
                return cluster
    raise ValueError(
        f"no such location: {location!r}; run overview() to list them"
    )


def drill(scan: dict[str, Any], location: int | str, *, kind: str | None = None,
          max_findings: int = DEFAULT_MAX_FINDINGS) -> dict[str, Any]:
    """One location — grouped by kind, or listing the findings of a single kind."""
    clusters, _seeding = _ranked_panels(scan)
    cluster = _resolve(clusters, location)
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
            # every kind at this location is listed, but the scan-wide caveats
            # still apply here — the reader is told to check coverage at each layer
            "coverage": {
                "kinds_total": len(by_kind),
                "kinds_shown": len(by_kind),
                "limitations": list(_seeding.get("limitations") or []),
            },
        }

    available = sorted({s["kind"] or "?" for s in cluster["seeds"]})
    if kind not in available:
        raise ValueError(
            f"no such kind at this location: {kind!r}; available: {', '.join(available)}"
        )
    matching = [s for s in cluster["seeds"] if (s["kind"] or "?") == kind]
    max_findings = max(0, max_findings)
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
            # scan-wide caveats belong here too: this is the layer where a
            # reader concludes "that is all of them", so it is where a silently
            # capped detector or an unrouted family matters most.
            "limitations": limitations + list(_seeding.get("limitations") or []),
        },
    }


# ---------- L4 ----------

def _iter_findings_with_seeds(scan: dict[str, Any]):
    clusters, _seeding = _clusters_of(scan)
    for cluster in clusters:
        for seed in cluster["seeds"]:
            yield cluster, seed


def _match_raw_finding(scan: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any] | None:
    """Recover the scan finding a seed came from, to show its evidence.

    Replays the seeding enumeration rather than recomputing one hash. Two
    reasons. Matching on the hashed tuple alone is not injective once ids
    collide -- which they do on real supplements -- so `explain` would serve one
    finding's evidence under another's heading. And a disambiguated id carries a
    `#N` suffix assigned over the whole seed list, so no per-finding hash can
    reproduce it: matching on the bare hash returned nothing at all, and
    `explain` reported the profile defaults for a finding the profile had
    demoted. Walking the same order and applying the same suffix rule makes the
    id mean here exactly what it means in the packet.
    """
    want = seed["seed_id"]
    seen: dict[str, int] = {}

    def disambiguated(base: str) -> str:
        # Same rule as _build_clusters, and the counter spans both passes because
        # that is one list there.
        if base in seen:
            seen[base] += 1
            return f"{base}#{seen[base]}"
        seen[base] = 0
        return base

    for blk, group, f in _iter_raw_findings(scan):
        if disambiguated(_block_seed(blk, group, f)["seed_id"]) == want:
            return f
    for f in scan.get("cross_sheet_findings") or []:
        if disambiguated(_cross_sheet_seed(f)["seed_id"]) == want:
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


def _reread_evidence(scan: dict[str, Any], seed: dict[str, Any],
                     raw: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a finding's evidence window from the source data.

    The scan stores a bounded window so a dense block is not copied whole into
    every finding. That is the right default -- the stored windows were most of
    a scan corpus's bytes -- but it must not be the only way to see the cells,
    or a trimmed window becomes a permanent loss. `input_dir` is recorded in the
    scan, so the block can be read again on request.

    Returns a dict carrying either the full window or, when the source cannot be
    reached, the reason. Never silently returns the trimmed view as if it were
    complete: a reader who asked for everything and got a subset would take the
    subset for the block.
    """
    from ._audit import (_FULL_EV_CELLS, _block_evidence, header_for,
                         load_table)

    in_dir = scan.get("input_dir")
    if not in_dir:
        return {"unavailable": "this scan records no input_dir, so the source "
                               "data cannot be located"}
    path = os.path.join(in_dir, seed.get("file") or "")
    if not os.path.isfile(path):
        return {"unavailable": f"source file not found at {path}; it moved or the "
                               f"scan was copied away from its inputs"}
    try:
        sheets = load_table(path)
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        return {"unavailable": f"{type(exc).__name__} reading {path}: {exc}"}

    sheet = sheets.get(seed.get("sheet"))
    if sheet is None:
        return {"unavailable": f"sheet {seed.get('sheet')!r} is not in {path} now"}

    rows = _span(seed.get("block_rows"))
    cols = _span(seed.get("block_cols"))
    if rows is None or cols is None:
        return {"unavailable": "this finding records no block span to re-read"}

    r0, r1 = rows
    c0, c1 = cols

    # The scan holds no identity for its inputs -- no hash, no mtime -- and
    # input_dir may be relative, so the same scan.json read from another
    # directory resolves against whatever tree is there. Re-reading blind turns
    # "you are seeing part of the block" into "you are seeing a different
    # block", which is worse than the trimming this exists to undo. The stored
    # window records the block's true extent, so a shape that no longer matches
    # is proof the source moved on, and the only honest answer is to refuse.
    stored = (raw.get("evidence") or {}).get("truncated")
    if isinstance(stored, dict):
        want_cols = stored.get("cols_total")
        if want_cols is not None and (c1 - c0) != want_cols:
            return {"unavailable": "the finding's column span does not match what "
                                   "the scan recorded; the scan or the finding is "
                                   "from a different run"}
        if sheet.nrows < r1:
            return {"unavailable": f"{seed.get('sheet')!r} in {path} now has "
                                   f"{sheet.nrows} rows, fewer than the {r1} this "
                                   f"finding covers -- the source changed since "
                                   f"the scan"}
    # Rebuilt from the sheet, not taken from the stored evidence: those headers
    # were already sliced to the stored window, so reusing them left the widened
    # window with a short header row and the columns effectively un-widened.
    header = header_for(sheet, r0, c0, c1)
    stored_ev = raw.get("evidence") or {}
    highlight_cols = stored_ev.get("highlight_cols") or []
    highlight_rows = stored_ev.get("highlight_rows") or []
    if not highlight_cols and not highlight_rows:
        # Without the markers the reader cannot see which cells the finding is
        # about, and a wide window of unmarked numbers is worse than the trimmed
        # one it replaces.
        return {"unavailable": "this finding's highlighted cells could not be "
                               "recovered, so a full window would not show what "
                               "the finding is about"}

    # Widened for this call, not unbounded: CLAUDE.md requires the evidence caps
    # be respected in new code paths, and a genomics block is millions of cells.
    # Passed as arguments rather than by raising the module globals, so a
    # concurrent scan is unaffected.
    span_rows, span_cols = r1 - r0 + 2, c1 - c0
    if span_rows * max(1, span_cols) > _FULL_EV_CELLS:
        allowed = max(1, _FULL_EV_CELLS // max(1, span_cols))
        return _block_evidence(sheet, r0, r1, c0, c1, header,
                               highlight_cols, highlight_rows,
                               max_rows=allowed, max_cols=span_cols)
    return _block_evidence(sheet, r0, r1, c0, c1, header,
                           highlight_cols, highlight_rows,
                           max_rows=span_rows, max_cols=span_cols)


def _span(text: Any) -> tuple[int, int] | None:
    """'3-120' -> (2, 120): scan spans are 1-based inclusive, slices are 0-based."""
    if not isinstance(text, str) or "-" not in text:
        return None
    lo, _, hi = text.partition("-")
    try:
        return int(lo) - 1, int(hi)
    except ValueError:
        return None


def explain(scan: dict[str, Any], finding_id: str, *, full: bool = False) -> dict[str, Any]:
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
            "evidence": (_reread_evidence(scan, seed, raw) if full
                         else raw.get("evidence")),
            # The renderer caps rows at a page's worth. Under --full that cap
            # would silently undo the re-read: the reader asked for the block,
            # got it, and still saw one page of it.
            "evidence_is_full": bool(full),
        }
    raise ValueError(f"no such finding: {finding_id!r}; run drill() to list them")

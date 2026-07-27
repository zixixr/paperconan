"""Text rendering for the layered views.

Each layer ends by naming the command for the next one. A reviewer reading these
in sequence should never have to guess the syntax, and an agent should be able to
follow the trail without the protocol being restated in its prompt.

Wording stays neutral throughout: these are statistical signals and unexplained
inconsistencies awaiting an author's clarification, never accusations.
"""
from __future__ import annotations

import os
from typing import Any


def _rule(width: int = 78) -> str:
    return "─" * width


def _coverage_lines(coverage: dict[str, Any], indent: str = "") -> list[str]:
    return [f"{indent}! {item}" for item in coverage.get("limitations") or []]


def render_overview(view: dict[str, Any]) -> str:
    cov = view["coverage"]
    out = [
        f"{view['signals_total']} signals across {cov['locations_total']} locations"
        f" in {view.get('files') or '?'} file(s)",
        "",
        f"  {'#':>2}  {'location':<44} {'strongest':>9} {'signals':>8} {'high':>6}",
        f"  {_rule(78)}",
    ]
    for loc in view["locations"]:
        label = loc["location"]
        if len(label) > 44:
            label = "…" + label[-43:]
        out.append(
            f"  {loc['n']:>2}  {label:<44} {loc['strongest']:>9} "
            f"{loc['signals']:>8} {loc['high']:>6}"
        )
        families = loc["families"][:4]
        # say when the list is cut, rather than quietly dropping the tail in the
        # one layer whose job is to declare what it left out
        more = len(loc["families"]) - len(families)
        suffix = f", … {more} more" if more > 0 else ""
        out.append(f"      {', '.join(families)}{suffix}")
    # Branch on the scan, not on this page: with --max-locations 0 the page is
    # empty while the scan is not, and the all-clear text would contradict both
    # the header and the coverage line on the same screen.
    if not cov["locations_total"]:
        out.append("  (no locations carry signal)")
        out.append("")
        out.append("  This means these detectors found nothing at these thresholds in")
        out.append("  the data supplied — not that the paper is free of problems.")
    out.append("")
    out += _coverage_lines(cov)
    if cov.get("limitations"):
        out.append("")
    out.append("next: paperconan drill <scan.json> <#>")
    return "\n".join(out)


def render_drill(view: dict[str, Any]) -> str:
    if "by_kind" in view:
        out = [
            view["location"],
            f"{view['signals']} signals",
            "",
            f"  {'kind':<34} {'n':>5} {'high':>5}  example",
            f"  {_rule(74)}",
        ]
        for group in view["by_kind"]:
            example = group["example"]
            if len(example) > 40:
                example = example[:39] + "…"
            out.append(
                f"  {group['kind']:<34} {group['n']:>5} {group['high']:>5}  {example}"
            )
        out.append("")
        out += _coverage_lines(view.get("coverage") or {})
        if (view.get("coverage") or {}).get("limitations"):
            out.append("")
        out.append("next: paperconan drill <scan.json> <#> --kind <kind>")
        return "\n".join(out)

    out = [
        f"{view['location']} · {view['kind']}",
        f"{view['coverage']['shown']} of {view['coverage']['total']} shown",
        "",
    ]
    for f in view["findings"]:
        out.append(f"  [{f['severity']}] {f['rule'] or f['kind']}")
        out.append(f"      {f['finding_id']}")
    out.append("")
    out += _coverage_lines(view["coverage"])
    if view["coverage"].get("limitations"):
        out.append("")
    out.append("next: paperconan explain <scan.json> <finding_id>")
    return "\n".join(out)


_EVIDENCE_ROW_LIMIT = 20
# Under --full the reader has explicitly asked for the whole block, so the page
# cap would defeat the re-read. Still finite: a terminal is not a spreadsheet.
_FULL_EVIDENCE_ROW_LIMIT = int(os.environ.get(
    "PAPERCONAN_FULL_EVIDENCE_ROW_LIMIT", "500"))


def _render_evidence(ev: dict[str, Any] | None, *, full: bool = False) -> list[str]:
    """Render the evidence window.

    Values are never clipped. This table exists so a reviewer can compare exact
    digits against the paper, and a silently shortened number is worse than a
    missing one — clipping to a fixed width turned -1.2345678e-100 into
    -1.234567, wrong by a hundred orders of magnitude. Columns widen to fit
    instead, and the row/column counts the scan itself trimmed are stated.
    """

    if isinstance(ev, dict) and ev.get("unavailable"):
        # --full could not reach the source. Saying so is the whole point: the
        # alternative is an empty table that reads as a block with nothing in it.
        return [f"  ! full evidence unavailable — {ev['unavailable']}"]

    if not ev or not ev.get("rows"):
        return ["  (no evidence table recorded)"]
    headers = [str(h) for h in (ev.get("headers") or [])]
    hi_cols = {int(c) for c in ev.get("highlight_cols") or []}
    hi_rows = {int(r) for r in ev.get("highlight_rows") or []}
    col_offset = int(ev.get("col_offset") or 0)
    rows = ev["rows"]

    def cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            # repr is Python's shortest round-tripping form: it never loses a
            # digit the value actually has. A %g format silently rounds, which
            # is the same defect as clipping — the reviewer is comparing digits.
            return repr(v)
        return str(v)

    limit = _FULL_EVIDENCE_ROW_LIMIT if full else _EVIDENCE_ROW_LIMIT
    shown = rows[:limit]
    # A row may carry more values than there are headers; widen rather than zip
    # them away, or whole columns vanish with no trace.
    n_cols = max([len(headers)] + [len(r.get("values") or []) for r in shown])
    labels = [headers[i] if i < len(headers) else f"col {col_offset + i + 1}"
              for i in range(n_cols)]

    # Plain loop, not a comprehension: rows may be ragged, and indexing past a
    # short row here is how the widths pass crashed while the render loop below
    # (which has the same guard) was fine.
    widths = []
    for i in range(n_cols):
        width = max(len(labels[i]), 3)
        for row in shown:
            values = row.get("values") or []
            if i < len(values):
                width = max(width, len(cell(values[i])))
        widths.append(width)

    head = "  row │ " + " ".join(
        f"{labels[i]:>{widths[i]}}" + ("*" if col_offset + i in hi_cols else " ")
        for i in range(n_cols)
    )
    out = [head, "  " + _rule(len(head))]
    for row in shown:
        idx = int(row.get("row_idx") or 0)
        mark = "▸" if idx in hi_rows else " "
        values = row.get("values") or []
        cells = " ".join(
            f"{cell(values[i]) if i < len(values) else '':>{widths[i]}} "
            for i in range(n_cols)
        )
        out.append(f"{mark} {idx:>3} │ {cells}")
    if len(rows) > limit:
        out.append(f"  … {len(rows) - limit} more rows in this window"
                   + ("" if full else "; add --full to read the block"))
    cut = ev.get("truncated")
    if cut:
        # The scan clipped the window before it ever reached here, so the table
        # above is not the whole block. Naming the scale matters: a reader told
        # only "trimmed" cannot tell 12-of-13 from 12-of-5000, and will read the
        # window as representative either way.
        if isinstance(cut, dict):
            where = (f"{cut.get('rows_shown')}x{cut.get('cols_shown')} of a "
                     f"{cut.get('rows_total')}x{cut.get('cols_total')} block")
            if cut.get("by") == "full_budget":
                # Saying "trimmed by the scan" here, and suggesting --full, sent
                # a reader who had just run --full back to run it again for the
                # identical window.
                out.append(f"  ! this window is {where}, bounded by --full's cell "
                           f"budget. Raise PAPERCONAN_MAX_FULL_EVIDENCE_CELLS to "
                           f"widen it.")
            else:
                out.append(f"  ! this window is {where}, trimmed by the scan. "
                           f"Re-run explain with --full to read it from the "
                           f"source data.")
        else:
            out.append("  ! this evidence window was trimmed by the scan; it does not "
                       "show the full block")
    out.append("  (* highlighted column, ▸ highlighted row)")
    return out


def render_explain(view: dict[str, Any]) -> str:
    sev = view["severity"]
    out = [
        view["location"],
        f"{view['kind']} · {view['finding_id']}",
        "",
        f"  rule:      {view['rule']}",
        f"  severity:  detector={sev['raw']}  displayed={sev['effective']}"
        f"  ({sev['profile_action']})",
    ]
    if sev["raw"] != sev["effective"]:
        # A demotion is a judgement the reviewer may want to overturn, so the
        # reason has to travel with it rather than being implied by the number.
        out.append("  down-weighted because:")
        for reason in sev["context"] or ["(no reason recorded)"]:
            out.append(f"      · {reason}")
        if sev.get("likely_benign"):
            out.append(f"      · {sev['likely_benign']}")
    all_params = view.get("parameters") or {}
    params = {k: v for k, v in all_params.items() if not isinstance(v, (list, dict))}
    if params:
        out.append("  parameters:")
        for k, v in sorted(params.items()):
            out.append(f"      {k} = {v}")
    # Sample values and prefilter flags live in list/dict parameters — exactly
    # what a reviewer checks against the paper. Naming them beats dropping them.
    structured = sorted(set(all_params) - set(params))
    if structured:
        out.append(f"      … {len(structured)} structured parameter(s) not shown "
                   f"({', '.join(structured)}); use --json")
    out.append("")
    out.append("  evidence:")
    out += _render_evidence(view.get("evidence"),
                            full=bool(view.get("evidence_is_full")))
    out.append("")
    out.append("This is a statistical signal, not a conclusion. Confirm against the "
               "original records,")
    out.append("figure legends and Methods before drawing any inference.")
    return "\n".join(out)

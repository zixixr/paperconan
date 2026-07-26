"""Text rendering for the layered views.

Each layer ends by naming the command for the next one. A reviewer reading these
in sequence should never have to guess the syntax, and an agent should be able to
follow the trail without the protocol being restated in its prompt.

Wording stays neutral throughout: these are statistical signals and unexplained
inconsistencies awaiting an author's clarification, never accusations.
"""
from __future__ import annotations

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
    if not view["locations"]:
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


def _render_evidence(ev: dict[str, Any] | None) -> list[str]:
    if not ev or not ev.get("rows"):
        return ["  (no evidence table recorded)"]
    headers = ev.get("headers") or []
    hi_cols = {int(c) for c in ev.get("highlight_cols") or []}
    hi_rows = {int(r) for r in ev.get("highlight_rows") or []}
    col_offset = int(ev.get("col_offset") or 0)

    def cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:.8g}"
        return str(v)

    widths = [max(9, len(str(h))) for h in headers]
    head = "  row │ " + " ".join(
        f"{str(h)[:w]:>{w}}" + ("*" if col_offset + i in hi_cols else " ")
        for i, (h, w) in enumerate(zip(headers, widths))
    )
    out = [head, "  " + _rule(len(head))]
    for row in ev["rows"][:20]:
        idx = int(row.get("row_idx") or 0)
        mark = "▸" if idx in hi_rows else " "
        cells = " ".join(
            f"{cell(v)[:w]:>{w}} " for v, w in zip(row.get("values") or [], widths)
        )
        out.append(f"{mark} {idx:>3} │ {cells}")
    if len(ev["rows"]) > 20:
        out.append(f"  … {len(ev['rows']) - 20} more rows")
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
    out += _render_evidence(view.get("evidence"))
    out.append("")
    out.append("This is a statistical signal, not a conclusion. Confirm against the "
               "original records,")
    out.append("figure legends and Methods before drawing any inference.")
    return "\n".join(out)

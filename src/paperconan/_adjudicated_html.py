"""HTML renderer for adjudicated PaperConan reports.

This is separate from ``_html.write_html_report`` on purpose:

- ``report.html`` is the deterministic detector/evidence browser.
- adjudicated reports combine a human/AI verdict with scan evidence.

The renderer is local-first and infrastructure-agnostic: no DB, Blob, cloud
worker, DOI claiming, or private batch assumptions live here.
"""
from __future__ import annotations

import html
import os
import re
from typing import Any

from ._html import _all_findings, _esc, _render_cross_sheet_examples, _render_evidence_table


_SECTION_TITLES = (
    "论文主结论",
    "异常位置",
    "标签含义",
    "为什么这是问题",
    "影响判断",
    "无辜解释的层次",
    "需要作者澄清",
    "证据",
)


def _inline_md(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def _render_md(md: str | None) -> str:
    """Render the small Markdown subset used by paperconan report_md."""
    if not md:
        return ""
    out: list[str] = []
    para: list[str] = []
    section_open = [False]
    lines = md.replace("\r\n", "\n").split("\n")
    i = 0

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline_md(" ".join(para).strip()) + "</p>")
            para.clear()

    def close_section() -> None:
        if section_open[0]:
            out.append("</section>")
            section_open[0] = False

    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if not s:
            flush_para()
            i += 1
            continue
        if s.startswith("### "):
            flush_para()
            close_section()
            title = s[4:].strip()
            sec_id = ""
            if title in _SECTION_TITLES:
                sec_id = f' id="sec-{_SECTION_TITLES.index(title) + 1}"'
            out.append(f'<section class="report-section"{sec_id}>'
                       f'<h2>{_inline_md(title)}</h2>')
            section_open[0] = True
        elif s.startswith("## "):
            flush_para()
            close_section()
            out.append(f'<h1 class="report-title">{_inline_md(s[3:].strip())}</h1>')
        elif re.match(r"[-*]\s+", s):
            flush_para()
            items = []
            while i < len(lines) and re.match(r"[-*]\s+", lines[i].strip()):
                item = re.sub(r"^[-*]\s+", "", lines[i].strip())
                items.append(f"<li>{_inline_md(item)}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        else:
            para.append(s)
        i += 1
    flush_para()
    close_section()
    return "\n".join(out)


def _verdict_badge(verdict: dict[str, Any]) -> str:
    v = str(verdict.get("verdict") or "NEEDS_HUMAN").upper()
    tier = verdict.get("suspicion_tier")
    impact = verdict.get("impact_scope")
    review = verdict.get("review_status") or "unreviewed"
    bits = [f'<span class="badge verdict">{_esc(v)}</span>']
    if tier:
        bits.append(f'<span class="badge tier">Tier {_esc(tier)}</span>')
    if impact:
        bits.append(f'<span class="badge impact">{_esc(impact)}</span>')
    bits.append(f'<span class="badge review">{_esc(review)}</span>')
    return "".join(bits)


def _finding_score(item: dict[str, Any]) -> tuple[int, int]:
    f = item["finding"]
    sev = str(f.get("severity") or "").lower()
    sev_rank = {"high": 0, "medium": 1, "low": 2}.get(sev, 3)
    scope_rank = 0 if item["scope"] == "cross_sheet" else 1
    return (sev_rank, scope_rank)


def _finding_matches_ref(item: dict[str, Any], ref: dict[str, Any]) -> bool:
    """Whether a finding item satisfies a verdict finding_ref selector.

    A ref may specify any subset of file/sheet/rows/kind/rule; every field it
    specifies must match. An empty ref matches nothing (so it never selects all).
    """
    f = item["finding"]
    checks = []
    if ref.get("file"):
        checks.append(str(ref["file"]) in str(item["file"]))
    if ref.get("sheet"):
        checks.append(str(ref["sheet"]) == str(item["sheet"]))
    if ref.get("rows"):
        checks.append(str(ref["rows"]) == str(item["block_rows"]))
    if ref.get("kind"):
        checks.append(str(ref["kind"]) == str(f.get("kind")))
    if ref.get("rule"):
        checks.append(str(ref["rule"]) in str(f.get("rule") or ""))
    return bool(checks) and all(checks)


def _render_key_finding(item: dict[str, Any], idx: int) -> str:
    f = item["finding"]
    kind = f.get("kind", "?")
    sev = str(f.get("severity") or "low").lower()
    rule = f.get("rule") or ""
    loc = f'{item["file"]} :: {item["sheet"]}'
    if item["scope"] == "block":
        loc += f' · rows {item["block_rows"]}'
        evidence = _render_evidence_table(f.get("evidence"))
    else:
        evidence = _render_cross_sheet_examples(f) or '<p class="no-evidence">no evidence table</p>'
    meta = []
    if f.get("n") is not None:
        meta.append(f'n={f.get("n")}')
    if f.get("profile_action"):
        meta.append(f'profile={f.get("profile_action")}')
    meta_html = " · ".join(_esc(x) for x in meta)
    parts = [
        f'<article class="finding-card" id="finding-{idx}">',
        "<header>",
        f'<span class="sev sev-{_esc(sev)}">{_esc(sev)}</span>',
        f'<span class="kind">{_esc(kind)}</span>',
        f'<span class="loc">{_esc(loc)}</span>',
        "</header>",
        f'<p class="rule"><code>{_esc(rule)}</code></p>',
    ]
    if meta_html:
        parts.append(f'<p class="meta">{meta_html}</p>')
    parts.extend([evidence, "</article>"])
    return "".join(parts)


def _scan_title(scan: dict[str, Any], verdict: dict[str, Any]) -> str:
    paper = scan.get("paper") or {}
    return (
        verdict.get("title")
        or paper.get("title")
        or paper.get("doi")
        or os.path.basename(os.path.normpath(scan.get("input_dir") or "paperconan audit"))
        or "paperconan audit"
    )


_CSS = """
:root {
  --bg:#f6f7f9; --paper:#ffffff; --ink:#20242b; --muted:#667085;
  --line:#d8dee7; --panel:#eef2f7; --accent:#3457d5;
  --t1:#b42318; --t2:#b54708; --t3:#475467; --ok:#067647;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,
       "PingFang SC","Microsoft YaHei",sans-serif; }
code, table.ev td, table.ev th { font-family:"SF Mono",Menlo,Consolas,monospace; }
.page { max-width:1180px; margin:0 auto; padding:28px 22px 48px; }
.hero { background:var(--paper); border:1px solid var(--line); border-radius:8px;
  padding:24px 28px; box-shadow:0 12px 30px rgba(16,24,40,.06); }
.eyebrow { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
h1 { margin:8px 0 14px; font-size:28px; line-height:1.2; letter-spacing:0; }
.badges { display:flex; gap:8px; flex-wrap:wrap; }
.badge { border:1px solid var(--line); border-radius:999px; padding:4px 10px;
  background:var(--panel); font-size:12px; font-weight:650; }
.badge.verdict { color:var(--ok); background:#ecfdf3; border-color:#abefc6; }
.badge.tier { color:var(--t1); background:#fef3f2; border-color:#fecdca; }
.badge.impact { color:var(--accent); background:#eef4ff; border-color:#c7d7fe; }
.badge.review { color:#344054; }
.notice { margin-top:16px; padding:10px 12px; border-left:4px solid var(--accent);
  background:#f5f8ff; color:#344054; border-radius:4px; }
.grid { display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:18px; margin-top:18px; }
.panel { background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:20px;
  box-shadow:0 8px 24px rgba(16,24,40,.04); }
.report-title { font-size:20px; margin:0 0 12px; }
.report-section { border-top:1px solid var(--line); padding-top:16px; margin-top:16px; }
.report-section h2 { font-size:16px; margin:0 0 8px; }
.report-section p { margin:8px 0; }
.side h2, .evidence h2 { font-size:16px; margin:0 0 12px; }
.kv { display:grid; grid-template-columns:120px 1fr; gap:8px; font-size:13px; }
.kv div:nth-child(odd) { color:var(--muted); }
.finding-card { border:1px solid var(--line); border-radius:8px; overflow:hidden; margin:12px 0;
  background:#fff; }
.finding-card header { display:flex; gap:8px; align-items:center; flex-wrap:wrap;
  padding:10px 12px; background:var(--panel); border-bottom:1px solid var(--line); }
.sev, .kind { border-radius:999px; padding:2px 8px; font-size:11px; font-weight:700; }
.sev-high { background:#fef3f2; color:var(--t1); }
.sev-medium { background:#fffaeb; color:var(--t2); }
.sev-low { background:#f2f4f7; color:var(--t3); }
.kind { background:#fff; border:1px solid var(--line); }
.loc { color:var(--muted); font-size:12px; }
.rule, .meta { margin:10px 12px; color:var(--muted); }
.ev-wrap { margin:10px 12px 14px; overflow:auto; border:1px solid var(--line); border-radius:6px; }
table.ev { width:100%; border-collapse:collapse; font-size:12px; }
table.ev th, table.ev td { border-bottom:1px solid var(--line); padding:5px 8px; white-space:nowrap; }
table.ev th { background:#f8fafc; color:var(--muted); text-align:left; }
.hi-col { background:#fff6d6; }
.hi-row { background:#fffaf0; }
.no-evidence { color:var(--muted); padding:0 12px; }
.scope-note { margin:0 0 12px; padding:8px 12px; background:var(--panel); border-radius:6px;
  color:#344054; font-size:13px; }
footer { margin-top:20px; color:var(--muted); font-size:12px; }
.finding-block { background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:20px;
  box-shadow:0 8px 24px rgba(16,24,40,.04); margin-top:18px; }
.fb-head { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;
  border-bottom:1px solid var(--line); padding-bottom:12px; margin-bottom:12px; }
.fb-head h2 { font-size:17px; margin:0; }
table.findings-index { width:100%; border-collapse:collapse; font-size:13px; margin-top:8px; }
table.findings-index th, table.findings-index td { border-bottom:1px solid var(--line); padding:6px 10px; text-align:left; }
table.findings-index th { color:var(--muted); font-weight:600; }
@media (max-width: 900px) { .grid { grid-template-columns:1fr; } .page { padding:18px 12px 32px; } }
"""


def _page(title: str, badges_html: str, main_html: str) -> str:
    """Wrap hero + body in the shared self-contained HTML document."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PaperConan adjudicated report · {_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
  <section class="hero">
    <div class="eyebrow">PaperConan adjudicated report</div>
    <h1>{_esc(title)}</h1>
    <div class="badges">{badges_html}</div>
    <div class="notice">This page combines a human/AI judgment with deterministic PaperConan scan evidence. Statistical signal, not verdict: it is not a finding of author intent or research misconduct.</div>
  </section>
  {main_html}
  <footer>Generated by paperconan. Keep original source tables and scan.json with this report so every claim remains reproducible.</footer>
</div>
</body>
</html>
"""


def _top_tier(findings: list[dict[str, Any]]) -> int | None:
    """Highest severity (numerically smallest) tier across findings, or None."""
    tiers = [f.get("suspicion_tier") for f in findings if isinstance(f.get("suspicion_tier"), int)]
    return min(tiers) if tiers else None


def _paper_badges(verdict: dict[str, Any], top_tier: int | None) -> str:
    v = str(verdict.get("verdict") or "NEEDS_HUMAN").upper()
    bits = [f'<span class="badge verdict">{_esc(v)}</span>']
    if top_tier:
        bits.append(f'<span class="badge tier">Tier {_esc(top_tier)}</span>')
    impact = verdict.get("overall_impact")
    if impact:
        bits.append(f'<span class="badge impact">{_esc(impact)}</span>')
    return "".join(bits)


def _match_finding(scan_findings: list[dict[str, Any]], ref: dict[str, Any]) -> dict[str, Any] | None:
    return next((it for it in scan_findings if _finding_matches_ref(it, ref)), None)


def _render_findings_index(findings: list[dict[str, Any]], scan_findings: list[dict[str, Any]]) -> str:
    rows = []
    for i, f in enumerate(findings, 1):
        ref = f.get("finding_ref") or {}
        matched = _match_finding(scan_findings, ref)
        sheet = matched["sheet"] if matched else (ref.get("sheet") or ref.get("file") or "—")
        rng = matched["block_rows"] if matched else (ref.get("rows") or "")
        loc = f"{sheet} {rng}".strip()
        detector = ref.get("kind") or (matched["finding"].get("kind") if matched else "—")
        tier = f.get("suspicion_tier")
        tier_txt = f"T{tier}" if tier else "—"
        status = f.get("review_status") or "unreviewed"
        rows.append(
            f"<tr><td>{i}</td><td>{_esc(loc)}</td><td>{_esc(detector)}</td>"
            f"<td>{_esc(tier_txt)}</td><td>{_esc(status)}</td></tr>"
        )
    return (
        '<table class="findings-index"><thead><tr>'
        "<th>#</th><th>位置</th><th>detector</th><th>tier</th><th>status</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _render_finding_block(scan_findings: list[dict[str, Any]], finding: dict[str, Any], idx: int) -> str:
    ref = finding.get("finding_ref") or {}
    matched = _match_finding(scan_findings, ref)
    tier = finding.get("suspicion_tier")
    status = finding.get("review_status") or "unreviewed"
    impact = finding.get("impact_scope")
    badges = []
    if tier:
        badges.append(f'<span class="badge tier">Tier {_esc(tier)}</span>')
    if impact:
        badges.append(f'<span class="badge impact">{_esc(impact)}</span>')
    badges.append(f'<span class="badge review">{_esc(status)}</span>')
    title = finding.get("title") or f"发现 {idx + 1}"
    body = _render_md(finding.get("report_md"))
    if matched is not None:
        evidence = _render_key_finding(matched, idx)
    else:
        evidence = '<p class="no-evidence">无匹配证据（finding_ref 未命中扫描结果）</p>'
    return (
        '<section class="finding-block">'
        f'<header class="fb-head"><h2>发现 {idx + 1} · {_esc(title)}</h2>'
        f'<div class="badges">{"".join(badges)}</div></header>'
        f"{body}{evidence}</section>"
    )


def _render_single(scan: dict[str, Any], verdict: dict[str, Any], title: str,
                   findings: list[dict[str, Any]]) -> str:
    """Legacy single-verdict layout: report_md + scoped/top key-evidence panel."""
    # If the verdict names which finding(s) it adjudicated, scope the evidence
    # panel to exactly those and demote the rest, so the report no longer reads
    # as if every top scan signal were part of the verdict.
    refs = verdict.get("finding_refs") or []
    focused = [item for item in findings if any(_finding_matches_ref(item, r) for r in refs)]
    scope_note = ""
    if focused:
        key_findings = focused
        others = len(findings) - len(focused)
        note = f"本次判定聚焦 {len(focused)} 条 finding"
        note += f"；此外扫描还发现 {others} 条信号，未纳入本次判定范围。" if others else "。"
        scope_note = f'<p class="scope-note">{note}</p>'
    else:
        key_findings = findings[:8]
    report_html = _render_md(verdict.get("report_md")) or (
        "<p>No formal report_md was supplied. Use this page as an evidence "
        "wrapper around the verdict metadata and scan findings.</p>"
    )
    evidence_html = scope_note + "".join(
        _render_key_finding(item, i) for i, item in enumerate(key_findings)
    )
    if not key_findings:
        evidence_html = '<p class="no-evidence">No scan findings were available.</p>'

    kv = {
        "verdict": verdict.get("verdict"),
        "tier_why": verdict.get("tier_why"),
        "innocent_explanation": verdict.get("innocent_explanation"),
        "needs_author_data": verdict.get("needs_author_data"),
        "tool_version": scan.get("tool_version"),
        "profile": scan.get("profile"),
    }
    kv_html = "".join(
        f"<div>{_esc(k)}</div><div>{_esc(v)}</div>"
        for k, v in kv.items()
        if v not in (None, "")
    )
    main_html = f"""<div class="grid">
    <main class="panel report">{report_html}</main>
    <aside class="panel side">
      <h2>判定摘要</h2>
      <div class="kv">{kv_html}</div>
    </aside>
  </div>
  <section class="panel evidence" style="margin-top:18px">
    <h2>关键证据</h2>
    {evidence_html}
  </section>"""
    return _page(title, _verdict_badge(verdict), main_html)


def _render_multi(scan: dict[str, Any], verdict: dict[str, Any], title: str,
                  scan_findings: list[dict[str, Any]], findings: list[dict[str, Any]]) -> str:
    """Multi-finding layout: paper header + findings index + per-finding blocks."""
    conclusion = _render_md(verdict.get("paper_conclusion")) or "<p>—</p>"
    index = _render_findings_index(findings, scan_findings)
    blocks = "".join(_render_finding_block(scan_findings, f, i) for i, f in enumerate(findings))
    note = _render_md(verdict.get("review_note")) if verdict.get("review_note") else ""
    kv = {"tool_version": scan.get("tool_version"), "profile": scan.get("profile")}
    kv_html = "".join(
        f"<div>{_esc(k)}</div><div>{_esc(v)}</div>"
        for k, v in kv.items()
        if v not in (None, "")
    )
    main_html = f"""<section class="panel">
    <h2>论文主结论</h2>
    {conclusion}
    <h2 style="margin-top:16px">发现清单</h2>
    {index}
  </section>
  {blocks}
  <section class="panel" style="margin-top:18px">
    <h2>方法与背景</h2>
    {note}<div class="kv">{kv_html}</div>
  </section>"""
    return _page(title, _paper_badges(verdict, _top_tier(findings)), main_html)


def render_adjudicated_report(scan: dict[str, Any], verdict: dict[str, Any]) -> str:
    """Return a self-contained HTML page for a judged PaperConan scan.

    Two shapes are supported: a legacy single verdict (``report_md`` +
    optional ``finding_refs``), and a multi-finding verdict carrying a
    ``findings`` array rendered as one self-contained block per finding.
    """
    title = _scan_title(scan, verdict)
    # Mirror the deterministic report: findings the active profile suppressed as
    # likely false positives must not resurface as key evidence here.
    visible = [
        item for item in _all_findings(scan)
        if str(item["finding"].get("profile_action") or "").lower() != "hidden"
    ]
    findings = sorted(visible, key=_finding_score)
    if verdict.get("findings"):
        return _render_multi(scan, verdict, title, findings, verdict["findings"])
    return _render_single(scan, verdict, title, findings)


def write_adjudicated_report(scan: dict[str, Any], verdict: dict[str, Any], out_path: str) -> None:
    """Write an adjudicated PaperConan HTML report."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render_adjudicated_report(scan, verdict))

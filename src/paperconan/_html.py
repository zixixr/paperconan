"""HTML report renderer for paperconan scan results.

Renders a single self-contained HTML file (inline CSS + vanilla JS, no external
deps, no CDN) showing every finding with its evidence table. Designed to be
emailed, attached to a PubPeer post, or saved alongside scan.json.
"""
from __future__ import annotations

import html
import os
from typing import Any, Iterable


# ---------- value formatting ----------

def _fmt_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return ""
        if abs(v) < 1e-12:
            return "0"
        if v == int(v) and abs(v) < 1e15:
            return f"{int(v)}"
        return f"{v:.8g}"
    return html.escape(str(v))


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


# ---------- finding extraction ----------

_PER_BLOCK_GROUPS = ("relations", "progressions", "equal_pairs",
                     "within_col", "identical_after_rounding", "grim")


def _iter_block_findings(scan: dict) -> Iterable[tuple[dict, dict]]:
    for blk in scan.get("relations_blocks", []) or []:
        for group in _PER_BLOCK_GROUPS:
            for f in blk.get(group, []) or []:
                yield blk, f


def _all_findings(scan: dict) -> list[dict]:
    out = []
    for blk, f in _iter_block_findings(scan):
        out.append({
            "scope": "block",
            "file": blk["file"],
            "sheet": blk["sheet"],
            "block_rows": blk["block"]["rows"],
            "block_cols": blk["block"]["cols"],
            "header": blk["block"].get("header") or [],
            "finding": f,
        })
    for cf in scan.get("cross_sheet_findings", []) or []:
        out.append({
            "scope": "cross_sheet",
            "file": cf.get("file", ""),
            "sheet": f"{cf.get('sheet_a', '?')} ↔ {cf.get('sheet_b', '?')}",
            "block_rows": "—",
            "block_cols": "—",
            "header": [],
            "finding": cf,
        })
    return out


def _severity_counts(findings: list[dict]) -> dict[str, int]:
    c = {"high": 0, "medium": 0, "low": 0}
    for item in findings:
        if item["finding"].get("profile_action") == "hidden":
            continue
        sev = (item["finding"].get("severity") or "low").lower()
        c[sev] = c.get(sev, 0) + 1
    return c


# ---------- evidence table rendering ----------

def _render_evidence_table(ev: dict | None) -> str:
    if not ev or not ev.get("rows"):
        return '<p class="no-evidence">no evidence table</p>'
    headers = ev.get("headers") or []
    col_offset = int(ev.get("col_offset") or 0)
    hi_cols = set(int(c) for c in ev.get("highlight_cols") or [])
    hi_rows = set(int(r) for r in ev.get("highlight_rows") or [])

    # Header row: empty corner, then each header cell. Highlight matching columns.
    head_cells = ['<th class="row-label">row</th>']
    for i, h in enumerate(headers):
        abs_col = col_offset + i
        cls = "hi-col" if abs_col in hi_cols else ""
        label = _esc(h) if h not in (None, "") else f"<span class='muted'>col {abs_col + 1}</span>"
        head_cells.append(f'<th class="{cls}">{label}</th>')

    body_rows = []
    for row in ev["rows"]:
        row_idx = int(row.get("row_idx") or 0)
        is_ctx = bool(row.get("is_context"))
        is_hi_row = row_idx in hi_rows
        tr_cls_parts = []
        if is_ctx:
            tr_cls_parts.append("ctx")
        if is_hi_row:
            tr_cls_parts.append("hi-row")
        tr_cls = " ".join(tr_cls_parts)
        cells = [f'<td class="row-label">{row_idx}</td>']
        for i, v in enumerate(row.get("values") or []):
            abs_col = col_offset + i
            cls = "hi-col" if abs_col in hi_cols else ""
            cells.append(f'<td class="{cls}">{_fmt_cell(v)}</td>')
        body_rows.append(f'<tr class="{tr_cls}">{"".join(cells)}</tr>')

    return (
        '<div class="ev-wrap"><table class="ev">'
        f'<thead><tr>{"".join(head_cells)}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def _render_cross_sheet_examples(cf: dict) -> str:
    examples = cf.get("examples") or []
    if not examples:
        return ""
    if examples and isinstance(examples[0], dict):
        rows = []
        for ex in examples[:10]:
            rows.append(
                f'<tr><td>{_esc(ex.get("row"))}</td>'
                f'<td>{_esc(ex.get("col"))}</td>'
                f'<td>{_fmt_cell(ex.get("value"))}</td></tr>'
            )
        return (
            '<div class="ev-wrap"><table class="ev">'
            '<thead><tr><th>row</th><th>col</th><th>value</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
        )
    # value-overlap form: list of floats
    chips = "".join(f'<span class="val-chip">{_fmt_cell(v)}</span>' for v in examples[:12])
    return f'<div class="shared-values"><span class="muted">shared values: </span>{chips}</div>'


# ---------- per-section rendering ----------

def _render_finding_card(item: dict) -> str:
    f = item["finding"]
    sev = (f.get("severity") or "low").lower()
    kind = f.get("kind", "?")
    rule = f.get("rule", "")
    n = f.get("n", f.get("n_cells", ""))
    file_ = item["file"]
    sheet = item["sheet"]
    block_rows = item["block_rows"]
    profile_action = (f.get("profile_action") or "kept").lower()

    if item["scope"] == "cross_sheet":
        evidence_html = _render_cross_sheet_examples(f)
        loc = f"{_esc(file_)} :: {_esc(sheet)}"
        extra_meta = ""
        if f.get("same_position_count") is not None:
            extra_meta = (
                f' · same-pos={_esc(f.get("same_position_count"))}'
                f'/{_esc(min(f.get("size_a", 0), f.get("size_b", 0)))}'
            )
    else:
        evidence_html = _render_evidence_table(f.get("evidence"))
        loc = f"{_esc(file_)} :: {_esc(sheet)} · rows {_esc(block_rows)}"
        extra_meta = f" · n={_esc(n)}" if n != "" else ""

    searchable = " ".join([
        str(file_), str(sheet), str(kind), str(rule),
    ]).lower()

    benign = f.get("likely_benign")
    benign_html = (f'<p class="benign">↳ likely benign: {_esc(benign)}</p>'
                   if benign else "")
    contexts = f.get("false_positive_context") or []
    ctx_html = ""
    if contexts:
        chips = "".join(f'<span class="ctx-chip">{_esc(c)}</span>' for c in contexts)
        ctx_html = f'<div class="profile-context">profile: {_esc(profile_action)} {chips}</div>'

    open_attr = " open" if sev == "high" else ""
    hidden_style = ' style="display:none"' if profile_action == "hidden" else ""
    return (
        f'<details class="finding" data-severity="{sev}" data-kind="{_esc(kind)}" '
        f'data-file="{_esc(file_)}" data-profile-action="{_esc(profile_action)}" '
        f'data-searchable="{_esc(searchable)}"{open_attr}{hidden_style}>'
        '<summary>'
        f'<span class="badge sev-{sev}">{sev}</span>'
        f'<span class="badge kind">{_esc(kind)}</span>'
        f'<span class="loc">{loc}{extra_meta}</span>'
        '</summary>'
        f'<p class="rule"><code>{_esc(rule)}</code></p>'
        f'{ctx_html}'
        f'{benign_html}'
        f'{evidence_html}'
        '</details>'
    )


def _render_filter_sidebar(findings: list[dict]) -> str:
    kinds = sorted({item["finding"].get("kind", "?") for item in findings})
    files = sorted({item["file"] for item in findings if item["file"]})

    def cb(cls: str, value: str, label: str) -> str:
        return (
            f'<label><input type="checkbox" class="{cls}" value="{_esc(value)}" checked>'
            f' {_esc(label)}</label>'
        )

    sev_box = "".join(cb("f-sev", s, s) for s in ("high", "medium", "low"))
    kind_box = "".join(cb("f-kind", k, k) for k in kinds) or '<span class="muted">none</span>'
    file_box = "".join(cb("f-file", f, f) for f in files) or '<span class="muted">none</span>'

    return (
        '<aside class="filters">'
        '<input type="search" id="filter-search" placeholder="search file / sheet / rule…">'
        '<label class="show-noisy"><input type="checkbox" id="show-noisy"> show noisy / hidden findings</label>'
        f'<fieldset><legend>severity</legend>{sev_box}</fieldset>'
        f'<fieldset><legend>detector</legend>{kind_box}</fieldset>'
        f'<fieldset><legend>file</legend>{file_box}</fieldset>'
        '<button type="button" id="reset-filters">reset</button>'
        '</aside>'
    )


def _render_digit_section(scan: dict) -> str:
    def _sig(d):  # prefer BH-FDR flag; fall back to raw p for pre-FDR scan.json
        return d["fdr_significant"] if "fdr_significant" in d else d.get("p", 1) < 1e-6
    items = sorted(
        [d for d in scan.get("digit_distribution") or [] if _sig(d)],
        key=lambda d: d.get("p_adj", d.get("p", 1)),
    )
    if not items:
        return ""
    cards = []
    for d in items:
        counts = d.get("counts") or {}
        # Sum over digits 1..9 (skip 0 which often dominates artificially).
        values = [int(counts.get(str(k), 0)) for k in range(0, 10)]
        max_v = max(values[1:]) or 1
        bars = []
        avg = (sum(values[1:]) / 9) if sum(values[1:]) > 0 else 0
        for digit in range(0, 10):
            v = values[digit]
            pct = (v / max_v * 100) if max_v else 0
            cls = "bar"
            if digit != 0 and v > avg * 1.6 and avg > 0:
                cls += " over"
            bars.append(
                f'<div class="bar-row"><span class="bar-label">{digit}</span>'
                f'<div class="{cls}" style="width:{pct:.1f}%"></div>'
                f'<span class="bar-val">{v}</span></div>'
            )
        top = ", ".join(f"{k}×{v}" for k, v in (d.get("top") or [])[:5])
        cards.append(
            '<div class="dig-card">'
            f'<header><span class="badge sev-medium">χ²</span> '
            f'<span class="loc">{_esc(d.get("label"))}</span> · '
            f'n={_esc(d.get("n"))} · χ²={float(d.get("chi2", 0)):.1f} · '
            f'p={float(d.get("p", 1)):.1e}'
            + (f' · q={float(d["p_adj"]):.1e}' if "p_adj" in d else "")
            + '</header>'
            f'<div class="bars">{"".join(bars)}</div>'
            f'<p class="meta">top: {_esc(top)}</p>'
            '</div>'
        )
    return (
        f'<section id="sec-digit" class="section">'
        f'<h2>Last-digit χ² anomalies ({len(items)} sheets, BH-FDR q ≤ 0.05)</h2>'
        f'<p class="hint">真实测量末位数字应近似均匀分布；偏离表示数字可能是人工构造。</p>'
        f'{"".join(cards)}</section>'
    )


def _render_decimal_section(scan: dict) -> str:
    items = [d for d in scan.get("decimal_endings") or [] if d.get("top")]
    if not items:
        return ""
    rows = []
    for d in items[:30]:
        top = ", ".join(f".{e}×{c}" for e, c in (d.get("top") or [])[:6])
        rows.append(
            f'<tr><td class="loc">{_esc(d.get("label"))}</td>'
            f'<td>{_esc(d.get("n"))}</td>'
            f'<td>{_esc(d.get("n_unique"))}</td>'
            f'<td class="ends">{_esc(top)}</td></tr>'
        )
    return (
        f'<section id="sec-decimal" class="section">'
        f'<h2>Over-represented two-decimal endings ({len(items)} sheets)</h2>'
        f'<p class="hint">某些末两位异常频繁出现，可能是编造数字的指纹。</p>'
        f'<div class="ev-wrap"><table class="ev meta-table">'
        '<thead><tr><th>sheet</th><th>n</th><th>unique endings</th>'
        '<th>top endings</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '</section>'
    )


# ---------- top-level template ----------

_CSS = """
:root {
  --bg:#101317; --panel:#181c22; --panel-2:#1f242c; --border:#2a3038;
  --text:#e6ebf2; --muted:#8a93a0; --accent:#60a5fa;
  --high:#dc2626; --medium:#d97706; --low:#64748b; --hi-cell:#facc15;
}
* { box-sizing:border-box; }
html, body { background:var(--bg); color:var(--text); margin:0;
  font:14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
       "PingFang SC", "Microsoft YaHei", sans-serif;
}
code, .ev td, .ev th { font-family: "SF Mono", Menlo, Consolas, monospace; font-size:12.5px; }
header.top { padding:18px 24px; border-bottom:1px solid var(--border); background:var(--panel); }
.brand { font-weight:700; font-size:18px; letter-spacing:.5px; }
.brand-sub { color:var(--muted); font-weight:400; margin-left:10px; font-size:12.5px; }
.stats { margin-top:8px; display:flex; gap:14px; flex-wrap:wrap; }
.stat { padding:4px 10px; background:var(--panel-2); border:1px solid var(--border);
  border-radius:6px; color:var(--muted); font-size:12.5px; }
.stat strong { color:var(--text); margin-right:4px; }
.stat.sev-high strong { color:var(--high); }
.stat.sev-medium strong { color:var(--medium); }
.stat.sev-low strong { color:var(--low); }
.warn { margin-top:10px; font-size:12px; color:var(--muted); }
.warn::before { content:"⚠ "; color:var(--medium); }
.layout { display:grid; grid-template-columns:240px 1fr; min-height:calc(100vh - 110px); }
aside.filters { border-right:1px solid var(--border); padding:16px;
  background:var(--panel); position:sticky; top:0; align-self:start;
  max-height:100vh; overflow:auto; }
aside fieldset { border:1px solid var(--border); border-radius:6px; padding:8px 10px;
  margin:12px 0; background:var(--panel-2); }
aside legend { color:var(--muted); padding:0 6px; font-size:11.5px; text-transform:uppercase;
  letter-spacing:.5px; }
aside label { display:block; padding:2px 0; color:var(--text); cursor:pointer; font-size:12.5px;
  word-break:break-all; }
aside label input { margin-right:6px; }
#filter-search { width:100%; padding:6px 8px; background:var(--panel-2);
  border:1px solid var(--border); color:var(--text); border-radius:4px; }
#reset-filters { width:100%; padding:6px; background:var(--panel-2); color:var(--text);
  border:1px solid var(--border); border-radius:4px; cursor:pointer; }
#reset-filters:hover { background:var(--border); }
main { padding:18px 26px 40px; min-width:0; }
.section { margin:0 0 28px; }
.section h2 { font-size:15px; margin:0 0 4px; color:var(--text);
  border-bottom:1px solid var(--border); padding-bottom:6px; }
.section .hint { color:var(--muted); font-size:12px; margin:4px 0 14px; }
details.finding { background:var(--panel); border:1px solid var(--border); border-radius:6px;
  margin:8px 0; padding:0; overflow:hidden; }
details.finding[data-severity="high"] { border-left:3px solid var(--high); }
details.finding[data-severity="medium"] { border-left:3px solid var(--medium); }
details.finding[data-severity="low"] { border-left:3px solid var(--low); }
details.finding summary { padding:10px 14px; cursor:pointer; list-style:none;
  display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
details.finding summary::-webkit-details-marker { display:none; }
details.finding > p, details.finding > .ev-wrap, details.finding > .shared-values { margin:0 14px 14px; }
.badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px;
  font-weight:600; letter-spacing:.3px; text-transform:uppercase; }
.badge.sev-high { background:rgba(220,38,38,.15); color:var(--high); border:1px solid rgba(220,38,38,.4); }
.badge.sev-medium { background:rgba(217,119,6,.15); color:var(--medium); border:1px solid rgba(217,119,6,.4); }
.badge.sev-low { background:rgba(100,116,139,.18); color:var(--low); border:1px solid rgba(100,116,139,.4); }
.badge.kind { background:var(--panel-2); color:var(--text); border:1px solid var(--border); text-transform:none; }
.loc { color:var(--muted); font-size:12.5px; }
p.rule { padding:8px 12px; background:var(--panel-2); border-radius:4px; margin:8px 14px; }
p.rule code { color:var(--text); }
p.benign { margin:6px 14px; padding:6px 12px; font-size:13px; color:var(--low);
           border-left:3px solid var(--low); background:rgba(100,116,139,.08); }
.profile-context { margin:6px 14px; color:var(--muted); font-size:12px; display:flex;
  gap:6px; flex-wrap:wrap; align-items:center; }
.ctx-chip { display:inline-block; padding:1px 7px; border-radius:10px;
  background:rgba(100,116,139,.14); border:1px solid rgba(100,116,139,.35);
  color:var(--muted); font-size:11px; }
.show-noisy { margin:10px 0 2px; padding:6px 8px; border:1px solid var(--border);
  border-radius:4px; background:var(--panel-2); }
.ev-wrap { overflow-x:auto; border:1px solid var(--border); border-radius:4px; background:var(--panel-2); }
table.ev { width:100%; border-collapse:collapse; }
table.ev th, table.ev td { padding:5px 9px; border-bottom:1px solid var(--border);
  text-align:left; white-space:nowrap; }
table.ev th { background:var(--panel); color:var(--muted); font-weight:500; position:sticky; top:0; }
table.ev td { color:var(--text); }
table.ev .row-label { color:var(--muted); background:var(--panel); width:1%; text-align:right; padding-right:12px; }
table.ev tr.ctx td { color:var(--muted); background:rgba(255,255,255,.015); }
table.ev tr.hi-row td:first-child + td { box-shadow:inset 3px 0 0 var(--high); }
table.ev td.hi-col { background:rgba(250,204,21,.18); color:#fde68a; }
table.ev th.hi-col { background:rgba(250,204,21,.10); color:#fde68a;
  border-bottom:2px solid rgba(250,204,21,.4); }
.no-evidence { color:var(--muted); margin:0 14px 12px; font-size:12px; }
.muted { color:var(--muted); }
.dig-card { background:var(--panel); border:1px solid var(--border); border-radius:6px;
  margin:10px 0; padding:12px 14px; }
.dig-card header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
.bars { display:flex; flex-direction:column; gap:3px; max-width:520px; }
.bar-row { display:grid; grid-template-columns:18px 1fr 40px; align-items:center; gap:8px;
  font-size:11.5px; }
.bar-label { color:var(--muted); text-align:right; }
.bar { height:14px; background:var(--accent); border-radius:2px; min-width:1px; }
.bar.over { background:var(--medium); }
.bar-val { color:var(--muted); font-variant-numeric:tabular-nums; }
.dig-card .meta { color:var(--muted); font-size:12px; margin:8px 0 0; }
.shared-values { padding:10px 12px; background:var(--panel-2); border-radius:4px;
  margin:8px 14px; display:flex; gap:6px; flex-wrap:wrap; }
.val-chip { padding:2px 8px; border:1px solid var(--border); border-radius:10px;
  font-family:"SF Mono", Menlo, monospace; font-size:12px; color:var(--text); background:var(--panel); }
.empty { color:var(--muted); padding:40px 20px; text-align:center; border:1px dashed var(--border);
  border-radius:6px; }
.section h2 .count { color:var(--muted); font-weight:400; margin-left:6px; }
footer.foot { grid-column:1 / -1; padding:16px 26px 28px; border-top:1px solid var(--border);
  color:var(--muted); font-size:11.5px; }
footer.foot code { color:var(--text); }
"""

_JS = """
(function() {
  const findings = document.querySelectorAll('details.finding');
  const search = document.getElementById('filter-search');
  const reset = document.getElementById('reset-filters');
  const showNoisy = document.getElementById('show-noisy');

  function getChecked(cls) {
    return new Set(Array.from(document.querySelectorAll('input.' + cls + ':checked'))
                        .map(i => i.value));
  }

  function applyFilters() {
    const sev = getChecked('f-sev');
    const kinds = getChecked('f-kind');
    const files = getChecked('f-file');
    const q = (search.value || '').trim().toLowerCase();
    findings.forEach(el => {
      const matchSev = sev.has(el.dataset.severity);
      const matchKind = kinds.has(el.dataset.kind);
      const matchFile = files.has(el.dataset.file);
      const matchQ = !q || (el.dataset.searchable || '').indexOf(q) !== -1;
      const matchProfile = showNoisy.checked || el.dataset.profileAction !== 'hidden';
      el.style.display = (matchSev && matchKind && matchFile && matchQ && matchProfile) ? '' : 'none';
    });
    document.querySelectorAll('.section').forEach(sec => {
      const visible = Array.from(sec.querySelectorAll('details.finding'))
                           .some(d => d.style.display !== 'none');
      const hasFindings = sec.querySelector('details.finding') !== null;
      sec.style.display = (!hasFindings || visible) ? '' : 'none';
    });
  }

  document.querySelectorAll('input.f-sev, input.f-kind, input.f-file')
          .forEach(i => i.addEventListener('change', applyFilters));
  search.addEventListener('input', applyFilters);
  showNoisy.addEventListener('change', applyFilters);
  reset.addEventListener('click', () => {
    document.querySelectorAll('input.f-sev, input.f-kind, input.f-file')
            .forEach(i => i.checked = true);
    showNoisy.checked = false;
    search.value = '';
    applyFilters();
  });
  applyFilters();
})();
"""


def write_html_report(scan: dict, out_path: str) -> None:
    input_dir = scan.get("input_dir", "")
    input_label = os.path.basename(os.path.normpath(input_dir)) or input_dir or "audit"
    findings = _all_findings(scan)
    n_sheets = len({(it["file"], it["sheet"]) for it in findings if it["scope"] == "block"})
    sev = _severity_counts(findings)

    cross = [it for it in findings if it["scope"] == "cross_sheet"]
    high = [it for it in findings if it["scope"] == "block" and it["finding"].get("severity") == "high"]
    medium = [it for it in findings if it["scope"] == "block" and it["finding"].get("severity") == "medium"]
    low = [it for it in findings if it["scope"] == "block" and it["finding"].get("severity") == "low"]

    def section(title: str, items: list[dict], id_: str, hint: str = "") -> str:
        if not items:
            return ""
        body = "".join(_render_finding_card(it) for it in items)
        hint_html = f'<p class="hint">{_esc(hint)}</p>' if hint else ""
        return (
            f'<section id="{id_}" class="section">'
            f'<h2>{_esc(title)}<span class="count">({len(items)})</span></h2>'
            f'{hint_html}{body}'
            '</section>'
        )

    sections = "".join([
        section("Cross-sheet bit-identical collisions", cross, "sec-cross",
                "同一文件的两张 sheet 在同位置出现高度一致的数值 — 最值得人工复核。"),
        section("High-severity findings", high, "sec-high"),
        section("Medium-severity findings", medium, "sec-medium"),
        section("Low-severity findings", low, "sec-low"),
        _render_digit_section(scan),
        _render_decimal_section(scan),
    ])
    if not sections:
        sections = '<p class="empty">no findings — nothing flagged in this dataset.</p>'

    sidebar = _render_filter_sidebar(findings) if findings else \
        '<aside class="filters"><p class="muted">no findings</p></aside>'

    stats = "".join([
        f'<span class="stat"><strong>{scan.get("n_files", 0)}</strong> files</span>',
        f'<span class="stat"><strong>{n_sheets}</strong> sheets w/ findings</span>',
        f'<span class="stat sev-high"><strong>{sev["high"]}</strong> high</span>',
        f'<span class="stat sev-medium"><strong>{sev["medium"]}</strong> medium</span>',
        f'<span class="stat sev-low"><strong>{sev["low"]}</strong> low</span>',
    ])

    ver = scan.get("tool_version", "")
    ts = scan.get("scanned_at", "")
    prov = " · ".join(p for p in [
        f'paperconan v{_esc(ver)}' if ver else "paperconan",
        _esc(ts) if ts else "",
        f'input: <code>{_esc(scan.get("input_dir", ""))}</code>',
    ] if p)
    footer = (
        f'<footer class="foot">generated by {prov}<br>'
        'Statistical anomalies — signal, not verdict. Final adjudication belongs to the '
        'original authors and journal editors. Route findings through PubPeer / journal '
        'ethics inquiry / research integrity office.</footer>'
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>paperconan audit · {_esc(input_label)}</title>
<style>{_CSS}</style>
</head>
<body>
<header class="top">
  <div class="brand">paperconan<span class="brand-sub">paper data audit · {_esc(input_label)}</span></div>
  <div class="stats">{stats}</div>
  <div class="warn">Statistical anomalies — signal, not verdict. Take findings to PubPeer / journal editor / research integrity office, not social media.</div>
</header>
<div class="layout">
  {sidebar}
  <main>{sections}</main>
  {footer}
</div>
<script>{_JS}</script>
</body>
</html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)

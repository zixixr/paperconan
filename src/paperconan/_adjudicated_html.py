"""HTML renderer for adjudicated PaperConan reports.

This is separate from ``_html.write_html_report`` on purpose:

- ``report.html`` is the deterministic detector/evidence browser.
- adjudicated reports combine a human/AI verdict with scan evidence.

The renderer is local-first and infrastructure-agnostic: no DB, Blob, cloud
worker, DOI claiming, or private batch assumptions live here.
"""
from __future__ import annotations

import copy
import html
from html.parser import HTMLParser
import os
import re
import tempfile
from typing import Any

from ._html import _all_findings, _esc, _render_cross_sheet_examples, _render_evidence_table
from ._neutral_language import contains_blocked_language
from .image._budget import report_image_evidence_bytes
from .image._evidence import (
    EvidenceBudget,
    registered_native_crop_data_uri,
    registered_preview_data_uri,
)


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

_IMAGE_FINDING_STATUSES = {"needs_human", "explained", "different", "unresolved"}
_IMAGE_REVIEW_STATUSES = {
    "completed", "partial", "unavailable_no_multimodal", "not_requested",
}
_MAX_MODERN_FINDINGS = 5000
_MAX_MODERN_REFERENCES = 1000
# Raw selectors and image cards accepted across one verdict.
_MAX_VERDICT_REFERENCES = 5000
_VISIBLE_TEXT_SEPARATOR_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed_depth = 0

    def _separator(self) -> None:
        if self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._suppressed_depth += 1
            return
        if self._suppressed_depth:
            return
        if tag in _VISIBLE_TEXT_SEPARATOR_TAGS:
            self._separator()

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self._suppressed_depth or tag in {"script", "style"}:
            return
        if tag in _VISIBLE_TEXT_SEPARATOR_TAGS:
            self._separator()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            if self._suppressed_depth:
                self._suppressed_depth -= 1
            return
        if self._suppressed_depth:
            return
        if tag in _VISIBLE_TEXT_SEPARATOR_TAGS:
            self._separator()

    def handle_data(self, data: str) -> None:
        if not self._suppressed_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _is_image_verdict_finding(finding: dict[str, Any]) -> bool:
    return finding.get("finding_type") == "image" or bool(finding.get("image_refs"))


def _validate_optional_markdown(value: object, message: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(message)


def _validate_top_level_verdict(verdict: object) -> None:
    if type(verdict) is not dict:
        raise ValueError("verdict must be a concrete JSON object")


def _modern_findings(verdict: dict[str, Any]) -> list[Any] | None:
    if "findings" not in verdict:
        return None
    findings = verdict["findings"]
    if not isinstance(findings, list):
        raise ValueError("verdict findings must be a list")
    if len(findings) > _MAX_MODERN_FINDINGS:
        raise ValueError(
            "verdict findings must contain at most "
            f"{_MAX_MODERN_FINDINGS} entries"
        )
    for finding in findings:
        if type(finding) is not dict:
            raise ValueError("verdict finding entries must be dictionaries")
        finding_ref = finding.get("finding_ref")
        if finding_ref is not None and type(finding_ref) is not dict:
            raise ValueError(
                "verdict finding_ref must be a dictionary or null"
            )
        for field in ("extra_refs", "image_refs"):
            if field not in finding:
                continue
            references = finding[field]
            if not isinstance(references, list):
                raise ValueError(f"verdict {field} must be a list")
            if len(references) > _MAX_MODERN_REFERENCES:
                raise ValueError(
                    f"verdict {field} must contain at most "
                    f"{_MAX_MODERN_REFERENCES} entries"
                )
            if any(type(reference) is not dict for reference in references):
                raise ValueError(
                    f"verdict {field} entries must be dictionaries"
                )
            if field == "image_refs":
                for reference in references:
                    if "box" not in reference:
                        continue
                    box = reference["box"]
                    if (
                        not isinstance(box, list)
                        or len(box) != 4
                        or any(
                            isinstance(value, bool) or not isinstance(value, int)
                            for value in box
                        )
                    ):
                        raise ValueError(
                            "verdict image_refs box must contain exactly four "
                            "non-boolean integers"
                        )
        _validate_optional_markdown(
            finding.get("report_md"),
            "verdict finding report_md must be a string or null",
        )
    return findings


def _legacy_finding_refs(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    references = verdict.get("finding_refs")
    if references is None:
        return []
    if not isinstance(references, list):
        raise ValueError("verdict finding_refs must be a list or null")
    if len(references) > _MAX_VERDICT_REFERENCES:
        raise ValueError(
            "verdict finding_refs must contain at most "
            f"{_MAX_VERDICT_REFERENCES} entries"
        )
    if any(type(reference) is not dict for reference in references):
        raise ValueError("verdict finding_refs entries must be dictionaries")
    return references


def _validate_verdict_reference_limit(
    findings: list[Any] | None,
    legacy_references: list[dict[str, Any]],
) -> None:
    count = len(legacy_references)
    for finding in findings or []:
        count += int(finding.get("finding_ref") is not None)
        count += len(finding.get("extra_refs") or [])
        count += len(finding.get("image_refs") or [])
        if count > _MAX_VERDICT_REFERENCES:
            raise ValueError(
                "verdict references must contain at most "
                f"{_MAX_VERDICT_REFERENCES} entries"
            )


_SELECTOR_REFERENCE_FIELDS = (
    "file",
    "sheet",
    "rows",
    "kind",
    "rule",
    "finding_id",
)


def _selector_reference_key(reference: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        str(value) if (value := reference.get(field)) else None
        for field in _SELECTOR_REFERENCE_FIELDS
    )


def _image_reference_key(
    reference: dict[str, Any],
    assets: dict[str, dict[str, Any]],
) -> tuple[Any, ...]:
    asset_id = str(reference.get("asset_id"))
    asset = assets.get(asset_id)
    label = reference.get("label") or (
        asset.get("file") if asset is not None else None
    )
    boxed = "box" in reference
    return (
        asset_id,
        boxed,
        tuple(reference["box"]) if boxed else None,
        str(label) if label not in (None, "") else None,
    )


def _deduplicate_references(
    references: list[dict[str, Any]],
    key,
) -> list[dict[str, Any]]:
    unique = []
    seen = set()
    for reference in references:
        canonical = key(reference)
        if canonical in seen:
            continue
        seen.add(canonical)
        unique.append(reference)
    return unique


def _deduplicate_modern_references(
    findings: list[Any],
    scan: dict[str, Any],
) -> None:
    assets = _image_asset_map(scan)
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if "extra_refs" in finding:
            finding["extra_refs"] = _deduplicate_references(
                finding["extra_refs"],
                _selector_reference_key,
            )
        if "image_refs" in finding:
            finding["image_refs"] = _deduplicate_references(
                finding["image_refs"],
                lambda reference: _image_reference_key(reference, assets),
            )


def _normalize_image_review_status(value: object) -> str:
    status = str(value or "").strip().lower()
    return status if status in _IMAGE_FINDING_STATUSES else "unresolved"


def _normalize_image_review(review: object, known_asset_ids: set[str]) -> dict[str, Any]:
    source = review if isinstance(review, dict) else {}
    status = str(source.get("status") or "").strip().lower()
    if status not in _IMAGE_REVIEW_STATUSES:
        status = "partial"
    result = {"status": status}
    keys = (
        "reviewed_asset_ids",
        "unresolved_asset_ids",
        "unreadable_asset_ids",
        "deferred_asset_ids",
    )
    category_ids = {}
    for key in keys:
        values = source.get(key) if isinstance(source.get(key), list) else []
        category_ids[key] = {
            str(x) for x in values
            if str(x) in known_asset_ids
        }
    conflicts = {
        asset_id
        for asset_id in known_asset_ids
        if sum(asset_id in category_ids[key] for key in keys) > 1
    }
    for key in keys:
        result[key] = sorted(category_ids[key] - conflicts)
    result["unresolved_asset_ids"] = sorted(
        set(result["unresolved_asset_ids"]) | conflicts
    )
    assigned = {
        asset_id
        for key in keys
        for asset_id in result[key]
    }
    missing = sorted(known_asset_ids - assigned)
    if missing:
        result["deferred_asset_ids"] = sorted(
            set(result["deferred_asset_ids"] + missing)
        )
        if status == "completed":
            result["status"] = "partial"
    if conflicts:
        result["status"] = "partial"
    if source.get("note"):
        result["note"] = str(source["note"])
    return result


def _iter_verdict_text(
    verdict: dict[str, Any],
    scan_findings: list[dict[str, Any]] | None = None,
):
    def visible_values(
        source: dict[str, Any],
        keys: tuple[str, ...],
        *,
        markdown: bool,
    ):
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                yield str(value), markdown

    yield from visible_values(
        verdict,
        (
            "verdict",
            "title",
            "overall_impact",
        ),
        markdown=False,
    )
    yield from visible_values(verdict, ("review_note",), markdown=True)
    findings = _modern_findings(verdict)
    if findings is not None:
        yield from visible_values(verdict, ("paper_conclusion",), markdown=True)
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            yield from visible_values(
                finding,
                (
                    "title",
                    "suspicion_tier",
                    "impact_scope",
                    "review_status",
                ),
                markdown=False,
            )
            yield from visible_values(finding, ("report_md",), markdown=True)
            for image_ref in finding.get("image_refs") or []:
                if isinstance(image_ref, dict):
                    yield from visible_values(
                        image_ref,
                        ("label",),
                        markdown=False,
                    )
    else:
        yield from visible_values(
            verdict,
            (
                "suspicion_tier",
                "impact_scope",
                "review_status",
                "tier_why",
                "innocent_explanation",
                "needs_author_data",
            ),
            markdown=False,
        )
        yield from visible_values(verdict, ("report_md",), markdown=True)
    image_review = verdict.get("image_review")
    if isinstance(image_review, dict):
        yield from visible_values(
            image_review,
            ("status", "note"),
            markdown=False,
        )
    if scan_findings is not None and isinstance(findings, list) and len(findings) > 1:
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            ref = finding.get("finding_ref")
            if not isinstance(ref, dict) or _match_finding(scan_findings, ref) is not None:
                continue
            location = ref.get("sheet") or ref.get("file")
            if location not in (None, ""):
                yield str(location), False
            for key in ("rows", "kind"):
                value = ref.get(key)
                if value not in (None, ""):
                    yield str(value), False


def _rendered_visible_text(value: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(_render_md(value))
    parser.close()
    return parser.text()


def _validate_neutral_verdict(
    verdict: dict[str, Any],
    scan_findings: list[dict[str, Any]] | None = None,
) -> None:
    text = "\n".join(
        _rendered_visible_text(value) if markdown else value
        for value, markdown in _iter_verdict_text(verdict, scan_findings)
    )
    if contains_blocked_language(text):
        raise ValueError(
            "verdict text violates the neutral-language policy; rewrite it as a "
            "statistical signal, data inconsistency, unresolved similarity, or "
            "request for clarification; quoted or negated blocked language must "
            "also be rewritten"
        )


def _normalized_verdict_copy(
    scan: dict[str, Any],
    verdict: dict[str, Any],
    *,
    scan_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_findings = _modern_findings(verdict)
    legacy_references = []
    if source_findings is None:
        legacy_references = _legacy_finding_refs(verdict)
    _validate_verdict_reference_limit(source_findings, legacy_references)
    _validate_optional_markdown(
        verdict.get("paper_conclusion"),
        "verdict paper_conclusion must be a string or null",
    )
    _validate_optional_markdown(
        verdict.get("review_note"),
        "verdict review_note must be a string or null",
    )
    if source_findings is None:
        _validate_optional_markdown(
            verdict.get("report_md"),
            "verdict report_md must be a string or null",
        )

    normalized = copy.deepcopy(verdict)
    findings = (
        normalized["findings"]
        if source_findings is not None
        else None
    )
    if findings is not None:
        _deduplicate_modern_references(findings, scan)
    known = {
        str(asset.get("asset_id"))
        for asset in scan.get("image_assets", []) or []
        if asset.get("asset_id")
    }
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        if _is_image_verdict_finding(finding):
            finding["review_status"] = _normalize_image_review_status(
                finding.get("review_status")
            )
    if known or "image_review" in normalized:
        normalized["image_review"] = _normalize_image_review(
            normalized.get("image_review"), known
        )
    if scan_findings is None:
        scan_findings = _visible_scan_findings(scan)
    _validate_neutral_verdict(normalized, scan_findings)
    return normalized


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


def _finding_score(item: dict[str, Any]) -> tuple[int, int]:
    f = item["finding"]
    sev = str(f.get("severity") or "").lower()
    sev_rank = {"high": 0, "medium": 1, "low": 2}.get(sev, 3)
    scope_rank = 0 if item["scope"] == "cross_sheet" else 1
    return (sev_rank, scope_rank)


def _visible_scan_findings(scan: dict[str, Any]) -> list[dict[str, Any]]:
    visible = [
        item for item in _all_findings(scan)
        if str(item["finding"].get("profile_action") or "").lower() != "hidden"
    ]
    return sorted(visible, key=_finding_score)


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
    if ref.get("finding_id"):
        checks.append(str(ref["finding_id"]) == str(f.get("finding_id")))
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
.fallback-evidence { margin:0 12px 8px; padding:8px 12px; background:#fff6d6;
  border-left:3px solid #d9a441; border-radius:4px; color:#5c4708; font-size:13px; }
.scope-note { margin:0 0 12px; padding:8px 12px; background:var(--panel); border-radius:6px;
  color:#344054; font-size:13px; }
footer { margin-top:20px; color:var(--muted); font-size:12px; }
.finding-block { background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:20px;
  box-shadow:0 8px 24px rgba(16,24,40,.04); margin-top:18px; }
.fb-head { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;
  border-bottom:1px solid var(--line); padding-bottom:12px; margin-bottom:12px; }
.fb-head h2 { font-size:17px; margin:0; }
.image-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:12px; margin:12px 0; }
.image-evidence { margin:0; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.image-preview { display:block; width:100%; height:auto; max-height:420px; object-fit:contain;
  background:#f8fafc; }
.image-evidence figcaption { padding:8px 10px; color:var(--muted); font-size:12px; }
.image-unavailable { padding:32px 12px; color:var(--muted); text-align:center; }
@media (max-width: 900px) { .grid { grid-template-columns:1fr; } .page { padding:18px 12px 32px; } }
"""


# The findings-index table only appears when there is more than one finding.
# Its CSS is injected next to the table (not baked into the global stylesheet) so
# a single-finding page never carries the class name at all.
_INDEX_CSS = (
    "table.findings-index { width:100%; border-collapse:collapse; font-size:13px; margin-top:8px; }"
    "table.findings-index th, table.findings-index td { border-bottom:1px solid var(--line);"
    " padding:6px 10px; text-align:left; }"
    "table.findings-index th { color:var(--muted); font-weight:600; }"
)


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
    <div class="notice">This page combines a human/AI judgment with deterministic PaperConan scan evidence. Statistical signal, not verdict: it does not establish author intent.</div>
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


def _image_asset_map(scan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(asset["asset_id"]): asset
        for asset in scan.get("image_assets", []) or []
        if asset.get("asset_id")
    }


def _render_image_refs(
    scan: dict[str, Any],
    finding: dict[str, Any],
    artifact_dir: str | None,
    budget: EvidenceBudget,
) -> str:
    assets = _image_asset_map(scan)
    cards = []
    for ref in finding.get("image_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        asset = assets.get(str(ref.get("asset_id")))
        if asset is None:
            continue
        boxed = "box" in ref
        uri = (
            registered_native_crop_data_uri(
                asset,
                ref.get("box"),
                artifact_dir,
                budget,
            )
            if boxed
            else registered_preview_data_uri(asset, artifact_dir, budget)
        )
        unavailable = (
            "requested native-pixel region unavailable"
            if boxed
            else "preview unavailable"
        )
        img = (
            f'<img class="image-preview" src="{_esc(uri)}" alt="{_esc(asset.get("file"))}">'
            if uri else f'<div class="image-unavailable">{unavailable}</div>'
        )
        region_label = ref.get("box") if boxed else "full image"
        cards.append(
            '<figure class="image-evidence">'
            f'{img}<figcaption>{_esc(ref.get("label") or asset.get("file"))} '
            f'· {_esc(region_label)}</figcaption></figure>'
        )
    if not cards:
        return '<p class="no-evidence">图像证据引用未命中</p>'
    return '<div class="image-grid">' + "".join(cards) + "</div>"


def _render_image_review(review: dict[str, Any] | None) -> str:
    if not review:
        return ""
    return (
        '<section class="panel image-review">'
        '<h2>图像语义复核覆盖</h2>'
        f'<p><strong>{_esc(review.get("status"))}</strong></p>'
        f'<p>{_esc(review.get("note"))}</p>'
        f'<p>reviewed={len(review.get("reviewed_asset_ids") or [])} · '
        f'unresolved={len(review.get("unresolved_asset_ids") or [])} · '
        f'unreadable={len(review.get("unreadable_asset_ids") or [])} · '
        f'deferred={len(review.get("deferred_asset_ids") or [])}</p>'
        "</section>"
    )


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


def _render_finding_block(
    scan: dict[str, Any],
    scan_findings: list[dict[str, Any]],
    finding: dict[str, Any],
    idx: int,
    artifact_dir: str | None,
    image_budget: EvidenceBudget,
    legacy_fallback: bool,
) -> str:
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
    is_image = _is_image_verdict_finding(finding)
    if is_image:
        evidence = _render_image_refs(scan, finding, artifact_dir, image_budget)
        if matched is not None:
            evidence += _render_key_finding(matched, idx)
    else:
        fallback_notice = ""
        if legacy_fallback and matched is None and scan_findings:
            matched = scan_findings[0]
            # The narrative above describes the cited signal; this card is the
            # strongest scan signal instead. Say so, or the two read as one.
            if ref:
                fallback_notice = (
                    '<p class="fallback-evidence">finding_ref 未命中扫描结果；'
                    "以下展示本次扫描中最强的统计信号，供定位参考。</p>"
                )
        evidence = (
            fallback_notice + _render_key_finding(matched, idx)
            if matched is not None
            else '<p class="no-evidence">无匹配证据（finding_ref 未命中扫描结果）</p>'
        )
    # Additional evidence tables for any extra refs the verdict adjudicated together.
    for j, xref in enumerate(finding.get("extra_refs") or []):
        xm = _match_finding(scan_findings, xref or {})
        if xm is not None:
            evidence += _render_key_finding(xm, (idx + 1) * 1000 + j)
    return (
        '<section class="finding-block">'
        f'<header class="fb-head"><h2>发现 {idx + 1} · {_esc(title)}</h2>'
        f'<div class="badges">{"".join(badges)}</div></header>'
        f"{body}{evidence}</section>"
    )


def _normalize_verdict(
    verdict: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Fold either verdict shape into (paper_fields, findings_list, summary).

    - multi shape: return its findings as-is.
    - legacy single shape (report_md + finding_refs): synthesize one finding and
      carry tier_why / innocent_explanation / needs_author_data as ``summary``.
    """
    findings = _modern_findings(verdict)
    if findings is not None:
        paper = {"paper_conclusion": verdict.get("paper_conclusion"),
                 "review_note": verdict.get("review_note")}
        return paper, list(findings), {}
    refs = _legacy_finding_refs(verdict)
    single = {
        "title": verdict.get("title") or "发现",
        "finding_ref": refs[0] if refs else None,
        "extra_refs": refs[1:],
        "suspicion_tier": verdict.get("suspicion_tier"),
        "impact_scope": verdict.get("impact_scope"),
        "review_status": verdict.get("review_status") or "unreviewed",
        "report_md": verdict.get("report_md"),
    }
    summary = {k: verdict.get(k) for k in
               ("tier_why", "innocent_explanation", "needs_author_data")
               if verdict.get(k)}
    paper = {"paper_conclusion": None, "review_note": verdict.get("review_note")}
    return paper, [single], summary


def _render_unified(scan: dict[str, Any], verdict: dict[str, Any], title: str,
                    scan_findings: list[dict[str, Any]], findings: list[dict[str, Any]],
                    paper: dict[str, Any], summary: dict[str, Any],
                    artifact_dir: str | None, *, legacy_fallback: bool) -> str:
    """One high-fidelity layout for every verdict shape.

    Paper header + (optional) findings index + one self-contained block per
    finding, each with its own evidence heatmap. A single finding hides the index;
    a verdict-level ``summary`` (tier_why / innocent_explanation /
    needs_author_data) renders as a compact kv block under the conclusion.
    """
    conclusion = _render_md(paper.get("paper_conclusion")) or "<p>—</p>"
    image_budget = EvidenceBudget(report_image_evidence_bytes())
    blocks = "".join(
        _render_finding_block(
            scan,
            scan_findings,
            f,
            i,
            artifact_dir,
            image_budget,
            legacy_fallback,
        )
        for i, f in enumerate(findings)
    )
    note = _render_md(paper.get("review_note")) if paper.get("review_note") else ""

    summary_html = ""
    if summary:
        summary_kv = "".join(
            f"<div>{_esc(k)}</div><div>{_esc(v)}</div>"
            for k, v in summary.items()
            if v not in (None, "")
        )
        if summary_kv:
            summary_html = (f'<h2 style="margin-top:16px">判定摘要</h2>'
                            f'<div class="kv">{summary_kv}</div>')

    # The findings index (and its CSS) only appear when there is more than one
    # finding — a one-row index adds no signal.
    index_html = ""
    if len(findings) > 1:
        index_html = (f'<style>{_INDEX_CSS}</style>'
                      f'<h2 style="margin-top:16px">发现清单</h2>'
                      f'{_render_findings_index(findings, scan_findings)}')

    kv = {"tool_version": scan.get("tool_version"), "profile": scan.get("profile")}
    kv_html = "".join(
        f"<div>{_esc(k)}</div><div>{_esc(v)}</div>"
        for k, v in kv.items()
        if v not in (None, "")
    )
    coverage = _render_image_review(verdict.get("image_review"))
    main_html = f"""<section class="panel">
    <h2>论文主结论</h2>
    {conclusion}
    {summary_html}
    {index_html}
  </section>
  {coverage}
  {blocks}
  <section class="panel" style="margin-top:18px">
    <h2>方法与背景</h2>
    {note}<div class="kv">{kv_html}</div>
  </section>"""
    return _page(title, _paper_badges(verdict, _top_tier(findings)), main_html)


def render_adjudicated_report(
    scan: dict[str, Any],
    verdict: dict[str, Any],
    *,
    artifact_dir: str | None = None,
) -> str:
    """Return a self-contained HTML page for a judged PaperConan scan.

    Both verdict shapes render through one high-fidelity path: a ``findings``
    array (the main shape) and a legacy single verdict (``report_md`` + optional
    ``finding_refs``) are folded by :func:`_normalize_verdict` into one findings
    list, then rendered as a paper header + per-finding blocks with evidence.
    """
    _validate_top_level_verdict(verdict)
    scan_findings = _visible_scan_findings(scan)
    verdict = _normalized_verdict_copy(
        scan,
        verdict,
        scan_findings=scan_findings,
    )
    title = _scan_title(scan, verdict)
    legacy_fallback = "findings" not in verdict
    paper, findings, summary = _normalize_verdict(verdict)
    return _render_unified(
        scan,
        verdict,
        title,
        scan_findings,
        findings,
        paper,
        summary,
        artifact_dir,
        legacy_fallback=legacy_fallback,
    )


def write_adjudicated_report(
    scan: dict[str, Any],
    verdict: dict[str, Any],
    out_path: str,
    *,
    artifact_dir: str | None = None,
) -> None:
    """Write an adjudicated PaperConan HTML report."""
    _validate_top_level_verdict(verdict)
    rendered = render_adjudicated_report(scan, verdict, artifact_dir=artifact_dir)
    absolute_out = os.path.abspath(out_path)
    destination_dir = os.path.dirname(absolute_out) or "."
    os.makedirs(destination_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=".paperconan-adjudicated-",
        suffix=".html",
        dir=destination_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(rendered)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, absolute_out)
        temp_path = ""
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

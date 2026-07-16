"""HTML report renderer for paperconan scan results.

Renders a single self-contained HTML file (inline CSS + vanilla JS, no external
deps, no CDN) showing every finding with its evidence table. Designed to be
emailed, attached to a PubPeer post, or saved alongside scan.json.
"""
from __future__ import annotations

import html
import os
from typing import Any, Iterable

from .image._budget import report_image_evidence_bytes
from .image._evidence import (
    _BoundedBytesIO,
    EvidenceBudget,
    _base64_encoded_size,
    _max_image_bytes,
    _max_image_pixels,
    _max_raw_size_for_base64_budget,
    registered_native_crop_data_uri,
    registered_preview_data_uri,
)


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
        if v == 0:
            return "0"
        if v == int(v) and abs(v) < 1e15:
            return f"{int(v)}"
        return f"{v:.8g}"
    return html.escape(str(v))


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


# ---------- finding extraction ----------

_PER_BLOCK_GROUPS = (
    "relations",
    "progressions",
    "equal_pairs",
    "row_pairs",
    "row_relations",
    "within_col",
    "identical_after_rounding",
    "grim",
)


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
    assets = {
        str(asset.get("asset_id")): asset
        for asset in scan.get("image_assets", []) or []
        if asset.get("asset_id")
    }
    for image_finding in scan.get("image_findings", []) or []:
        asset_ids = [
            str(value)
            for value in image_finding.get("asset_ids", []) or []
        ]
        files = [
            assets[asset_id].get("file", asset_id)
            for asset_id in asset_ids
            if asset_id in assets
        ]
        out.append({
            "scope": "image",
            "file": " / ".join(files) or "registered image asset",
            "sheet": "image",
            "block_rows": "native pixels",
            "block_cols": "native pixels",
            "header": [],
            "finding": image_finding,
            "image_assets": [
                assets[asset_id]
                for asset_id in asset_ids
                if asset_id in assets
            ],
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


def _status_value(value: Any) -> str:
    if isinstance(value, dict):
        items = sorted(
            value.items(),
            key=lambda item: _status_sort_key(item[0]),
        )
        return "{" + ", ".join(
            f"{_status_value(key)}: {_status_value(item)}"
            for key, item in items
        ) + "}"
    if isinstance(value, (list, tuple)):
        left, right = ("[", "]") if isinstance(value, list) else ("(", ")")
        return left + ", ".join(_status_value(item) for item in value) + right
    text = str(value)
    if text == "":
        return '""'
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _status_sort_key(value: Any) -> tuple[str, str, str]:
    return (_status_value(value), type(value).__name__, str(value))


def _render_scan_status(scan: dict) -> str:
    status = scan.get("scan_status")
    coverage = scan.get("coverage") or {}
    limitations = coverage.get("limitations") or []

    if status is None:
        return (
            '<section class="scan-status status-legacy" aria-label="Scan status">'
            '<h2>Legacy scan</h2>'
            '<p>Detailed coverage status is unavailable for this archived scan.</p>'
            '</section>'
        )

    normalized = str(status).strip().lower()
    if normalized == "complete":
        return (
            '<section class="scan-status status-complete" aria-label="Scan status">'
            '<h2>Scan complete</h2>'
            '<p>Detailed coverage metadata reports no scan limitations.</p>'
            '</section>'
        )

    if normalized == "failed":
        summary = (
            "No input table reached numeric scanning. "
            "This report does not represent a completed scan."
        )
    elif normalized == "partial":
        summary = (
            "Findings below reflect completed numeric scanning, "
            "but the listed coverage limitations apply."
        )
    else:
        summary = "Detailed coverage status is unavailable for this scan."

    items = []
    for limitation in limitations:
        if isinstance(limitation, dict):
            reason_value = limitation.get("reason")
            if "reason" not in limitation or reason_value is None:
                reason_value = "unspecified"
            reason = _status_value(reason_value)
            detail_keys = (["scope"] if "scope" in limitation else []) + sorted(
                (key for key in limitation if key not in {"reason", "scope"}),
                key=_status_sort_key,
            )
            details = "".join(
                f'<span><strong>{_esc(_status_value(key).replace("_", " ").strip())}:</strong> '
                f'{_esc(_status_value(limitation[key]))}</span>'
                for key in detail_keys
                if limitation.get(key) is not None
            )
        else:
            reason = _status_value(limitation)
            details = ""
        detail_html = f'<span class="status-limit-detail">{details}</span>' if details else ""
        items.append(
            f'<li><code>{_esc(reason)}</code>{detail_html}</li>'
        )

    list_html = (
        '<h3>Coverage limitations</h3>'
        f'<ul class="status-limits">{"".join(items)}</ul>'
        if items else ""
    )
    status_class = normalized if normalized in {"partial", "failed"} else "legacy"
    label = (
        f"Scan {_esc(_status_value(normalized))}"
        if normalized else "Scan status unavailable"
    )
    return (
        f'<section class="scan-status status-{status_class}" aria-label="Scan status">'
        f'<h2>{label}</h2><p>{summary}</p>{list_html}</section>'
    )


def _render_omission_warning(scan: dict, omitted: int) -> str:
    noun = "finding was" if omitted == 1 else "findings were"
    prefix = (
        "At least "
        if scan.get("findings_omitted_is_lower_bound")
        else ""
    )
    base = (
        f"{prefix}{omitted:,} {noun} omitted to bound retained report output."
    )
    coverage = scan.get("coverage")
    if not isinstance(coverage, dict):
        return f'<div class="warn">{base}</div>'

    relevant_reasons = {
        "finding_limit": "PAPERCONAN_MAX_FINDINGS_PER_BLOCK",
        "global_finding_limit": "PAPERCONAN_MAX_TOTAL_FINDINGS",
        "row_pair_finding_limit": None,
        "recurring_row_vector_finding_limit": None,
        "recurring_row_vector_finalization_limit": None,
        "within_row_repeated_segment_candidate_limit": None,
        "within_row_repeated_segment_finalization_limit": None,
        "within_row_repeated_segment_finding_limit": None,
    }
    reasons = []
    controls = []
    for limitation in coverage.get("limitations") or []:
        if not isinstance(limitation, dict):
            continue
        reason = str(limitation.get("reason") or "")
        if reason not in relevant_reasons or reason in reasons:
            continue
        reasons.append(reason)
        control = relevant_reasons[reason]
        if control and control not in controls:
            controls.append(control)

    details = ""
    if reasons:
        reason_codes = ", ".join(
            f"<code>{_esc(reason)}</code>" for reason in reasons
        )
        details += f" Coverage reasons: {reason_codes}."
    if controls:
        control_codes = ", ".join(
            f"<code>{_esc(control)}</code>" for control in controls
        )
        details += f" Applicable controls: {control_codes}."
    return f'<div class="warn">{base}{details}</div>'


# ---------- evidence table rendering ----------

def _render_evidence_table(ev: dict | None) -> str:
    windows = ev.get("windows") if ev else None
    if isinstance(windows, list):
        rendered = [
            _render_evidence_table(window)
            for window in windows
            if isinstance(window, dict)
        ]
        if rendered:
            return '<div class="ev-windows">' + "".join(rendered) + "</div>"
    if not ev or not ev.get("rows"):
        return '<p class="no-evidence">no evidence table</p>'
    headers = ev.get("headers") or []
    col_offset = int(ev.get("col_offset") or 0)
    col_indices = ev.get("col_indices")
    if col_indices is None:
        col_indices = [
            col_offset + index for index in range(len(headers))
        ]
    else:
        col_indices = [int(index) for index in col_indices]
    hi_cols = set(int(c) for c in ev.get("highlight_cols") or [])
    hi_rows = set(int(r) for r in ev.get("highlight_rows") or [])

    # Header row: empty corner, then each header cell. Highlight matching columns.
    head_cells = ['<th class="row-label">row</th>']
    for i, h in enumerate(headers):
        abs_col = col_indices[i]
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
            abs_col = col_indices[i]
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
        dict_examples = [
            example for example in examples[:10]
            if isinstance(example, dict)
        ]
        keys = {
            key
            for example in dict_examples
            for key in example
        }
        if not keys:
            return (
                '<div class="shared-values">'
                '<span class="muted">empty example object</span>'
                "</div>"
            )
        decimal_tail_keys = (
            "row_a",
            "col_a",
            "value_a",
            "row_b",
            "col_b",
            "value_b",
            "decimal_tail",
        )
        same_position_keys = ("row", "col", "value")
        recurring_location_keys = (
            "file",
            "sheet",
            "row",
            "col",
            "start_col",
            "end_col",
            "value",
            "values",
            "vector",
        )
        if set(decimal_tail_keys).issubset(keys):
            ordered_keys = list(decimal_tail_keys)
        elif set(same_position_keys).issubset(keys):
            ordered_keys = list(same_position_keys)
        elif keys == {"value"}:
            ordered_keys = ["value"]
        elif keys & {"file", "sheet", "start_col", "end_col", "values", "vector"}:
            ordered_keys = [
                key for key in recurring_location_keys
                if key in keys
            ]
        else:
            ordered_keys = sorted(keys, key=_status_sort_key)
        ordered_keys.extend(
            sorted(keys - set(ordered_keys), key=_status_sort_key)
        )
        labels = {
            "row_a": "row A",
            "col_a": "col A",
            "value_a": "value A",
            "row_b": "row B",
            "col_b": "col B",
            "value_b": "value B",
            "decimal_tail": "decimal tail",
            "start_col": "start col",
            "end_col": "end col",
        }

        def render_value(value):
            if isinstance(value, (dict, list, tuple)):
                return _esc(_status_value(value))
            return _fmt_cell(value)

        headers = "".join(
            f"<th>{_esc(labels.get(key, key))}</th>"
            for key in ordered_keys
        )
        rows = []
        for ex in dict_examples:
            rows.append(
                "<tr>"
                + "".join(
                    (
                        f"<td>{render_value(ex[key])}</td>"
                        if key in ex
                        else "<td></td>"
                    )
                    for key in ordered_keys
                )
                + "</tr>"
            )
        return (
            '<div class="ev-wrap"><table class="ev">'
            f"<thead><tr>{headers}</tr></thead>"
            f'<tbody>{"".join(rows)}</tbody></table></div>'
        )
    # value-overlap form: list of floats
    chips = "".join(f'<span class="val-chip">{_fmt_cell(v)}</span>' for v in examples[:12])
    return f'<div class="shared-values"><span class="muted">shared values: </span>{chips}</div>'


def _physical_sheet_name(file_name: Any, sheet_name: Any) -> str | None:
    if file_name in (None, "") or sheet_name in (None, "", "?", "—"):
        return None
    file_text = str(file_name)
    sheet_text = str(sheet_name)
    prefix = f"{file_text}::"
    if sheet_text.startswith(prefix):
        sheet_text = sheet_text[len(prefix):]
    return sheet_text or None


def _cross_sheet_locations(finding: dict) -> set[tuple[str, str]]:
    locations = set()
    file_a = finding.get("file_a")
    file_b = finding.get("file_b")
    sheet_a = finding.get("sheet_a")
    sheet_b = finding.get("sheet_b")

    if finding.get("kind") == "recurring_row_vector":
        for example in finding.get("examples") or []:
            if not isinstance(example, dict):
                continue
            example_file = example.get("file")
            physical_sheet = _physical_sheet_name(
                example_file, example.get("sheet")
            )
            if (
                example_file not in (None, "")
                and physical_sheet is not None
            ):
                locations.add(
                    (str(example_file), physical_sheet)
                )
        if locations:
            return locations
        if finding.get("same_file") is True:
            recurring_file = (
                file_a
                if file_a not in (None, "")
                else file_b
            )
            if recurring_file in (None, ""):
                recurring_file = finding.get("file")
            if (
                recurring_file not in (None, "")
                and " + " not in str(recurring_file)
                and "; " not in str(recurring_file)
            ):
                for sheet_name in (sheet_a, sheet_b):
                    physical_sheet = _physical_sheet_name(
                        recurring_file, sheet_name
                    )
                    if physical_sheet is not None:
                        locations.add(
                            (str(recurring_file), physical_sheet)
                        )
        return locations

    if file_a not in (None, ""):
        physical_sheet = _physical_sheet_name(file_a, sheet_a)
        if physical_sheet is not None:
            locations.add((str(file_a), physical_sheet))
    if file_b not in (None, ""):
        physical_sheet = _physical_sheet_name(file_b, sheet_b)
        if physical_sheet is not None:
            locations.add((str(file_b), physical_sheet))

    if locations:
        return locations

    legacy_file = finding.get("file")
    if legacy_file in (None, ""):
        return locations
    legacy_file = str(legacy_file)
    split_fields_absent = (
        finding.get("file_a") in (None, "")
        and finding.get("file_b") in (None, "")
    )
    same_file = finding.get("same_file")
    if (
        split_fields_absent
        and same_file is not False
        and " + " not in legacy_file
    ):
        for sheet_name in (sheet_a, sheet_b):
            physical_sheet = _physical_sheet_name(
                legacy_file, sheet_name
            )
            if physical_sheet is not None:
                locations.add((legacy_file, physical_sheet))
    return locations


# ---------- per-section rendering ----------

def _registered_pair_preview_data_uri(
    item: dict,
    artifact_dir: str | None,
    budget: EvidenceBudget,
) -> str | None:
    import base64
    import io

    try:
        from PIL import Image
    except ImportError:
        return None

    assets = {
        str(asset.get("asset_id")): asset
        for asset in item.get("image_assets", []) or []
        if asset.get("asset_id")
    }
    regions = item["finding"].get("regions") or []
    if not regions:
        return None

    remaining_encoded_bytes = max(
        0,
        budget.max_bytes - budget.used_bytes,
    )
    validation_budget = EvidenceBudget(remaining_encoded_bytes)
    previews = []
    canvas = None
    try:
        for region in regions:
            if not isinstance(region, dict):
                return None
            asset = assets.get(str(region.get("asset_id")))
            if asset is None:
                return None
            uri = registered_native_crop_data_uri(
                asset,
                region.get("box"),
                artifact_dir,
                validation_budget,
            )
            marker = "data:image/png;base64,"
            if uri is None or not uri.startswith(marker):
                return None
            payload = base64.b64decode(
                uri[len(marker):],
                validate=True,
            )
            with Image.open(io.BytesIO(payload)) as crop:
                crop.load()
                preview = crop.copy()
            preview.thumbnail((760, 760))
            converted = preview.convert("RGB")
            if converted is not preview:
                preview.close()
            previews.append(converted)

        gap = 20
        width = sum(image.width for image in previews)
        width += gap * max(0, len(previews) - 1)
        height = max(image.height for image in previews)
        if (
            width <= 0
            or height <= 0
            or width * height > _max_image_pixels()
        ):
            return None
        canvas = Image.new("RGB", (width, height), "white")
        offset = 0
        for preview in previews:
            canvas.paste(preview, (offset, 0))
            offset += preview.width + gap

        remaining_encoded_bytes = max(
            0,
            budget.max_bytes - budget.used_bytes,
        )
        payload_limit = min(
            _max_image_bytes(),
            _max_raw_size_for_base64_budget(remaining_encoded_bytes),
        )
        if payload_limit <= 0:
            return None
        output = _BoundedBytesIO(payload_limit)
        canvas.save(output, format="JPEG", quality=88, optimize=True)
        payload = output.getvalue()
        encoded_size = _base64_encoded_size(len(payload))
        if not budget.can_consume(encoded_size):
            return None
        encoded = base64.b64encode(payload).decode("ascii")
        if len(encoded) != encoded_size or not budget.consume(encoded_size):
            return None
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        return None
    finally:
        if canvas is not None:
            canvas.close()
        for preview in previews:
            preview.close()


def _render_finding_card(
    item: dict,
    *,
    artifact_dir: str | None = None,
    image_budget: EvidenceBudget | None = None,
) -> str:
    f = item["finding"]
    sev = (f.get("severity") or "low").lower()
    kind = f.get("kind", "?")
    rule = f.get("rule", "")
    n = f.get("n", f.get("n_cells", ""))
    file_ = item["file"]
    sheet = item["sheet"]
    block_rows = item["block_rows"]
    profile_action = (f.get("profile_action") or "kept").lower()

    if item["scope"] == "image":
        regions = f.get("regions") or []
        chips = "".join(
            f'<span class="val-chip">{_esc(region.get("asset_id"))} '
            f'{_esc(region.get("box"))}</span>'
            for region in regions
        )
        preview_uri = None
        if image_budget is not None:
            if regions:
                preview_uri = _registered_pair_preview_data_uri(
                    item,
                    artifact_dir,
                    image_budget,
                )
            else:
                for asset in item.get("image_assets", []) or []:
                    preview_uri = registered_preview_data_uri(
                        asset,
                        artifact_dir,
                        image_budget,
                    )
                    if preview_uri is not None:
                        break
        chips_html = (
            f'<div class="shared-values">{chips}</div>'
            if chips else ""
        )
        if preview_uri:
            evidence_html = (
                f'<img class="image-pair-preview" src="{_esc(preview_uri)}" '
                f'alt="registered image pair evidence">{chips_html}'
            )
        else:
            evidence_html = (
                chips_html
                + (
                    '<p class="no-evidence">'
                    "registered image evidence unavailable</p>"
                    if regions else ""
                )
                or '<p class="no-evidence">no registered image region</p>'
            )
        loc = _esc(file_)
        extra_meta = (
            f' · score={_esc(f.get("score"))}'
            f' · transform={_esc(f.get("transform"))}'
        )
    elif item["scope"] == "cross_sheet":
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
    scope = item.get("scope", "block")
    return (
        f'<details class="finding" data-severity="{sev}" data-scope="{_esc(scope)}" '
        f'data-kind="{_esc(kind)}" '
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

    def cb(cls: str, value: str, label: str, checked: bool = True) -> str:
        checked_attr = " checked" if checked else ""
        return (
            f'<label><input type="checkbox" class="{cls}" value="{_esc(value)}"{checked_attr}>'
            f' {_esc(label)}</label>'
        )

    # low-severity is false-positive-heavy (within-column repeats, derived columns, rounded
    # grids…), so it is hidden by default to keep the initial view triage-worthy. The "How to
    # read" banner tells the reader it is one click away.
    sev_box = "".join(cb("f-sev", s, s, checked=(s != "low")) for s in ("high", "medium", "low"))
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
        f'<p class="hint">末位数字分布偏离均匀性是统计信号，需要结合测量精度、取整规则和数据来源核查。</p>'
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
        f'<p class="hint">某些末两位出现频率较高，是需要结合测量精度和数据处理流程核查的统计信号。</p>'
        f'<div class="ev-wrap"><table class="ev meta-table">'
        '<thead><tr><th>sheet</th><th>n</th><th>unique endings</th>'
        '<th>top endings</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '</section>'
    )


def _render_tail_cluster_section(scan: dict) -> str:
    items = scan.get("decimal_tail_clusters") or []
    if not items:
        return ""
    rows = []
    for item in items[:30]:
        top = ", ".join(
            f"...{tail} x {count}"
            for tail, count in (item.get("top") or [])[:6]
        )
        rows.append(
            f'<tr><td class="loc">{_esc(item.get("label"))}</td>'
            f'<td>{_esc(item.get("n"))}</td>'
            f'<td>{_esc(round(100 * (item.get("top_share") or 0)))}%</td>'
            f'<td>{_esc(item.get("complementary_pairs") or 0)}</td>'
            f'<td class="ends">{_esc(top)}</td></tr>'
        )
    return (
        '<section id="sec-tail-cluster" class="section">'
        f'<h2>Clustered high-precision fractional tails ({len(items)} sheets)</h2>'
        '<p class="hint">A small set of multi-digit fractional tails is '
        'over-represented across many distinct values. This statistical '
        'signal requires contextual review.</p>'
        '<div class="ev-wrap"><table class="ev meta-table">'
        '<thead><tr><th>sheet</th><th>high-precision n</th>'
        '<th>top share</th><th>complementary pairs</th>'
        '<th>top tails</th></tr></thead>'
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
.scan-status { background:var(--panel); border:1px solid var(--border);
  border-left:3px solid var(--low); border-radius:6px; padding:11px 14px;
  margin:0 0 14px; }
.scan-status h2 { margin:0; font-size:13.5px; }
.scan-status p { margin:3px 0 0; color:var(--muted); font-size:12.5px; }
.scan-status h3 { margin:10px 0 4px; color:var(--text); font-size:12px; }
.scan-status.status-complete { border-left-color:#16a34a; }
.scan-status.status-partial { border-left-color:var(--medium); }
.scan-status.status-failed { border-left-color:var(--high); }
.status-limits { margin:4px 0 0; padding-left:20px; color:var(--muted); font-size:12px; }
.status-limits li { margin:3px 0; }
.status-limits code { color:var(--text); }
.status-limit-detail { margin-left:8px; }
.status-limit-detail span + span::before { content:" · "; color:var(--border); }
.how-to-read { background:var(--panel-2); border:1px solid var(--border); border-left:3px solid var(--accent);
  border-radius:6px; padding:12px 16px; margin:0 0 22px; font-size:13px; line-height:1.6; color:var(--text); }
.how-to-read h3 { margin:0 0 6px; font-size:13.5px; color:var(--accent); font-weight:600; }
.how-to-read p { margin:0 0 6px; color:var(--muted); }
.how-to-read strong { color:var(--text); }
.how-to-read code { background:var(--panel); padding:1px 5px; border-radius:3px; }
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
.image-pair-preview { display:block; max-width:calc(100% - 28px); height:auto;
  margin:0 14px 14px; border:1px solid var(--border); }
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
      // Cross-sheet collisions are the flagship section and stay visible regardless of the
      // severity filter — otherwise a low/blank-severity collision would hide the whole
      // "most worth reviewing" section on load (low is unchecked by default).
      const matchSev = el.dataset.scope === 'cross_sheet' || sev.has(el.dataset.severity);
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
    document.querySelectorAll('input.f-kind, input.f-file')
            .forEach(i => i.checked = true);
    // Restore the initial triage view exactly: low severity stays unchecked on reset
    // (it is hidden by default), so reset matches a fresh page load rather than showing more.
    document.querySelectorAll('input.f-sev')
            .forEach(i => i.checked = (i.value !== 'low'));
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
    artifact_dir = os.path.dirname(os.path.abspath(out_path))
    image_budget = EvidenceBudget(report_image_evidence_bytes())
    raw_scan_status = scan.get("scan_status")
    scan_status = (
        str(raw_scan_status).strip().lower()
        if raw_scan_status is not None else None
    )
    status_html = _render_scan_status(scan)
    findings = _all_findings(scan)
    finding_locations = {
        (item["file"], item["sheet"])
        for item in findings
        if item["scope"] == "block"
    }
    for finding in scan.get("cross_sheet_findings", []) or []:
        if isinstance(finding, dict):
            finding_locations.update(
                _cross_sheet_locations(finding)
            )
    n_sheets = len(finding_locations)
    sev = _severity_counts(findings)

    cross = [it for it in findings if it["scope"] == "cross_sheet"]
    images = [it for it in findings if it["scope"] == "image"]
    high = [it for it in findings if it["scope"] == "block" and it["finding"].get("severity") == "high"]
    medium = [it for it in findings if it["scope"] == "block" and it["finding"].get("severity") == "medium"]
    low = [it for it in findings if it["scope"] == "block" and it["finding"].get("severity") == "low"]

    def section(title: str, items: list[dict], id_: str, hint: str = "") -> str:
        if not items:
            return ""
        body = "".join(
            _render_finding_card(
                item,
                artifact_dir=artifact_dir,
                image_budget=image_budget,
            )
            for item in items
        )
        hint_html = f'<p class="hint">{_esc(hint)}</p>' if hint else ""
        return (
            f'<section id="{id_}" class="section">'
            f'<h2>{_esc(title)}<span class="count">({len(items)})</span></h2>'
            f'{hint_html}{body}'
            '</section>'
        )

    sections = "".join([
        section("Cross-table statistical signals", cross, "sec-cross",
                "跨表信号可包括同位置数值、共享小数尾数、重复列或重复向量；需要结合表格语义和方法人工复核。"),
        section(
            "Optional deterministic image signals",
            images,
            "sec-images",
            "Non-gating hints only; semantic image review remains external.",
        ),
        section("High-severity findings", high, "sec-high"),
        section("Medium-severity findings", medium, "sec-medium"),
        section("Low-severity findings", low, "sec-low"),
        _render_digit_section(scan),
        _render_decimal_section(scan),
        _render_tail_cluster_section(scan),
    ])
    if not sections:
        if scan_status == "failed":
            sections = (
                '<p class="empty">scan failed — no input table reached numeric scanning.</p>'
            )
        elif scan_status == "partial":
            sections = (
                '<p class="empty">no findings recorded in the completed portion '
                'of this partial scan.</p>'
            )
        elif scan_status is None:
            sections = (
                '<p class="empty">no findings recorded in this legacy scan; '
                'detailed coverage is unavailable.</p>'
            )
        elif scan_status == "complete":
            sections = '<p class="empty">no findings — nothing flagged in this dataset.</p>'
        else:
            sections = (
                '<p class="empty">no findings recorded; detailed coverage status '
                'is unavailable for this scan.</p>'
            )

    how_to_read = (
        '<div class="how-to-read">'
        '<h3>如何阅读本报告 · How to read this</h3>'
        '<p>这是 paperconan 检测器的<strong>原始信号</strong>,不是结论,也不是经过人工/AI 判定的报告。'
        '每条 finding 只是一个统计异常,<strong>大多数都有良性解释</strong>'
        '(共享对照、重绘坐标轴、单位换算、派生列、固定分母比值、边界值、四舍五入网格…)。</p>'
        '<p>请把它当作<strong>待逐条人工复核的线索清单</strong>:对照原始表格、图注与 Methods 之后再判断,'
        '不要据此对论文或作者下任何结论。经判定的正式报告(含逐条裁决)是另一份单独产物。</p>'
        '<p>为便于分诊,<strong>low 级信号默认隐藏</strong>(误报偏多);在左侧勾选 <code>low</code> 可显示。'
        '优先看 cross-sheet 与 high。</p>'
        '</div>'
    )

    sidebar = _render_filter_sidebar(findings) if findings else \
        '<aside class="filters"><p class="muted">no findings</p></aside>'

    stats = "".join([
        f'<span class="stat"><strong>{scan.get("n_files", 0)}</strong> files</span>',
        f'<span class="stat"><strong>{n_sheets}</strong> sheets w/ findings</span>',
        f'<span class="stat sev-high"><strong>{sev["high"]}</strong> high</span>',
        f'<span class="stat sev-medium"><strong>{sev["medium"]}</strong> medium</span>',
        f'<span class="stat sev-low"><strong>{sev["low"]}</strong> low</span>',
    ])

    omitted = int(scan.get("findings_omitted") or 0)
    omitted_html = ""
    if omitted:
        omitted_html = _render_omission_warning(scan, omitted)

    ver = scan.get("tool_version", "")
    ts = scan.get("scanned_at")
    elapsed_ms = (scan.get("scan_stats") or {}).get("elapsed_ms")
    prov = " · ".join(p for p in [
        f'paperconan v{_esc(ver)}' if ver else "paperconan",
        _esc(ts) if ts is not None else "",
        (
            f'elapsed: <code>{_esc(elapsed_ms)} ms</code>'
            if elapsed_ms is not None
            else ""
        ),
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
  {omitted_html}
</header>
<div class="layout">
  {sidebar}
  <main>{status_html}{how_to_read}{sections}</main>
  {footer}
</div>
<script>{_JS}</script>
</body>
</html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)

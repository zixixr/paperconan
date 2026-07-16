# Image Diagnostics Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `paperconan-watch` image benchmark reproducible from lab-local inputs, keep whole-image, oracle-pair, and end-to-end measurements separate, and write complete machine-readable run metadata.

**Architecture:** Refactor the existing `lab/image_forensics/eval/` builders into import-safe CLI functions with explicit input/output paths, then add one scorer that consumes external Agent judgment JSONL and emits three independent metric tracks. Builders and tests use synthetic or gitignored local data; no model API call and no real paper asset is added to git.

**Tech Stack:** Python >=3.10, argparse, pathlib, dataclasses, hashlib, json, existing NumPy/OpenCV/Pillow lab dependencies, pytest.

**Baseline:** `paperconan-watch` branch `pubpeer-loop-m0` at `9ae5ca2`; `uv run python -m pytest lab/image_forensics/test_panels.py lab/image_forensics/test_screen.py -q` reports 14 passing tests.

## Global Constraints

- Rebuild the missing planted/blind data from lab-local inputs; do not rely on an earlier session transcript.
- Model-dependent tracks must be rerun with fresh no-context multimodal subagents.
- No builder may contain a user-specific absolute path or session scratch directory.
- Every builder must accept explicit `--lab-dir` and `--out`; dependent builders also accept `--cold-dir`.
- Whole-image, oracle-pair, and end-to-end metrics must never be combined into one recall number.
- The scorer accepts external judgment files; it does not import a model SDK or manage keys.
- Result JSON records git commit, timestamp, Python/package versions, model label, prompt version, top-K, input manifest hash, counts, unresolved items, and artifact paths.
- Real paper figures, DOIs, judgments, generated crops, and benchmark result files stay under ignored local directories.
- All benchmark wording remains neutral and reports review signals, unresolved cases, and requests for clarification.

---

## File Structure

- Create `lab/image_forensics/eval/common.py` - path validation, JSON helpers, hashing, run metadata.
- Modify `lab/image_forensics/eval/build_blind.py` - `build_blind(lab_dir, out_dir)` plus CLI.
- Modify `lab/image_forensics/eval/build_cold.py` - `build_cold(lab_dir, out_dir)` plus CLI.
- Modify `lab/image_forensics/screen.py` - retain native A/B crops and keep a bounded preview only for browsing.
- Modify `lab/image_forensics/test_screen.py` - native-crop dimension regression.
- Modify `lab/image_forensics/eval/build_pairwise.py` - `build_pairwise(cold_dir, out_dir, topk)` using `screen.screen_figure`.
- Create `lab/image_forensics/eval/score_benchmark.py` - judgment loader and three-track scorer.
- Create `lab/image_forensics/eval/test_benchmark.py` - path, schema, metric-separation, and ignore guards.
- Modify `lab/image_forensics/.gitignore` - ignore `_bench_runs/` and `eval/results/`.
- Modify `lab/image_forensics/HANDOFF.md` - replace scratch-path instructions with reproducible commands and result schema.
- Modify `lab/image_forensics/leads/validation_results.md` - record benchmark protocol only; do not record new model-dependent numbers until rerun.

## CLI Contracts

```bash
uv run python lab/image_forensics/eval/build_blind.py \
  --lab-dir lab/image_forensics \
  --out lab/image_forensics/_bench_runs/2026-07-10/blind

uv run python lab/image_forensics/eval/build_cold.py \
  --lab-dir lab/image_forensics \
  --out lab/image_forensics/_bench_runs/2026-07-10/cold

uv run python lab/image_forensics/eval/build_pairwise.py \
  --lab-dir lab/image_forensics \
  --cold-dir lab/image_forensics/_bench_runs/2026-07-10/cold \
  --out lab/image_forensics/_bench_runs/2026-07-10/pairwise \
  --topk 4

uv run python lab/image_forensics/eval/score_benchmark.py \
  --whole-key lab/image_forensics/_bench_runs/2026-07-10/cold/_key.json \
  --whole-verdicts lab/image_forensics/_bench_runs/2026-07-10/whole.jsonl \
  --oracle-key lab/image_forensics/_bench_runs/2026-07-10/blind/_key.json \
  --oracle-verdicts lab/image_forensics/_bench_runs/2026-07-10/oracle.jsonl \
  --e2e-manifest lab/image_forensics/_bench_runs/2026-07-10/pairwise/manifest.json \
  --e2e-verdicts lab/image_forensics/_bench_runs/2026-07-10/e2e.jsonl \
  --model "fresh-multimodal-agent" \
  --prompt-version "image-benchmark-v1" \
  --out lab/image_forensics/_bench_runs/2026-07-10/result.json
```

## Judgment JSONL Contract

Each line:

```json
{
  "item_id": "fig_00.png",
  "decision": "needs_human",
  "confidence": 0.82,
  "reason": "two regions retain matching structures; source context is required"
}
```

Allowed `decision` values:

- `needs_human`
- `different`
- `explained`
- `unresolved`

Unknown or missing decisions normalize to `unresolved`.

## Result JSON Contract

```json
{
  "schema_version": "image-benchmark-v1",
  "run": {
    "created_at": "ISO-8601 UTC",
    "git_commit": "full SHA",
    "python": "3.x.y",
    "numpy": "x.y.z",
    "opencv": "x.y.z",
    "pillow": "x.y.z",
    "model": "fresh-multimodal-agent",
    "prompt_version": "image-benchmark-v1",
    "topk": 4,
    "input_manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "tracks": {
    "whole_image": {
      "n_items": 10,
      "n_positive": 5,
      "n_negative": 5,
      "tp": 0,
      "fp": 0,
      "tn": 0,
      "fn": 0,
      "unresolved": 0,
      "recall": 0.0,
      "false_positive_rate": 0.0
    },
    "oracle_pair": {
      "n_items": 4,
      "n_positive": 2,
      "n_negative": 2,
      "tp": 0,
      "fp": 0,
      "tn": 0,
      "fn": 0,
      "unresolved": 0,
      "recall": 0.0,
      "false_positive_rate": 0.0
    },
    "end_to_end": {
      "n_positive_figures": 5,
      "n_clean_figures": 5,
      "retrieved_positive_figures": 0,
      "model_confirmed_retrieved_figures": 0,
      "clean_figures_escalated": 0,
      "unresolved_figures": 0,
      "candidate_recall": 0.0,
      "semantic_recall_on_retrieved": 0.0,
      "end_to_end_recall": 0.0,
      "clean_escalation_rate": 0.0
    }
  },
  "artifacts": {
    "whole_key": "_key.json",
    "oracle_key": "_key.json",
    "e2e_manifest": "manifest.json"
  }
}
```

---

### Task 1: Reproducible Builders, Separate Metrics, And Run Metadata

**Files:**
- Create: `lab/image_forensics/eval/common.py`
- Modify: `lab/image_forensics/eval/build_blind.py`
- Modify: `lab/image_forensics/eval/build_cold.py`
- Modify: `lab/image_forensics/eval/build_pairwise.py`
- Create: `lab/image_forensics/eval/score_benchmark.py`
- Create: `lab/image_forensics/eval/test_benchmark.py`
- Modify: `lab/image_forensics/.gitignore`
- Modify: `lab/image_forensics/HANDOFF.md`
- Modify: `lab/image_forensics/leads/validation_results.md`

**Interfaces:**
- Produces: `build_blind(lab_dir: Path, out_dir: Path) -> dict`.
- Produces: `build_cold(lab_dir: Path, out_dir: Path) -> dict`.
- Produces: `build_pairwise(lab_dir: Path, cold_dir: Path, out_dir: Path, topk: int) -> dict`.
- Produces: `load_judgments(path: Path) -> dict[str, dict]`.
- Produces: `score_binary_track(key, judgments, positive_labels) -> dict`.
- Produces: `score_end_to_end(manifest, judgments) -> dict`.
- Produces: `build_result(repo, whole_key_path, whole_verdicts_path, oracle_key_path, oracle_verdicts_path, e2e_manifest_path, e2e_verdicts_path, model, prompt_version) -> dict`.

- [ ] **Step 1: Write failing tests for import safety and explicit paths**

Create `lab/image_forensics/eval/test_benchmark.py`:

```python
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
LAB = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(LAB))


def test_builders_import_without_creating_session_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("build_blind", "build_cold", "build_pairwise"):
        module = importlib.import_module(name)
        assert callable(getattr(module, name))
    assert list(tmp_path.iterdir()) == []


def test_builder_sources_have_no_user_or_session_absolute_paths():
    text = "\n".join(
        (HERE / name).read_text(encoding="utf-8")
        for name in ("build_blind.py", "build_cold.py", "build_pairwise.py")
    )
    assert "/Users/" not in text
    assert "/private/tmp/" not in text
    assert "scratchpad" not in text
```

- [ ] **Step 2: Run path tests and verify red**

Run:

```bash
uv run python -m pytest lab/image_forensics/eval/test_benchmark.py::test_builders_import_without_creating_session_paths lab/image_forensics/eval/test_benchmark.py::test_builder_sources_have_no_user_or_session_absolute_paths -q
```

Expected: import triggers immediate builder execution or source checks find hardcoded paths.

- [ ] **Step 3: Add shared path and metadata helpers**

Create `lab/image_forensics/eval/common.py`:

```python
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


VALID_DECISIONS = {"needs_human", "different", "explained", "unresolved"}


def require_dir(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise SystemExit(f"{label} does not exist: {resolved}")
    return resolved


def ensure_out(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def git_commit(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()


def normalize_decision(value: object) -> str:
    decision = str(value or "").strip().lower()
    return decision if decision in VALID_DECISIONS else "unresolved"


def run_metadata(
    *,
    repo: Path,
    model: str,
    prompt_version: str,
    topk: int,
    manifest_path: Path,
) -> dict:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(repo),
        "python": platform.python_version(),
        "numpy": package_version("numpy"),
        "opencv": package_version("opencv-python-headless"),
        "pillow": package_version("pillow"),
        "model": model,
        "prompt_version": prompt_version,
        "topk": topk,
        "input_manifest_sha256": sha256_file(manifest_path),
    }
```

- [ ] **Step 4: Refactor `build_blind.py` into a CLI function**

Keep the existing four stimuli and fixed permutation, but store native A/B arrays instead of a pre-resized side-by-side image:

```python
stimuli["s1"] = (
    crop(C53, 55, 79, 203, 210),
    crop(C53, 55, 255, 203, 415),
    "SAME",
    "C5_3 Fig2C-WT vs Fig3C-WT",
)
stimuli["s2"] = (
    crop(C1, 540, 115, 975, 510),
    crop(C1, 120, 590, 530, 965),
    "SAME",
    "C1 Fig4B(Mock) vs Fig4C(NC)",
)
stimuli["s3"] = (
    crop(C53, 55, 79, 203, 210),
    crop(C53, 213, 82, 361, 210),
    "DIFFERENT",
    "C5_3 C-WT vs C-CCR2",
)
stimuli["s4"] = (
    canon[cA[1]:cA[3], cA[0]:cA[2]],
    canon[cB[1]:cB[3], cB[0]:cB[2]],
    "DIFFERENT",
    "two separate single-nucleus fields",
)
```

Move all module-level work into `build_blind(lab_dir: Path, out_dir: Path) -> dict`. Its first two statements are:

```python
    lab_dir = require_dir(lab_dir, "lab directory")
    out_dir = ensure_out(out_dir)
```

Replace `LAB` references with `lab_dir`, use `lab_dir / "leads/comparison_crops/C5_3_dup.png"`, `lab_dir / "leads/comparison_crops/C1_dup.png"`, and the existing basename under `lab_dir / "_fp_flagged"`. After the stimulus loop, use:

```python
    key_path = out_dir / "_key.json"
    write_json(key_path, key)
    manifest = {
        "schema_version": "image-benchmark-input-v1",
        "track": "oracle_pair",
        "items": sorted(key),
        "primary_items": sorted(
            item_id for item_id in key if item_id.startswith("HR_item_")
        ),
        "key": key_path.name,
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest
```

Add:

```python
def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build_blind(args.lab_dir, args.out)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add this pure writer:

```python
def write_pair_files(out_dir: Path, item_id: str, image_a, image_b) -> dict:
    path_a = out_dir / f"{item_id}_A.png"
    path_b = out_dir / f"{item_id}_B.png"
    preview = out_dir / f"{item_id}_preview.png"
    cv2.imwrite(str(path_a), image_a)
    cv2.imwrite(str(path_b), image_b)
    cv2.imwrite(str(preview), sidebyside(image_a, image_b))
    return {
        "image_a": path_a.name,
        "image_b": path_b.name,
        "preview": preview.name,
    }
```

For HR items, pass the original arrays to `write_pair_files()`. For LR items, downscale each A/B array to 32% with `INTER_AREA` and save that smaller array without re-expanding it. Merge the returned paths with `truth` and `note` in `key[item_id]`.

Add to `test_benchmark.py`:

```python
def test_oracle_pair_writer_keeps_native_dimensions(tmp_path):
    import cv2
    import numpy as np
    from build_blind import write_pair_files

    image_a = np.zeros((73, 91, 3), dtype=np.uint8)
    image_b = np.zeros((61, 87, 3), dtype=np.uint8)
    paths = write_pair_files(tmp_path, "HR_item_00", image_a, image_b)
    saved_a = cv2.imread(str(tmp_path / paths["image_a"]))
    saved_b = cv2.imread(str(tmp_path / paths["image_b"]))
    assert saved_a.shape[:2] == (73, 91)
    assert saved_b.shape[:2] == (61, 87)
```

The HR A/B files remain native. Only the preview may be resized. LR A/B files are explicitly degraded derivatives and remain outside the primary oracle metric.

- [ ] **Step 5: Refactor `build_cold.py` into a CLI function with input validation**

Move current logic into `build_cold(lab_dir: Path, out_dir: Path) -> dict`. Replace the module constants with:

```python
    lab_dir = require_dir(lab_dir, "lab directory")
    out_dir = ensure_out(out_dir)
    flag_dir = require_dir(lab_dir / "_fp_flagged", "clean-page input directory")
    pages = sorted(
        path for path in flag_dir.glob("*.png")
        if path.name != "flagged.json"
    )
```

Retain the existing `good_panels`, `far_pair`, `paste`, and `clone_patch` calls. Immediately after the usable-page loop, add:

```python
    if len(usable) < 10:
        raise SystemExit(
            f"need at least 10 usable pages, found {len(usable)} in {flag_dir}"
        )
```

After the existing fixed-shuffle output loop, add:

```python
    write_json(out_dir / "_key.json", key)
    manifest = {
        "schema_version": "image-benchmark-input-v1",
        "track": "whole_image",
        "items": sorted(key),
        "key": "_key.json",
        "n_planted": sum(v["class"] == "PLANTED" for v in key.values()),
        "n_clean": sum(v["class"] == "CLEAN" for v in key.values()),
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest
```

Add the same `main(argv=None)` pattern with required `--lab-dir` and `--out`.

- [ ] **Step 6: Refactor pairwise builder around the actual screen manifest**

First change `screen.screen_figure()` so every inter-panel candidate writes two native images plus one bounded preview:

```python
crop_a_path = os.path.join(out_dir, f"{stem}_inter_{rank:02d}_A.png")
crop_b_path = os.path.join(out_dir, f"{stem}_inter_{rank:02d}_B.png")
preview_path = os.path.join(out_dir, f"{stem}_inter_{rank:02d}_preview.png")
native_a = native[nbi[1]:nbi[3], nbi[0]:nbi[2]]
native_b = native[nbj[1]:nbj[3], nbj[0]:nbj[2]]
cv2.imwrite(crop_a_path, native_a)
cv2.imwrite(crop_b_path, native_b)
cv2.imwrite(preview_path, _sidebyside(native_a, native_b))
cands.append({
    "figure": fig,
    "type": "inter",
    "boxes": [list(nbi), list(nbj)],
    "transform": transform,
    "score": round(float(score), 3),
    "crop_a": crop_a_path,
    "crop_b": crop_b_path,
    "preview": preview_path,
    "crop": preview_path,
})
```

Apply the same A/B native crop contract to intra-panel candidates. Keep `crop` as a backward-compatible alias to the preview.

Add to `test_screen.py`:

```python
def test_candidate_native_crops_keep_box_dimensions(tmp_path):
    img, boxes = _grid(2, 2, seed=8)
    src, dst = boxes[(0, 0)], boxes[(1, 1)]
    img[dst[1]:dst[3], dst[0]:dst[2]] = img[src[1]:src[3], src[0]:src[2]]
    path = str(tmp_path / "fig.png")
    cv2.imwrite(path, img)
    candidate = screen.screen_figure(path, str(tmp_path / "out"), topk=1)[0]
    crop_a = cv2.imread(candidate["crop_a"])
    crop_b = cv2.imread(candidate["crop_b"])
    for crop, box in zip((crop_a, crop_b), candidate["boxes"]):
        assert crop.shape[1] == box[2] - box[0]
        assert crop.shape[0] == box[3] - box[1]
```

Then replace duplicated ranking helpers in `build_pairwise.py` with calls to `screen.screen_figure()`. Resolve `screen` from the repository, not a user path:

```python
import sys
from pathlib import Path

LAB_MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_MODULE_DIR))

import screen
```

Implement:

```python
def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if not inter:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _matches_truth(candidate: dict, truth: dict) -> bool:
    if truth.get("class") != "PLANTED":
        return False
    src, dst = truth.get("src"), truth.get("dst")
    if not src or not dst:
        return False
    a, b = candidate["boxes"]
    return (
        (_iou(a, src) >= 0.5 and _iou(b, dst) >= 0.5)
        or (_iou(a, dst) >= 0.5 and _iou(b, src) >= 0.5)
    )


def build_pairwise(
    lab_dir: Path,
    cold_dir: Path,
    out_dir: Path,
    topk: int,
) -> dict:
    require_dir(lab_dir, "lab directory")
    cold_dir = require_dir(cold_dir, "cold directory")
    out_dir = ensure_out(out_dir)
    key = read_json(cold_dir / "_key.json")
    items = []
    figures = []
    for figure_name in sorted(key):
        truth = key[figure_name]
        candidates = screen.screen_figure(
            str(cold_dir / figure_name),
            str(out_dir / "crops"),
            topk=topk,
        )
        figure_record = {
            "figure_id": figure_name,
            "truth_class": truth["class"],
            "truth_transform": truth.get("transform"),
            "n_candidates": len(candidates),
        }
        figures.append(figure_record)
        for index, candidate in enumerate(candidates):
            candidate_id = f"{figure_name}::candidate::{index:02d}"
            crop_a = Path(candidate["crop_a"]).resolve()
            crop_b = Path(candidate["crop_b"]).resolve()
            preview = Path(candidate["preview"]).resolve()
            items.append({
                "item_id": candidate_id,
                "figure_id": figure_name,
                "image_a": crop_a.relative_to(out_dir).as_posix(),
                "image_b": crop_b.relative_to(out_dir).as_posix(),
                "preview": preview.relative_to(out_dir).as_posix(),
                "type": candidate["type"],
                "boxes": candidate["boxes"],
                "transform": candidate["transform"],
                "score": candidate["score"],
                "truth_class": truth["class"],
                "truth_match": _matches_truth(candidate, truth),
            })
    manifest = {
        "schema_version": "image-benchmark-input-v1",
        "track": "end_to_end",
        "topk": topk,
        "cold_key_sha256": sha256_file(cold_dir / "_key.json"),
        "figures": figures,
        "items": items,
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest
```

CLI arguments: required `--lab-dir`, `--cold-dir`, `--out`; `--topk` integer default `4`, minimum `1`.

- [ ] **Step 7: Run import/path tests and verify green**

Run:

```bash
uv run python -m pytest lab/image_forensics/eval/test_benchmark.py::test_builders_import_without_creating_session_paths lab/image_forensics/eval/test_benchmark.py::test_builder_sources_have_no_user_or_session_absolute_paths -q
```

Expected: `2 passed`.

- [ ] **Step 8: Add failing tests for separated metric tracks**

Append:

```python
from score_benchmark import (
    load_judgments,
    score_binary_track,
    score_end_to_end,
)


def test_unknown_judgment_becomes_unresolved(tmp_path):
    path = tmp_path / "judgments.jsonl"
    path.write_text(
        json.dumps({"item_id": "x", "decision": "unknown-token"}) + "\n",
        encoding="utf-8",
    )
    assert load_judgments(path)["x"]["decision"] == "unresolved"


def test_whole_and_oracle_tracks_are_scored_independently():
    whole_key = {
        "fig_a.png": {"class": "PLANTED"},
        "fig_b.png": {"class": "CLEAN"},
    }
    whole = score_binary_track(
        whole_key,
        {
            "fig_a.png": {"decision": "needs_human"},
            "fig_b.png": {"decision": "explained"},
        },
        positive_labels={"PLANTED"},
        label_field="class",
    )
    oracle_key = {
        "HR_item_00.png": {"truth": "SAME"},
        "HR_item_01.png": {"truth": "DIFFERENT"},
    }
    oracle = score_binary_track(
        oracle_key,
        {
            "HR_item_00.png": {"decision": "unresolved"},
            "HR_item_01.png": {"decision": "different"},
        },
        positive_labels={"SAME"},
        label_field="truth",
    )
    assert whole["recall"] == 1.0
    assert whole["unresolved"] == 0
    assert oracle["recall"] == 0.0
    assert oracle["unresolved"] == 1


def test_end_to_end_reports_candidate_semantic_and_total_recall_separately():
    manifest = {
        "topk": 4,
        "figures": [
            {"figure_id": "p1", "truth_class": "PLANTED", "n_candidates": 1},
            {"figure_id": "p2", "truth_class": "PLANTED", "n_candidates": 1},
            {"figure_id": "p3", "truth_class": "PLANTED", "n_candidates": 0},
            {"figure_id": "n1", "truth_class": "CLEAN", "n_candidates": 1},
        ],
        "items": [
            {
                "item_id": "p1::candidate::00",
                "figure_id": "p1",
                "truth_class": "PLANTED",
                "truth_match": True,
            },
            {
                "item_id": "p2::candidate::00",
                "figure_id": "p2",
                "truth_class": "PLANTED",
                "truth_match": False,
            },
            {
                "item_id": "n1::candidate::00",
                "figure_id": "n1",
                "truth_class": "CLEAN",
                "truth_match": False,
            },
        ],
    }
    score = score_end_to_end(
        manifest,
        {
            "p1::candidate::00": {"decision": "needs_human"},
            "p2::candidate::00": {"decision": "different"},
            "n1::candidate::00": {"decision": "explained"},
        },
    )
    assert score["candidate_recall"] == pytest.approx(1 / 3)
    assert score["semantic_recall_on_retrieved"] == 1.0
    assert score["end_to_end_recall"] == pytest.approx(1 / 3)
    assert score["clean_escalation_rate"] == 0.0
```

- [ ] **Step 9: Run scorer tests and verify red**

Run:

```bash
uv run python -m pytest lab/image_forensics/eval/test_benchmark.py -q
```

Expected: scorer imports fail because `score_benchmark.py` does not exist.

- [ ] **Step 10: Implement the scorer**

Create `lab/image_forensics/eval/score_benchmark.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import normalize_decision, read_json, run_metadata, write_json


def load_judgments(path: Path) -> dict[str, dict]:
    out = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        item_id = str(item.get("item_id") or "")
        if not item_id:
            raise ValueError(f"{path}:{line_number}: missing item_id")
        if item_id in out:
            raise ValueError(f"{path}:{line_number}: duplicate item_id {item_id}")
        item["decision"] = normalize_decision(item.get("decision"))
        out[item_id] = item
    return out


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def score_binary_track(
    key: dict,
    judgments: dict[str, dict],
    *,
    positive_labels: set[str],
    label_field: str,
) -> dict:
    tp = fp = tn = fn = unresolved = 0
    positives = negatives = 0
    for item_id, truth in sorted(key.items()):
        positive = str(truth.get(label_field)) in positive_labels
        positives += int(positive)
        negatives += int(not positive)
        decision = normalize_decision((judgments.get(item_id) or {}).get("decision"))
        if decision == "unresolved":
            unresolved += 1
        elif positive and decision == "needs_human":
            tp += 1
        elif positive:
            fn += 1
        elif decision == "needs_human":
            fp += 1
        else:
            tn += 1
    return {
        "n_items": len(key),
        "n_positive": positives,
        "n_negative": negatives,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "unresolved": unresolved,
        "recall": _rate(tp, positives),
        "false_positive_rate": _rate(fp, negatives),
    }


def score_end_to_end(manifest: dict, judgments: dict[str, dict]) -> dict:
    by_figure: dict[str, list[dict]] = {}
    for item in manifest.get("items", []):
        by_figure.setdefault(item["figure_id"], []).append(item)
    positive_ids = sorted(
        item["figure_id"] for item in manifest.get("figures", [])
        if item.get("truth_class") == "PLANTED"
    )
    clean_ids = sorted(
        item["figure_id"] for item in manifest.get("figures", [])
        if item.get("truth_class") == "CLEAN"
    )
    retrieved = confirmed = clean_escalated = unresolved = 0
    for figure_id in positive_ids:
        candidates = by_figure.get(figure_id, [])
        matched = [item for item in candidates if item.get("truth_match")]
        if matched:
            retrieved += 1
        decisions = [
            normalize_decision((judgments.get(item["item_id"]) or {}).get("decision"))
            for item in matched
        ]
        if "needs_human" in decisions:
            confirmed += 1
        elif matched and (not decisions or all(x == "unresolved" for x in decisions)):
            unresolved += 1
    for figure_id in clean_ids:
        decisions = [
            normalize_decision((judgments.get(item["item_id"]) or {}).get("decision"))
            for item in by_figure.get(figure_id, [])
        ]
        if "needs_human" in decisions:
            clean_escalated += 1
        elif decisions and all(x == "unresolved" for x in decisions):
            unresolved += 1
    return {
        "n_positive_figures": len(positive_ids),
        "n_clean_figures": len(clean_ids),
        "retrieved_positive_figures": retrieved,
        "model_confirmed_retrieved_figures": confirmed,
        "clean_figures_escalated": clean_escalated,
        "unresolved_figures": unresolved,
        "candidate_recall": _rate(retrieved, len(positive_ids)),
        "semantic_recall_on_retrieved": _rate(confirmed, retrieved),
        "end_to_end_recall": _rate(confirmed, len(positive_ids)),
        "clean_escalation_rate": _rate(clean_escalated, len(clean_ids)),
    }
```

Add `build_result()` and CLI:

```python
def build_result(
    *,
    repo: Path,
    whole_key_path: Path,
    whole_verdicts_path: Path,
    oracle_key_path: Path,
    oracle_verdicts_path: Path,
    e2e_manifest_path: Path,
    e2e_verdicts_path: Path,
    model: str,
    prompt_version: str,
) -> dict:
    whole_key = read_json(whole_key_path)
    oracle_key = {
        item_id: truth
        for item_id, truth in read_json(oracle_key_path).items()
        if item_id.startswith("HR_item_")
    }
    e2e_manifest = read_json(e2e_manifest_path)
    return {
        "schema_version": "image-benchmark-v1",
        "run": run_metadata(
            repo=repo,
            model=model,
            prompt_version=prompt_version,
            topk=int(e2e_manifest["topk"]),
            manifest_path=e2e_manifest_path,
        ),
        "tracks": {
            "whole_image": score_binary_track(
                whole_key,
                load_judgments(whole_verdicts_path),
                positive_labels={"PLANTED"},
                label_field="class",
            ),
            "oracle_pair": score_binary_track(
                oracle_key,
                load_judgments(oracle_verdicts_path),
                positive_labels={"SAME"},
                label_field="truth",
            ),
            "end_to_end": score_end_to_end(
                e2e_manifest,
                load_judgments(e2e_verdicts_path),
            ),
        },
        "artifacts": {
            "whole_key": whole_key_path.name,
            "oracle_key": oracle_key_path.name,
            "e2e_manifest": e2e_manifest_path.name,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--whole-key", type=Path, required=True)
    parser.add_argument("--whole-verdicts", type=Path, required=True)
    parser.add_argument("--oracle-key", type=Path, required=True)
    parser.add_argument("--oracle-verdicts", type=Path, required=True)
    parser.add_argument("--e2e-manifest", type=Path, required=True)
    parser.add_argument("--e2e-verdicts", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[3]
    result = build_result(
        repo=repo,
        whole_key_path=args.whole_key,
        whole_verdicts_path=args.whole_verdicts,
        oracle_key_path=args.oracle_key,
        oracle_verdicts_path=args.oracle_verdicts,
        e2e_manifest_path=args.e2e_manifest,
        e2e_verdicts_path=args.e2e_verdicts,
        model=args.model,
        prompt_version=args.prompt_version,
    )
    write_json(args.out, result)
    print(json.dumps(result["tracks"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 11: Add full result metadata test**

Append:

```python
def test_result_schema_keeps_three_named_tracks(tmp_path, monkeypatch):
    import score_benchmark as score

    whole_key = tmp_path / "whole.json"
    oracle_key = tmp_path / "oracle.json"
    manifest = tmp_path / "manifest.json"
    whole_v = tmp_path / "whole.jsonl"
    oracle_v = tmp_path / "oracle.jsonl"
    e2e_v = tmp_path / "e2e.jsonl"
    whole_key.write_text('{"a":{"class":"PLANTED"}}', encoding="utf-8")
    oracle_key.write_text('{"b":{"truth":"SAME"}}', encoding="utf-8")
    manifest.write_text(
        '{"topk":4,"figures":[{"figure_id":"c","truth_class":"PLANTED",'
        '"n_candidates":1}],"items":[{"item_id":"c","figure_id":"c",'
        '"truth_class":"PLANTED","truth_match":true}]}',
        encoding="utf-8",
    )
    whole_v.write_text('{"item_id":"a","decision":"needs_human"}\n', encoding="utf-8")
    oracle_v.write_text('{"item_id":"b","decision":"needs_human"}\n', encoding="utf-8")
    e2e_v.write_text('{"item_id":"c","decision":"needs_human"}\n', encoding="utf-8")
    monkeypatch.setattr(score, "run_metadata", lambda **kwargs: {
        "created_at": "x",
        "git_commit": "abc",
        "python": "3.x",
        "numpy": "n",
        "opencv": "c",
        "pillow": "p",
        "model": kwargs["model"],
        "prompt_version": kwargs["prompt_version"],
        "topk": kwargs["topk"],
        "input_manifest_sha256": "hash",
    })
    result = score.build_result(
        repo=tmp_path,
        whole_key_path=whole_key,
        whole_verdicts_path=whole_v,
        oracle_key_path=oracle_key,
        oracle_verdicts_path=oracle_v,
        e2e_manifest_path=manifest,
        e2e_verdicts_path=e2e_v,
        model="model-x",
        prompt_version="prompt-v1",
    )
    assert result["schema_version"] == "image-benchmark-v1"
    assert set(result["tracks"]) == {"whole_image", "oracle_pair", "end_to_end"}
    assert result["run"]["model"] == "model-x"
    assert result["run"]["topk"] == 4
```

- [ ] **Step 12: Add ignore guards and test them**

Append to `lab/image_forensics/.gitignore`:

```gitignore
_bench_runs/
eval/results/
```

Append:

```python
def test_benchmark_outputs_are_gitignored():
    ignore = (LAB / ".gitignore").read_text(encoding="utf-8")
    assert "_bench_runs/" in ignore
    assert "eval/results/" in ignore
```

Run:

```bash
uv run python -m pytest lab/image_forensics/eval/test_benchmark.py -q
git check-ignore -v lab/image_forensics/_bench_runs/example/result.json
```

Expected: all tests pass and `git check-ignore` points to the new `_bench_runs/` rule.

- [ ] **Step 13: Rebuild the local benchmark data**

Use a fresh ignored directory:

```bash
run_dir="lab/image_forensics/_bench_runs/$(date -u +%Y%m%dT%H%M%SZ)"
uv run python lab/image_forensics/eval/build_blind.py \
  --lab-dir lab/image_forensics \
  --out "$run_dir/blind"
uv run python lab/image_forensics/eval/build_cold.py \
  --lab-dir lab/image_forensics \
  --out "$run_dir/cold"
uv run python lab/image_forensics/eval/build_pairwise.py \
  --lab-dir lab/image_forensics \
  --cold-dir "$run_dir/cold" \
  --out "$run_dir/pairwise" \
  --topk 4
```

Expected:

- blind manifest contains eight files: four high-resolution and four degraded-resolution stimuli;
- every oracle item has separate A/B files; HR A/B dimensions match the native source regions;
- cold manifest contains five planted and five clean figures;
- pairwise manifest contains all ten figure records, including figures with zero candidates;
- pairwise candidate records contain native `image_a`/`image_b`, `truth_match`, and no absolute crop path;
- all generated files are ignored.

- [ ] **Step 14: Rerun whole-image judgments with fresh no-context subagents**

Dispatch one fresh multimodal subagent per `cold/fig_*.png`. Give only the image and this prompt:

```text
Review this complete scientific figure as an independent image-quality check.
Return exactly one JSON object:
{"item_id":"<filename>","decision":"needs_human|different|explained|unresolved","confidence":0.0,"reason":"one neutral sentence"}
Use needs_human only when a specific region-level similarity remains unexplained.
Use explained for visible shared templates, processing steps, insets, channels, or other clear context.
Use unresolved when the complete figure is too dense or small to decide.
Do not infer intent and do not use external case knowledge.
```

Write the returned objects, one per line, to `$run_dir/whole.jsonl`. Do not expose `_key.json` to these subagents.

- [ ] **Step 15: Rerun oracle-pair judgments with fresh no-context subagents**

Dispatch one fresh multimodal subagent per logical `blind/HR_item_*`. Give only its registered `_A.png` and `_B.png` native files and:

```text
Compare region A and region B in this side-by-side scientific image.
Return exactly one JSON object:
{"item_id":"<filename>","decision":"needs_human|different|explained|unresolved","confidence":0.0,"reason":"one neutral sentence"}
Use needs_human when the regions appear to share the same underlying image and no visible context explains it.
Use different when they are distinct samples.
Use explained when a visible processing/channel/template relationship accounts for the similarity.
Use unresolved when image detail is insufficient.
Do not infer intent and do not use external case knowledge.
```

Write results to `$run_dir/oracle.jsonl` using logical IDs such as `HR_item_00`. Keep LR stimuli as a separate diagnostic appendix; `build_result()` filters them out of the primary oracle-pair metric.

- [ ] **Step 16: Rerun end-to-end candidate judgments**

Dispatch one fresh multimodal subagent per `pairwise/manifest.json items[]`. Give only that item's native `image_a` and `image_b` files and:

```text
Review this candidate region pair from a scientific figure.
Return exactly one JSON object:
{"item_id":"<manifest item_id>","decision":"needs_human|different|explained|unresolved","confidence":0.0,"reason":"one neutral sentence"}
Check whether A and B share the same underlying image, then look for visible channel, processing-step, inset, shared-control, or template explanations.
Use unresolved rather than guessing.
Do not infer intent and do not use external case knowledge.
```

Write results to `$run_dir/e2e.jsonl`. The scorer will distinguish candidate retrieval from semantic judgment.

- [ ] **Step 17: Score and inspect machine-readable output**

Run:

```bash
uv run python lab/image_forensics/eval/score_benchmark.py \
  --whole-key "$run_dir/cold/_key.json" \
  --whole-verdicts "$run_dir/whole.jsonl" \
  --oracle-key "$run_dir/blind/_key.json" \
  --oracle-verdicts "$run_dir/oracle.jsonl" \
  --e2e-manifest "$run_dir/pairwise/manifest.json" \
  --e2e-verdicts "$run_dir/e2e.jsonl" \
  --model "fresh-multimodal-agent" \
  --prompt-version "image-benchmark-v1" \
  --out "$run_dir/result.json"
uv run python -m json.tool "$run_dir/result.json" >/dev/null
```

Expected: valid JSON with exactly the three named tracks. Do not publish a combined recall number.

- [ ] **Step 18: Update handoff and validation protocol**

In `HANDOFF.md`:

- replace every session-specific path instruction with the CLI commands above;
- state that external judgments must be freshly rerun;
- point to `result.json` as the authoritative run artifact;
- state that whole-image, oracle-pair, and end-to-end metrics answer different questions.

In `leads/validation_results.md`, add a protocol section with the result schema and commands. Add new numbers only after Steps 14-17 complete; label unresolved counts explicitly.

- [ ] **Step 19: Run final benchmark regression**

Run:

```bash
uv run python -m pytest lab/image_forensics/test_panels.py lab/image_forensics/test_screen.py lab/image_forensics/eval/test_benchmark.py -q
git status --short -- lab/image_forensics
```

Expected:

- all existing 14 tests plus new benchmark tests pass;
- status shows only code, tests, `.gitignore`, and documentation;
- `_bench_runs/`, generated images, keys, judgments, and `result.json` do not appear.

- [ ] **Step 20: Commit the benchmark task**

Add files explicitly:

```bash
git add \
  lab/image_forensics/screen.py \
  lab/image_forensics/test_screen.py \
  lab/image_forensics/eval/common.py \
  lab/image_forensics/eval/build_blind.py \
  lab/image_forensics/eval/build_cold.py \
  lab/image_forensics/eval/build_pairwise.py \
  lab/image_forensics/eval/score_benchmark.py \
  lab/image_forensics/eval/test_benchmark.py \
  lab/image_forensics/.gitignore \
  lab/image_forensics/HANDOFF.md \
  lab/image_forensics/leads/validation_results.md
git commit -m "test(image-forensics): make multimodal benchmark reproducible"
```

Do not use `git add -A`; the repository already contains unrelated untracked files.

---

## Self-Review Record

- Spec coverage: configurable builders, fresh Agent reruns, separate whole/oracle/end-to-end tracks, machine-readable metadata, and no committed real assets are all explicit.
- Naming consistency: builder functions, CLI flags, JSON keys, `decision` values, and result track names are identical across tests and implementation steps.
- Metric clarity: candidate recall, semantic recall on retrieved candidates, and total end-to-end recall have distinct denominators.
- Data safety: generated images, keys, judgments, and results live under `_bench_runs/`; the commit uses an explicit path list.
- Ambiguity removed: the scorer never calls a model; LR oracle stimuli are excluded from the primary oracle metric; unknown decisions become `unresolved`.

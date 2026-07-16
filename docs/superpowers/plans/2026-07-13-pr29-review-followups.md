# PR 29 Review Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all actionable PR #29 review threads while preserving neutral language, deterministic output, bounded resource use, optional image dependencies, and the unified multi-finding report.

**Architecture:** Keep fixes inside the existing report, fetch, image diagnostics, and image asset boundaries. Use explicit failure records instead of tracebacks or silent omission, keep deterministic image hints non-gating, and document cases intentionally delegated to the external multimodal Agent.

**Tech Stack:** Python 3.10+, pytest, urllib, tarfile/zipfile/zlib, Pillow, OpenCV, uv.

## Global Constraints

- Output remains a statistical signal or data inconsistency requiring contextual review.
- Do not add a model provider, SDK, API key, separate image report, or parallel fetch pipeline.
- Do not commit real paper data, images, judgments, credentials, or review transcripts.
- Every behavior change follows a red-green test cycle.
- Keep scan and report ordering deterministic and all resource limits bounded.

---

### Task 1: Neutral-language validation and report CLI errors

**Files:**
- Modify: `src/paperconan/_adjudicated_html.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `src/paperconan/_neutral_language.py`
- Modify: `tests/test_adjudicated_report.py`
- Modify: `tests/test_skill_docs.py`
- Modify: `docs/reports.md`

**Interfaces:**
- Consumes: `contains_blocked_language(text: str) -> bool`
- Produces: clean one-line `paperconan report` failures and complete policy matching

- [x] Add validator tests for identifier-style text, the missing Chinese policy term, and a subprocess CLI failure with no traceback.
- [x] Run the focused tests and confirm they fail for the reviewed reasons.
- [x] Remove pre-match case folding, extend the generic rewrite guidance, and catch `ValueError` at the report CLI boundary.
- [x] Document migration guidance for older verdict text, run focused tests, and commit.

Run:

```bash
uv run pytest -q tests/test_adjudicated_report.py tests/test_skill_docs.py
```

Commit:

```bash
git commit -m "fix(report): enforce neutral language at CLI boundaries"
```

### Task 2: Archive and provenance publication failures

**Files:**
- Modify: `src/paperconan/fetch/_download.py`
- Modify: `tests/fetch/test_download.py`

**Interfaces:**
- Consumes: existing archive reconciliation and `download_candidate`
- Produces: per-archive skipped outcomes, reserved sidecar filtering, and visible sidecar failures

- [x] Add failing tests for unsupported/encrypted/corrupt ZIP processing, truncated TAR/gzip processing, a remote reserved sidecar name, and a sidecar write returning unavailable.
- [x] Run the focused tests and confirm the exceptions currently escape or the provenance outcome is incorrect.
- [x] Add explicit ZIP/TAR exception tuples, skip the reserved basename before publication, and record a warning when sidecar publication returns unavailable.
- [x] Run fetch tests and commit.

Run:

```bash
uv run pytest -q tests/fetch/test_download.py
```

Commit:

```bash
git commit -m "fix(fetch): harden archive and provenance outcomes"
```

### Task 3: Network response boundaries

**Files:**
- Modify: `src/paperconan/fetch/_download.py`
- Modify: `src/paperconan/fetch/_http.py`
- Modify: `src/paperconan/fetch/_sources.py`
- Modify: `tests/fetch/test_download.py`
- Modify: `tests/fetch/test_http.py`
- Modify: `tests/fetch/test_sources_dryad.py`

**Interfaces:**
- Consumes: urllib request helpers and normalized repository file references
- Produces: HTTP(S)-only redirect handling, bounded JSON reads, and valid Dryad file URLs

- [x] Add failing tests for a redirect to a non-HTTP(S) scheme, oversized JSON responses, and Dryad records without download links.
- [x] Run focused tests and confirm the current behavior fails the new boundaries.
- [x] Enforce credential-free HTTP(S) redirect targets and final URLs while permitting HTTPS CDN hosts, bound JSON bodies, and omit unusable Dryad file references.
- [x] Run HTTP/source/download tests and commit.

Run:

```bash
uv run pytest -q tests/fetch/test_http.py tests/fetch/test_sources_dryad.py tests/fetch/test_download.py
```

Commit:

```bash
git commit -m "fix(fetch): bound redirects and API responses"
```

### Task 4: Deterministic image hint calibration

**Files:**
- Modify: `src/paperconan/image/_diagnostics.py`
- Modify: `src/paperconan/image/_evidence.py`
- Modify: `src/paperconan/schema.py`
- Modify: `tests/test_image_diagnostics.py`
- Modify: `skills/paperconan/SKILL.md`
- Modify: `skills/paperconan/references/output-schema.md`
- Modify: `docs/cli.md`
- Modify: `docs/reports.md`
- Modify: `docs/superpowers/specs/2026-07-10-adaptive-image-diagnostics-design.md`

**Interfaces:**
- Consumes: registered assets, bounded panel proposals, scan-wide comparison and finding caps
- Produces: eight transform variants, margin-trimmed structural scoring, typed source-integrity failures, and findings that survive other evidence failures

- [x] Add failing tests for vertical/transpose transforms, offset duplicated content, the reviewed non-duplicate calibration case, budget-limited evidence, and source replacement after scoring.
- [x] Run focused tests and confirm recall, calibration, and evidence behavior fail as reviewed.
- [x] Trim low-information margins for scoring only, combine intensity and edge agreement without lowering the finding threshold, and add all dihedral transforms.
- [x] Add a typed source-integrity evidence failure; emit `evidence: null` for other evidence failures only when the scored source remains stable, and suppress findings after source replacement.
- [x] State that deterministic comparisons are within one asset, image profile fields are informational and not prefiltered, and cross-asset review belongs to the external Agent.
- [x] Run image diagnostics/report/workflow tests and commit.

Run:

```bash
uv run pytest -q tests/test_image_diagnostics.py tests/test_image_report.py tests/test_image_workflow.py tests/test_skill_docs.py
```

Commit:

```bash
git commit -m "fix(image): calibrate deterministic similarity hints"
```

### Task 5: Image asset compatibility and optional dependency tests

**Files:**
- Modify: `src/paperconan/image/_assets.py`
- Modify: `tests/test_image_assets.py`
- Modify: `tests/test_image_dependencies.py`

**Interfaces:**
- Consumes: Pillow decoding and no-replace asset publication
- Produces: unchanged host Pillow globals, actionable preview mismatch errors, and partial-install-safe tests

- [x] Add failing tests that `Image.MAX_IMAGE_PIXELS` is unchanged and preview-only mismatch errors include deterministic remediation.
- [x] Run focused tests and confirm current behavior mutates the global and lacks remediation.
- [x] Remove the process-global mutation, add bounded remediation text, and add the cv2 import skip to the dependency test module.
- [x] Roll back every finding for an asset when its scored source changes, clean only identity-matching evidence, and resynchronize the artifact budget.
- [x] Run all image tests and commit.

Run:

```bash
uv run pytest -q tests/test_image_assets.py tests/test_image_dependencies.py
```

Commit:

```bash
git commit -m "fix(image): preserve decoder and rerun boundaries"
```

### Task 6: Final verification and PR updates

**Files:**
- Verify all changed files
- Update: PR #29 body and review threads

**Interfaces:**
- Consumes: all preceding commits
- Produces: clean Draft PR with green CI and traceable review responses

- [ ] Run `uv run python -m pytest -q`, `uv lock --check`, `uv build`, neutral-language scan, conflict-marker scan, and `git diff --check`.
- [ ] Request a focused final review and address any Critical or Important findings.
- [ ] Push the branch, update the PR validation summary, reply to each addressed inline thread, and resolve only verified threads.
- [ ] Confirm PR state is `MERGEABLE` / `CLEAN` and Python 3.10-3.12 checks pass.

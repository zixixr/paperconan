#!/usr/bin/env bash
# Build paperconan-skill.zip — the downloadable skill bundle attached to GitHub releases.
#
# The bundle is the skill directory (skills/paperconan/) plus the worked example
# (examples/), repacked under a single top-level `paperconan/` folder so it drops
# straight into ~/.claude/skills/paperconan/. The zip itself is gitignored; this
# script is the source of truth for how it's produced.
#
# Usage:  ./build_skill_zip.sh [output-path]
# Then:   gh release upload <tag> paperconan-skill.zip --clobber
set -euo pipefail

SKILL_ZIP_SOURCES=(
  "skills/paperconan/SKILL.md"
  "skills/paperconan/references/adjudication-tiers.md"
  "skills/paperconan/references/adversarial-review.md"
  "skills/paperconan/references/batch-workflow.md"
  "skills/paperconan/references/case-patterns.md"
  "skills/paperconan/references/detectors.md"
  "skills/paperconan/references/interpretation.md"
  "skills/paperconan/references/judgment-rubric.md"
  "skills/paperconan/references/output-schema.md"
  "skills/paperconan/references/report-templates.md"
  "examples/README.md"
  "examples/demo_paper/ED_Fig2_tumor_volume.xlsx"
  "examples/demo_paper/ED_Fig4_qPCR.xlsx"
  "examples/demo_paper/audit/report.html"
  "examples/demo_paper/audit/scan.json"
  "examples/make_demo_data.py"
  "examples/report-preview.png"
)

CALLER_DIR="$(pwd -P)"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
if [[ $# -eq 0 ]]; then
  OUT_CANDIDATE="$REPO_ROOT/paperconan-skill.zip"
else
  OUT_INPUT="$1"
  case "$OUT_INPUT" in
    /*) OUT_CANDIDATE="$OUT_INPUT" ;;
    *) OUT_CANDIDATE="$CALLER_DIR/$OUT_INPUT" ;;
  esac
fi

for SOURCE in "${SKILL_ZIP_SOURCES[@]}"; do
  case "$SOURCE" in
    skills/paperconan/*)
      RELATIVE="${SOURCE#skills/paperconan/}"
      ;;
    examples/*)
      RELATIVE="$SOURCE"
      ;;
    *)
      echo "unsupported Skill ZIP source: $SOURCE" >&2
      exit 1
      ;;
  esac
  case "/$RELATIVE/" in
    *"/../"*|*"/./"*)
      echo "unsafe Skill ZIP source: $SOURCE" >&2
      exit 1
      ;;
  esac
  SOURCE_PATH="$REPO_ROOT/$SOURCE"
  if [[ ! -f "$SOURCE_PATH" ]]; then
    echo "missing Skill ZIP source: $SOURCE" >&2
    exit 1
  fi
  if [[ "$OUT_CANDIDATE" -ef "$SOURCE_PATH" ]]; then
    echo "output path aliases a Skill ZIP source: $SOURCE" >&2
    exit 1
  fi
done

OUT_DIR="$(dirname "$OUT_CANDIDATE")"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"
OUT="$OUT_DIR/$(basename "$OUT_CANDIDATE")"

for SOURCE in "${SKILL_ZIP_SOURCES[@]}"; do
  if [[ "$OUT" -ef "$REPO_ROOT/$SOURCE" ]]; then
    echo "output path aliases a Skill ZIP source: $SOURCE" >&2
    exit 1
  fi
done

STAGE="$(mktemp -d "$OUT_DIR/.paperconan-skill.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

ROOT="$STAGE/paperconan"
ARCHIVE="$STAGE/paperconan-skill.zip"
ZIP_MEMBERS=()
for SOURCE in "${SKILL_ZIP_SOURCES[@]}"; do
  case "$SOURCE" in
    skills/paperconan/*)
      RELATIVE="${SOURCE#skills/paperconan/}"
      ;;
    examples/*)
      RELATIVE="$SOURCE"
      ;;
  esac
  DESTINATION="$ROOT/$RELATIVE"
  mkdir -p "$(dirname "$DESTINATION")"
  cp -- "$REPO_ROOT/$SOURCE" "$DESTINATION"
  ZIP_MEMBERS+=("paperconan/$RELATIVE")
done

( cd "$STAGE" && zip -X "$ARCHIVE" "${ZIP_MEMBERS[@]}" )
unzip -t "$ARCHIVE" >/dev/null
mv -f "$ARCHIVE" "$OUT"

echo "built $OUT"
unzip -l "$OUT"

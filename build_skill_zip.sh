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

OUT="${1:-paperconan-skill.zip}"
OUT_DIR="$(dirname "$OUT")"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"
OUT="$OUT_DIR/$(basename "$OUT")"
rm -f "$OUT"

cd "$(dirname "$0")"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

ROOT="$STAGE/paperconan"
ZIP_MEMBERS=()
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
  if [[ ! -f "$SOURCE" ]]; then
    echo "missing Skill ZIP source: $SOURCE" >&2
    exit 1
  fi
  DESTINATION="$ROOT/$RELATIVE"
  mkdir -p "$(dirname "$DESTINATION")"
  cp -- "$SOURCE" "$DESTINATION"
  ZIP_MEMBERS+=("paperconan/$RELATIVE")
done

( cd "$STAGE" && zip -X "$OUT" "${ZIP_MEMBERS[@]}" )

echo "built $OUT"
unzip -l "$OUT"

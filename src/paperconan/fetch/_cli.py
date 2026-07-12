# src/paperconan/fetch/_cli.py
"""`paperconan fetch` subcommand: find and download scanner-supported inputs."""
from __future__ import annotations
import argparse
import json
import sys

from paperconan._input import SUPPORTED_INPUT_EXTS

from . import search_all
from . import _resolve
from ._download import download_candidate

_SUPPORTED_INPUT_LABEL = "/".join(f".{ext}" for ext in SUPPORTED_INPUT_EXTS)


def _print_table(cands):
    if not cands:
        print("no candidate datasets found in Zenodo / Figshare / Dryad / Europe PMC.")
        print("the data may be in journal supplementary (paywalled) or not deposited.")
        return
    for c in cands:
        sig = c.get("match_signals") or {}
        flags = []
        if sig.get("doi_in_related"):
            flags.append("DOI-match")
        if sig.get("title_overlap"):
            flags.append(f"title~{sig['title_overlap']}")
        if not _resolve.is_confident_match(c):
            flags.append("⚠ no DOI/title match")
        ninputs = len(c.get("tabular_files", []))
        print(f"[{c['cand_id']}] {c['source']:8} inputs={ninputs}/{c.get('all_files_count','?')} "
              f"{' '.join(flags):20} {c.get('title','')[:60]}")
        if ninputs == 0:
            print(
                f"    (no scanner-supported inputs "
                f"({_SUPPORTED_INPUT_LABEL}) in this dataset)"
            )


def fetch_main(argv):
    ap = argparse.ArgumentParser(prog="paperconan fetch",
                                 description="Find/download a paper's scanner-supported inputs")
    ap.add_argument("query", help="paper DOI or title")
    ap.add_argument("--json", action="store_true", help="print candidates as JSON (listing mode)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--download", metavar="CAND_ID", help="download this candidate's files")
    mode.add_argument("--auto", action="store_true", help="download the top-ranked candidate")
    ap.add_argument("--out", default=None, help="output dir for downloads (--download/--auto only)")
    ap.add_argument("--force", action="store_true",
                    help="download even a candidate with no DOI/title match (--download)")
    ap.add_argument(
        "--all",
        action="store_true",
        help="download files that are not scanner-supported inputs too",
    )
    ap.add_argument("--per-source", type=int, default=5, help="max results per repository (default: 5)")
    args = ap.parse_args(argv)

    cands = search_all(args.query, per_source=args.per_source)

    target = None
    if args.download:
        target = next((c for c in cands if c["cand_id"] == args.download), None)
        if target is None:
            print(f"candidate {args.download!r} not in results "
                  f"(check the cand_id from a list run, or increase --per-source)",
                  file=sys.stderr)
            return 2
        # Guard: a repo search can return unrelated deposits. Refuse to download a
        # candidate that doesn't match the paper unless the user insists with --force.
        if not _resolve.is_confident_match(target) and not args.force:
            print(f"candidate {args.download!r} has no DOI/title match to this paper "
                  f"(title: {target.get('title','')[:60]!r}); it is probably NOT this "
                  f"paper's data. Re-run with --force if you are sure.", file=sys.stderr)
            return 2
    elif args.auto:
        if not cands:
            print("--auto: no candidate datasets found; cannot select automatically",
                  file=sys.stderr)
            return 1
        # Only auto-pick a candidate we are confident is the paper's own dataset;
        # otherwise fall through to journal guidance rather than fetch a stranger's data.
        if _resolve.is_confident_match(cands[0]):
            target = cands[0]
        else:
            q = _resolve.normalize_query(args.query)
            print("--auto: no candidate confidently matches this paper "
                  "(no DOI match, weak title overlap), so nothing was downloaded.\n")
            print(_resolve.journal_guidance({"doi": q.get("doi"), "title": q.get("title")}))
            return 1

    if target is None:
        if args.json:
            print(json.dumps(cands, indent=2, default=str))
        else:
            _print_table(cands)
            # No usable tabular dataset in the open repos: point the user at where the
            # source data most likely lives (the journal article page).
            if not any(c.get("tabular_files") for c in cands):
                q = _resolve.normalize_query(args.query)
                print()
                print(_resolve.journal_guidance({"doi": q.get("doi"), "title": q.get("title")}))
        return 0

    out_dir = args.out or "paperconan_data"
    summary = download_candidate(target, out_dir, tabular_only=not args.all)
    print(f"downloaded {len(summary['downloaded'])} file(s) from {target['cand_id']} -> {out_dir}")
    for p in summary["downloaded"]:
        print(f"  {p}")
    for s in summary["skipped"]:
        print(f"  skipped {s['name']}: {s['reason']}")
    if summary["downloaded"]:
        print(f"\n  → now run: paperconan {out_dir}")
    return 0

# src/paperconan/fetch/_cli.py
"""`paperconan fetch` subcommand: search repositories for a paper's data and
optionally download a chosen candidate's tabular files."""
from __future__ import annotations
import argparse
import json
import sys

from . import search_all
from . import _resolve
from ._download import download_candidate


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
        ntab = len(c.get("tabular_files", []))
        print(f"[{c['cand_id']}] {c['source']:8} tabular={ntab}/{c.get('all_files_count','?')} "
              f"{' '.join(flags):20} {c.get('title','')[:60]}")
        if ntab == 0:
            print("    (no .xlsx/.csv/.tsv files in this dataset)")


def fetch_main(argv):
    ap = argparse.ArgumentParser(prog="paperconan fetch",
                                 description="Find/download a paper's tabular source data")
    ap.add_argument("query", help="paper DOI or title")
    ap.add_argument("--json", action="store_true", help="print candidates as JSON (listing mode)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--download", metavar="CAND_ID", help="download this candidate's files")
    mode.add_argument("--auto", action="store_true", help="download the top-ranked candidate")
    ap.add_argument("--out", default=None, help="output dir for downloads (--download/--auto only)")
    ap.add_argument("--all", action="store_true", help="download non-tabular files too")
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
    elif args.auto:
        if not cands:
            print("--auto: no candidate datasets found; cannot select automatically",
                  file=sys.stderr)
            return 1
        target = cands[0]

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

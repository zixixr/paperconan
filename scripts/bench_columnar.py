"""Peak-RSS + wall-time for scan_dir on a directory.
Usage: python scripts/bench_columnar.py <dir>"""
import resource, sys, time, tempfile
from paperconan._audit import scan_dir
d = sys.argv[1]
t0 = time.perf_counter()
with tempfile.TemporaryDirectory() as out:
    scan_dir(d, out, write_md=False, write_html=False)
dt = time.perf_counter() - t0
peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
peak_mb = peak_kb / (1024 if sys.platform == "darwin" else 1) / 1024
print(f"scan {d}: {dt:.1f}s  peak_rss={peak_mb:.0f}MB")

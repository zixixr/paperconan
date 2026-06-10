"""paperconan — 论文数据 sanity check.

Run on a directory of supplementary source-data xlsx files and surface
numeric patterns that warrant a closer look (identical columns, byte-identical
replicates, fixed arithmetic grids, last-digit anomalies, …).

The output is a *signal*, not a verdict — final adjudication belongs to
journal editors and the original authors. See README for full usage.
"""
from ._audit import scan_dir, main  # noqa: F401
from ._audit import scan_dir as audit_dir  # noqa: F401  public library entry point
from ._html import write_html_report  # noqa: F401
from .schema import PaperconanInputError  # noqa: F401

__version__ = "0.6.0"

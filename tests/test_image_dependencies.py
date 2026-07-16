from __future__ import annotations

import base64
import builtins
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("cv2")

from paperconan import scan_dir
from paperconan import _audit
from paperconan._html import write_html_report
from paperconan.image import ImageDependencyError


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _csv_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.csv").write_text(
        "a,b\n1,2\n2,3\n3,4\n4,5\n",
        encoding="utf-8",
    )
    return source


def _missing(monkeypatch, *missing_names):
    real_import_module = importlib.import_module
    real_import = builtins.__import__
    missing = set(missing_names)

    def import_module(name):
        if name in missing:
            raise ImportError(f"synthetic missing dependency: {name}")
        return real_import_module(name)

    def import_hook(name, globals=None, locals=None, fromlist=(), level=0):
        if name in missing or any(
            candidate.startswith(f"{name}.")
            for candidate in missing
        ):
            raise ImportError(f"synthetic missing dependency: {name}")
        return real_import(name, globals, locals, fromlist, level)

    try:
        dependencies = real_import_module("paperconan.image._dependencies")
    except ImportError:
        monkeypatch.setattr(builtins, "__import__", import_hook)
    else:
        monkeypatch.setattr(dependencies, "_import_module", import_module)


def _assert_numeric_publication_survives(
    scan,
    output,
    *,
    image_asset_count=0,
):
    assert scan["relations_blocks"]
    assert len(scan["image_assets"]) == image_asset_count
    assert scan["image_findings"] == []
    assert (output / "scan.json").exists()


def test_missing_pillow_is_non_gating_for_numeric_publication(tmp_path, monkeypatch):
    source = _csv_source(tmp_path)
    output = tmp_path / "audit"
    _missing(monkeypatch, "PIL.Image")

    scan = scan_dir(
        str(source),
        str(output),
        write_html=False,
        images=True,
    )

    _assert_numeric_publication_survives(scan, output)
    assert any(
        'pip install "paperconan[image]"' in item["error"]
        for item in scan["scan_errors"]
    )


def test_missing_pillow_is_non_gating_for_image_only_requested_reports(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Fig1.png").write_bytes(PNG_1X1)
    output = tmp_path / "audit"
    _missing(monkeypatch, "PIL.Image")

    scan = scan_dir(
        str(source),
        str(output),
        images=True,
    )

    assert scan["relations_blocks"] == []
    assert scan["image_assets"] == []
    assert scan["image_findings"] == []
    assert (output / "scan.json").exists()
    assert (output / "report.html").exists()
    assert any(
        'pip install "paperconan[image]"' in item["error"]
        for item in scan["scan_errors"]
    )


def test_missing_pdf_renderer_is_non_gating_for_numeric_publication(
    tmp_path,
    monkeypatch,
):
    source = _csv_source(tmp_path)
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    output = tmp_path / "audit"
    _missing(monkeypatch, "pypdfium2")

    scan = scan_dir(
        str(source),
        str(output),
        write_html=False,
        images=True,
    )

    _assert_numeric_publication_survives(scan, output)
    assert any(
        "PDF image rendering requires" in item["error"]
        for item in scan["scan_errors"]
    )


def test_missing_diagnostics_dependency_is_non_gating_for_numeric_publication(
    tmp_path,
    monkeypatch,
):
    source = _csv_source(tmp_path)
    (source / "Fig1.png").write_bytes(PNG_1X1)
    output = tmp_path / "audit"
    _missing(monkeypatch, "cv2")

    scan = scan_dir(
        str(source),
        str(output),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )

    _assert_numeric_publication_survives(
        scan,
        output,
        image_asset_count=1,
    )
    assert any(
        "image diagnostics require" in item["error"]
        for item in scan["scan_errors"]
    )


def test_pdf_renderer_is_not_required_without_pdf_sources(tmp_path, monkeypatch):
    source = _csv_source(tmp_path)
    _missing(monkeypatch, "pypdfium2")

    scan = scan_dir(
        str(source),
        str(tmp_path / "audit"),
        write_html=False,
        images=True,
    )

    assert scan["n_files"] == 1
    assert scan["image_assets"] == []


def test_numeric_only_scan_does_not_preflight_image_dependencies(
    tmp_path,
    monkeypatch,
):
    source = _csv_source(tmp_path)
    _missing(monkeypatch, "PIL.Image", "pypdfium2", "cv2")

    scan = scan_dir(
        str(source),
        str(tmp_path / "audit"),
        write_html=False,
    )

    assert scan["n_files"] == 1
    assert scan["image_assets"] == []


def test_numeric_only_report_ignores_image_dependency_and_limit_state(
    tmp_path,
    monkeypatch,
):
    _missing(monkeypatch, "PIL.Image", "PIL.ImageOps")
    monkeypatch.setenv(
        "PAPERCONAN_MAX_IMAGE_EVIDENCE_MB",
        "not-a-number",
    )
    out = tmp_path / "report.html"

    write_html_report(
        {
            "input_dir": str(tmp_path / "input"),
            "relations_blocks": [],
            "cross_sheet_findings": [],
            "image_assets": [],
            "image_findings": [],
        },
        str(out),
    )

    assert out.exists()


def test_cli_publishes_numeric_results_with_image_install_guidance(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = _csv_source(tmp_path)
    _missing(monkeypatch, "PIL.Image")
    monkeypatch.setattr(
        _audit.sys,
        "argv",
        [
            "paperconan",
            str(source),
            "--out",
            str(tmp_path / "audit"),
            "--no-html",
            "--images",
        ],
    )

    _audit.main()

    output = tmp_path / "audit"
    scan = json.loads((output / "scan.json").read_text(encoding="utf-8"))
    _assert_numeric_publication_survives(scan, output)
    captured = capsys.readouterr()
    assert "wrote " in captured.out
    assert any(
        'pip install "paperconan[image]"' in item["error"]
        for item in scan["scan_errors"]
    )


def test_scan_records_late_pdf_dependency_error(tmp_path, monkeypatch):
    from paperconan.image import _assets, _dependencies

    source = _csv_source(tmp_path)
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    error = ImageDependencyError(
        'PDF image rendering requires `pip install "paperconan[image]"`'
    )

    def unavailable(*args, **kwargs):
        raise error
        yield

    monkeypatch.setattr(
        _dependencies,
        "preflight_image_dependencies",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        _assets,
        "preflight_image_dependencies",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(_assets, "_render_pdf_pages", unavailable)

    output = tmp_path / "audit"
    scan = scan_dir(
        str(source),
        str(output),
        write_html=False,
        images=True,
    )

    _assert_numeric_publication_survives(scan, output)
    assert any(str(error) in item["error"] for item in scan["scan_errors"])


def test_scan_records_late_diagnostics_dependency_error(tmp_path, monkeypatch):
    from paperconan.image import _diagnostics

    source = _csv_source(tmp_path)
    output = tmp_path / "audit"
    error = ImageDependencyError(
        'image diagnostics require `pip install "paperconan[image]"`'
    )
    monkeypatch.setattr(
        _diagnostics,
        "diagnose_image_assets",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    scan = scan_dir(
        str(source),
        str(output),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )

    _assert_numeric_publication_survives(scan, output)
    assert any(str(error) in item["error"] for item in scan["scan_errors"])


def test_optional_image_error_context_is_bounded(tmp_path, monkeypatch):
    from paperconan.image import _dependencies

    source = _csv_source(tmp_path)
    output = tmp_path / "audit"
    detail = "x" * 2000
    monkeypatch.setattr(
        _dependencies,
        "preflight_image_dependencies",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError(detail)),
    )

    scan = scan_dir(
        str(source),
        str(output),
        write_html=False,
        images=True,
    )

    _assert_numeric_publication_survives(scan, output)
    errors = [item["error"] for item in scan["scan_errors"]]
    assert len(errors) == 1
    assert errors[0].startswith("optional image inventory unavailable: ")
    assert len(errors[0]) < 550
    assert errors[0].endswith("...")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PAPERCONAN_MAX_IMAGE_MB", "not-a-number"),
        ("PAPERCONAN_MAX_IMAGE_PIXELS", "inf"),
        ("PAPERCONAN_MAX_IMAGE_ASSETS", "9" * 5000),
    ],
)
def test_invalid_optional_image_limits_are_lazy_and_non_gating_in_subprocess(
    tmp_path,
    name,
    value,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.csv").write_text(
        "a,b\n1,2\n2,3\n3,4\n4,5\n",
        encoding="utf-8",
    )
    output = tmp_path / "audit"
    script = """
import json
import sys
from paperconan import scan_dir
import paperconan.image._assets

scan = scan_dir(sys.argv[1], sys.argv[2], write_html=False, images=True)
assert scan["relations_blocks"]
assert scan["image_assets"] == []
assert any(sys.argv[3] in item["error"] for item in scan["scan_errors"])
assert __import__("pathlib").Path(sys.argv[2], "scan.json").exists()
"""
    env = os.environ.copy()
    env[name] = value

    result = subprocess.run(
        [sys.executable, "-c", script, str(source), str(output), name],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

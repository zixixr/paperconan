from __future__ import annotations

import json
import os
import sys
import threading
import types
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL.Image")

from paperconan import scan_dir
from paperconan import _audit
from paperconan.image import ImageDependencyError
from paperconan.image import _assets
from paperconan.schema import PaperconanInputError


def _image(path: Path, size=(80, 60), color=(20, 90, 180)):
    PIL.new("RGB", size, color).save(path)


def _stub_pdfium(monkeypatch, page_sizes):
    events = {
        "rendered": [],
        "pil_closed": [],
        "bitmap_closed": [],
        "page_closed": [],
        "document_closed": 0,
    }

    class StubPILImage:
        def __init__(self, page_number):
            self.page_number = page_number
            self.image = PIL.new("RGB", (12, 8), (page_number, 20, 30))

        def save(self, path, format=None):
            self.image.save(path, format=format)

        def close(self):
            events["pil_closed"].append(self.page_number)
            self.image.close()

    class StubBitmap:
        def __init__(self, page_number):
            self.page_number = page_number

        def to_pil(self):
            return StubPILImage(self.page_number)

        def close(self):
            events["bitmap_closed"].append(self.page_number)

    class StubPage:
        def __init__(self, page_number, size):
            self.page_number = page_number
            self.size = size

        def get_size(self):
            return self.size

        def render(self, scale):
            events["rendered"].append(self.page_number)
            return StubBitmap(self.page_number)

        def close(self):
            events["page_closed"].append(self.page_number)

    class StubDocument:
        def __init__(self, path):
            self.pages = [
                StubPage(index, size)
                for index, size in enumerate(page_sizes, 1)
            ]

        def __len__(self):
            return len(self.pages)

        def __getitem__(self, index):
            return self.pages[index]

        def close(self):
            events["document_closed"] += 1

    monkeypatch.setitem(
        sys.modules,
        "pypdfium2",
        types.SimpleNamespace(PdfDocument=StubDocument),
    )
    return events


def test_prepare_image_assets_preserves_native_pixels_and_stable_ids(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png", size=(80, 60))
    _image(source / "FigB.png", size=(40, 30), color=(180, 60, 20))
    out = tmp_path / "audit"

    first, errors = _assets.prepare_image_assets(str(source), str(out))
    second, errors2 = _assets.prepare_image_assets(str(source), str(out))

    assert errors == errors2 == []
    assert [a["asset_id"] for a in first] == [a["asset_id"] for a in second]
    assert [a["file"] for a in first] == ["FigA.png", "FigB.png"]
    native = PIL.open(out / first[0]["path"])
    assert native.size == (80, 60)
    assert (out / first[0]["path"]).read_bytes() == (source / "FigA.png").read_bytes()
    assert first[0]["width"] == 80 and first[0]["height"] == 60
    assert first[0]["path"] != first[0]["preview_path"]


def test_prepare_image_assets_preserves_process_global_pillow_pixel_limit(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    original_limit = PIL.MAX_IMAGE_PIXELS
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", "123456")

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert errors == []
    assert len(assets) == 1
    assert PIL.MAX_IMAGE_PIXELS == original_limit


def test_prepare_image_assets_orders_assets_by_stable_source_name(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    first_path = source / "FigA.png"
    second_path = source / "FigB.png"
    _image(first_path)
    _image(second_path, color=(180, 60, 20))
    ids = {
        _assets._sha256(first_path): "img:zzzz",
        _assets._sha256(second_path): "img:aaaa",
    }
    monkeypatch.setattr(_assets, "_asset_id", ids.__getitem__)

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert errors == []
    assert [asset["file"] for asset in assets] == ["FigA.png", "FigB.png"]


def test_prepare_image_assets_sorts_only_supported_candidates(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    (source / "notes.txt").write_text("not an image", encoding="utf-8")
    real_stable_name_key = _assets._stable_name_key

    def supported_name_key(name):
        assert name != "notes.txt"
        return real_stable_name_key(name)

    monkeypatch.setattr(_assets, "_stable_name_key", supported_name_key)

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert errors == []
    assert [asset["file"] for asset in assets] == ["FigA.png"]


def test_exact_duplicate_files_are_one_asset_with_all_source_names(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "A.png")
    (source / "B.png").write_bytes((source / "A.png").read_bytes())
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    assert errors == []
    assert len(assets) == 1
    assert assets[0]["source_files"] == ["A.png", "B.png"]


def test_source_provenance_does_not_follow_sidecar_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    outside = tmp_path / "outside-source.json"
    outside.write_text(
        json.dumps({
            "downloads": [{
                "file": "FigA.png",
                "source_url": "https://example.test/outside.png",
            }],
        }),
        encoding="utf-8",
    )
    (source / "paperconan_source.json").symlink_to(outside)

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert errors == []
    assert assets[0]["source_type"] == "local_image"
    assert assets[0]["source_url"] is None


def test_source_provenance_rejects_oversized_sidecar(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    sidecar = source / "paperconan_source.json"
    sidecar.write_text(
        json.dumps({
            "downloads": [{
                "file": "FigA.png",
                "source_url": "https://example.test/oversized.png",
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(_assets, "_MAX_SOURCE_PROVENANCE_BYTES", 32)

    assert _assets._source_provenance(source) == {}


def test_source_provenance_rejects_non_dictionary_download_items(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "paperconan_source.json").write_text(
        '{"downloads":["value"]}',
        encoding="utf-8",
    )

    assert _assets._source_provenance(source) == {}


def test_source_provenance_rejects_sidecar_path_swap(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    sidecar = source / "paperconan_source.json"
    displaced = source / "paperconan_source.displaced.json"
    replacement = source / "paperconan_source.replacement.json"
    sidecar.write_text(
        '{"downloads":[{"file":"FigA.png","source_url":"original"}]}',
        encoding="utf-8",
    )
    replacement.write_text(
        '{"downloads":[{"file":"FigA.png","source_url":"swapped!"}]}',
        encoding="utf-8",
    )
    real_open = os.open
    swapped = False

    def swap_after_sidecar_open(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = real_open(path, flags, *args, **kwargs)
        if (
            not swapped
            and os.fspath(path) == "paperconan_source.json"
            and kwargs.get("dir_fd") is not None
        ):
            sidecar.rename(displaced)
            replacement.rename(sidecar)
            swapped = True
        return fd

    monkeypatch.setattr(_assets.os, "open", swap_after_sidecar_open)

    assert _assets._source_provenance(source) == {}
    assert swapped


def test_source_provenance_rejects_same_inode_mutation_during_read(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    sidecar = source / "paperconan_source.json"
    original = b'{"downloads":[{"file":"FigA.png","source_url":"original"}]}'
    replacement = b'{"downloads":[{"file":"FigA.png","source_url":"mutated!"}]}'
    assert len(original) == len(replacement)
    sidecar.write_bytes(original)
    real_read = os.read
    mutated = False

    def mutate_after_first_read(fd, size):
        nonlocal mutated
        payload = real_read(fd, size)
        if payload and not mutated:
            with sidecar.open("r+b") as fh:
                fh.write(replacement)
                fh.flush()
                os.fsync(fh.fileno())
            mutated = True
        return payload

    monkeypatch.setattr(_assets.os, "read", mutate_after_first_read)

    assert _assets._source_provenance(source) == {}
    assert mutated


@pytest.mark.parametrize(
    "value",
    ["-1", "inf", "not-a-number", "1e10000", "1e10000000"],
)
def test_invalid_total_image_artifact_budget_fails_closed(
    tmp_path,
    monkeypatch,
    value,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_TOTAL_MB", value)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors == [{
        "error": "invalid PAPERCONAN_MAX_IMAGE_TOTAL_MB limit",
    }]
    assert not (output / "images").exists()


def test_total_image_artifact_budget_removes_rejected_staging(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_TOTAL_MB", "0")

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert len(errors) == 1
    assert "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in errors[0]["error"]
    assert "budget exhausted" in errors[0]["error"]
    assert not list((output / "images").rglob("*.*"))


def test_total_image_artifact_budget_credits_rerun_replacements(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    first, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    total = sum(
        (output / first[0][key]).stat().st_size
        for key in ("path", "preview_path")
    )
    monkeypatch.setenv(
        "PAPERCONAN_MAX_IMAGE_TOTAL_MB",
        str(total / (1024 * 1024)),
    )
    _image(source / "FigB.png", color=(180, 60, 20))

    second, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert second == first
    assert len(rerun_errors) == 1
    assert rerun_errors[0]["file"] == "FigB.png"
    assert "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in rerun_errors[0]["error"]
    assert "budget exhausted" in rerun_errors[0]["error"]


def test_asset_budget_fresh_accounting_counts_visible_insertion(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    baseline = tmp_path / "baseline"
    baseline_assets, baseline_errors = _assets.prepare_image_assets(
        str(source),
        str(baseline),
    )
    assert baseline_errors == []
    pair_size = sum(
        (baseline / baseline_assets[0][key]).stat().st_size
        for key in ("path", "preview_path")
    )
    output = tmp_path / "audit"
    (output / "images").mkdir(parents=True)
    budget = _assets.ImageArtifactBudget(pair_size)
    budget.initialize_from_root(output)
    (output / "images" / "external.bin").write_bytes(b"x")

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(output),
        artifact_budget=budget,
    )

    assert assets == []
    assert any("PAPERCONAN_MAX_IMAGE_TOTAL_MB" in item["error"] for item in errors)
    assert budget.used_bytes == 1
    assert not list((output / "images" / "native").glob("img-*"))
    assert not list((output / "images" / "preview").glob("img-*"))


def test_asset_pair_budget_coordinates_concurrent_paperconan_writers(
    tmp_path,
    monkeypatch,
):
    sources = []
    pair_sizes = []
    for index, color in enumerate(((20, 90, 180), (180, 60, 20))):
        source = tmp_path / f"source-{index}"
        source.mkdir()
        _image(source / f"Fig{index}.png", color=color)
        baseline = tmp_path / f"baseline-{index}"
        assets, errors = _assets.prepare_image_assets(str(source), str(baseline))
        assert errors == []
        pair_sizes.append(sum(
            (baseline / assets[0][key]).stat().st_size
            for key in ("path", "preview_path")
        ))
        sources.append(source)
    cap = max(pair_sizes)
    output = tmp_path / "shared"
    first_inside = threading.Event()
    release_first = threading.Event()
    second_inside = threading.Event()
    original_temp_path = _assets._asset_temp_path
    local = threading.local()

    def controlled_temp_path(directory_fd, *, suffix):
        calls = getattr(local, "calls", 0)
        local.calls = calls + 1
        if calls == 0 and threading.current_thread().name == "asset-first":
            first_inside.set()
            assert release_first.wait(5)
        elif calls == 0 and threading.current_thread().name == "asset-second":
            second_inside.set()
        return original_temp_path(directory_fd, suffix=suffix)

    monkeypatch.setattr(_assets, "_asset_temp_path", controlled_temp_path)
    results = {}

    def run(name, source):
        results[name] = _assets.prepare_image_assets(
            str(source),
            str(output),
            artifact_budget=_assets.ImageArtifactBudget(cap),
        )

    first = threading.Thread(
        target=run,
        args=("first", sources[0]),
        name="asset-first",
    )
    second = threading.Thread(
        target=run,
        args=("second", sources[1]),
        name="asset-second",
    )
    first.start()
    assert first_inside.wait(5)
    second.start()
    second_entered_while_first_active = second_inside.wait(0.2)
    release_first.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not second_entered_while_first_active
    successful = [name for name, (assets, _) in results.items() if assets]
    rejected = [name for name, (_, errors) in results.items() if errors]
    assert len(successful) == 1
    assert len(rejected) == 1
    assert any(
        "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in item["error"]
        for item in results[rejected[0]][1]
    )
    visible_bytes = sum(
        path.stat().st_size
        for path in (output / "images").rglob("*")
        if path.is_file()
    )
    assert visible_bytes <= cap


def test_prepare_image_assets_renders_pdf_pages(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    pdf = Path("tests/fixtures/supp_table.pdf")
    (source / "supp.pdf").write_bytes(pdf.read_bytes())
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    pages = [a for a in assets if a["source_type"] == "pdf_page"]
    assert errors == []
    assert pages
    assert pages[0]["parent_file"] == "supp.pdf"
    assert pages[0]["page"] == 1
    assert pages[0]["render_dpi"] == 200


def test_prepare_image_assets_retains_raster_when_pdf_renderer_is_unavailable(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    (source / "supp.pdf").write_bytes(
        Path("tests/fixtures/supp_table.pdf").read_bytes()
    )

    def unavailable_pdf_renderer(*, render_pdf, diagnostics):
        if render_pdf:
            raise ImageDependencyError("renderer unavailable " + "x" * 2000)

    monkeypatch.setattr(
        _assets,
        "preflight_image_dependencies",
        unavailable_pdf_renderer,
    )

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert [asset["file"] for asset in assets] == ["FigA.png"]
    assert len(errors) == 1
    assert errors[0]["file"] == "supp.pdf"
    assert errors[0]["error"].startswith("PDF image rendering unavailable:")
    assert len(errors[0]["error"]) <= 550


def test_pdf_render_uses_stable_open_source_when_path_is_replaced(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    pdf = source / "supp.pdf"
    original_payload = b"original-pdf-payload"
    replacement_payload = b"replacement-pdf-payload"
    pdf.write_bytes(original_payload)
    displaced = tmp_path / "displaced.pdf"
    events = {"consumed": None, "rendered": 0, "document_closed": 0}

    class StableImage:
        def __init__(self):
            self.image = PIL.new("RGB", (12, 8), (20, 30, 40))

        def save(self, destination, format=None):
            self.image.save(destination, format=format)

        def close(self):
            self.image.close()

    class StableBitmap:
        def to_pil(self):
            return StableImage()

        def close(self):
            pass

    class StablePage:
        def get_size(self):
            return 72, 72

        def render(self, scale):
            events["rendered"] += 1
            return StableBitmap()

        def close(self):
            pass

    class StableDocument:
        def __init__(self, pdf_input):
            pdf.rename(displaced)
            pdf.write_bytes(replacement_payload)
            if hasattr(pdf_input, "read"):
                pdf_input.seek(0)
                events["consumed"] = pdf_input.read()
            else:
                events["consumed"] = Path(pdf_input).read_bytes()

        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return StablePage()

        def close(self):
            events["document_closed"] += 1

    monkeypatch.setitem(
        sys.modules,
        "pypdfium2",
        types.SimpleNamespace(PdfDocument=StableDocument),
    )

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert errors == []
    assert len(assets) == 1
    assert events == {
        "consumed": original_payload,
        "rendered": 1,
        "document_closed": 1,
    }
    assert pdf.read_bytes() == replacement_payload


def test_pdf_asset_limit_stops_later_pages_and_closes_resources(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    events = _stub_pdfium(monkeypatch, [(72, 72), (72, 72), (72, 72)])
    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 1)

    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))

    assert len(assets) == 1
    assert any("PAPERCONAN_MAX_IMAGE_ASSETS" in item["error"] for item in errors)
    assert events == {
        "rendered": [1],
        "pil_closed": [1],
        "bitmap_closed": [1],
        "page_closed": [1],
        "document_closed": 1,
    }


def test_pdf_pixel_limit_is_checked_before_render_and_resources_close(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    events = _stub_pdfium(monkeypatch, [(1000, 1000)])
    monkeypatch.setattr(_assets, "_MAX_IMAGE_PIXELS", 100)

    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))

    assert assets == []
    assert any("PAPERCONAN_MAX_IMAGE_PIXELS" in item["error"] for item in errors)
    assert events == {
        "rendered": [],
        "pil_closed": [],
        "bitmap_closed": [],
        "page_closed": [1],
        "document_closed": 1,
    }


def test_pdf_rendered_temp_is_removed_after_each_registration(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    _stub_pdfium(monkeypatch, [(72, 72), (72, 72), (72, 72)])
    output = tmp_path / "audit"
    observed_temp_dirs = []
    observed_temp_files = []
    real_record_image = _assets._record_image
    synthetic_digest = "a" * 64

    def observe_and_hash(_file_fd):
        temp_dirs = list(output.glob(".paperconan-rendered-*"))
        assert len(temp_dirs) == 1
        temp_dir = temp_dirs[0]
        opened = _assets.os.fstat(_file_fd)
        staged_files = list(temp_dir.glob("*.png"))
        if not any(
            (current := staged.stat()).st_dev == opened.st_dev
            and current.st_ino == opened.st_ino
            for staged in staged_files
        ):
            return synthetic_digest
        observed_temp_dirs.append(temp_dir)
        observed_temp_files.append(
            sorted(item.name for item in staged_files)
        )
        if staged_files[0].name == "supp.p3.png":
            return "b" * 64
        return synthetic_digest

    def reject_third_page(path, *args, **kwargs):
        if path.name == "supp.p3.png":
            raise ValueError("synthetic page rejection")
        return real_record_image(path, *args, **kwargs)

    monkeypatch.setattr(_assets, "_sha256_fd", observe_and_hash)
    monkeypatch.setattr(_assets, "_record_image", reject_third_page)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert observed_temp_files == [
        ["supp.p1.png"],
        ["supp.p2.png"],
        ["supp.p3.png"],
    ]
    assert len(assets) == 1
    assert assets[0]["source_files"] == ["supp.p1.png", "supp.p2.png"]
    assert errors == [{
        "file": "supp.p3.png",
        "error": "synthetic page rejection",
    }]
    assert len(set(observed_temp_dirs)) == 1
    temp_dir = observed_temp_dirs[0]
    assert temp_dir.parent == output
    assert temp_dir.name.startswith(".paperconan-rendered-")
    assert not temp_dir.exists()
    assert not list(output.glob(".paperconan-rendered-*"))
    with PIL.open(output / assets[0]["path"]) as native:
        assert native.size == (12, 8)
    with PIL.open(output / assets[0]["preview_path"]) as preview:
        assert preview.size == (12, 8)


def test_pdf_staging_does_not_follow_or_delete_images_symlink(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    _stub_pdfium(monkeypatch, [(72, 72)])
    output = tmp_path / "audit"
    output.mkdir()
    outside = tmp_path / "outside"
    rendered = outside / ".rendered"
    rendered.mkdir(parents=True)
    sentinel = rendered / "sentinel.txt"
    sentinel.write_text("outside-sentinel", encoding="utf-8")
    (output / "images").symlink_to(outside, target_is_directory=True)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors
    assert "outside artifact root" in errors[0]["error"]
    assert (output / "images").is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "outside-sentinel"
    assert sorted(path.relative_to(outside) for path in outside.rglob("*")) == [
        Path(".rendered"),
        Path(".rendered/sentinel.txt"),
    ]
    assert not list(output.glob(".paperconan-rendered-*"))


def test_pdf_render_staging_respects_total_image_artifact_budget(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    events = _stub_pdfium(monkeypatch, [(72, 72)])
    output = tmp_path / "audit"
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_TOTAL_MB", "0")

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert len(errors) == 1
    assert errors[0]["file"] == "supp.pdf"
    assert "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in errors[0]["error"]
    assert events["rendered"] == []
    assert not list(output.glob(".paperconan-rendered-*"))
    assert not list((output / "images").rglob("*.*"))


def test_pdf_render_staging_is_created_under_pinned_root_fd(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    _stub_pdfium(monkeypatch, [(72, 72)])
    output = tmp_path / "audit"
    real_mkdir = _assets.os.mkdir
    staging_dir_fds = []

    def observe_mkdir(path, mode=0o777, *, dir_fd=None):
        if Path(path).name.startswith(".paperconan-rendered-"):
            staging_dir_fds.append(dir_fd)
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(_assets.os, "mkdir", observe_mkdir)
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            observe_mkdir if function is real_mkdir else function
            for function in _assets.os.supports_dir_fd
        ),
    )

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert errors == []
    assert assets
    assert staging_dir_fds
    assert all(directory_fd is not None for directory_fd in staging_dir_fds)


def test_failed_pdf_pages_consume_scan_wide_attempt_budget(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (source / name).write_bytes(b"synthetic-pdf")
    events = {
        "documents_opened": [],
        "rendered": [],
        "pages_closed": [],
        "documents_closed": [],
    }
    pdf_names = iter(("a.pdf", "b.pdf"))

    class FailingPage:
        def __init__(self, pdf_name):
            self.pdf_name = pdf_name

        def get_size(self):
            return 72, 72

        def render(self, scale):
            events["rendered"].append(self.pdf_name)
            raise ValueError(f"{self.pdf_name} render failed")

        def close(self):
            events["pages_closed"].append(self.pdf_name)

    class FailingDocument:
        def __init__(self, pdf_input):
            assert hasattr(pdf_input, "read")
            self.pdf_name = next(pdf_names)
            events["documents_opened"].append(self.pdf_name)

        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return FailingPage(self.pdf_name)

        def close(self):
            events["documents_closed"].append(self.pdf_name)

    monkeypatch.setitem(
        sys.modules,
        "pypdfium2",
        types.SimpleNamespace(PdfDocument=FailingDocument),
    )
    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 2)

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert assets == []
    assert errors == [
        {"file": "a.pdf", "page": 1, "error": "a.pdf render failed"},
        {"file": "b.pdf", "page": 1, "error": "b.pdf render failed"},
        {
            "file": "c.pdf",
            "page": 1,
            "error": _assets._asset_limit_error(),
        },
    ]
    assert events == {
        "documents_opened": ["a.pdf", "b.pdf"],
        "rendered": ["a.pdf", "b.pdf"],
        "pages_closed": ["a.pdf", "b.pdf"],
        "documents_closed": ["a.pdf", "b.pdf"],
    }


def test_partial_page_cleanup_failure_still_consumes_attempt_budget(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.pdf", "b.pdf"):
        (source / name).write_bytes(b"synthetic-pdf")
    events = {
        "documents_opened": [],
        "rendered": [],
        "pages_closed": [],
        "documents_closed": [],
    }
    pdf_names = iter(("a.pdf",))

    class FailingImage:
        def __init__(self, pdf_name):
            self.pdf_name = pdf_name

        def save(self, destination, format=None):
            destination.write(b"partial-page")
            destination.flush()
            raise ValueError(f"{self.pdf_name} save failed")

        def close(self):
            pass

    class FailingBitmap:
        def __init__(self, pdf_name):
            self.pdf_name = pdf_name

        def to_pil(self):
            return FailingImage(self.pdf_name)

        def close(self):
            pass

    class FailingPage:
        def __init__(self, pdf_name):
            self.pdf_name = pdf_name

        def get_size(self):
            return 72, 72

        def render(self, scale):
            events["rendered"].append(self.pdf_name)
            return FailingBitmap(self.pdf_name)

        def close(self):
            events["pages_closed"].append(self.pdf_name)

    class FailingDocument:
        def __init__(self, pdf_input):
            assert hasattr(pdf_input, "read")
            self.pdf_name = next(pdf_names)
            events["documents_opened"].append(self.pdf_name)

        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return FailingPage(self.pdf_name)

        def close(self):
            events["documents_closed"].append(self.pdf_name)

    real_unlink_at = _assets._unlink_at

    def fail_partial_page_unlink(name, directory_fd):
        if name == "a.p1.png":
            raise OSError("cleanup failed")
        return real_unlink_at(name, directory_fd)

    monkeypatch.setitem(
        sys.modules,
        "pypdfium2",
        types.SimpleNamespace(PdfDocument=FailingDocument),
    )
    monkeypatch.setattr(_assets, "_unlink_at", fail_partial_page_unlink)
    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 1)
    output = tmp_path / "audit"

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors == [
        {
            "file": "a.pdf",
            "page": 1,
            "error": (
                "a.pdf save failed; partial page cleanup failed: cleanup failed"
            ),
        },
        {
            "file": "b.pdf",
            "page": 1,
            "error": _assets._asset_limit_error(),
        },
    ]
    assert events == {
        "documents_opened": ["a.pdf"],
        "rendered": ["a.pdf"],
        "pages_closed": ["a.pdf"],
        "documents_closed": ["a.pdf"],
    }
    assert not list(output.glob(".paperconan-rendered-*"))


def test_message_less_page_exception_is_page_numbered(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    events = {
        "rendered": 0,
        "page_closed": 0,
        "document_closed": 0,
    }

    class MessageLessPage:
        def get_size(self):
            return 72, 72

        def render(self, scale):
            events["rendered"] += 1
            raise ValueError()

        def close(self):
            events["page_closed"] += 1

    class MessageLessDocument:
        def __init__(self, path):
            pass

        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return MessageLessPage()

        def close(self):
            events["document_closed"] += 1

    monkeypatch.setitem(
        sys.modules,
        "pypdfium2",
        types.SimpleNamespace(PdfDocument=MessageLessDocument),
    )

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert assets == []
    assert errors == [{
        "file": "supp.pdf",
        "page": 1,
        "error": "ValueError",
    }]
    assert events == {
        "rendered": 1,
        "page_closed": 1,
        "document_closed": 1,
    }


def test_image_asset_limit_is_explicit(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "A.png")
    _image(source / "B.png", color=(1, 2, 3))
    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 1)
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    assert len(assets) == 1
    assert any("PAPERCONAN_MAX_IMAGE_ASSETS" in e["error"] for e in errors)


def test_duplicate_is_merged_after_unique_asset_limit_is_reached(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "A.png")
    (source / "B.png").write_bytes((source / "A.png").read_bytes())
    _image(source / "C.png", color=(1, 2, 3))
    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 1)

    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))

    assert len(assets) == 1
    assert assets[0]["source_files"] == ["A.png", "B.png"]
    assert [item["file"] for item in errors] == ["C.png"]


def test_multiframe_image_is_recorded_in_errors_not_silently_truncated(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = PIL.new("RGB", (20, 20), (1, 2, 3))
    second = PIL.new("RGB", (20, 20), (4, 5, 6))
    first.save(source / "stack.tiff", save_all=True, append_images=[second])
    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))
    assert assets == []
    assert any("multi-frame images are not silently truncated" in e["error"] for e in errors)


def test_case_tied_filenames_have_deterministic_metadata_and_error_order(
    tmp_path,
    monkeypatch,
):
    duplicate_source = tmp_path / "duplicates"
    duplicate_source.mkdir()
    _image(duplicate_source / "A.png")
    (duplicate_source / "a.PNG").write_bytes(
        (duplicate_source / "A.png").read_bytes()
    )
    error_source = tmp_path / "errors"
    error_source.mkdir()
    _image(error_source / "A.png")
    _image(error_source / "a.PNG", color=(1, 2, 3))
    real_iterdir = Path.iterdir

    def reverse_case_ties(path):
        if path == duplicate_source:
            return iter([path / "a.PNG", path / "A.png"])
        if path == error_source:
            return iter([path / "a.PNG", path / "A.png"])
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", reverse_case_ties)
    assets, errors = _assets.prepare_image_assets(
        str(duplicate_source),
        str(tmp_path / "duplicate-audit"),
    )
    assert errors == []
    assert assets[0]["file"] == "A.png"
    assert assets[0]["source_files"] == ["A.png", "a.PNG"]

    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 0)
    assets, errors = _assets.prepare_image_assets(
        str(error_source),
        str(tmp_path / "error-audit"),
    )
    assert assets == []
    assert [item["file"] for item in errors] == ["A.png", "a.PNG"]


def test_exif_orientation_changes_preview_dimensions_only(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = PIL.new("RGB", (40, 20), (20, 90, 180))
    exif = PIL.Exif()
    exif[274] = 6
    original.save(source / "oriented.jpg", exif=exif)
    source_bytes = (source / "oriented.jpg").read_bytes()

    assets, errors = _assets.prepare_image_assets(str(source), str(tmp_path / "audit"))

    assert errors == []
    asset = assets[0]
    assert asset["width"] == 40
    assert asset["height"] == 20
    assert asset["exif_orientation"] == 6
    assert (tmp_path / "audit" / asset["path"]).read_bytes() == source_bytes
    with PIL.open(tmp_path / "audit" / asset["preview_path"]) as preview:
        assert preview.size == (20, 40)


def test_asset_publication_uses_same_directory_temps_and_atomic_no_replace(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    links = []
    real_link = os.link

    def observe_link(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        src_name = Path(src).name
        dst_name = Path(dst).name
        if src_name.startswith(".paperconan-image-"):
            assert not dst_name.startswith(".paperconan-image-")
            assert src_dir_fd is not None
            assert src_dir_fd == dst_dir_fd
            assert os.stat(src, dir_fd=src_dir_fd, follow_symlinks=False)
            links.append(dst_name)
        real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(_assets.os, "link", observe_link)
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            observe_link if function is real_link else function
            for function in _assets.os.supports_dir_fd
        ),
    )

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert errors == []
    assert len(links) == 2
    assert set(links) == {
        Path(assets[0]["path"]).name,
        Path(assets[0]["preview_path"]).name,
    }
    assert not list((output / "images").rglob(".paperconan-image-*"))


def test_rerun_retains_stale_native_and_preview_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    native.write_bytes(b"stale-native")
    preview.write_bytes(b"stale-preview")

    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert rerun_assets == []
    assert rerun_errors
    assert "retained existing visible entry" in rerun_errors[0]["error"]
    assert native.read_bytes() == b"stale-native"
    assert preview.read_bytes() == b"stale-preview"


def test_preview_only_mismatch_reports_deterministic_rescan_remediation(
    tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    native_bytes = native.read_bytes()
    preview.write_bytes(b"preview-from-another-encoder")

    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert rerun_assets == []
    assert len(rerun_errors) == 1
    assert native.read_bytes() == native_bytes
    assert preview.read_bytes() == b"preview-from-another-encoder"
    error = rerun_errors[0]["error"]
    assert (
        f"delete {assets[0]['preview_path']} and rescan"
        in error
    )


def test_rerun_retains_final_asset_symlinks_without_touching_targets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    native_target = tmp_path / "native-target"
    preview_target = tmp_path / "preview-target"
    native_target.write_bytes(b"native-sentinel")
    preview_target.write_bytes(b"preview-sentinel")
    native.unlink()
    preview.unlink()
    native.symlink_to(native_target)
    preview.symlink_to(preview_target)

    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert rerun_assets == []
    assert rerun_errors
    assert "retained existing visible entry" in rerun_errors[0]["error"]
    assert native_target.read_bytes() == b"native-sentinel"
    assert preview_target.read_bytes() == b"preview-sentinel"
    assert native.is_symlink()
    assert preview.is_symlink()
    assert native.readlink() == native_target
    assert preview.readlink() == preview_target


@pytest.mark.parametrize(
    "unsafe_dir",
    ["images", "images/native", "images/preview"],
)
def test_output_asset_directory_symlink_outside_artifact_root_is_rejected(
    tmp_path,
    unsafe_dir,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    unsafe_path = output / unsafe_dir
    unsafe_path.parent.mkdir(parents=True, exist_ok=True)
    unsafe_path.symlink_to(outside, target_is_directory=True)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors
    assert "outside artifact root" in errors[0]["error"]
    assert list(outside.iterdir()) == []


def test_output_artifact_root_symlink_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    outside = tmp_path / "outside"
    outside.mkdir()
    output = tmp_path / "audit"
    output.symlink_to(outside, target_is_directory=True)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors
    assert "artifact root" in errors[0]["error"]
    assert list(outside.iterdir()) == []


def test_asset_preparation_pins_root_across_all_assets(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    _image(source / "FigB.png", color=(180, 60, 20))
    output = tmp_path / "audit"
    outside = tmp_path / "outside"
    outside.mkdir()
    displaced = tmp_path / "displaced-audit"
    real_record_image = _assets._record_image
    recorded = 0

    def record_then_swap(*args, **kwargs):
        nonlocal recorded
        result = real_record_image(*args, **kwargs)
        recorded += 1
        if recorded == 1:
            output.rename(displaced)
            output.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(_assets, "_record_image", record_then_swap)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets
    assert errors
    assert any("artifact root" in item["error"] for item in errors)
    assert list(outside.iterdir()) == []
    assert (displaced / assets[0]["path"]).is_file()


def test_failed_source_validation_leaves_no_published_asset_pair(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "broken.png").write_bytes(b"not-an-image")
    output = tmp_path / "audit"

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors
    native_dir = output / "images" / "native"
    preview_dir = output / "images" / "preview"
    assert not native_dir.exists() or list(native_dir.iterdir()) == []
    assert not preview_dir.exists() or list(preview_dir.iterdir()) == []


def test_failed_preview_generation_leaves_no_published_asset_pair(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"

    def reject_preview(*args, **kwargs):
        raise ValueError("synthetic preview rejection")

    monkeypatch.setattr(_assets, "_write_preview", reject_preview)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors == [{
        "file": "FigA.png",
        "error": "synthetic preview rejection",
    }]
    native_dir = output / "images" / "native"
    preview_dir = output / "images" / "preview"
    assert not native_dir.exists() or list(native_dir.iterdir()) == []
    assert not preview_dir.exists() or list(preview_dir.iterdir()) == []


@pytest.mark.parametrize("swapped_dir", ["native", "preview"])
def test_directory_swap_after_validation_does_not_publish_outside_root(
    tmp_path,
    monkeypatch,
    swapped_dir,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_write_preview = _assets._write_preview

    def write_preview_then_swap(image, destination, *args, **kwargs):
        real_write_preview(image, destination, *args, **kwargs)
        directory = output / "images" / swapped_dir
        moved = outside / swapped_dir
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)

    monkeypatch.setattr(_assets, "_write_preview", write_preview_then_swap)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors
    assert "changed during asset publication" in errors[0]["error"]
    assert list((outside / swapped_dir).iterdir()) == []


def test_staging_path_swap_does_not_publish_symlink_or_touch_target(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    outside_target = tmp_path / "outside-target"
    outside_target.write_bytes(b"outside-sentinel")
    real_write_preview = _assets._write_preview

    def write_preview_then_swap_staging(image, destination, *args, **kwargs):
        real_write_preview(image, destination, *args, **kwargs)
        native_dir = output / "images" / "native"
        staged_native = next(native_dir.glob(".paperconan-image-*.png"))
        staged_native.unlink()
        staged_native.symlink_to(outside_target)

    monkeypatch.setattr(
        _assets,
        "_write_preview",
        write_preview_then_swap_staging,
    )

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors
    assert "changed during asset publication" in errors[0]["error"]
    assert outside_target.read_bytes() == b"outside-sentinel"
    assert not list((output / "images").rglob("img-*"))


def test_final_path_swap_after_link_is_detected_without_deleting_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "FigA.png"
    _image(image_path)
    digest = _assets._sha256(image_path)
    stem = _assets._asset_id(digest).replace(":", "-")
    native_name = f"{stem}.png"
    outside_target = tmp_path / "outside-target"
    outside_target.write_bytes(b"outside-sentinel")
    real_link = os.link
    swapped = False

    def link_then_swap_final(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal swapped
        real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        if (
            not swapped
            and Path(src).name.startswith(".paperconan-image-")
            and Path(dst).name == native_name
        ):
            swapped = True
            os.unlink(dst, dir_fd=dst_dir_fd)
            os.symlink(outside_target, dst, dir_fd=dst_dir_fd)

    monkeypatch.setattr(_assets.os, "link", link_then_swap_final)
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            link_then_swap_final if function is real_link else function
            for function in _assets.os.supports_dir_fd
        ),
    )
    output = tmp_path / "audit"

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors
    assert "changed during asset publication" in errors[0]["error"]
    assert "retained uncertain visible entry" in errors[0]["error"]
    assert outside_target.read_bytes() == b"outside-sentinel"
    final_native = output / "images" / "native" / native_name
    assert final_native.is_symlink()
    assert final_native.readlink() == outside_target


def test_source_mutation_after_initial_hash_publishes_nothing(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "FigA.png"
    _image(image_path)
    original_digest = _assets._sha256(image_path)
    original_stat = image_path.stat()
    original_identity = (original_stat.st_dev, original_stat.st_ino)
    real_sha256_fd = _assets._sha256_fd
    mutated = False

    def hash_then_mutate(fd):
        nonlocal mutated
        digest = real_sha256_fd(fd)
        current = os.fstat(fd)
        if (
            not mutated
            and (current.st_dev, current.st_ino) == original_identity
        ):
            mutated = True
            _image(image_path, color=(1, 2, 3))
        return digest

    monkeypatch.setattr(_assets, "_sha256_fd", hash_then_mutate)
    output = tmp_path / "audit"

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert original_digest != _assets._sha256(image_path)
    assert assets == []
    assert errors
    assert "source changed while preparing image asset" in errors[0]["error"]
    assert not list((output / "images").rglob("img-*"))


def test_local_image_stat_hash_replacement_uses_one_stable_source_handle(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "FigA.png"
    _image(image_path)
    original_bytes = image_path.read_bytes()
    original_stat = image_path.stat()
    original_identity = (original_stat.st_dev, original_stat.st_ino)
    original_digest = _assets._sha256(image_path)
    replacement_bytes = b"replacement" * (len(original_bytes) + 1)
    replacement_path = tmp_path / "replacement.png"
    replacement_path.write_bytes(replacement_bytes)
    monkeypatch.setattr(_assets, "_MAX_IMAGE_BYTES", len(original_bytes))
    real_is_file = Path.is_file
    real_fstat = os.fstat
    real_sha256 = _assets._sha256
    real_image_open = PIL.open
    image_is_file_calls = 0
    source_fstat_calls = 0
    swapped = False
    pathname_hashed = False
    decoded_identities = []

    def replace_after_is_file(path):
        nonlocal image_is_file_calls, swapped
        is_file = real_is_file(path)
        if path == image_path:
            image_is_file_calls += 1
            if image_is_file_calls == 2 and not swapped:
                replacement_path.replace(image_path)
                swapped = True
        return is_file

    def replace_after_descriptor_size(fd):
        nonlocal source_fstat_calls, swapped
        current = real_fstat(fd)
        if (current.st_dev, current.st_ino) == original_identity:
            source_fstat_calls += 1
            if source_fstat_calls == 2 and not swapped:
                replacement_path.replace(image_path)
                swapped = True
        return current

    def track_pathname_hash(path):
        nonlocal pathname_hashed
        pathname_hashed = True
        return real_sha256(path)

    def track_decode(fp, *args, **kwargs):
        if hasattr(fp, "fileno"):
            current = real_fstat(fp.fileno())
            decoded_identities.append((current.st_dev, current.st_ino))
        return real_image_open(fp, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", replace_after_is_file)
    monkeypatch.setattr(_assets.os, "fstat", replace_after_descriptor_size)
    monkeypatch.setattr(_assets, "_sha256", track_pathname_hash)
    monkeypatch.setattr(PIL, "open", track_decode)
    output = tmp_path / "audit"

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert image_is_file_calls == 1
    assert swapped
    assert not pathname_hashed
    assert errors == []
    assert len(assets) == 1
    assert assets[0]["sha256"] == original_digest
    assert len(decoded_identities) == 1
    assert decoded_identities[0] != original_identity
    assert (output / assets[0]["path"]).read_bytes() == original_bytes
    assert image_path.read_bytes() == replacement_bytes


def test_local_image_decode_stays_bound_to_verified_native_staging(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "FigA.png"
    _image(image_path, size=(80, 60), color=(20, 90, 180))
    original_bytes = image_path.read_bytes()
    original_digest = _assets._sha256(image_path)
    original_identity = (
        image_path.stat().st_dev,
        image_path.stat().st_ino,
    )
    replacement_path = tmp_path / "replacement.png"
    _image(replacement_path, size=(31, 17), color=(190, 30, 40))
    replacement_bytes = replacement_path.read_bytes()
    real_sha256_fd = _assets._sha256_fd
    source_mutated = False

    def hash_staging_then_mutate_source(fd):
        nonlocal source_mutated
        digest = real_sha256_fd(fd)
        current = os.fstat(fd)
        if (
            not source_mutated
            and (current.st_dev, current.st_ino) != original_identity
            and digest == original_digest
        ):
            with image_path.open("r+b") as source_fh:
                source_fh.seek(0)
                source_fh.write(replacement_bytes)
                source_fh.truncate()
            source_mutated = True
        return digest

    monkeypatch.setattr(_assets, "_sha256_fd", hash_staging_then_mutate_source)
    output = tmp_path / "audit"

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert source_mutated
    assert image_path.read_bytes() == replacement_bytes
    assert errors == []
    assert len(assets) == 1
    asset = assets[0]
    assert asset["sha256"] == original_digest
    assert (asset["width"], asset["height"]) == (80, 60)
    assert (output / asset["path"]).read_bytes() == original_bytes
    with PIL.open(output / asset["preview_path"]) as preview:
        assert preview.size == (80, 60)


def test_second_temp_allocation_failure_cleans_first_temp(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    real_asset_temp_path = _assets._asset_temp_path
    calls = 0

    def fail_second_temp(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic temp allocation failure")
        return real_asset_temp_path(*args, **kwargs)

    monkeypatch.setattr(_assets, "_asset_temp_path", fail_second_temp)

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors == [{
        "file": "FigA.png",
        "error": "synthetic temp allocation failure",
    }]
    assert not list((output / "images").rglob(".paperconan-image-*"))
    assert not list((output / "images").rglob("img-*"))


def test_second_no_replace_install_failure_retains_first_fresh_asset(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    image_path = source / "FigA.png"
    _image(image_path)
    digest = _assets._sha256(image_path)
    stem = _assets._asset_id(digest).replace(":", "-")
    preview_name = f"{stem}.jpg"
    real_link = os.link
    failed = False

    def fail_preview_link(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal failed
        if not failed and Path(dst).name == preview_name:
            failed = True
            raise OSError("synthetic preview link failure")
        return real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(_assets.os, "link", fail_preview_link)
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            fail_preview_link if function is real_link else function
            for function in _assets.os.supports_dir_fd
        ),
    )
    output = tmp_path / "audit"
    budget = _assets.ImageArtifactBudget(1024 * 1024 * 1024)

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(output),
        artifact_budget=budget,
    )

    assert assets == []
    assert errors
    assert "synthetic preview link failure" in errors[0]["error"]
    assert "retained uncertain visible entry" in errors[0]["error"]
    assert os.path.isfile(output / "images" / "native" / f"{stem}.png")
    assert not os.path.lexists(output / "images" / "preview" / preview_name)
    retained_bytes = (
        output / "images" / "native" / f"{stem}.png"
    ).stat().st_size
    assert budget.used_bytes == retained_bytes

    budget.max_bytes = retained_bytes
    _image(source / "FigB.png", color=(180, 60, 20))
    later_assets, later_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
        artifact_budget=budget,
    )

    assert later_assets == []
    assert later_errors
    assert all(
        "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in item["error"]
        for item in later_errors
    )
    assert budget.used_bytes == retained_bytes
    assert not any(
        path.name.startswith("img-") and path.name != f"{stem}.png"
        for path in (output / "images").rglob("*")
    )


def test_rerun_retains_prior_symlink_pair_without_publication(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    native_target = tmp_path / "native-target"
    preview_target = tmp_path / "preview-target"
    native_target.write_bytes(b"native-sentinel")
    preview_target.write_bytes(b"preview-sentinel")
    native.unlink()
    preview.unlink()
    native.symlink_to(native_target)
    preview.symlink_to(preview_target)
    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert rerun_assets == []
    assert rerun_errors
    error = rerun_errors[0]["error"]
    assert "retained existing visible entry" in error
    assert native.is_symlink()
    assert preview.is_symlink()
    assert native.readlink() == native_target
    assert preview.readlink() == preview_target
    assert native_target.read_bytes() == b"native-sentinel"
    assert preview_target.read_bytes() == b"preview-sentinel"
    assert not list((output / "images").rglob(".paperconan-image-*.backup"))


def test_mismatched_pair_publication_retains_existing_entries(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    native_target = tmp_path / "native-target"
    native_target.write_bytes(b"native-sentinel")
    native.unlink()
    native.symlink_to(native_target)
    preview.write_bytes(b"preview-sentinel")
    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert rerun_assets == []
    assert rerun_errors
    error = rerun_errors[0]["error"]
    assert "publication incomplete" in error
    assert "retained existing visible entry" in error
    assert native_target.read_bytes() == b"native-sentinel"
    assert native.is_symlink()
    assert native.readlink() == native_target
    assert preview.read_bytes() == b"preview-sentinel"
    assert not list((output / "images").rglob(".paperconan-image-*.backup"))


def test_preview_failure_cleans_preexisting_partial_final_pair(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    preview.unlink()
    native.write_bytes(b"partial-native")

    def reject_preview(*args, **kwargs):
        raise ValueError("synthetic preview rejection")

    monkeypatch.setattr(_assets, "_write_preview", reject_preview)

    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert rerun_assets == []
    assert rerun_errors == [{
        "file": "FigA.png",
        "error": "synthetic preview rejection",
    }]
    assert native.read_bytes() == b"partial-native"
    assert not os.path.lexists(preview)


def test_failed_preview_install_retains_concurrent_native_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    native.unlink()
    preview.unlink()
    concurrent_bytes = b"concurrent native replacement"
    real_link = os.link
    native_replaced = False

    def link_native_then_fail_preview(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal native_replaced
        src_name = Path(src).name
        dst_name = Path(dst).name
        if (
            src_name.startswith(".paperconan-image-")
            and dst_name == native.name
        ):
            real_link(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            os.unlink(dst, dir_fd=dst_dir_fd)
            replacement_fd = os.open(
                dst,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dst_dir_fd,
            )
            try:
                os.write(replacement_fd, concurrent_bytes)
            finally:
                os.close(replacement_fd)
            native_replaced = True
            return
        if (
            native_replaced
            and src_name.startswith(".paperconan-image-")
            and dst_name == preview.name
        ):
            raise OSError("synthetic preview install failure")
        return real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(_assets.os, "link", link_native_then_fail_preview)
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            link_native_then_fail_preview if function is real_link else function
            for function in _assets.os.supports_dir_fd
        ),
    )

    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert native_replaced
    assert rerun_assets == []
    assert native.read_bytes() == concurrent_bytes
    assert rerun_errors
    error = rerun_errors[0]["error"]
    assert "retained uncertain visible entry" in error
    assert f"images/native/{native.name}" in error
    assert not list((output / "images").rglob(".paperconan-image-*.backup"))


def test_final_preview_replacement_survives_publication_verification_failure(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    preview = output / assets[0]["preview_path"]
    preview.unlink()
    concurrent_target = tmp_path / "concurrent-preview.jpg"
    concurrent_target.write_bytes(b"concurrent preview replacement")
    real_link = os.link
    preview_replaced = False

    def link_preview_then_replace_with_symlink(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal preview_replaced
        real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        if (
            not preview_replaced
            and Path(src).name.startswith(".paperconan-image-")
            and Path(dst).name == preview.name
        ):
            os.unlink(dst, dir_fd=dst_dir_fd)
            os.symlink(concurrent_target, dst, dir_fd=dst_dir_fd)
            preview_replaced = True

    monkeypatch.setattr(
        _assets.os,
        "link",
        link_preview_then_replace_with_symlink,
    )
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            link_preview_then_replace_with_symlink
            if function is real_link
            else function
            for function in _assets.os.supports_dir_fd
        ),
    )

    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert preview_replaced
    assert rerun_assets == []
    assert preview.is_symlink()
    assert preview.readlink() == concurrent_target
    assert concurrent_target.read_bytes() == b"concurrent preview replacement"
    assert rerun_errors
    assert "retained uncertain visible entry" in rerun_errors[0]["error"]
    assert f"images/preview/{preview.name}" in rerun_errors[0]["error"]


def test_successful_asset_rerun_creates_no_recovery_backups(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert rerun_errors == []
    assert rerun_assets == assets
    assert not list((output / "images").rglob(".paperconan-image-*.backup"))


def test_asset_publication_does_not_move_concurrent_final_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    assets, errors = _assets.prepare_image_assets(str(source), str(output))
    assert errors == []
    native = output / assets[0]["path"]
    preview = output / assets[0]["preview_path"]
    native.unlink()
    concurrent_bytes = b"concurrent native replacement"
    real_link = os.link
    replacement_installed = False

    def create_final_before_link(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal replacement_installed
        if (
            not replacement_installed
            and Path(dst).name == native.name
        ):
            replacement_fd = os.open(
                dst,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dst_dir_fd,
            )
            try:
                os.write(replacement_fd, concurrent_bytes)
            finally:
                os.close(replacement_fd)
            replacement_installed = True
        return real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(
        _assets.os,
        "link",
        create_final_before_link,
    )
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            create_final_before_link if function is real_link else function
            for function in _assets.os.supports_dir_fd
        ),
    )

    rerun_assets, rerun_errors = _assets.prepare_image_assets(
        str(source),
        str(output),
    )

    assert replacement_installed
    assert rerun_assets == []
    assert rerun_errors
    assert "retained existing visible entry" in rerun_errors[0]["error"]
    assert native.read_bytes() == concurrent_bytes
    assert preview.is_file()


def test_secure_dirfd_capability_unavailable_is_explicit(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    monkeypatch.setattr(_assets.os, "supports_dir_fd", frozenset())

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert len(errors) == 1
    assert "secure image asset publication is unavailable" in errors[0]["error"]
    assert "dir_fd" in errors[0]["error"]
    assert not (output / "images").exists()


def test_secure_dirfd_runtime_error_includes_sanitized_context(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")
    output = tmp_path / "audit"
    output.mkdir()
    original_open = _assets.os.open

    def reject_root_open(path, flags, *args, **kwargs):
        if Path(path) == output:
            raise NotImplementedError(
                "synthetic runtime failure /private/sensitive token=top-secret"
            )
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(_assets.os, "open", reject_root_open)
    monkeypatch.setattr(
        _assets.os,
        "supports_dir_fd",
        frozenset(
            reject_root_open if function is original_open else function
            for function in _assets.os.supports_dir_fd
        ),
    )

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert len(errors) == 1
    error = errors[0]["error"]
    assert "NotImplementedError" in error
    assert "synthetic runtime failure" in error
    assert "/private/sensitive" not in error
    assert "top-secret" not in error


def test_oversized_pdf_is_rejected_before_pdfium_open(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    events = _stub_pdfium(monkeypatch, [])
    monkeypatch.setattr(_assets, "_MAX_IMAGE_BYTES", 1)
    output = tmp_path / "audit"

    assets, errors = _assets.prepare_image_assets(str(source), str(output))

    assert assets == []
    assert errors == [{
        "file": "supp.pdf",
        "error": "supp.pdf: exceeds PAPERCONAN_MAX_IMAGE_MB=100",
    }]
    assert events["document_closed"] == 0


def test_pdf_page_attempt_limit_counts_duplicate_pages(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "supp.pdf").write_bytes(b"synthetic-pdf")
    events = _stub_pdfium(
        monkeypatch,
        [(72, 72), (72, 72), (72, 72), (72, 72)],
    )
    monkeypatch.setattr(_assets, "_MAX_IMAGE_ASSETS", 2)
    real_sha256 = _assets._sha256

    def duplicate_page_hash(path):
        if path.parent.name.startswith(".paperconan-rendered-"):
            return "a" * 64
        return real_sha256(path)

    monkeypatch.setattr(_assets, "_sha256", duplicate_page_hash)
    monkeypatch.setattr(_assets, "_sha256_fd", lambda _fd: "a" * 64)

    assets, errors = _assets.prepare_image_assets(
        str(source),
        str(tmp_path / "audit"),
    )

    assert len(assets) == 1
    assert assets[0]["source_files"] == ["supp.p1.png", "supp.p2.png"]
    assert errors == [{
        "file": "supp.pdf",
        "page": 3,
        "error": _assets._asset_limit_error(),
    }]
    assert events["rendered"] == [1, 2]
    assert events["page_closed"] == [1, 2]
    assert events["document_closed"] == 1


def test_image_only_scan_requires_opt_in_and_preserves_numeric_file_count(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FigA.png")

    with pytest.raises(PaperconanInputError):
        scan_dir(str(source), str(tmp_path / "default"), write_html=False)

    scan = scan_dir(
        str(source),
        str(tmp_path / "images"),
        write_html=False,
        images=True,
    )
    assert scan["n_files"] == 0
    assert scan["n_image_source_files"] == 1
    assert scan["n_image_assets"] == 1
    assert scan["image_findings"] == []


def test_uppercase_image_only_scan_is_admitted_with_images_enabled(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _image(source / "FIG.PNG")

    scan = scan_dir(
        str(source),
        str(tmp_path / "audit"),
        write_html=False,
        images=True,
    )

    assert scan["n_files"] == 0
    assert scan["n_image_source_files"] == 1
    assert scan["n_image_assets"] == 1


def test_cli_rejects_image_diagnostics_without_images(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        _audit.sys,
        "argv",
        ["paperconan", str(tmp_path), "--image-diagnostics"],
    )
    with pytest.raises(SystemExit) as exc:
        _audit.main()
    assert exc.value.code == 2
    assert "--image-diagnostics requires --images" in capsys.readouterr().err

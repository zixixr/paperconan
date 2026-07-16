from __future__ import annotations

import base64
import hashlib
import io
import os
import stat
import threading
import weakref
from pathlib import Path

import numpy as np
import pytest

Image = pytest.importorskip("PIL.Image")
pytest.importorskip("cv2")

from paperconan.image._assets import prepare_image_assets
from paperconan.image import _diagnostics, _evidence
from paperconan.image._budget import ImageArtifactBudget
from paperconan import _html
from paperconan.image._diagnostics import diagnose_image_assets
from paperconan.image._evidence import write_native_pair_evidence


def _two_panel(path: Path):
    left = np.zeros((120, 140, 3), dtype=np.uint8)
    yy, xx = np.indices(left.shape[:2])
    left[:, :, 0] = (xx * 3 + yy * 5) % 255
    left[:, :, 1] = (xx * 7) % 255
    left[:, :, 2] = (yy * 11) % 255
    right = np.fliplr(left)
    canvas = np.full((140, 310, 3), 255, dtype=np.uint8)
    canvas[10:130, 10:150] = left
    canvas[10:130, 160:300] = right
    Image.fromarray(canvas).save(path)


def _asymmetric_panel(seed: int, *, height: int = 180, width: int = 260):
    rng = np.random.default_rng(seed)
    panel = rng.integers(0, 2, size=(height, width), dtype=np.uint8) * 255
    panel[15:55, 20:75] = 40 + seed * 10
    panel[95:160, 150:235] = 180 - seed * 10
    return np.repeat(panel[:, :, None], 3, axis=2)


def _offset_duplicate_figure(path: Path):
    first = _asymmetric_panel(1)
    canvas = np.full((650, 900, 3), 255, dtype=np.uint8)
    canvas[60:240, 60:320] = first
    canvas[50:230, 550:810] = _asymmetric_panel(2)
    canvas[360:540, 40:300] = _asymmetric_panel(3)
    canvas[380:560, 560:820] = first
    Image.fromarray(canvas).save(path)


def _blurred_noise_pair_at_mirrored_offsets():
    cv2 = _diagnostics._cv2()

    def blurred_panel(seed: int):
        rng = np.random.default_rng(seed)
        raw = rng.normal(size=(180, 260)).astype(np.float32)
        blurred = cv2.GaussianBlur(raw, (0, 0), 15)
        scaled = (blurred - blurred.min()) / (blurred.max() - blurred.min())
        gray = (scaled * 180 + 30).astype(np.uint8)
        return np.repeat(gray[:, :, None], 3, axis=2)

    left = np.full((280, 410, 3), 255, dtype=np.uint8)
    right = np.full((280, 410, 3), 255, dtype=np.uint8)
    left[20:200, 20:280] = blurred_panel(132)
    right[80:260, 130:390] = blurred_panel(10_132)
    return left, right


def _diagnostic_asset(root: Path) -> tuple[Path, list[dict]]:
    out = root / "audit"
    native = out / "images" / "native" / "Fig1.png"
    native.parent.mkdir(parents=True)
    Image.new("RGB", (16, 16), "white").save(native)
    return out, [{
        "asset_id": "img:fig1",
        "file": "Fig1.png",
        "path": native.relative_to(out).as_posix(),
        "sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
    }]


def _install_synthetic_panel_grid(monkeypatch, *, rows: int, cols: int):
    patch_std_calls = 0

    class SyntheticPatch:
        def std(self):
            nonlocal patch_std_calls
            patch_std_calls += 1
            return 9.0

    class SyntheticGray:
        shape = (rows * 64, cols * 64)

        def std(self, axis):
            return np.empty(self.shape[axis], dtype=np.float64)

        def __getitem__(self, key):
            return SyntheticPatch()

    class SyntheticCv2:
        COLOR_BGR2GRAY = 0
        IMREAD_COLOR = 1
        IMREAD_IGNORE_ORIENTATION = 2

        def cvtColor(self, image, mode):
            return SyntheticGray()

        def imdecode(self, payload, mode):
            return np.zeros((*SyntheticGray.shape, 3), dtype=np.uint8)

    run_sets = iter([
        [(index * 64 - 1, index * 64 + 1) for index in range(1, rows)],
        [(index * 64 - 1, index * 64 + 1) for index in range(1, cols)],
    ])
    monkeypatch.setattr(_diagnostics, "_cv2", lambda: SyntheticCv2())
    monkeypatch.setattr(_diagnostics, "_uniform_runs", lambda values: next(run_sets))
    return lambda: patch_std_calls


def _raw_pair_preview_item(root: Path, *, size=(10, 10)) -> tuple[dict, list[Path]]:
    native_dir = root / "images" / "native"
    native_dir.mkdir(parents=True)
    paths = [native_dir / "a.png", native_dir / "b.png"]
    Image.new("RGB", size, (255, 0, 0)).save(paths[0])
    Image.new("RGB", size, (0, 255, 0)).save(paths[1])
    assets = [
        {
            "asset_id": f"img:{name}",
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "width": size[0],
            "height": size[1],
        }
        for name, path in zip(("a", "b"), paths)
    ]
    return {
        "image_assets": assets,
        "finding": {
            "regions": [
                {"asset_id": "img:a", "box": (0, 0, *size)},
                {"asset_id": "img:b", "box": (0, 0, *size)},
            ],
        },
    }, paths


def test_raw_pair_preview_rejects_file_size_before_pillow_decode(
    tmp_path,
    monkeypatch,
):
    item, _ = _raw_pair_preview_item(tmp_path)
    budget = _evidence.EvidenceBudget(1024 * 1024)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_MB", "0")
    pillow_called = False
    original_open = Image.open

    def track_pillow_open(fp, *args, **kwargs):
        nonlocal pillow_called
        pillow_called = True
        return original_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", track_pillow_open)

    uri = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert not pillow_called
    assert budget.used_bytes == 0


def test_raw_pair_preview_rejects_full_image_pixels_before_crop(
    tmp_path,
    monkeypatch,
):
    item, _ = _raw_pair_preview_item(tmp_path, size=(11, 10))
    budget = _evidence.EvidenceBudget(1024 * 1024)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", "100")
    crop_called = False
    original_crop = Image.Image.crop

    def track_crop(image, box, *args, **kwargs):
        nonlocal crop_called
        crop_called = True
        return original_crop(image, box, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "crop", track_crop)

    uri = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert not crop_called
    assert budget.used_bytes == 0


def test_raw_pair_preview_reuses_crop_area_validation_before_crop(
    tmp_path,
    monkeypatch,
):
    item, _ = _raw_pair_preview_item(tmp_path, size=(5, 5))
    budget = _evidence.EvidenceBudget(1024 * 1024)
    crop_called = False
    validation_called = False
    original_crop = Image.Image.crop
    original_validate = _evidence._validated_crop_box

    def enforce_smaller_crop_cap(box, *, width, height, max_pixels):
        nonlocal validation_called
        validation_called = True
        return original_validate(
            box,
            width=width,
            height=height,
            max_pixels=24,
        )

    def track_crop(image, box, *args, **kwargs):
        nonlocal crop_called
        crop_called = True
        return original_crop(image, box, *args, **kwargs)

    monkeypatch.setattr(
        _evidence,
        "_validated_crop_box",
        enforce_smaller_crop_cap,
    )
    monkeypatch.setattr(Image.Image, "crop", track_crop)

    uri = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        budget,
    )

    assert validation_called
    assert uri is None
    assert not crop_called
    assert budget.used_bytes == 0


def test_raw_pair_preview_rejects_path_swap_after_secure_open(
    tmp_path,
    monkeypatch,
):
    item, paths = _raw_pair_preview_item(tmp_path)
    first = paths[0]
    displaced = tmp_path / "displaced-a.png"
    budget = _evidence.EvidenceBudget(1024 * 1024)
    original_open = Image.open
    opened_from_file_object = False
    swapped = False

    def swap_during_pillow_open(fp, *args, **kwargs):
        nonlocal opened_from_file_object, swapped
        if not swapped:
            opened_from_file_object = hasattr(fp, "read")
            first.rename(displaced)
            Image.new("RGB", (10, 10), (0, 0, 255)).save(first)
            swapped = True
        return original_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", swap_during_pillow_open)

    uri = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        budget,
    )

    assert opened_from_file_object
    assert uri is None
    assert budget.used_bytes == 0


def test_raw_pair_preview_charges_encoded_payload_length(tmp_path):
    item, _ = _raw_pair_preview_item(tmp_path)
    first = _evidence.EvidenceBudget(1024 * 1024)
    uri = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        first,
    )
    assert uri is not None
    encoded_size = len(uri.split(",", 1)[1])
    raw_size = len(base64.b64decode(uri.split(",", 1)[1]))
    assert encoded_size > raw_size
    assert first.used_bytes == encoded_size

    insufficient = _evidence.EvidenceBudget(encoded_size - 1)
    rejected = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        insufficient,
    )

    assert rejected is None
    assert insufficient.used_bytes == 0


@pytest.mark.parametrize("swap_kind", ["parent", "final"])
def test_raw_pair_preview_fails_closed_on_symlink_swap(
    tmp_path,
    monkeypatch,
    swap_kind,
):
    item, paths = _raw_pair_preview_item(tmp_path)
    first = paths[0]
    native_dir = first.parent
    outside_dir = tmp_path / "outside-native"
    outside_dir.mkdir()
    outside = outside_dir / first.name
    Image.new("RGB", (10, 10), (0, 0, 255)).save(outside)
    displaced = tmp_path / f"displaced-{swap_kind}"
    budget = _evidence.EvidenceBudget(1024 * 1024)
    original_os_open = _evidence.os.open
    original_image_open = Image.open
    outside_identity = (outside.stat().st_dev, outside.stat().st_ino)
    outside_consumed = False
    swapped = False

    def swap_before_registered_open(path, flags, *args, **kwargs):
        nonlocal swapped
        path_text = _evidence.os.fspath(path)
        should_swap = (
            swap_kind == "parent"
            and path_text == "native"
            and kwargs.get("dir_fd") is not None
        ) or (
            swap_kind == "final"
            and path_text == first.name
            and kwargs.get("dir_fd") is not None
        )
        if not swapped and should_swap:
            if swap_kind == "parent":
                native_dir.rename(displaced)
                native_dir.symlink_to(outside_dir, target_is_directory=True)
            else:
                first.rename(displaced)
                first.symlink_to(outside)
            swapped = True
        return original_os_open(path, flags, *args, **kwargs)

    def track_image_open(fp, *args, **kwargs):
        nonlocal outside_consumed
        if hasattr(fp, "fileno"):
            opened = _evidence.os.fstat(fp.fileno())
            outside_consumed = (
                opened.st_dev,
                opened.st_ino,
            ) == outside_identity
        return original_image_open(fp, *args, **kwargs)

    monkeypatch.setattr(_evidence.os, "open", swap_before_registered_open)
    monkeypatch.setattr(Image, "open", track_image_open)

    uri = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        budget,
    )

    assert swapped
    assert uri is None
    assert not outside_consumed
    assert budget.used_bytes == 0


def test_raw_pair_preview_rejects_registered_native_symlink_loop(
    tmp_path,
    monkeypatch,
):
    item, paths = _raw_pair_preview_item(tmp_path)
    loop_a = paths[0]
    loop_b = loop_a.with_name("loop-b.png")
    loop_a.unlink()
    loop_a.symlink_to(loop_b.name)
    loop_b.symlink_to(loop_a.name)
    budget = _evidence.EvidenceBudget(1024 * 1024)
    original_resolve = Path.resolve

    def resolve_with_legacy_loop_error(path, *args, **kwargs):
        if path == loop_a:
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_legacy_loop_error)

    uri = _html._registered_pair_preview_data_uri(
        item,
        str(tmp_path),
        budget,
    )

    assert uri is None
    assert budget.used_bytes == 0


def test_diagnostics_find_transform_related_panels_and_keep_assets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    before = [dict(asset) for asset in assets]

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert errors == diagnostic_errors == []
    assert assets == before
    assert findings
    finding = findings[0]
    assert finding["kind"] == "image_pair_similarity_signal"
    assert finding["transform"] == "flip"
    assert finding["score"] >= 0.92
    assert len(finding["regions"]) == 2


@pytest.mark.parametrize(
    ("expected_transform", "make_variant"),
    [
        ("identity", lambda cv2, image: image.copy()),
        ("flip", lambda cv2, image: cv2.flip(image, 1)),
        ("flip_vertical", lambda cv2, image: cv2.flip(image, 0)),
        (
            "rotate90",
            lambda cv2, image: cv2.rotate(
                image,
                cv2.ROTATE_90_COUNTERCLOCKWISE,
            ),
        ),
        (
            "rotate180",
            lambda cv2, image: cv2.rotate(image, cv2.ROTATE_180),
        ),
        (
            "rotate270",
            lambda cv2, image: cv2.rotate(
                image,
                cv2.ROTATE_90_CLOCKWISE,
            ),
        ),
        ("transpose_main", lambda cv2, image: cv2.transpose(image)),
        (
            "transpose_anti",
            lambda cv2, image: cv2.flip(cv2.transpose(image), -1),
        ),
    ],
)
def test_similarity_covers_all_dihedral_variants(
    expected_transform,
    make_variant,
):
    cv2 = _diagnostics._cv2()
    panel = _asymmetric_panel(4, height=96, width=128)
    variant = make_variant(cv2, panel)

    score, transform = _diagnostics.transform_robust_similarity(
        panel,
        variant,
    )

    assert score >= _diagnostics._SIMILARITY_THRESHOLD
    assert transform == expected_transform


def test_diagnostics_trim_margins_for_scoring_but_keep_native_boxes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _offset_duplicate_figure(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    expected_boxes = [
        (20, 25, 435, 300),
        (435, 25, 860, 300),
        (20, 300, 435, 605),
        (435, 300, 860, 605),
    ]
    expected_regions = [
        {"asset_id": assets[0]["asset_id"], "box": list(expected_boxes[0])},
        {"asset_id": assets[0]["asset_id"], "box": list(expected_boxes[3])},
    ]

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert diagnostic_errors == []
    matching = [
        finding
        for finding in findings
        if finding["regions"] == expected_regions
    ]
    assert len(matching) == 1
    finding = matching[0]
    assert finding["score"] >= _diagnostics._SIMILARITY_THRESHOLD
    assert finding["asset_ids"] == [assets[0]["asset_id"]]
    assert finding["evidence"] is not None
    with Image.open(out / finding["evidence"]["crop_a_path"]) as crop_a:
        assert crop_a.size == (415, 275)
    with Image.open(out / finding["evidence"]["crop_b_path"]) as crop_b:
        assert crop_b.size == (425, 305)


def test_similarity_rejects_reviewed_blurred_noise_non_duplicate():
    left, right = _blurred_noise_pair_at_mirrored_offsets()

    score, _ = _diagnostics.transform_robust_similarity(left, right)

    assert score < _diagnostics._SIMILARITY_THRESHOLD


def test_margin_trim_falls_back_when_content_is_too_small():
    image = np.full((100, 120, 3), 255, dtype=np.uint8)
    image[49:51, 59:61] = 0

    trimmed = _diagnostics._trim_low_information_margins(image)

    assert trimmed.shape == image.shape


def test_similarity_downsamples_both_inputs_before_margin_analysis(monkeypatch):
    seen_shapes = []
    original_trim = _diagnostics._trim_low_information_margins

    def bounded_trim(image):
        seen_shapes.append(image.shape)
        return original_trim(image)

    monkeypatch.setattr(
        _diagnostics,
        "_trim_low_information_margins",
        bounded_trim,
    )
    left = np.zeros((1200, 1600, 3), dtype=np.uint8)
    right = np.zeros((1600, 1200, 3), dtype=np.uint8)

    score, transform = _diagnostics.transform_robust_similarity(left, right)

    assert np.isfinite(score)
    assert isinstance(transform, str)
    assert seen_shapes[:2] == [
        (768, 1024, 3),
        (1024, 768, 3),
    ]
    assert seen_shapes[0][0] * left.shape[1] == (
        seen_shapes[0][1] * left.shape[0]
    )
    assert seen_shapes[1][0] * right.shape[1] == (
        seen_shapes[1][1] * right.shape[0]
    )


def test_similarity_does_not_upscale_small_inputs_before_margin_analysis(
    monkeypatch,
):
    seen = []
    original_trim = _diagnostics._trim_low_information_margins

    def track_trim(image):
        seen.append(image)
        return original_trim(image)

    monkeypatch.setattr(
        _diagnostics,
        "_trim_low_information_margins",
        track_trim,
    )
    left = np.zeros((120, 160, 3), dtype=np.uint8)
    right = np.zeros((160, 120, 3), dtype=np.uint8)

    _diagnostics.transform_robust_similarity(left, right)

    assert seen[0] is left
    assert seen[1] is right
    assert seen[0].shape == (120, 160, 3)
    assert seen[1].shape == (160, 120, 3)


def test_similarity_releases_each_transform_before_generating_the_next(
    monkeypatch,
):
    cv2 = _diagnostics._cv2()
    created_names = []
    created_refs = []
    scored_names = []
    transpose_names = iter(("transpose_main", "transpose_anti"))

    class TransformResult:
        def __init__(self, name):
            self.name = name

        def __getitem__(self, key):
            assert key == (
                slice(None, None, -1),
                slice(None, None, -1),
            )
            return self

    def features(image):
        if isinstance(image, TransformResult):
            scored_names.append(image.name)
        values = np.ones((4, 4), dtype=np.float32)
        return values, values

    def make_result(name):
        if created_refs:
            assert created_refs[-1]() is None
            assert scored_names == created_names
        result = TransformResult(name)
        created_names.append(name)
        created_refs.append(weakref.ref(result))
        return result

    def flip(image, code):
        name = "flip" if code == 1 else "flip_vertical"
        return make_result(name)

    def rotate(image, code):
        names = {
            cv2.ROTATE_90_CLOCKWISE: "rotate90",
            cv2.ROTATE_180: "rotate180",
            cv2.ROTATE_90_COUNTERCLOCKWISE: "rotate270",
        }
        return make_result(names[code])

    def transpose(image):
        return make_result(next(transpose_names))

    monkeypatch.setattr(_diagnostics, "_comparison_features", features)
    monkeypatch.setattr(cv2, "flip", flip)
    monkeypatch.setattr(cv2, "rotate", rotate)
    monkeypatch.setattr(cv2, "transpose", transpose)

    _diagnostics.transform_robust_similarity(
        np.zeros((8, 8, 3), dtype=np.uint8),
        np.zeros((8, 8, 3), dtype=np.uint8),
    )

    assert created_names == [
        "flip",
        "flip_vertical",
        "rotate90",
        "rotate180",
        "rotate270",
        "transpose_main",
        "transpose_anti",
    ]
    assert scored_names == created_names
    assert all(reference() is None for reference in created_refs)


def test_similarity_ties_choose_lexicographically_largest_transform(
    monkeypatch,
):
    def equal_features(image):
        values = np.ones((4, 4), dtype=np.float32)
        return values, values

    monkeypatch.setattr(_diagnostics, "_comparison_features", equal_features)
    image = np.zeros((8, 8, 3), dtype=np.uint8)

    results = [
        _diagnostics.transform_robust_similarity(image, image)
        for _ in range(5)
    ]

    assert results == [(1.0, "transpose_main")] * 5


def test_diagnostics_decode_registered_bytes_with_imdecode(tmp_path, monkeypatch):
    out, assets = _diagnostic_asset(tmp_path)
    cv2 = _diagnostics._cv2()
    real_imdecode = cv2.imdecode
    decoded = False

    def reject_imread(*args, **kwargs):
        raise AssertionError("diagnostics must not reopen a pathname with imread")

    def track_imdecode(payload, flags):
        nonlocal decoded
        decoded = True
        return real_imdecode(payload, flags)

    monkeypatch.setattr(cv2, "imread", reject_imread)
    monkeypatch.setattr(cv2, "imdecode", track_imdecode)

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert errors == []
    assert decoded


def test_diagnostics_rejects_registered_image_outside_manifest_identity(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    Image.new("RGB", (310, 140), (20, 80, 140)).save(native)
    proposed = False

    def track_panel_proposal(image):
        nonlocal proposed
        proposed = True
        return [(0, 0, 140, 120), (150, 0, 290, 120)], False

    monkeypatch.setattr(
        _diagnostics,
        "_propose_panels_bounded",
        track_panel_proposal,
    )

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert not proposed
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "registered image identity does not match asset manifest",
    }]


def test_diagnostics_reject_file_size_before_imdecode(tmp_path, monkeypatch):
    out, assets = _diagnostic_asset(tmp_path)
    cv2 = _diagnostics._cv2()
    decoded = False

    def track_imdecode(*args, **kwargs):
        nonlocal decoded
        decoded = True
        raise AssertionError("oversized registered image must not be decoded")

    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_MB", "0")
    monkeypatch.setattr(cv2, "imdecode", track_imdecode)

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert not decoded
    assert errors == [{
        "file": "Fig1.png",
        "error": "registered image exceeds PAPERCONAN_MAX_IMAGE_MB",
    }]


def test_diagnostics_reject_full_image_pixels_before_imdecode(
    tmp_path,
    monkeypatch,
):
    out, assets = _diagnostic_asset(tmp_path)
    cv2 = _diagnostics._cv2()
    decoded = False

    def track_imdecode(*args, **kwargs):
        nonlocal decoded
        decoded = True
        raise AssertionError("over-pixel registered image must not be decoded")

    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", "100")
    monkeypatch.setattr(cv2, "imdecode", track_imdecode)

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert not decoded
    assert errors == [{
        "file": "Fig1.png",
        "error": "registered image exceeds PAPERCONAN_MAX_IMAGE_PIXELS",
    }]


@pytest.mark.parametrize("swap_kind", ["parent", "final"])
def test_diagnostics_fail_closed_on_registered_path_swap(
    tmp_path,
    monkeypatch,
    swap_kind,
):
    out, assets = _diagnostic_asset(tmp_path)
    native = out / assets[0]["path"]
    native_dir = native.parent
    outside_dir = tmp_path / "outside-native"
    outside_dir.mkdir()
    outside = outside_dir / native.name
    Image.new("RGB", (16, 16), "black").save(outside)
    displaced = tmp_path / f"displaced-{swap_kind}"
    original_os_open = _evidence.os.open
    cv2 = _diagnostics._cv2()
    decoded = False
    swapped = False

    def swap_before_registered_open(path, flags, *args, **kwargs):
        nonlocal swapped
        path_text = _evidence.os.fspath(path)
        should_swap = (
            swap_kind == "parent"
            and path_text == "native"
            and kwargs.get("dir_fd") is not None
        ) or (
            swap_kind == "final"
            and path_text == native.name
            and kwargs.get("dir_fd") is not None
        )
        if not swapped and should_swap:
            if swap_kind == "parent":
                native_dir.rename(displaced)
                native_dir.symlink_to(outside_dir, target_is_directory=True)
            else:
                native.rename(displaced)
                native.symlink_to(outside)
            swapped = True
        return original_os_open(path, flags, *args, **kwargs)

    def track_imdecode(*args, **kwargs):
        nonlocal decoded
        decoded = True
        raise AssertionError("swapped registered image must not be decoded")

    monkeypatch.setattr(_evidence.os, "open", swap_before_registered_open)
    monkeypatch.setattr(cv2, "imdecode", track_imdecode)

    findings, errors = diagnose_image_assets(assets, str(out))

    assert swapped
    assert findings == []
    assert not decoded
    assert errors == [{
        "file": "Fig1.png",
        "error": "registered image path is not stable under artifact root",
    }]


def test_bounded_candidates_never_retain_more_than_limit():
    retained = _diagnostics._BoundedCandidates(2)
    candidates = [
        {"finding_id": "image:pair:c", "score": 0.93},
        {"finding_id": "image:pair:b", "score": 0.99},
        {"finding_id": "image:pair:a", "score": 0.99},
        {"finding_id": "image:pair:d", "score": 0.95},
    ]

    for candidate in candidates:
        retained.consider(candidate)
        assert len(retained) <= 2

    assert [item["finding_id"] for item in retained.best()] == [
        "image:pair:a",
        "image:pair:b",
    ]
    assert retained.qualifying_count == 4
    assert retained.omitted_count == 2


def test_panel_proposal_stops_after_observing_one_candidate_beyond_cap(
    monkeypatch,
):
    patch_std_calls = _install_synthetic_panel_grid(
        monkeypatch,
        rows=5,
        cols=14,
    )

    boxes = _diagnostics.propose_panels(
        np.zeros((1, 1, 3), dtype=np.uint8),
    )

    assert len(boxes) == 64
    assert patch_std_calls() == 65


def test_diagnostics_report_panel_candidate_omission_per_asset(
    tmp_path,
    monkeypatch,
):
    out, assets = _diagnostic_asset(tmp_path)
    patch_std_calls = _install_synthetic_panel_grid(
        monkeypatch,
        rows=5,
        cols=14,
    )
    comparisons = 0

    def dissimilar(left, right):
        nonlocal comparisons
        comparisons += 1
        return 0.0, "identity"

    monkeypatch.setattr(
        _diagnostics,
        "transform_robust_similarity",
        dissimilar,
    )

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert comparisons == 2016
    assert patch_std_calls() == 65
    assert errors == [{
        "file": "Fig1.png",
        "error": "image panel candidates omitted; limit is 64",
    }]


def test_diagnostics_stop_before_comparison_beyond_pair_budget(
    tmp_path,
    monkeypatch,
):
    out, assets = _diagnostic_asset(tmp_path)
    boxes = [
        (0, 0, 4, 4),
        (4, 0, 8, 4),
        (8, 0, 12, 4),
        (12, 0, 16, 4),
    ]
    comparisons = 0

    def dissimilar(left, right):
        nonlocal comparisons
        comparisons += 1
        return 0.0, "identity"

    monkeypatch.setattr(
        _diagnostics,
        "_propose_panels_bounded",
        lambda image: (boxes, False),
        raising=False,
    )
    monkeypatch.setattr(
        _diagnostics,
        "_MAX_PANEL_PAIR_COMPARISONS",
        2,
        raising=False,
    )
    monkeypatch.setattr(
        _diagnostics,
        "transform_robust_similarity",
        dissimilar,
    )

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert comparisons == 2
    assert errors == [{
        "file": "Fig1.png",
        "error": "image panel-pair comparisons omitted; limit is 2",
    }]


def test_zero_finding_cap_keeps_work_bounds_and_resource_errors(
    tmp_path,
    monkeypatch,
):
    out, assets = _diagnostic_asset(tmp_path)
    boxes = [
        (0, 0, 4, 4),
        (4, 0, 8, 4),
        (8, 0, 12, 4),
        (12, 0, 16, 4),
    ]
    comparisons = 0

    def similar(left, right):
        nonlocal comparisons
        comparisons += 1
        return 1.0, "identity"

    def evidence_must_not_run(*args, **kwargs):
        raise AssertionError("zero finding cap must not write evidence")

    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_FINDINGS", "0")
    monkeypatch.setattr(
        _diagnostics,
        "_propose_panels_bounded",
        lambda image: (boxes, True),
        raising=False,
    )
    monkeypatch.setattr(
        _diagnostics,
        "_MAX_PANEL_PAIR_COMPARISONS",
        2,
        raising=False,
    )
    monkeypatch.setattr(
        _diagnostics,
        "transform_robust_similarity",
        similar,
    )
    monkeypatch.setattr(
        _diagnostics,
        "write_native_pair_evidence",
        evidence_must_not_run,
    )

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert comparisons == 2
    assert errors == [
        {
            "file": "Fig1.png",
            "error": "image panel candidates omitted; limit is 64",
        },
        {
            "file": "Fig1.png",
            "error": "image panel-pair comparisons omitted; limit is 2",
        },
        {
            "error": (
                "2 image findings omitted; "
                "set PAPERCONAN_MAX_IMAGE_FINDINGS to raise"
            ),
        },
    ]
    assert not (out / "images" / "evidence").exists()


def test_scan_wide_comparison_ceiling_stops_across_assets(
    tmp_path,
    monkeypatch,
):
    out, first_assets = _diagnostic_asset(tmp_path)
    second = out / "images" / "native" / "Fig2.png"
    Image.new("RGB", (16, 16), "black").save(second)
    assets = first_assets + [{
        "asset_id": "img:fig2",
        "file": "Fig2.png",
        "path": second.relative_to(out).as_posix(),
        "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
    }]
    boxes = [
        (0, 0, 4, 4),
        (4, 0, 8, 4),
        (8, 0, 12, 4),
    ]
    comparisons = 0

    def dissimilar(left, right):
        nonlocal comparisons
        comparisons += 1
        return 0.0, "identity"

    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_COMPARISONS", "4")
    monkeypatch.setattr(
        _diagnostics,
        "_propose_panels_bounded",
        lambda image: (boxes, False),
    )
    monkeypatch.setattr(
        _diagnostics,
        "transform_robust_similarity",
        dissimilar,
    )

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert comparisons == 4
    assert errors == [{
        "error": (
            "image comparisons omitted; scan-wide limit is 4 "
            "(PAPERCONAN_MAX_IMAGE_COMPARISONS)"
        ),
    }]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PAPERCONAN_MAX_IMAGE_FINDINGS", "-1"),
        ("PAPERCONAN_MAX_IMAGE_FINDINGS", "not-a-number"),
        ("PAPERCONAN_MAX_IMAGE_FINDINGS", "9" * 5000),
        ("PAPERCONAN_MAX_IMAGE_COMPARISONS", "-1"),
        ("PAPERCONAN_MAX_IMAGE_COMPARISONS", "not-a-number"),
        ("PAPERCONAN_MAX_IMAGE_COMPARISONS", "9" * 5000),
    ],
)
def test_diagnostics_invalid_scan_limits_fail_closed(
    tmp_path,
    monkeypatch,
    name,
    value,
):
    out, assets = _diagnostic_asset(tmp_path)
    monkeypatch.setenv(name, value)

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert errors == [{"error": f"invalid {name} limit"}]
    assert not (out / "images" / "evidence").exists()


def test_cmyk_scan_diagnostics_never_abort_or_remove_assets(tmp_path):
    from paperconan import scan_dir

    template = tmp_path / "template.png"
    _two_panel(template)
    source = tmp_path / "source"
    source.mkdir()
    with Image.open(template) as image:
        image.convert("CMYK").save(source / "Fig1.jpg", quality=95)

    scan = scan_dir(
        str(source),
        str(tmp_path / "audit"),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )

    assert scan["n_image_assets"] == 1
    assert len(scan["image_assets"]) == 1
    assert scan["image_findings"] or scan["scan_errors"]


def test_candidate_evidence_write_error_is_non_gating(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))

    def unavailable(*args, **kwargs):
        raise OSError("synthetic evidence write error")

    monkeypatch.setattr(_diagnostics, "write_native_pair_evidence", unavailable)

    findings, errors = diagnose_image_assets(assets, str(out))

    assert len(findings) == 1
    assert findings[0]["evidence"] is None
    assert errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: synthetic evidence write error",
    }]


def test_diagnostics_reject_evidence_when_scored_asset_is_replaced(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(replacement)
    replaced = False

    def score_then_replace(left, right):
        nonlocal replaced
        if not replaced:
            replacement.replace(native)
            replaced = True
        return 0.99, "identity"

    monkeypatch.setattr(
        _diagnostics,
        "transform_robust_similarity",
        score_then_replace,
    )

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert replaced
    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]
    assert not (out / "images" / "evidence").exists()


def test_diagnostics_rechecks_source_after_publication_failure(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(replacement)

    def replace_then_fail(*args, **kwargs):
        replacement.replace(native)
        raise OSError("synthetic evidence publication error")

    monkeypatch.setattr(
        _diagnostics,
        "write_native_pair_evidence",
        replace_then_fail,
    )

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]


def test_source_change_rolls_back_all_findings_and_owned_evidence_for_asset(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(replacement)
    boxes = [
        (0, 0, 100, 100),
        (105, 0, 205, 100),
        (210, 0, 310, 100),
    ]
    original_write = _diagnostics.write_native_pair_evidence
    write_calls = 0

    def publish_once_then_replace(*args, **kwargs):
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            replacement.replace(native)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(
        _diagnostics,
        "_propose_panels_bounded",
        lambda image: (boxes, False),
    )
    monkeypatch.setattr(
        _diagnostics,
        "transform_robust_similarity",
        lambda left, right: (0.99, "identity"),
    )
    monkeypatch.setattr(
        _diagnostics,
        "write_native_pair_evidence",
        publish_once_then_replace,
    )
    budget = ImageArtifactBudget(1024 * 1024 * 1024)

    findings, diagnostic_errors = diagnose_image_assets(
        assets,
        str(out),
        artifact_budget=budget,
    )

    assert write_calls == 2
    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]
    evidence_dir = out / "images" / "evidence"
    assert not evidence_dir.exists() or list(evidence_dir.iterdir()) == []
    visible_bytes = sum(
        path.stat().st_size
        for path in (out / "images").rglob("*")
        if stat.S_ISREG(path.lstat().st_mode)
    )
    assert budget.used_bytes == visible_bytes


def test_source_change_rollback_retains_concurrent_evidence_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(replacement)
    concurrent_target = tmp_path / "concurrent-evidence.bin"
    concurrent_bytes = b"concurrent evidence replacement"
    concurrent_target.write_bytes(concurrent_bytes)
    boxes = [
        (0, 0, 100, 100),
        (105, 0, 205, 100),
        (210, 0, 310, 100),
    ]
    original_write = _diagnostics.write_native_pair_evidence
    write_calls = 0
    concurrent_final = None

    def publish_replace_one_then_change_source(*args, **kwargs):
        nonlocal write_calls, concurrent_final
        write_calls += 1
        if write_calls == 2:
            replacement.replace(native)
            return original_write(*args, **kwargs)
        evidence = original_write(*args, **kwargs)
        concurrent_final = out / evidence["crop_a_path"]
        concurrent_final.unlink()
        concurrent_final.symlink_to(concurrent_target)
        return evidence

    monkeypatch.setattr(
        _diagnostics,
        "_propose_panels_bounded",
        lambda image: (boxes, False),
    )
    monkeypatch.setattr(
        _diagnostics,
        "transform_robust_similarity",
        lambda left, right: (0.99, "identity"),
    )
    monkeypatch.setattr(
        _diagnostics,
        "write_native_pair_evidence",
        publish_replace_one_then_change_source,
    )
    budget = ImageArtifactBudget(1024 * 1024 * 1024)

    findings, diagnostic_errors = diagnose_image_assets(
        assets,
        str(out),
        artifact_budget=budget,
    )

    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]
    assert concurrent_final is not None
    assert concurrent_final.is_symlink()
    assert concurrent_final.readlink() == concurrent_target
    assert concurrent_target.read_bytes() == concurrent_bytes
    evidence_entries = list((out / "images" / "evidence").iterdir())
    assert evidence_entries == [concurrent_final]
    visible_bytes = sum(
        path.stat().st_size
        for path in (out / "images").rglob("*")
        if stat.S_ISREG(path.lstat().st_mode)
    )
    assert budget.used_bytes == visible_bytes


def test_source_change_rollback_does_not_claim_post_publication_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(replacement)
    concurrent_bytes = b"external regular evidence replacement"
    concurrent_final = None
    original_publish = _evidence._publish_staged_images

    def publish_then_replace(
        root,
        root_fd,
        images_fd,
        evidence_fd,
        staged,
    ):
        nonlocal concurrent_final
        receipt = original_publish(
            root,
            root_fd,
            images_fd,
            evidence_fd,
            staged,
        )
        concurrent_final = root / "images" / "evidence" / staged[0][2]
        concurrent_final.unlink()
        concurrent_final.write_bytes(concurrent_bytes)
        replacement.replace(native)
        return receipt

    monkeypatch.setattr(
        _evidence,
        "_publish_staged_images",
        publish_then_replace,
    )

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]
    assert concurrent_final is not None
    assert concurrent_final.read_bytes() == concurrent_bytes


def test_source_change_rolls_back_partial_evidence_publication(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(replacement)
    real_link = _evidence.os.link
    install_calls = 0

    def publish_one_then_fail(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal install_calls
        if Path(src).name.startswith(".paperconan-evidence-"):
            install_calls += 1
            if install_calls == 2:
                raise OSError("synthetic second evidence install failure")
            result = real_link(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            replacement.replace(native)
            return result
        return real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(_evidence.os, "link", publish_one_then_fail)

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert install_calls == 2
    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]
    evidence_dir = out / "images" / "evidence"
    assert not evidence_dir.exists() or list(evidence_dir.iterdir()) == []


def test_source_change_rolls_back_evidence_after_post_link_verification_failure(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(replacement)
    real_verify = _evidence._verify_regular_file_entry
    post_link_verifications = 0

    def fail_first_post_link_verification(evidence_fd, name, file_fd):
        nonlocal post_link_verifications
        if not name.startswith(".paperconan-evidence-"):
            post_link_verifications += 1
            installed = os.stat(
                name,
                dir_fd=evidence_fd,
                follow_symlinks=False,
            )
            staged = os.fstat(file_fd)
            assert (installed.st_dev, installed.st_ino) == (
                staged.st_dev,
                staged.st_ino,
            )
            replacement.replace(native)
            raise ValueError("synthetic post-link verification failure")
        return real_verify(evidence_fd, name, file_fd)

    monkeypatch.setattr(
        _evidence,
        "_verify_regular_file_entry",
        fail_first_post_link_verification,
    )

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert post_link_verifications == 1
    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]
    evidence_dir = out / "images" / "evidence"
    assert not evidence_dir.exists() or list(evidence_dir.iterdir()) == []


def test_post_link_failure_rollback_retains_concurrent_final_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    source_replacement = tmp_path / "replacement.png"
    Image.new("RGB", (310, 140), (20, 80, 140)).save(source_replacement)
    concurrent_bytes = b"external regular evidence replacement"
    concurrent_source = tmp_path / "concurrent-evidence.bin"
    concurrent_source.write_bytes(concurrent_bytes)
    real_verify = _evidence._verify_regular_file_entry
    concurrent_final = None

    def replace_final_then_fail_verification(evidence_fd, name, file_fd):
        nonlocal concurrent_final
        if not name.startswith(".paperconan-evidence-"):
            installed = os.stat(
                name,
                dir_fd=evidence_fd,
                follow_symlinks=False,
            )
            staged = os.fstat(file_fd)
            assert (installed.st_dev, installed.st_ino) == (
                staged.st_dev,
                staged.st_ino,
            )
            concurrent_final = out / "images" / "evidence" / name
            concurrent_final.unlink()
            concurrent_source.replace(concurrent_final)
            source_replacement.replace(native)
            raise ValueError("synthetic post-link verification failure")
        return real_verify(evidence_fd, name, file_fd)

    monkeypatch.setattr(
        _evidence,
        "_verify_regular_file_entry",
        replace_final_then_fail_verification,
    )

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert diagnostic_errors == [{
        "file": "Fig1.png",
        "error": "image evidence unavailable: registered image changed after scoring",
    }]
    assert concurrent_final is not None
    assert concurrent_final.read_bytes() == concurrent_bytes
    assert list((out / "images" / "evidence").iterdir()) == [concurrent_final]


def _owned_evidence_rollback_state(tmp_path):
    out = tmp_path / "audit"
    evidence_dir = out / "images" / "evidence"
    evidence_dir.mkdir(parents=True)
    evidence = evidence_dir / "owned.png"
    owned_bytes = b"owned evidence"
    evidence.write_bytes(owned_bytes)
    owned_state = evidence.stat()
    receipt = {
        "images/evidence/owned.png": (
            owned_state.st_dev,
            owned_state.st_ino,
            owned_state.st_size,
            hashlib.sha256(owned_bytes).hexdigest(),
        ),
    }
    budget = ImageArtifactBudget(1024 * 1024)
    budget.initialize_from_root(out)
    return out, evidence_dir, evidence, receipt, budget


def test_owned_evidence_rollback_retries_private_quarantine_collision(
    tmp_path,
    monkeypatch,
):
    out, evidence_dir, evidence, receipt, budget = (
        _owned_evidence_rollback_state(tmp_path)
    )
    real_mkdir = _evidence.os.mkdir
    quarantine_mkdir_calls = 0
    collision_directory = None

    def collide_with_first_quarantine_directory(
        path,
        mode=0o777,
        *,
        dir_fd=None,
    ):
        nonlocal quarantine_mkdir_calls, collision_directory
        if _evidence.os.fspath(path).startswith(
            ".paperconan-evidence-rollback-"
        ):
            quarantine_mkdir_calls += 1
            if quarantine_mkdir_calls == 1:
                real_mkdir(path, mode, dir_fd=dir_fd)
                collision_directory = evidence_dir / path
                collision_fd = _evidence.os.open(
                    path,
                    _evidence.os.O_RDONLY
                    | _evidence.os.O_DIRECTORY
                    | _evidence.os.O_NOFOLLOW,
                    dir_fd=dir_fd,
                )
                try:
                    marker_fd = _evidence.os.open(
                        "external-marker",
                        _evidence.os.O_WRONLY
                        | _evidence.os.O_CREAT
                        | _evidence.os.O_EXCL
                        | _evidence.os.O_NOFOLLOW,
                        0o600,
                        dir_fd=collision_fd,
                    )
                    try:
                        _evidence.os.write(
                            marker_fd,
                            b"external quarantine entry",
                        )
                    finally:
                        _evidence.os.close(marker_fd)
                finally:
                    _evidence.os.close(collision_fd)
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(
        _evidence.os,
        "mkdir",
        collide_with_first_quarantine_directory,
    )

    _evidence.remove_published_evidence_if_owned(
        str(out),
        [receipt],
        artifact_budget=budget,
    )

    assert not evidence.exists()
    assert quarantine_mkdir_calls == 2
    assert collision_directory is not None
    assert (
        collision_directory / "external-marker"
    ).read_bytes() == b"external quarantine entry"
    assert [
        path
        for path in evidence_dir.iterdir()
        if path.name.startswith(".paperconan-evidence-rollback-")
    ] == [collision_directory]
    assert budget.used_bytes == len(b"external quarantine entry")


def test_owned_evidence_rollback_restores_replacement_after_identity_check(
    tmp_path,
    monkeypatch,
):
    out, evidence_dir, evidence, receipt, budget = (
        _owned_evidence_rollback_state(tmp_path)
    )
    replacement_bytes = b"other evidence"
    assert len(replacement_bytes) == len(evidence.read_bytes())
    expected_device, expected_inode, _, _ = next(iter(receipt.values()))
    real_stat = _evidence.os.stat
    real_fstat = _evidence.os.fstat
    real_open = _evidence.os.open
    real_unlink = _evidence.os.unlink
    quarantined_fds = set()
    replaced = False

    class MatchingIdentity:
        def __init__(self, current):
            self._current = current
            self.st_dev = expected_device
            self.st_ino = expected_inode

        def __getattr__(self, name):
            return getattr(self._current, name)

    def replace_after_identity_check(path, *args, **kwargs):
        nonlocal replaced
        current = real_stat(path, *args, **kwargs)
        if (
            not replaced
            and _evidence.os.fspath(path) == evidence.name
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
        ):
            real_unlink(path, dir_fd=kwargs["dir_fd"])
            replacement_fd = _evidence.os.open(
                path,
                _evidence.os.O_WRONLY
                | _evidence.os.O_CREAT
                | _evidence.os.O_EXCL
                | _evidence.os.O_NOFOLLOW,
                0o600,
                dir_fd=kwargs["dir_fd"],
            )
            try:
                _evidence.os.write(replacement_fd, replacement_bytes)
            finally:
                _evidence.os.close(replacement_fd)
            replaced = True
        if (
            replaced
            and _evidence.os.fspath(path)
            == _evidence._ROLLBACK_QUARANTINE_ENTRY
        ):
            return MatchingIdentity(current)
        return current

    def record_quarantined_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if (
            _evidence.os.fspath(path)
            == _evidence._ROLLBACK_QUARANTINE_ENTRY
        ):
            quarantined_fds.add(fd)
        return fd

    def matching_quarantined_fstat(fd):
        current = real_fstat(fd)
        if fd in quarantined_fds:
            return MatchingIdentity(current)
        return current

    monkeypatch.setattr(_evidence.os, "stat", replace_after_identity_check)
    monkeypatch.setattr(_evidence.os, "open", record_quarantined_open)
    monkeypatch.setattr(_evidence.os, "fstat", matching_quarantined_fstat)

    _evidence.remove_published_evidence_if_owned(
        str(out),
        [receipt],
        artifact_budget=budget,
    )

    assert replaced
    assert evidence.read_bytes() == replacement_bytes
    assert not any(
        path.name.startswith(".paperconan-evidence-rollback-")
        for path in evidence_dir.iterdir()
    )
    assert budget.used_bytes == len(replacement_bytes)


def test_owned_evidence_rollback_restores_same_inode_mutation_after_hash(
    tmp_path,
    monkeypatch,
):
    out, evidence_dir, evidence, receipt, budget = (
        _owned_evidence_rollback_state(tmp_path)
    )
    mutated_bytes = b"other evidence"
    assert len(mutated_bytes) == len(evidence.read_bytes())
    writer_fd = _evidence.os.open(
        evidence,
        _evidence.os.O_WRONLY | _evidence.os.O_NOFOLLOW,
    )
    real_stat = _evidence.os.stat
    initial_quarantine_state = None
    quarantine_stat_calls = 0
    mutated = False

    def mutate_during_final_quarantine_stat(path, *args, **kwargs):
        nonlocal initial_quarantine_state, quarantine_stat_calls, mutated
        if (
            _evidence.os.fspath(path)
            == _evidence._ROLLBACK_QUARANTINE_ENTRY
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
        ):
            quarantine_stat_calls += 1
            if quarantine_stat_calls == 1:
                initial_quarantine_state = real_stat(
                    path,
                    *args,
                    **kwargs,
                )
                return initial_quarantine_state
            if quarantine_stat_calls == 2:
                assert initial_quarantine_state is not None
                _evidence.os.pwrite(writer_fd, mutated_bytes, 0)
                _evidence.os.fsync(writer_fd)
                _evidence.os.utime(
                    path,
                    ns=(
                        initial_quarantine_state.st_atime_ns,
                        initial_quarantine_state.st_mtime_ns
                        + 1_000_000_000,
                    ),
                    dir_fd=kwargs["dir_fd"],
                    follow_symlinks=False,
                )
                current = real_stat(path, *args, **kwargs)
                assert (
                    current.st_mtime_ns
                    != initial_quarantine_state.st_mtime_ns
                )
                mutated = True
                return current
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        _evidence.os,
        "stat",
        mutate_during_final_quarantine_stat,
    )

    try:
        _evidence.remove_published_evidence_if_owned(
            str(out),
            [receipt],
            artifact_budget=budget,
        )
    finally:
        _evidence.os.close(writer_fd)

    assert quarantine_stat_calls == 2
    assert mutated
    assert evidence.read_bytes() == mutated_bytes
    assert not any(
        path.name.startswith(".paperconan-evidence-rollback-")
        for path in evidence_dir.iterdir()
    )
    assert budget.used_bytes == len(mutated_bytes)


def test_diagnostic_evidence_respects_remaining_total_artifact_budget(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    total = sum(
        (out / assets[0][key]).stat().st_size
        for key in ("path", "preview_path")
    )
    monkeypatch.setenv(
        "PAPERCONAN_MAX_IMAGE_TOTAL_MB",
        str(total / (1024 * 1024)),
    )

    findings, diagnostic_errors = diagnose_image_assets(assets, str(out))

    assert len(findings) == 1
    assert findings[0]["evidence"] is None
    assert len(diagnostic_errors) == 1
    assert "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in diagnostic_errors[0]["error"]
    assert "budget exhausted" in diagnostic_errors[0]["error"]
    evidence_dir = out / "images" / "evidence"
    assert not evidence_dir.exists() or list(evidence_dir.iterdir()) == []


def test_budget_initialization_failure_retains_stable_findings(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    budget = ImageArtifactBudget(1024 * 1024)

    def unavailable(root):
        raise ValueError("synthetic artifact budget unavailable")

    monkeypatch.setattr(budget, "initialize_from_root", unavailable)

    findings, diagnostic_errors = diagnose_image_assets(
        assets,
        str(out),
        artifact_budget=budget,
    )

    assert len(findings) == 1
    assert findings[0]["evidence"] is None
    assert diagnostic_errors == [{
        "error": "synthetic artifact budget unavailable",
    }]


def test_evidence_budget_fresh_accounting_counts_visible_insertion(tmp_path):
    baseline_root, baseline_assets = _diagnostic_asset(tmp_path / "baseline")
    baseline_native = baseline_root / baseline_assets[0]["path"]
    baseline_evidence = write_native_pair_evidence(
        str(baseline_native),
        (0, 0, 8, 8),
        (8, 0, 16, 8),
        str(baseline_root),
        "image-pair-baseline-budget",
    )
    native_size = baseline_native.stat().st_size
    bundle_size = sum(
        (baseline_root / path).stat().st_size
        for path in baseline_evidence.values()
    )
    root, assets = _diagnostic_asset(tmp_path / "shared")
    native = root / assets[0]["path"]
    budget = ImageArtifactBudget(native_size + bundle_size)
    budget.initialize_from_root(root)
    (root / "images" / "external.bin").write_bytes(b"x")

    with pytest.raises(ValueError, match="PAPERCONAN_MAX_IMAGE_TOTAL_MB"):
        write_native_pair_evidence(
            str(native),
            (0, 0, 8, 8),
            (8, 0, 16, 8),
            str(root),
            "image-pair-fresh-accounting",
            artifact_budget=budget,
        )

    assert budget.used_bytes == native_size + 1
    assert not any(
        path.name.startswith("image-pair-fresh-accounting")
        for path in (root / "images" / "evidence").iterdir()
    )


def test_evidence_bundle_budget_coordinates_concurrent_paperconan_writers(
    tmp_path,
    monkeypatch,
):
    baseline_root, baseline_assets = _diagnostic_asset(tmp_path / "baseline")
    baseline_native = baseline_root / baseline_assets[0]["path"]
    baseline_evidence = write_native_pair_evidence(
        str(baseline_native),
        (0, 0, 8, 8),
        (8, 0, 16, 8),
        str(baseline_root),
        "image-pair-baseline-concurrency",
    )
    cap = baseline_native.stat().st_size + sum(
        (baseline_root / path).stat().st_size
        for path in baseline_evidence.values()
    )
    root, assets = _diagnostic_asset(tmp_path / "shared")
    native = root / assets[0]["path"]
    first_inside = threading.Event()
    release_first = threading.Event()
    second_inside = threading.Event()
    original_stage_image = _evidence._stage_image
    local = threading.local()

    def controlled_stage_image(image, evidence_fd, image_format, **save_kwargs):
        calls = getattr(local, "calls", 0)
        local.calls = calls + 1
        if calls == 0 and threading.current_thread().name == "evidence-first":
            first_inside.set()
            assert release_first.wait(5)
        elif calls == 0 and threading.current_thread().name == "evidence-second":
            second_inside.set()
        return original_stage_image(
            image,
            evidence_fd,
            image_format,
            **save_kwargs,
        )

    monkeypatch.setattr(_evidence, "_stage_image", controlled_stage_image)
    results = {}

    def run(name, evidence_id):
        try:
            results[name] = write_native_pair_evidence(
                str(native),
                (0, 0, 8, 8),
                (8, 0, 16, 8),
                str(root),
                evidence_id,
                artifact_budget=ImageArtifactBudget(cap),
            )
        except Exception as exc:
            results[name] = exc

    first = threading.Thread(
        target=run,
        args=("first", "image-pair-concurrent-a"),
        name="evidence-first",
    )
    second = threading.Thread(
        target=run,
        args=("second", "image-pair-concurrent-b"),
        name="evidence-second",
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
    successful = [value for value in results.values() if isinstance(value, dict)]
    rejected = [value for value in results.values() if isinstance(value, Exception)]
    assert len(successful) == 1
    assert len(rejected) == 1
    assert "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in str(rejected[0])
    visible_bytes = sum(
        path.stat().st_size
        for path in (root / "images").rglob("*")
        if path.is_file()
    )
    assert visible_bytes <= cap


def test_scan_records_total_artifact_budget_exhaustion_without_losing_assets(
    tmp_path,
    monkeypatch,
):
    from paperconan import scan_dir

    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    trial = tmp_path / "trial"
    trial_assets, trial_errors = prepare_image_assets(str(source), str(trial))
    assert trial_errors == []
    total = sum(
        (trial / trial_assets[0][key]).stat().st_size
        for key in ("path", "preview_path")
    )
    monkeypatch.setenv(
        "PAPERCONAN_MAX_IMAGE_TOTAL_MB",
        str(total / (1024 * 1024)),
    )

    scan = scan_dir(
        str(source),
        str(tmp_path / "audit"),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )

    assert len(scan["image_assets"]) == 1
    assert len(scan["image_findings"]) == 1
    assert scan["image_findings"][0]["evidence"] is None
    assert any(
        "PAPERCONAN_MAX_IMAGE_TOTAL_MB" in item["error"]
        for item in scan["scan_errors"]
    )


def test_scan_catches_unexpected_diagnostic_error(tmp_path, monkeypatch):
    from paperconan import scan_dir

    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic diagnostic error")

    monkeypatch.setattr(_diagnostics, "diagnose_image_assets", unavailable)

    scan = scan_dir(
        str(source),
        str(tmp_path / "audit"),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )

    assert len(scan["image_assets"]) == 1
    assert scan["image_findings"] == []
    assert scan["scan_errors"] == [{
        "error": "optional image diagnostics unavailable: synthetic diagnostic error",
    }]


def test_native_evidence_crops_are_not_resized(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    findings, _ = diagnose_image_assets(assets, str(out))
    evidence = findings[0]["evidence"]
    for region, key in zip(findings[0]["regions"], ("crop_a_path", "crop_b_path")):
        image = Image.open(out / evidence[key])
        x0, y0, x1, y1 = region["box"]
        assert image.size == (x1 - x0, y1 - y0)
    preview = Image.open(out / evidence["preview_path"])
    assert preview.width <= 1600


def test_native_evidence_preserves_cmyk_crop_mode_and_channels(tmp_path):
    template = tmp_path / "template.png"
    _two_panel(template)
    source = tmp_path / "source"
    source.mkdir()
    with Image.open(template) as image:
        image.convert("CMYK").save(source / "Fig1.jpg", quality=95)
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    native = out / assets[0]["path"]
    box_a = (10, 10, 150, 130)
    box_b = (160, 10, 300, 130)

    evidence = write_native_pair_evidence(
        str(native),
        box_a,
        box_b,
        str(out),
        "image-pair-cmyk",
    )

    with Image.open(native) as image:
        expected_crops = [
            image.crop(box_a).copy(),
            image.crop(box_b).copy(),
        ]
    for key, expected in zip(
        ("crop_a_path", "crop_b_path"),
        expected_crops,
    ):
        assert evidence[key].endswith(".tif")
        with Image.open(out / evidence[key]) as crop:
            assert crop.mode == expected.mode == "CMYK"
            assert crop.size == expected.size == (140, 120)
            assert np.array_equal(np.asarray(crop), np.asarray(expected))


@pytest.mark.parametrize(
    "box",
    [
        (0, 0, 10),
        (0, 0, 10, 10, 20),
        (False, 0, 10, 10),
        (0, 0, 10.5, 10),
        (-1, 0, 10, 10),
        (10, 0, 10, 10),
        (11, 0, 10, 10),
        (0, 10, 10, 10),
        (0, 11, 10, 10),
        (0, 0, 311, 10),
        (0, 0, 10, 141),
    ],
    ids=[
        "too-short",
        "too-long",
        "bool",
        "fractional",
        "negative",
        "empty-x",
        "reversed-x",
        "empty-y",
        "reversed-y",
        "x-out-of-bounds",
        "y-out-of-bounds",
    ],
)
def test_native_evidence_rejects_invalid_crop_boxes_without_files(tmp_path, box):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))

    with pytest.raises(ValueError, match="crop box"):
        write_native_pair_evidence(
            str(out / assets[0]["path"]),
            box,
            (10, 10, 20, 20),
            str(out),
            "image-pair-invalid-box",
        )

    assert not (out / "images" / "evidence").exists()


def test_native_evidence_validates_second_crop_before_writing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))

    with pytest.raises(ValueError, match="crop box"):
        write_native_pair_evidence(
            str(out / assets[0]["path"]),
            (10, 10, 20, 20),
            (20, 20, True, 30),
            str(out),
            "image-pair-invalid-second-box",
        )

    assert not (out / "images" / "evidence").exists()


def test_native_evidence_rejects_oversized_replacement_before_hash_or_decode(
    tmp_path,
    monkeypatch,
):
    out = tmp_path / "audit"
    native = out / "images" / "native" / "source.png"
    native.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), (255, 0, 0)).save(native)
    expected_sha256 = hashlib.sha256(native.read_bytes()).hexdigest()
    native.write_bytes(b"replacement" * 32)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_MB", "0.00001")

    def reject_hashing(*args, **kwargs):
        raise AssertionError("oversized evidence source must not be hashed")

    def reject_decode(*args, **kwargs):
        raise AssertionError("oversized evidence source must not be decoded")

    monkeypatch.setattr(_evidence.hashlib, "sha256", reject_hashing)
    monkeypatch.setattr(Image, "open", reject_decode)

    with pytest.raises(
        ValueError,
        match="registered image exceeds PAPERCONAN_MAX_IMAGE_MB",
    ):
        write_native_pair_evidence(
            str(native),
            (0, 0, 4, 4),
            (4, 4, 8, 8),
            str(out),
            "image-pair-oversized-replacement",
            expected_sha256=expected_sha256,
        )

    assert not (out / "images" / "evidence").exists()


def test_native_evidence_raises_typed_source_change_after_scoring(tmp_path):
    out = tmp_path / "audit"
    native = out / "images" / "native" / "source.png"
    native.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), (255, 0, 0)).save(native)
    expected_sha256 = hashlib.sha256(native.read_bytes()).hexdigest()
    Image.new("RGB", (8, 8), (0, 0, 255)).save(native)

    with pytest.raises(
        _evidence.ImageEvidenceSourceChangedError,
        match="registered image changed after scoring",
    ):
        write_native_pair_evidence(
            str(native),
            (0, 0, 4, 4),
            (4, 4, 8, 8),
            str(out),
            "image-pair-source-changed",
            expected_sha256=expected_sha256,
        )

    assert not (out / "images" / "evidence").exists()


def test_native_evidence_rejects_full_image_pixels_before_crop_validation(
    tmp_path,
    monkeypatch,
):
    out = tmp_path / "audit"
    native = out / "images" / "native" / "source.png"
    native.parent.mkdir(parents=True)
    Image.new("RGB", (11, 10), (255, 0, 0)).save(native)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", "100")

    def reject_crop_validation(*args, **kwargs):
        raise AssertionError("oversized full image must precede crop validation")

    monkeypatch.setattr(
        _evidence,
        "_validated_crop_box",
        reject_crop_validation,
    )

    with pytest.raises(
        ValueError,
        match="registered image exceeds PAPERCONAN_MAX_IMAGE_PIXELS",
    ):
        write_native_pair_evidence(
            str(native),
            (0, 0, 5, 5),
            (5, 5, 10, 10),
            str(out),
            "image-pair-full-pixel-cap",
        )

    assert not (out / "images" / "evidence").exists()


def test_native_evidence_full_image_pixel_cap_is_dynamic_and_writes_nothing_on_rejection(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    native = out / assets[0]["path"]
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", "43399")

    with pytest.raises(ValueError, match="PAPERCONAN_MAX_IMAGE_PIXELS"):
        write_native_pair_evidence(
            str(native),
            (0, 0, 10, 10),
            (10, 0, 20, 10),
            str(out),
            "image-pair-pixel-cap",
        )

    assert not (out / "images" / "evidence").exists()

    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", "43400")
    evidence = write_native_pair_evidence(
        str(native),
        (0, 0, 10, 10),
        (10, 0, 20, 10),
        str(out),
        "image-pair-pixel-cap",
    )
    assert (out / evidence["crop_a_path"]).is_file()


def test_native_evidence_uses_stable_file_object_when_source_path_is_swapped(
    tmp_path,
    monkeypatch,
):
    out = tmp_path / "audit"
    native = out / "images" / "native" / "source.png"
    native.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), (255, 0, 0)).save(native)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (8, 8), (0, 0, 255)).save(outside)
    displaced = tmp_path / "displaced.png"
    original_open = Image.open
    opened_from_file_object = False
    swapped = False

    def swap_during_pillow_open(fp, *args, **kwargs):
        nonlocal opened_from_file_object, swapped
        opened_from_file_object = hasattr(fp, "read")
        if not swapped:
            native.rename(displaced)
            native.symlink_to(outside)
            swapped = True
        return original_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", swap_during_pillow_open)

    evidence = write_native_pair_evidence(
        str(native),
        (0, 0, 4, 4),
        (4, 4, 8, 8),
        str(out),
        "image-pair-stable-source",
    )

    assert opened_from_file_object
    with original_open(out / evidence["crop_a_path"]) as crop:
        assert crop.getpixel((0, 0)) == (255, 0, 0)


def test_native_evidence_pins_one_root_for_source_and_all_publication(
    tmp_path,
    monkeypatch,
):
    out = tmp_path / "audit"
    native = out / "images" / "native" / "source.png"
    native.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), (255, 0, 0)).save(native)
    displaced = tmp_path / "displaced-audit"
    original_open = Image.open
    swapped = False

    def swap_root_during_source_open(fp, *args, **kwargs):
        nonlocal swapped
        if hasattr(fp, "read") and not swapped:
            out.rename(displaced)
            out.mkdir()
            swapped = True
        return original_open(fp, *args, **kwargs)

    monkeypatch.setattr(Image, "open", swap_root_during_source_open)
    evidence_id = "image-pair-pinned-root"

    with pytest.raises(ValueError, match="artifact root.*changed"):
        write_native_pair_evidence(
            str(native),
            (0, 0, 4, 4),
            (4, 4, 8, 8),
            str(out),
            evidence_id,
        )

    assert swapped
    expected_names = {
        f"{evidence_id}-a.png",
        f"{evidence_id}-b.png",
        f"{evidence_id}-preview.jpg",
    }
    for root in (out, displaced):
        evidence_dir = root / "images" / "evidence"
        assert not evidence_dir.exists() or not (
            expected_names & {path.name for path in evidence_dir.iterdir()}
        )


def test_native_evidence_rejects_native_parent_swap(tmp_path, monkeypatch):
    out = tmp_path / "audit"
    native_dir = out / "images" / "native"
    native_dir.mkdir(parents=True)
    native = native_dir / "source.png"
    Image.new("RGB", (8, 8), (255, 0, 0)).save(native)
    outside_dir = tmp_path / "outside-native"
    outside_dir.mkdir()
    outside = outside_dir / native.name
    Image.new("RGB", (8, 8), (0, 0, 255)).save(outside)
    displaced = tmp_path / "displaced-native"
    source_path = str(native.resolve())
    original_os_open = _evidence.os.open
    original_image_open = Image.open
    outside_identity = (outside.stat().st_dev, outside.stat().st_ino)
    outside_consumed = False
    swapped = False

    def swap_parent_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        path_text = _evidence.os.fspath(path)
        if not swapped and (
            path_text == source_path
            or (path_text == "native" and kwargs.get("dir_fd") is not None)
        ):
            native_dir.rename(displaced)
            native_dir.symlink_to(outside_dir, target_is_directory=True)
            swapped = True
        return original_os_open(path, flags, *args, **kwargs)

    def track_image_open(fp, *args, **kwargs):
        nonlocal outside_consumed
        if hasattr(fp, "fileno"):
            opened = _evidence.os.fstat(fp.fileno())
            outside_consumed = (
                opened.st_dev,
                opened.st_ino,
            ) == outside_identity
        return original_image_open(fp, *args, **kwargs)

    monkeypatch.setattr(_evidence.os, "open", swap_parent_before_open)
    monkeypatch.setattr(Image, "open", track_image_open)

    with pytest.raises(ValueError, match="artifact root"):
        write_native_pair_evidence(
            str(native),
            (0, 0, 4, 4),
            (4, 4, 8, 8),
            str(out),
            "image-pair-parent-swap",
        )

    assert not outside_consumed
    assert not (out / "images" / "evidence").exists()


@pytest.mark.parametrize(
    "value",
    ["inf", "not-a-number", "9" * 5000],
    ids=["non-finite", "malformed", "overflow"],
)
def test_native_evidence_rejects_invalid_pixel_limits_cleanly(
    tmp_path,
    monkeypatch,
    value,
):
    out = tmp_path / "audit"
    native = out / "images" / "native" / "source.png"
    native.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(native)
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_PIXELS", value)

    with pytest.raises(
        ValueError,
        match="invalid PAPERCONAN_MAX_IMAGE_PIXELS",
    ):
        write_native_pair_evidence(
            str(native),
            (0, 0, 4, 4),
            (4, 4, 8, 8),
            str(out),
            "image-pair-invalid-limit",
        )

    assert not (out / "images" / "evidence").exists()


def test_diagnostics_use_native_coordinates_for_exif_oriented_images(tmp_path):
    template = tmp_path / "template.png"
    _two_panel(template)
    source = tmp_path / "source"
    source.mkdir()
    exif = Image.Exif()
    exif[274] = 6
    with Image.open(template) as image:
        image.save(source / "Fig1.jpg", quality=95, exif=exif)
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))

    findings, errors = diagnose_image_assets(assets, str(out))

    assert errors == []
    assert findings
    for region in findings[0]["regions"]:
        x0, y0, x1, y1 = region["box"]
        assert 0 <= x0 < x1 <= assets[0]["width"]
        assert 0 <= y0 < y1 <= assets[0]["height"]


def test_native_evidence_reads_only_from_artifact_root(tmp_path):
    outside = tmp_path / "outside.png"
    _two_panel(outside)
    out = tmp_path / "audit"

    with pytest.raises(ValueError, match="artifact root"):
        write_native_pair_evidence(
            str(outside),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            "image-pair-test",
        )


def test_native_evidence_rejects_path_components_in_evidence_id(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))

    with pytest.raises(ValueError, match="evidence_id"):
        write_native_pair_evidence(
            str(out / assets[0]["path"]),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            "../../../outside",
        )


def test_native_evidence_rejects_outside_symlink_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    outside = tmp_path / "outside"
    outside.mkdir()
    (out / "images" / "evidence").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="destination escapes artifact root"):
        write_native_pair_evidence(
            str(out / assets[0]["path"]),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            "image-pair-symlink",
        )

    assert list(outside.iterdir()) == []


def test_native_evidence_rejects_evidence_parent_swap_before_staging(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    evidence_dir = out / "images" / "evidence"
    evidence_dir.mkdir()
    displaced = tmp_path / "displaced-evidence"
    outside = tmp_path / "outside-evidence"
    outside.mkdir()
    original_open = _evidence.os.open
    swapped = False

    def swap_before_stage_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if (
            not swapped
            and flags & _evidence.os.O_CREAT
            and flags & _evidence.os.O_EXCL
        ):
            evidence_dir.rename(displaced)
            evidence_dir.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(_evidence.os, "open", swap_before_stage_open)

    with pytest.raises(ValueError, match="destination|artifact root|changed"):
        write_native_pair_evidence(
            str(out / assets[0]["path"]),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            "image-pair-parent-swap",
        )

    assert list(outside.iterdir()) == []
    assert list(displaced.iterdir()) == []


def test_native_evidence_retains_final_symlinks_and_exact_reruns(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    evidence_id = "image-pair-final-symlink"
    evidence_dir = out / "images" / "evidence"
    evidence_dir.mkdir()
    outside_crop = tmp_path / "outside-crop.bin"
    outside_preview = tmp_path / "outside-preview.bin"
    crop_sentinel = b"outside crop sentinel"
    preview_sentinel = b"outside preview sentinel"
    outside_crop.write_bytes(crop_sentinel)
    outside_preview.write_bytes(preview_sentinel)
    final_crop = evidence_dir / f"{evidence_id}-a.png"
    final_preview = evidence_dir / f"{evidence_id}-preview.jpg"
    final_crop.symlink_to(outside_crop)
    final_preview.symlink_to(outside_preview)

    with pytest.raises(RuntimeError) as exc_info:
        write_native_pair_evidence(
            str(out / assets[0]["path"]),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            evidence_id,
        )

    assert "retained existing visible entry" in str(exc_info.value)
    assert outside_crop.read_bytes() == crop_sentinel
    assert outside_preview.read_bytes() == preview_sentinel
    assert final_crop.is_symlink()
    assert final_preview.is_symlink()
    assert final_crop.readlink() == outside_crop
    assert final_preview.readlink() == outside_preview

    rerun_id = "image-pair-exact-rerun"
    evidence = write_native_pair_evidence(
        str(out / assets[0]["path"]),
        (10, 10, 150, 130),
        (160, 10, 300, 130),
        str(out),
        rerun_id,
    )

    rerun = write_native_pair_evidence(
        str(out / assets[0]["path"]),
        (10, 10, 150, 130),
        (160, 10, 300, 130),
        str(out),
        rerun_id,
    )

    assert rerun == evidence
    with Image.open(out / evidence["crop_a_path"]) as crop:
        assert crop.size == (140, 120)
    with Image.open(out / evidence["preview_path"]) as preview:
        assert preview.width <= 1600
    assert not any(path.name.startswith(".") for path in evidence_dir.iterdir())


@pytest.mark.parametrize("failure_step", [2, 3])
def test_native_evidence_publication_failure_retains_visible_partial_set(
    tmp_path,
    monkeypatch,
    failure_step,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    evidence_id = f"image-pair-partial-{failure_step}"
    final_names = [
        f"{evidence_id}-a.png",
        f"{evidence_id}-b.png",
        f"{evidence_id}-preview.jpg",
    ]
    evidence_dir = out / "images" / "evidence"
    budget = ImageArtifactBudget(1024 * 1024 * 1024)
    budget.initialize_from_root(out)
    used_before = budget.used_bytes
    real_link = os.link
    install_calls = 0
    failed = False

    def fail_selected_install(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal install_calls, failed
        if (
            not failed
            and Path(src).name.startswith(".paperconan-evidence-")
            and Path(dst).name in final_names
        ):
            install_calls += 1
            if install_calls == failure_step:
                failed = True
                raise OSError(
                    f"synthetic evidence install {failure_step} failure"
                )
        return real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(_evidence.os, "link", fail_selected_install)

    with pytest.raises(
        RuntimeError,
        match=f"synthetic evidence install {failure_step} failure",
    ) as exc_info:
        write_native_pair_evidence(
            str(native),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            evidence_id,
            artifact_budget=budget,
        )

    assert failed
    installed_count = failure_step - 1
    assert [
        os.path.lexists(evidence_dir / name)
        for name in final_names
    ] == [
        index < installed_count
        for index in range(len(final_names))
    ]
    retained_size = sum(
        (evidence_dir / name).stat().st_size
        for name in final_names[:installed_count]
    )
    assert budget.used_bytes == used_before + retained_size
    error = str(exc_info.value)
    assert "publication incomplete" in error
    for name in final_names[:installed_count]:
        assert f"images/evidence/{name}" in error
    hidden_entries = [
        path
        for path in evidence_dir.iterdir()
        if path.name.startswith(".paperconan-evidence-")
    ]
    assert hidden_entries == []

    budget.max_bytes = budget.used_bytes
    later_id = f"{evidence_id}-later"
    with pytest.raises(
        ValueError,
        match="PAPERCONAN_MAX_IMAGE_TOTAL_MB",
    ):
        write_native_pair_evidence(
            str(native),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            later_id,
            artifact_budget=budget,
        )
    assert not any(
        path.name.startswith(later_id)
        for path in evidence_dir.iterdir()
    )


def test_evidence_publication_retains_concurrent_final_without_backups(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    evidence_id = "image-pair-concurrent-final"
    write_native_pair_evidence(
        str(native),
        (10, 10, 150, 130),
        (160, 10, 300, 130),
        str(out),
        evidence_id,
    )
    evidence_dir = out / "images" / "evidence"
    first_final = evidence_dir / f"{evidence_id}-a.png"
    second_final = evidence_dir / f"{evidence_id}-b.png"
    preview_final = evidence_dir / f"{evidence_id}-preview.jpg"
    first_final.unlink()
    second_final.unlink()
    preview_final.unlink()
    concurrent_target = tmp_path / "concurrent-evidence.png"
    Image.new("RGB", (7, 5), (10, 20, 30)).save(concurrent_target)
    target_bytes = concurrent_target.read_bytes()
    real_link = os.link
    first_replaced = False

    def link_first_then_fail_second(
        src,
        dst,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal first_replaced
        src_name = Path(src).name
        dst_name = Path(dst).name
        if (
            src_name.startswith(".paperconan-evidence-")
            and dst_name == first_final.name
        ):
            real_link(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            os.unlink(dst, dir_fd=dst_dir_fd)
            os.symlink(concurrent_target, dst, dir_fd=dst_dir_fd)
            first_replaced = True
            return
        if (
            first_replaced
            and src_name.startswith(".paperconan-evidence-")
            and dst_name == second_final.name
        ):
            raise OSError("synthetic second evidence install failure")
        return real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(
        _evidence.os,
        "link",
        link_first_then_fail_second,
    )

    with pytest.raises(RuntimeError) as exc_info:
        write_native_pair_evidence(
            str(native),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            evidence_id,
        )

    assert first_replaced
    assert first_final.is_symlink()
    assert first_final.readlink() == concurrent_target
    assert concurrent_target.read_bytes() == target_bytes
    error = str(exc_info.value)
    assert "retained uncertain visible entry" in error
    assert f"images/evidence/{first_final.name}" in error
    hidden_entries = [
        path
        for path in evidence_dir.iterdir()
        if path.name.startswith(".paperconan-evidence-")
    ]
    assert hidden_entries == []


def test_evidence_publication_does_not_move_concurrent_final_replacement(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, errors = prepare_image_assets(str(source), str(out))
    assert errors == []
    native = out / assets[0]["path"]
    evidence_id = "image-pair-concurrent-prepare"
    evidence = write_native_pair_evidence(
        str(native),
        (10, 10, 150, 130),
        (160, 10, 300, 130),
        str(out),
        evidence_id,
    )
    first_final = out / evidence["crop_a_path"]
    second_final = out / evidence["crop_b_path"]
    first_final.unlink()
    concurrent_bytes = b"concurrent evidence replacement"
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
            and Path(dst).name == first_final.name
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
        _evidence.os,
        "link",
        create_final_before_link,
    )

    with pytest.raises(RuntimeError) as exc_info:
        write_native_pair_evidence(
            str(native),
            (10, 10, 150, 130),
            (160, 10, 300, 130),
            str(out),
            evidence_id,
        )

    assert replacement_installed
    assert "retained existing visible entry" in str(exc_info.value)
    assert first_final.read_bytes() == concurrent_bytes
    assert second_final.is_file()


def test_diagnostic_finding_ids_and_order_are_deterministic(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    first, _ = diagnose_image_assets(assets, str(out))
    second, _ = diagnose_image_assets(assets, str(out))
    assert [f["finding_id"] for f in first] == [f["finding_id"] for f in second]
    assert first == second


def test_zero_finding_cap_writes_no_evidence(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    assets, _ = prepare_image_assets(str(source), str(out))
    monkeypatch.setenv("PAPERCONAN_MAX_IMAGE_FINDINGS", "0")

    findings, errors = diagnose_image_assets(assets, str(out))

    assert findings == []
    assert errors == [{
        "error": (
            "1 image findings omitted; "
            "set PAPERCONAN_MAX_IMAGE_FINDINGS to raise"
        ),
    }]
    assert not (out / "images" / "evidence").exists()


def test_scan_diagnostics_never_change_asset_inventory(tmp_path):
    from paperconan import scan_dir

    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    without = scan_dir(
        str(source),
        str(tmp_path / "without"),
        write_html=False,
        images=True,
        image_diagnostics=False,
    )
    with_hints = scan_dir(
        str(source),
        str(tmp_path / "with"),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )
    comparable = lambda asset: {
        key: value for key, value in asset.items()
        if key not in {"path", "preview_path"}
    }
    assert [comparable(a) for a in without["image_assets"]] == [
        comparable(a) for a in with_hints["image_assets"]
    ]
    assert without["image_findings"] == []
    assert with_hints["image_findings"]


def test_deterministic_report_embeds_registered_image_evidence(tmp_path):
    from paperconan import scan_dir

    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    scan_dir(
        str(source),
        str(out),
        write_html=True,
        images=True,
        image_diagnostics=True,
    )
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "image_pair_similarity_signal" in html
    assert "data:image/jpeg;base64," in html


def test_report_ignores_finding_evidence_path_and_uses_registered_asset(tmp_path):
    from paperconan import scan_dir
    from paperconan._html import write_html_report

    source = tmp_path / "source"
    source.mkdir()
    _two_panel(source / "Fig1.png")
    out = tmp_path / "audit"
    scan = scan_dir(
        str(source),
        str(out),
        write_html=False,
        images=True,
        image_diagnostics=True,
    )
    sentinel = b"unregistered-image-evidence-sentinel"
    (out / "arbitrary.jpg").write_bytes(sentinel)
    scan["image_findings"][0]["evidence"]["preview_path"] = "arbitrary.jpg"

    write_html_report(scan, str(out / "report.html"))

    html = (out / "report.html").read_text(encoding="utf-8")
    sentinel_b64 = base64.b64encode(sentinel).decode("ascii")
    assert sentinel_b64 not in html
    encoded = html.split("data:image/jpeg;base64,", 1)[1].split('"', 1)[0]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as preview:
        assert preview.width <= 1600

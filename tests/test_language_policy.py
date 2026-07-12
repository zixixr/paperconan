from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
_TOKEN_HEX = (
    "6672617564",
    "6661627269636174",
    "66616b65",
    "6d6973636f6e64756374",
    "6775696c7479",
    "e980a0e58187",
    "e4bcaae980a0",
    "e5ada6e69cafe4b88de7abaf",
)
_DETECTOR_COPY_HEX = (
    "e68c87e7bab9",
    "e7bc96e980a0",
    "e980a0e695b0",
    "e6898be694b9",
    "e5a48de588b6",
    "e9878de696b0e6b497e7898c",
    "e58faae694b9e5898de5afbce695b0e5ad97",
    "e58f8de59091",
    "e5a3b0e7a7b0",
    "e8afafe6a087",
    "e99a8fe6898be58791e695b0",
    "e4bcaae7b2bee7a1ae",
    "e5a19ee8bf9b",
    "e58585e5bd93",
    "e68ea8e587bae58fa6e4b880e58897",
    "e6b4bee7949fe5b08fe5b985e5baa6e689b0e58aa8",
    "e4b998e4ba86206b20e5808de5908e",
    "636f6c5f6220e794b120636f6c5f61",
    "e69c80e5bcbae79a84e4bfa1e58fb7",
    "e7a1aee5ae9ee69c89e6b4bee7949fe585b3e7b3bb",
    "e78bace7ab8be5b08fe9bca0e4b88de58fafe883bde59ca8e5a49ae7bb84",
    "e5a48de794a8e99381e8af81",
    "e7a1ace4b88be7bb93e8aeba",
    "e4bfa1e58fb7e69bb4e7a1ac",
    "e695b0e68daee5a48de794a8",
    "e69e81e5b091",
    "e694b9e4b880e6a0bc",
    "e4bd9ce88085e794a8",
    "e4bd9ce88085e4bb8e",
    "e593aae4b8aae69cabe4bd8de8a2abe5818fe59091e4ba86",
    "e69cace5b0b1e5928ce6ba90e58897e4b8a5e6a0bce79bb8e585b3efbc8ce59088e79086",
    "e5b19ee5b8b8e68081",
    "e4b88de7ad89e4ba8e22e6b2a1e997aee9a29822",
    "e9809ae5b8b8",
    "e69cace69da5e5b0b1e78bace7ab8be6b58be9878f",
    "e5a4a9e784b6",
    "e588bbe6848fe68e92e999a4",
    "e6bc82e4baae",
    "e5818fe5a5bd",
    "e58fb7e7a7b0",
    "e694b9e4ba86e5b091e9878fe580bc",
    "e69cace8afa5e78bace7ab8b",
    "e69c80e69893e8afafe68aa5",
    "e68aa4e6a08fe69c80e4b8a5",
    "e59088e79086e79a84e585b1e4baabe5afb9e785a7e7bb84",
    "e69c80e580bce5be97e8bfbd",
    "e59088e6b395e5a48de794a8",
    "e6ada3e5bd93e79086e794b1",
    "e5b19ee9a284e69c9f",
    "e79c9fe5ae9ee4ba92e8a1a5e585b3e7b3bb",
    "e7a1aee69c89e4b8a5e6a0bce7babfe680a7e585b3e7b3bb",
    "e5908ce4b880e4bbbde695b0e68daee5a49ae59bbee9878de7bb98e5b19ee9a284e69c9f",
    "e58588e58699e6a682e695b0",
    "e5a1abe4ba86e4b8a4e6aca1",
    "e4bb8ee4b880e7bb84206261736520e695b0e68dae",
)
_DETECTOR_DOC_PATHS = (
    "docs/detectors.md",
    "skills/paperconan/references/detectors.md",
)
_TEXT_SUFFIXES = {
    ".py", ".md", ".toml", ".yml", ".yaml",
    ".json", ".html", ".sh", ".txt",
}
_FALLBACK_ROOTS = (
    "src", "tests", "skills", "docs", "examples", ".github",
)
_EXPECTED_NEUTRAL_COPY = (
    (
        "src/paperconan/_html.py",
        "末位数字分布偏离均匀性是统计信号，需要结合测量精度、取整规则和数据来源核查。",
    ),
    (
        "src/paperconan/_html.py",
        "某些末两位出现频率较高，是需要结合测量精度和数据处理流程核查的统计信号。",
    ),
    (
        "examples/demo_paper/audit/report.html",
        "末位数字分布偏离均匀性是统计信号，需要结合测量精度、取整规则和数据来源核查。",
    ),
    (
        "skills/paperconan/references/detectors.md",
        'col_b 与 col_a 呈现固定偏移，但被标注为独立"实验组"，属于需要澄清的数据不一致。',
    ),
    (
        "skills/paperconan/references/detectors.md",
        "这比整 sheet 末位分布更局部，是需要核查的数据不一致信号；仍需结合原表、Methods 和上下文解释。",
    ),
    (
        "README.md",
        "更高效地识别和澄清数据不一致",
    ),
    (
        "examples/README.md",
        "those exact relationships are data inconsistencies that need clarification.",
    ),
    (
        "examples/README.md",
        "but rounding, instrument settings, normalization, or other processing may explain them.",
    ),
    (
        "docs/faq.md",
        "实验实施过程、原始记录和样本来源是否可核查，不在其评估范围内。",
    ),
    (
        "docs/faq.md",
        "选择性分析、引用实践、同行评议流程、图像取证和图表像素数字化也不覆盖。",
    ),
)


def _tokens():
    return [
        bytes.fromhex(value).decode("utf-8").casefold()
        for value in _TOKEN_HEX
    ]


def _detector_copy_fragments():
    return [
        bytes.fromhex(value).decode("utf-8").casefold()
        for value in _DETECTOR_COPY_HEX
    ]


def _allowed_path(path, root=ROOT):
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return False
    if relative.startswith(("recheck/", "batches/")):
        return False
    return path.name == ".gitignore" or path.suffix.lower() in _TEXT_SUFFIXES


def _tracked_public_text_files(root=ROOT):
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout:
        paths = []
        for value in proc.stdout.split(b"\0"):
            if not value:
                continue
            try:
                relative = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
            paths.append(root / relative)
    else:
        paths = [
            path
            for name in _FALLBACK_ROOTS
            for path in (root / name).rglob("*")
            if path.is_file()
        ]
        paths.extend(
            root / name
            for name in ("README.md", "pyproject.toml", ".gitignore")
            if (root / name).is_file()
        )
    return sorted(path for path in paths if _allowed_path(path, root))


def _policy_hits(path):
    hits = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return hits
    for line_number, line in enumerate(lines, 1):
        folded = line.casefold()
        for token_number, token in enumerate(_tokens(), 1):
            if token in folded:
                hits.append((line_number, token_number))
    return hits


def test_public_files_use_neutral_language():
    hits = [
        (
            path.relative_to(ROOT).as_posix(),
            line_number,
            token_number,
        )
        for path in _tracked_public_text_files()
        for line_number, token_number in _policy_hits(path)
    ]
    assert not hits, "\n".join(
        f"{path}:{line}:T{token}"
        for path, line, token in hits
    )


def test_fallback_uses_supplied_root_and_stays_bounded(tmp_path, monkeypatch):
    if "root" not in inspect.signature(_allowed_path).parameters:
        pytest.fail("alternate root is not supported")

    included = (
        "src/package.py",
        "tests/test_example.py",
        "docs/guide.md",
        "examples/demo.txt",
        ".github/workflows/test.yml",
        "README.md",
        "pyproject.toml",
        ".gitignore",
    )
    excluded = (
        "recheck/private.md",
        "batches/private.md",
        "private/other.md",
        "examples/demo.xlsx",
    )
    for relative in included + excluded:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b""),
    )

    assert _tracked_public_text_files(tmp_path) == sorted(
        tmp_path / relative for relative in included
    )


def test_git_collection_skips_undecodable_path_records(tmp_path, monkeypatch):
    public = tmp_path / "docs" / "guide.md"
    public.parent.mkdir(parents=True)
    public.write_text("content", encoding="utf-8")
    stdout = b"docs/guide.md\0docs/" + bytes([255]) + b".md\0"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout),
    )

    try:
        paths = _tracked_public_text_files(tmp_path)
    except UnicodeDecodeError:
        raise AssertionError("undecodable git path was not skipped") from None

    assert paths == [public]


def test_reviewed_public_copy_is_neutral_and_precise():
    missing = [
        (path, copy_id)
        for copy_id, (path, expected) in enumerate(_EXPECTED_NEUTRAL_COPY, 1)
        if " ".join(expected.split()) not in " ".join(
            (ROOT / path).read_text(encoding="utf-8").split()
        )
    ]
    assert not missing, "\n".join(
        f"{path}:C{copy_id}"
        for path, copy_id in missing
    )


def test_detector_docs_use_observed_or_conditional_language():
    hits = []
    for relative in _DETECTOR_DOC_PATHS:
        path = ROOT / relative
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            1,
        ):
            folded = line.casefold()
            for copy_id, fragment in enumerate(
                _detector_copy_fragments(),
                1,
            ):
                if fragment in folded:
                    hits.append((relative, line_number, copy_id))

    assert not hits, "\n".join(
        f"{path}:{line}:D{copy_id}"
        for path, line, copy_id in hits
    )

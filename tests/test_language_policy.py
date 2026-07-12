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

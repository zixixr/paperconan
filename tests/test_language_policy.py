from __future__ import annotations

from pathlib import Path
import subprocess


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


def _tokens():
    return [
        bytes.fromhex(value).decode("utf-8").casefold()
        for value in _TOKEN_HEX
    ]


def _allowed_path(path):
    relative = path.relative_to(ROOT).as_posix()
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
        paths = [
            root / value.decode("utf-8")
            for value in proc.stdout.split(b"\0")
            if value
        ]
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
    return sorted(path for path in paths if _allowed_path(path))


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

"""Shared neutral-language policy matching."""
from __future__ import annotations

import re


_IDENTIFIER_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])"
    r"|(?<=[A-Z])(?=[A-Z][a-z])"
    r"|(?<=[A-Za-z])(?=[0-9])"
    r"|(?<=[0-9])(?=[A-Za-z])"
)
_BLOCKED_LANGUAGE_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\b(?:de)?" + "fr" + r"aud(?:s|ed|ing|ulent(?:ly)?|ulence|ster(?:s)?|er(?:s)?)?\b",
        r"\b" + "fabri" + r"cat(?:e|es|ed|ing|ion(?:s)?|or(?:s)?)\b",
        r"\b" + "fa" + r"k(?:e|es|ed|ing|er(?:s)?|ery)\b",
        r"\b" + "fal" + r"sif(?:y|ies|ied|ying|ication(?:s)?|ier(?:s)?)\b",
        r"\b" + "mis" + r"conduct(?:s|ed|ing)?\b",
        r"\b" + "guil" + r"t(?:y|ier|iest|ily|iness)?\b",
        re.escape("造" + "假"),
        re.escape("伪" + "造"),
        re.escape("捏" + "造"),
        re.escape("作" + "假"),
        re.escape("实" + "锤"),
    )
)


def _with_identifier_boundaries(text: str) -> str:
    return _IDENTIFIER_BOUNDARY.sub(" ", text).replace("_", " ")


def contains_blocked_language(text: str) -> bool:
    """Return whether text contains a blocked expression family."""
    normalized = _with_identifier_boundaries(text)
    return any(
        pattern.search(text) or pattern.search(normalized)
        for pattern in _BLOCKED_LANGUAGE_PATTERNS
    )

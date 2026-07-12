from __future__ import annotations

import json
import math
import re


class SidecarLimitError(ValueError):
    def __init__(self, reason, **details):
        self.reason = reason
        self.details = details
        super().__init__(reason)


def _skip_whitespace(text, position):
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _utf8_codepoint_length(codepoint):
    if codepoint <= 0x7F:
        return 1
    if codepoint <= 0x7FF:
        return 2
    if codepoint <= 0xFFFF:
        return 3
    return 4


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _unicode_escape_value(text, position):
    end = position + 4
    if (
        end > len(text)
        or any(char not in _HEX_DIGITS for char in text[position:end])
    ):
        raise ValueError("invalid Unicode escape")
    return int(text[position:end], 16)


def _scan_json_string_token(text, position):
    if position >= len(text) or text[position] != '"':
        raise ValueError("expected JSON string")
    start = position
    position += 1
    utf8_length = 0
    while position < len(text):
        char = text[position]
        codepoint = ord(char)
        if char == '"':
            return position + 1, utf8_length
        if codepoint < 0x20:
            raise ValueError("invalid control character")
        if char != "\\":
            utf8_length += _utf8_codepoint_length(codepoint)
            position += 1
            continue

        if position + 1 >= len(text):
            raise ValueError("unterminated JSON escape")
        escape = text[position + 1]
        if escape in '"\\/bfnrt':
            utf8_length += 1
            position += 2
            continue
        if escape != "u":
            raise ValueError("invalid JSON escape")

        codepoint = _unicode_escape_value(text, position + 2)
        next_position = position + 6
        if (
            0xD800 <= codepoint <= 0xDBFF
            and text.startswith("\\u", next_position)
        ):
            low = _unicode_escape_value(text, next_position + 2)
            if 0xDC00 <= low <= 0xDFFF:
                utf8_length += 4
                position = next_position + 6
                continue
        utf8_length += 3
        position = next_position
    raise ValueError(f"unterminated JSON string at {start}")


def _decode_json_string_token(text, start, end):
    return json.loads(text[start:end])


_NUMBER_RE = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
)


class _SidecarParser:
    def __init__(
        self,
        text,
        *,
        entry_limit,
        name_byte_limit,
        normalize_name,
        retain_managed_names,
    ):
        self.text = text
        self.position = 0
        self.entry_limit = max(0, int(entry_limit))
        self.name_byte_limit = max(0, int(name_byte_limit))
        self.normalize_name = normalize_name
        self.retain_managed_names = retain_managed_names

    def _skip_whitespace(self):
        self.position = _skip_whitespace(
            self.text, self.position
        )

    def _parse_string(self, *, retain=True):
        self._skip_whitespace()
        start = self.position
        end, utf8_length = _scan_json_string_token(
            self.text, start
        )
        self.position = end
        if not retain:
            return None, utf8_length
        return (
            _decode_json_string_token(self.text, start, end),
            utf8_length,
        )

    def _parse_number(self, *, retain):
        match = _NUMBER_RE.match(self.text, self.position)
        if match is None:
            raise ValueError("invalid JSON value")
        token = match.group(0)
        self.position = match.end()
        return json.loads(token) if retain else None

    def _parse_array(self, *, retain):
        self.position += 1
        self._skip_whitespace()
        values = [] if retain else None
        if (
            self.position < len(self.text)
            and self.text[self.position] == "]"
        ):
            self.position += 1
            return values
        while True:
            value = self._parse_value(retain=retain)
            if retain:
                values.append(value)
            self._skip_whitespace()
            if self.position >= len(self.text):
                raise ValueError("unterminated JSON array")
            delimiter = self.text[self.position]
            self.position += 1
            if delimiter == "]":
                return values
            if delimiter != ",":
                raise ValueError("invalid JSON array")
            self._skip_whitespace()

    def _parse_object(self, *, retain):
        self.position += 1
        self._skip_whitespace()
        value = {} if retain else None
        if (
            self.position < len(self.text)
            and self.text[self.position] == "}"
        ):
            self.position += 1
            return value
        while True:
            key, _length = self._parse_string(retain=True)
            self._skip_whitespace()
            if (
                self.position >= len(self.text)
                or self.text[self.position] != ":"
            ):
                raise ValueError("invalid JSON object")
            self.position += 1
            item = self._parse_value(retain=retain)
            if retain:
                value[key] = item
            self._skip_whitespace()
            if self.position >= len(self.text):
                raise ValueError("unterminated JSON object")
            delimiter = self.text[self.position]
            self.position += 1
            if delimiter == "}":
                return value
            if delimiter != ",":
                raise ValueError("invalid JSON object")
            self._skip_whitespace()

    def _parse_value(self, *, retain=True):
        self._skip_whitespace()
        if self.position >= len(self.text):
            raise ValueError("missing JSON value")
        char = self.text[self.position]
        if char == '"':
            value, _length = self._parse_string(retain=retain)
            return value
        if char == "[":
            return self._parse_array(retain=retain)
        if char == "{":
            return self._parse_object(retain=retain)
        for literal, value in (
            ("true", True),
            ("false", False),
            ("null", None),
            ("NaN", math.nan),
            ("Infinity", math.inf),
            ("-Infinity", -math.inf),
        ):
            if self.text.startswith(literal, self.position):
                self.position += len(literal)
                return value if retain else None
        return self._parse_number(retain=retain)

    def _parse_managed_array(self):
        self._skip_whitespace()
        if (
            self.position >= len(self.text)
            or self.text[self.position] != "["
        ):
            self._parse_value(retain=False)
            return []
        self.position += 1
        self._skip_whitespace()
        if (
            self.position < len(self.text)
            and self.text[self.position] == "]"
        ):
            self.position += 1
            return []

        names = set()
        entries_inspected = 0
        retained_name_bytes = 0
        while True:
            if entries_inspected >= self.entry_limit:
                raise SidecarLimitError(
                    "source sidecar managed entry limit",
                    managed_entries_inspected=entries_inspected,
                    managed_entries_retained=len(names),
                    managed_name_bytes_retained=(
                        retained_name_bytes
                    ),
                )
            entries_inspected += 1
            self._skip_whitespace()
            if (
                self.position < len(self.text)
                and self.text[self.position] == '"'
            ):
                start = self.position
                end, requested_name_bytes = (
                    _scan_json_string_token(self.text, start)
                )
                if (
                    self.retain_managed_names
                    and retained_name_bytes
                    + requested_name_bytes
                    > self.name_byte_limit
                ):
                    raise SidecarLimitError(
                        "source sidecar managed name byte limit",
                        managed_entries_inspected=(
                            entries_inspected
                        ),
                        managed_entries_retained=len(names),
                        managed_name_bytes_retained=(
                            retained_name_bytes
                        ),
                        requested_name_bytes=(
                            requested_name_bytes
                        ),
                    )
                self.position = end
                if self.retain_managed_names:
                    decoded = _decode_json_string_token(
                        self.text, start, end
                    )
                    normalized = self.normalize_name(decoded)
                    if (
                        normalized is not None
                        and normalized not in names
                    ):
                        normalized_bytes = len(
                            normalized.encode(
                                "utf-8",
                                errors="surrogatepass",
                            )
                        )
                        if (
                            retained_name_bytes
                            + normalized_bytes
                            > self.name_byte_limit
                        ):
                            raise SidecarLimitError(
                                "source sidecar managed name byte limit",
                                managed_entries_inspected=(
                                    entries_inspected
                                ),
                                managed_entries_retained=len(names),
                                managed_name_bytes_retained=(
                                    retained_name_bytes
                                ),
                                requested_name_bytes=(
                                    normalized_bytes
                                ),
                            )
                        names.add(normalized)
                        retained_name_bytes += normalized_bytes
            else:
                self._parse_value(retain=False)

            self._skip_whitespace()
            if self.position >= len(self.text):
                raise ValueError("unterminated managed_files array")
            delimiter = self.text[self.position]
            self.position += 1
            if delimiter == "]":
                return sorted(names)
            if delimiter != ",":
                raise ValueError("invalid managed_files array")
            self._skip_whitespace()

    def parse_sidecar(self):
        self._skip_whitespace()
        if (
            self.position >= len(self.text)
            or self.text[self.position] != "{"
        ):
            return None
        self.position += 1
        self._skip_whitespace()
        if (
            self.position < len(self.text)
            and self.text[self.position] == "}"
        ):
            self.position += 1
            return {}

        data = {}
        while True:
            key, _length = self._parse_string(retain=True)
            self._skip_whitespace()
            if (
                self.position >= len(self.text)
                or self.text[self.position] != ":"
            ):
                return None
            self.position += 1
            if key == "managed_files":
                value = self._parse_managed_array()
                if self.retain_managed_names:
                    data[key] = value
            else:
                data[key] = self._parse_value(retain=True)
            self._skip_whitespace()
            if self.position >= len(self.text):
                return None
            delimiter = self.text[self.position]
            self.position += 1
            if delimiter == "}":
                break
            if delimiter != ",":
                return None
            self._skip_whitespace()
        if _skip_whitespace(self.text, self.position) != len(
            self.text
        ):
            return None
        return data


def parse_sidecar_bytes(
    payload,
    *,
    entry_limit,
    name_byte_limit,
    normalize_name,
    retain_managed_names=True,
):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parser = _SidecarParser(
        text,
        entry_limit=entry_limit,
        name_byte_limit=name_byte_limit,
        normalize_name=normalize_name,
        retain_managed_names=retain_managed_names,
    )
    try:
        return parser.parse_sidecar()
    except SidecarLimitError:
        raise
    except (RecursionError, TypeError, ValueError):
        return None


def read_sidecar(
    path,
    *,
    byte_limit,
    entry_limit,
    name_byte_limit,
    normalize_name,
    retain_managed_names=True,
):
    byte_limit = max(0, int(byte_limit))
    try:
        with open(path, "rb") as stream:
            payload = stream.read(byte_limit + 1)
    except OSError:
        return None
    if len(payload) > byte_limit:
        raise SidecarLimitError(
            "source sidecar byte limit",
            observed_bytes=len(payload),
            observed_bytes_is_lower_bound=True,
        )
    return parse_sidecar_bytes(
        payload,
        entry_limit=entry_limit,
        name_byte_limit=name_byte_limit,
        normalize_name=normalize_name,
        retain_managed_names=retain_managed_names,
    )


_SIMPLE_ESCAPES = {
    '"': b'\\"',
    "\\": b"\\\\",
    "\b": b"\\b",
    "\f": b"\\f",
    "\n": b"\\n",
    "\r": b"\\r",
    "\t": b"\\t",
}


def _json_string_escape(codepoint):
    if codepoint <= 0xFFFF:
        return f"\\u{codepoint:04x}".encode("ascii")
    codepoint -= 0x10000
    high = 0xD800 | (codepoint >> 10)
    low = 0xDC00 | (codepoint & 0x3FF)
    return f"\\u{high:04x}\\u{low:04x}".encode("ascii")


class _BoundedJsonWriter:
    def __init__(self, byte_limit):
        self.byte_limit = max(0, int(byte_limit))
        self.output = bytearray()
        self.markers = set()

    def _write(self, chunk):
        requested_size = len(self.output) + len(chunk)
        if requested_size > self.byte_limit:
            raise SidecarLimitError(
                "source sidecar byte limit",
                observed_bytes=requested_size,
                observed_bytes_is_lower_bound=True,
            )
        self.output.extend(chunk)

    def _write_spaces(self, count):
        while count:
            size = min(count, 4096)
            self._write(b" " * size)
            count -= size

    def _write_indent(self, level):
        self._write_spaces(level * 2)

    def _write_string(self, value):
        self._write(b'"')
        pending = bytearray()

        def flush():
            if pending:
                self._write(pending)
                pending.clear()

        for char in value:
            escaped = _SIMPLE_ESCAPES.get(char)
            if escaped is None:
                codepoint = ord(char)
                if codepoint < 0x20 or codepoint >= 0x7F:
                    escaped = _json_string_escape(codepoint)
                else:
                    escaped = bytes((codepoint,))
            if len(pending) + len(escaped) > 4096:
                flush()
            pending.extend(escaped)
        flush()
        self._write(b'"')

    def _enter_container(self, value):
        marker = id(value)
        if marker in self.markers:
            raise ValueError("circular sidecar value")
        self.markers.add(marker)
        return marker

    def _write_dict(self, value, level):
        if len(value) > self.byte_limit:
            raise SidecarLimitError(
                "source sidecar byte limit",
                observed_bytes=self.byte_limit + 1,
                observed_bytes_is_lower_bound=True,
            )
        if any(type(key) is not str for key in value):
            raise TypeError(
                "unsupported sidecar object key"
            )
        keys = sorted(value)
        marker = self._enter_container(value)
        try:
            self._write(b"{")
            for index, key in enumerate(keys):
                self._write(b"\n" if index == 0 else b",\n")
                self._write_indent(level + 1)
                self._write_string(key)
                self._write(b": ")
                self._write_value(value[key], level + 1)
            if keys:
                self._write(b"\n")
                self._write_indent(level)
            self._write(b"}")
        finally:
            self.markers.remove(marker)

    def _write_iterable(self, value, level):
        if (
            isinstance(value, (list, tuple))
            and len(value) > self.byte_limit
        ):
            raise SidecarLimitError(
                "source sidecar byte limit",
                observed_bytes=self.byte_limit + 1,
                observed_bytes_is_lower_bound=True,
            )
        marker = self._enter_container(value)
        try:
            iterator = iter(value)
            self._write(b"[")
            count = 0
            for item in iterator:
                self._write(
                    b"\n" if count == 0 else b",\n"
                )
                self._write_indent(level + 1)
                self._write_value(item, level + 1)
                count += 1
            if count:
                self._write(b"\n")
                self._write_indent(level)
            self._write(b"]")
        finally:
            self.markers.remove(marker)

    def _write_value(self, value, level):
        if value is None:
            self._write(b"null")
        elif value is True:
            self._write(b"true")
        elif value is False:
            self._write(b"false")
        elif type(value) is str:
            self._write_string(value)
        elif type(value) is int:
            self._write(str(value).encode("ascii"))
        elif type(value) is float:
            if math.isnan(value):
                self._write(b"NaN")
            elif math.isinf(value):
                self._write(
                    b"Infinity" if value > 0 else b"-Infinity"
                )
            else:
                self._write(repr(value).encode("ascii"))
        elif type(value) is dict:
            self._write_dict(value, level)
        elif isinstance(value, (bytes, bytearray, set, frozenset)):
            raise TypeError(
                "unsupported sidecar value: "
                f"{type(value).__name__}"
            )
        else:
            try:
                iter(value)
            except TypeError:
                raise TypeError(
                    "unsupported sidecar value: "
                    f"{type(value).__name__}"
                ) from None
            self._write_iterable(value, level)

    def encode(self, value):
        self._write_value(value, 0)
        self._write(b"\n")
        return bytes(self.output)


def encode_sidecar(value, *, byte_limit):
    return _BoundedJsonWriter(byte_limit).encode(value)

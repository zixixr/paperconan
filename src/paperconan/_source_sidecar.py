from __future__ import annotations

import json


class SidecarLimitError(ValueError):
    def __init__(self, reason, **details):
        self.reason = reason
        self.details = details
        super().__init__(reason)


def _skip_whitespace(text, position):
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _parse_value(decoder, text, position):
    return decoder.raw_decode(text, _skip_whitespace(text, position))


def _parse_managed_array(
    decoder,
    text,
    position,
    *,
    entry_limit,
    name_byte_limit,
    normalize_name,
    retain_names,
):
    position = _skip_whitespace(text, position)
    if position >= len(text) or text[position] != "[":
        _value, position = _parse_value(
            decoder, text, position
        )
        return [], position
    position = _skip_whitespace(text, position + 1)
    if position < len(text) and text[position] == "]":
        return [], position + 1

    names = set()
    entries_inspected = 0
    retained_name_bytes = 0
    while True:
        if entries_inspected >= entry_limit:
            raise SidecarLimitError(
                "source sidecar managed entry limit",
                managed_entries_inspected=entries_inspected,
                managed_entries_retained=len(names),
                managed_name_bytes_retained=retained_name_bytes,
            )
        value, position = _parse_value(decoder, text, position)
        entries_inspected += 1
        if retain_names and isinstance(value, str):
            normalized = normalize_name(value)
            if normalized is not None and normalized not in names:
                name_bytes = len(
                    normalized.encode(
                        "utf-8", errors="surrogatepass"
                    )
                )
                if (
                    retained_name_bytes + name_bytes
                    > name_byte_limit
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
                        requested_name_bytes=name_bytes,
                    )
                names.add(normalized)
                retained_name_bytes += name_bytes
        position = _skip_whitespace(text, position)
        if position >= len(text):
            raise ValueError("unterminated managed_files array")
        delimiter = text[position]
        position += 1
        if delimiter == "]":
            return sorted(names), position
        if delimiter != ",":
            raise ValueError("invalid managed_files array")
        position = _skip_whitespace(text, position)


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
    decoder = json.JSONDecoder()
    position = _skip_whitespace(text, 0)
    if position >= len(text) or text[position] != "{":
        return None
    position = _skip_whitespace(text, position + 1)
    if position < len(text) and text[position] == "}":
        return {}

    data = {}
    try:
        while True:
            key, position = _parse_value(
                decoder, text, position
            )
            if not isinstance(key, str):
                return None
            position = _skip_whitespace(text, position)
            if (
                position >= len(text)
                or text[position] != ":"
            ):
                return None
            position += 1
            if key == "managed_files":
                value, position = _parse_managed_array(
                    decoder,
                    text,
                    position,
                    entry_limit=max(0, int(entry_limit)),
                    name_byte_limit=max(
                        0, int(name_byte_limit)
                    ),
                    normalize_name=normalize_name,
                    retain_names=retain_managed_names,
                )
                if retain_managed_names:
                    data[key] = value
            else:
                value, position = _parse_value(
                    decoder, text, position
                )
                data[key] = value
            position = _skip_whitespace(text, position)
            if position >= len(text):
                return None
            delimiter = text[position]
            position += 1
            if delimiter == "}":
                break
            if delimiter != ",":
                return None
            position = _skip_whitespace(text, position)
        if _skip_whitespace(text, position) != len(text):
            return None
    except SidecarLimitError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data


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


def _preflight_top_level_strings(value, byte_limit):
    if not isinstance(value, dict):
        return
    for item in value.values():
        if not isinstance(item, str):
            continue
        lower_bound = 0
        for offset in range(0, len(item), 65536):
            lower_bound += len(
                item[offset:offset + 65536].encode(
                    "utf-8", errors="surrogatepass"
                )
            )
            if lower_bound > byte_limit:
                raise SidecarLimitError(
                    "source sidecar byte limit",
                    observed_bytes=lower_bound,
                    observed_bytes_is_lower_bound=True,
                )


def encode_sidecar(value, *, byte_limit):
    byte_limit = max(0, int(byte_limit))
    _preflight_top_level_strings(value, byte_limit)
    encoder = json.JSONEncoder(
        indent=2,
        sort_keys=True,
        default=str,
    )
    chunks = []
    size = 0
    for text in encoder.iterencode(value):
        chunk = text.encode("utf-8")
        requested_size = size + len(chunk)
        if requested_size > byte_limit:
            raise SidecarLimitError(
                "source sidecar byte limit",
                observed_bytes=requested_size,
                observed_bytes_is_lower_bound=True,
            )
        chunks.append(chunk)
        size = requested_size
    if size + 1 > byte_limit:
        raise SidecarLimitError(
            "source sidecar byte limit",
            observed_bytes=size + 1,
            observed_bytes_is_lower_bound=True,
        )
    chunks.append(b"\n")
    return b"".join(chunks)

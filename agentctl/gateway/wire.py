"""Header-only fast path for the Python reference proxy - the mirror of the Go `wire` package.

The proxy only ever reads one field (session_id, field 1, for routing) and writes one
(attributes["canary_arm"], the map at field 16). So the hot path never needs the typed Frame:
``session_id`` scans field 1 and stops; ``set_canary_arm`` appends one map entry to the tail (map
fields merge, last key wins, so appending overrides - and the frozen header, fields 1-4, is never
rewritten). Pure stdlib protobuf wire encoding; parity with the typed path is pinned against the same
conformance fixtures the Go side uses.
"""
from __future__ import annotations

_FIELD_SESSION_ID = 1
_FIELD_ATTRIBUTES = 16
CANARY_ARM_KEY = "canary_arm"


def _read_varint(b: bytes, i: int) -> tuple[int, int]:
    """Decode a base-128 varint at offset i; return (value, next_offset)."""
    shift = result = 0
    while True:
        byte = b[i]
        result |= (byte & 0x7F) << shift
        i += 1
        if not byte & 0x80:
            return result, i
        shift += 7


def _append_varint(out: bytearray, v: int) -> None:
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _skip_field(b: bytes, i: int, wiretype: int) -> int:
    if wiretype == 0:          # varint
        _, i = _read_varint(b, i)
        return i
    if wiretype == 2:          # length-delimited
        ln, i = _read_varint(b, i)
        return i + ln
    if wiretype == 1:          # 64-bit
        return i + 8
    if wiretype == 5:          # 32-bit
        return i + 4
    raise ValueError(f"unsupported wiretype {wiretype}")


def session_id(raw: bytes) -> str | None:
    """Scan Frame.session_id (field 1) from the wire bytes without building the message. Returns None
    if absent/malformed. session_id is the lowest field number, so normally the first tag read."""
    i, n = 0, len(raw)
    try:
        while i < n:
            tag, i = _read_varint(raw, i)
            field, wiretype = tag >> 3, tag & 7
            if field == _FIELD_SESSION_ID and wiretype == 2:
                ln, i = _read_varint(raw, i)
                return raw[i:i + ln].decode("utf-8")
            i = _skip_field(raw, i, wiretype)
    except (IndexError, ValueError):
        return None
    return None


def _append_len_delim(out: bytearray, field: int, payload: bytes) -> None:
    _append_varint(out, (field << 3) | 2)
    _append_varint(out, len(payload))
    out.extend(payload)


def _map_entry(key: str, val: str) -> bytes:
    e = bytearray()
    _append_len_delim(e, 1, key.encode("utf-8"))   # map key  = field 1
    _append_len_delim(e, 2, val.encode("utf-8"))   # map value = field 2
    return bytes(e)


def set_canary_arm(raw: bytes, arm: str) -> bytes:
    """Return raw with attributes["canary_arm"]=arm appended as one field-16 map entry. Decoder-safe
    (map occurrences merge, last key wins) and leaves the frozen header untouched."""
    out = bytearray(raw)
    _append_len_delim(out, _FIELD_ATTRIBUTES, _map_entry(CANARY_ARM_KEY, arm))
    return bytes(out)

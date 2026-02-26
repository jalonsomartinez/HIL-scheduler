"""Modbus holding-register codec helpers with endpoint-level ordering."""

import math
import struct

from modbus_units import external_to_internal, internal_to_external

_FORMAT_META = {
    "int16": {"word_count": 1, "byte_count": 2, "kind": "int", "signed": True},
    "uint16": {"word_count": 1, "byte_count": 2, "kind": "int", "signed": False},
    "int32": {"word_count": 2, "byte_count": 4, "kind": "int", "signed": True},
    "uint32": {"word_count": 2, "byte_count": 4, "kind": "int", "signed": False},
    "float32": {"word_count": 2, "byte_count": 4, "kind": "float"},
}


def format_meta(format_name):
    try:
        return dict(_FORMAT_META[str(format_name)])
    except KeyError as exc:
        raise ValueError(f"Unsupported Modbus point format: {format_name!r}") from exc


def _validate_endpoint_ordering(endpoint_cfg):
    byte_order = str(endpoint_cfg.get("byte_order", "")).strip().lower()
    word_order = str(endpoint_cfg.get("word_order", "")).strip().lower()
    if byte_order not in {"big", "little"}:
        raise ValueError(f"Invalid endpoint byte_order={byte_order!r}")
    if word_order not in {"msw_first", "lsw_first"}:
        raise ValueError(f"Invalid endpoint word_order={word_order!r}")
    return byte_order, word_order


def _validate_scale(point_spec):
    try:
        scale = float(point_spec.get("eng_per_count"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid eng_per_count for point: {point_spec!r}") from exc
    if scale <= 0.0:
        raise ValueError(f"eng_per_count must be > 0 for point: {point_spec!r}")
    return scale


def _canonical_bytes_to_words(payload, *, byte_order, word_order):
    if len(payload) % 2 != 0:
        raise ValueError("Payload length must be even (full 16-bit words).")
    chunks = [payload[i : i + 2] for i in range(0, len(payload), 2)]
    if word_order == "lsw_first" and len(chunks) > 1:
        chunks = list(reversed(chunks))
    if byte_order == "little":
        chunks = [chunk[::-1] for chunk in chunks]
    return [int.from_bytes(chunk, byteorder="big", signed=False) for chunk in chunks]


def _words_to_canonical_bytes(words, *, byte_order, word_order):
    chunks = [int(word).to_bytes(2, byteorder="big", signed=False) for word in words]
    if byte_order == "little":
        chunks = [chunk[::-1] for chunk in chunks]
    if word_order == "lsw_first" and len(chunks) > 1:
        chunks = list(reversed(chunks))
    return b"".join(chunks)


def _int_bounds(format_name):
    meta = format_meta(format_name)
    bits = meta["byte_count"] * 8
    if meta.get("signed"):
        return -(2 ** (bits - 1)), (2 ** (bits - 1)) - 1
    return 0, (2**bits) - 1


def _quantize_integer_raw(value):
    if not math.isfinite(value):
        raise ValueError(f"Non-finite raw integer value: {value!r}")
    nearest = round(value)
    if abs(value - nearest) < 1e-9:
        return int(nearest)
    return int(math.trunc(value))


def encode_engineering_value(endpoint_cfg, point_spec, eng_value):
    """Encode an engineering value into holding-register words."""
    byte_order, word_order = _validate_endpoint_ordering(endpoint_cfg)
    format_name = str(point_spec.get("format", "")).strip().lower()
    meta = format_meta(format_name)
    scale = _validate_scale(point_spec)
    raw_value = float(eng_value) / scale

    if meta["kind"] == "float":
        payload = struct.pack(">f", float(raw_value))
    else:
        raw_int = _quantize_integer_raw(raw_value)
        min_raw, max_raw = _int_bounds(format_name)
        if raw_int < min_raw or raw_int > max_raw:
            raise ValueError(
                f"Raw value {raw_int} out of range for {format_name} "
                f"({min_raw}..{max_raw}) point={point_spec!r}"
            )
        payload = int(raw_int).to_bytes(meta["byte_count"], byteorder="big", signed=bool(meta.get("signed")))

    words = _canonical_bytes_to_words(payload, byte_order=byte_order, word_order=word_order)
    if len(words) != int(meta["word_count"]):
        raise ValueError("Encoded word count mismatch.")
    return words


def decode_engineering_value(endpoint_cfg, point_spec, raw_words):
    """Decode holding-register words into an engineering value."""
    byte_order, word_order = _validate_endpoint_ordering(endpoint_cfg)
    format_name = str(point_spec.get("format", "")).strip().lower()
    meta = format_meta(format_name)
    scale = _validate_scale(point_spec)

    words = [int(word) & 0xFFFF for word in (raw_words or [])]
    if len(words) != int(meta["word_count"]):
        raise ValueError(
            f"Expected {meta['word_count']} words for {format_name}, got {len(words)} "
            f"for point={point_spec!r}"
        )

    payload = _words_to_canonical_bytes(words, byte_order=byte_order, word_order=word_order)
    if len(payload) != int(meta["byte_count"]):
        raise ValueError("Decoded payload size mismatch.")

    if meta["kind"] == "float":
        raw_value = struct.unpack(">f", payload)[0]
    else:
        raw_value = int.from_bytes(payload, byteorder="big", signed=bool(meta.get("signed")))
    return raw_value * scale


def read_point_holding(client, endpoint_cfg, point_spec):
    """Read and decode a single Modbus point from holding registers."""
    word_count = int(point_spec.get("word_count") or format_meta(point_spec.get("format"))["word_count"])
    regs = client.read_holding_registers(int(point_spec["address"]), word_count)
    if not regs or len(regs) != word_count:
        return None
    return decode_engineering_value(endpoint_cfg, point_spec, regs)


def write_point_holding(client, endpoint_cfg, point_spec, eng_value):
    """Encode and write a single Modbus point to holding registers."""
    words = encode_engineering_value(endpoint_cfg, point_spec, eng_value)
    address = int(point_spec["address"])

    if len(words) == 1:
        return bool(client.write_single_register(address, int(words[0])))

    if hasattr(client, "write_multiple_registers"):
        return bool(client.write_multiple_registers(address, [int(word) for word in words]))

    ok = True
    for offset, word in enumerate(words):
        ok = bool(client.write_single_register(address + offset, int(word))) and ok
    return ok


def _resolve_point_name_and_spec(endpoint_cfg, point_name_or_spec):
    if isinstance(point_name_or_spec, str):
        point_name = point_name_or_spec
        point_spec = (endpoint_cfg.get("points") or {}).get(point_name)
        if point_spec is None:
            raise KeyError(f"Point {point_name!r} not found in endpoint config.")
        return point_name, point_spec

    point_spec = point_name_or_spec or {}
    point_name = point_spec.get("name")
    if not point_name:
        raise ValueError("Point spec must include 'name' when passed directly.")
    return str(point_name), point_spec


def read_point_words(client, endpoint_cfg, point_name_or_spec):
    """Read raw holding-register words for a point, preserving on-wire encoding."""
    _, point_spec = _resolve_point_name_and_spec(endpoint_cfg, point_name_or_spec)
    word_count = int(point_spec.get("word_count") or format_meta(point_spec.get("format"))["word_count"])
    regs = client.read_holding_registers(int(point_spec["address"]), word_count)
    if not regs or len(regs) != word_count:
        return None
    return [int(word) & 0xFFFF for word in regs]


def read_point_internal(client, endpoint_cfg, point_name_or_spec):
    point_name, point_spec = _resolve_point_name_and_spec(endpoint_cfg, point_name_or_spec)
    external_value = read_point_holding(client, endpoint_cfg, point_spec)
    if external_value is None:
        return None
    return external_to_internal(point_name, point_spec.get("unit"), external_value)


def encode_point_internal_words(endpoint_cfg, point_name_or_spec, internal_value):
    """Encode an internal runtime value to the raw holding-register words for a point."""
    point_name, point_spec = _resolve_point_name_and_spec(endpoint_cfg, point_name_or_spec)
    external_value = internal_to_external(point_name, point_spec.get("unit"), internal_value)
    return [int(word) & 0xFFFF for word in encode_engineering_value(endpoint_cfg, point_spec, external_value)]


def write_point_internal(client, endpoint_cfg, point_name_or_spec, internal_value):
    point_name, point_spec = _resolve_point_name_and_spec(endpoint_cfg, point_name_or_spec)
    external_value = internal_to_external(point_name, point_spec.get("unit"), internal_value)
    return write_point_holding(client, endpoint_cfg, point_spec, external_value)

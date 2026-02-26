"""Shared parsing helpers for simple runtime/config coercions."""


def parse_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ["1", "true", "yes", "on"]
    if value is None:
        return default
    return bool(value)

"""Unit normalization and conversions between Modbus engineering units and internal runtime units."""


_SOC_UNITS = {"pu", "pc"}
_P_UNITS = {"w", "kw", "mw"}
_Q_UNITS = {"var", "kvar", "mvar"}
_V_UNITS = {"v", "kv"}
_RAW_UNITS = {"raw"}


def normalize_unit_token(unit_str):
    if unit_str is None:
        raise ValueError("Modbus point unit is required.")
    token = str(unit_str).strip()
    if not token:
        raise ValueError("Modbus point unit must be non-empty.")
    token_lower = token.lower()
    if token_lower == "%":
        return "pc"
    return token_lower


def infer_point_quantity(point_name):
    name = str(point_name or "").strip()
    if name == "soc":
        return "soc"
    if name in {"p_setpoint", "p_battery", "p_poi"}:
        return "p"
    if name in {"q_setpoint", "q_battery", "q_poi"}:
        return "q"
    if name == "v_poi":
        return "v"
    if name in {"enable", "start_command", "stop_command"}:
        return "raw"
    return "unknown"


def validate_point_unit(point_name, unit):
    quantity = infer_point_quantity(point_name)
    normalized_unit = normalize_unit_token(unit)

    if quantity == "soc" and normalized_unit in _SOC_UNITS:
        return normalized_unit
    if quantity == "p" and normalized_unit in _P_UNITS:
        return normalized_unit
    if quantity == "q" and normalized_unit in _Q_UNITS:
        return normalized_unit
    if quantity == "v" and normalized_unit in _V_UNITS:
        return normalized_unit
    if quantity == "raw" and normalized_unit in _RAW_UNITS:
        return normalized_unit
    if quantity == "unknown" and normalized_unit in _RAW_UNITS:
        return normalized_unit

    raise ValueError(
        f"Invalid unit {unit!r} for point {point_name!r}. "
        f"Quantity={quantity!r}."
    )


def external_to_internal(point_name, unit, value):
    quantity = infer_point_quantity(point_name)
    normalized_unit = validate_point_unit(point_name, unit)
    if value is None:
        return None
    number = float(value)

    if quantity == "soc":
        return number if normalized_unit == "pu" else number / 100.0
    if quantity == "p":
        if normalized_unit == "kw":
            return number
        if normalized_unit == "w":
            return number / 1000.0
        return number * 1000.0  # mw -> kW
    if quantity == "q":
        if normalized_unit == "kvar":
            return number
        if normalized_unit == "var":
            return number / 1000.0
        return number * 1000.0  # mvar -> kvar
    if quantity == "v":
        return number if normalized_unit == "kv" else number / 1000.0
    return number  # raw


def internal_to_external(point_name, unit, value):
    quantity = infer_point_quantity(point_name)
    normalized_unit = validate_point_unit(point_name, unit)
    if value is None:
        return None
    number = float(value)

    if quantity == "soc":
        return number if normalized_unit == "pu" else number * 100.0
    if quantity == "p":
        if normalized_unit == "kw":
            return number
        if normalized_unit == "w":
            return number * 1000.0
        return number / 1000.0  # kW -> mw
    if quantity == "q":
        if normalized_unit == "kvar":
            return number
        if normalized_unit == "var":
            return number * 1000.0
        return number / 1000.0  # kvar -> mvar
    if quantity == "v":
        return number if normalized_unit == "kv" else number * 1000.0
    return number  # raw

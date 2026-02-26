"""Shared runtime defaults used across modules.

Keep this module lightweight (no pandas/heavy imports) so low-level modules can
import shared constants without creating avoidable import dependencies.
"""

DEFAULT_TIMEZONE_NAME = "Europe/Madrid"

DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES = {
    "p_setpoint_kw": 0.0,
    "battery_active_power_kw": 0.1,
    "q_setpoint_kvar": 0.0,
    "battery_reactive_power_kvar": 0.1,
    "soc_pu": 0.0001,
    "p_poi_kw": 0.1,
    "q_poi_kvar": 0.1,
    "v_poi_kV": 0.001,
}

DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S = 3600.0

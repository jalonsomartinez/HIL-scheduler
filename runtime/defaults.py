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


def default_measurement_post_status():
    """Return a fresh default measurement posting status entry."""
    return {
        "posting_enabled": False,
        "last_success": None,
        "last_attempt": None,
        "last_error": None,
        "pending_queue_count": 0,
        "oldest_pending_age_s": None,
        "last_enqueue": None,
    }


def default_measurement_post_status_by_plant(plant_ids):
    """Return default measurement posting status map for all plants."""
    return {plant_id: default_measurement_post_status() for plant_id in plant_ids}

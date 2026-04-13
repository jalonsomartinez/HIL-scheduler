"""Shared SoC seed resolution and estimation helpers."""

from measurement.storage import find_latest_persisted_soc_for_plant
from runtime.paths import get_data_dir


def clamp_soc_pu(value, fallback):
    try:
        soc_value = float(value)
    except (TypeError, ValueError):
        soc_value = float(fallback)
    return min(1.0, max(0.0, soc_value))


def resolve_startup_soc_seed(config, plant_id, tz, *, caller_file):
    startup_initial_soc_pu = float(config.get("STARTUP_INITIAL_SOC_PU", 0.5))
    plants_cfg = config.get("PLANTS", {})
    plant_cfg = plants_cfg.get(plant_id, {}) or {}
    fallback_soc_pu = clamp_soc_pu(startup_initial_soc_pu, startup_initial_soc_pu)
    latest = find_latest_persisted_soc_for_plant(
        get_data_dir(caller_file),
        plant_cfg.get("name", plant_id),
        plant_id,
        tz,
    )
    if latest is not None:
        return {
            "soc_pu": clamp_soc_pu(latest.get("soc_pu"), fallback_soc_pu),
            "source": "disk",
            "file_path": latest.get("file_path"),
            "timestamp": latest.get("timestamp"),
            "message": latest.get("file_path"),
        }
    return {
        "soc_pu": fallback_soc_pu,
        "source": "startup_fallback",
        "file_path": None,
        "timestamp": None,
        "message": "no persisted soc found",
    }


class SocEstimator:
    """Track best-known SoC for one plant across real and estimated updates."""

    def __init__(self, capacity_kwh, initial_soc_pu, *, timestamp=None):
        try:
            capacity_value = float(capacity_kwh)
        except (TypeError, ValueError):
            capacity_value = 0.0
        self.capacity_kwh = max(0.0, capacity_value)
        self.soc_pu = clamp_soc_pu(initial_soc_pu, 0.5)
        self.timestamp = timestamp

    def sync(self, soc_pu, *, timestamp=None):
        self.soc_pu = clamp_soc_pu(soc_pu, self.soc_pu)
        self.timestamp = timestamp if timestamp is not None else self.timestamp
        return self.soc_pu

    def estimate_from_power(self, p_battery_kw, *, timestamp=None):
        try:
            power_kw = float(p_battery_kw)
        except (TypeError, ValueError):
            power_kw = 0.0

        if (
            self.capacity_kwh > 0.0
            and timestamp is not None
            and self.timestamp is not None
            and timestamp >= self.timestamp
        ):
            dt_h = float((timestamp - self.timestamp).total_seconds()) / 3600.0
            if dt_h > 0.0:
                next_soc = self.soc_pu - (power_kw * dt_h / self.capacity_kwh)
                self.soc_pu = clamp_soc_pu(next_soc, self.soc_pu)

        self.timestamp = timestamp if timestamp is not None else self.timestamp
        return self.soc_pu

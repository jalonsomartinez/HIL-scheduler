import importlib.util
import json
import unittest
from pathlib import Path


_PANDAPOWER_AVAILABLE = importlib.util.find_spec("pandapower") is not None

if _PANDAPOWER_AVAILABLE:
    import pandapower as pp

    from grid_map_digital_twin.simulator import run_power_flow
else:  # pragma: no cover - exercised only in lightweight environments
    pp = None
    run_power_flow = None


@unittest.skipUnless(_PANDAPOWER_AVAILABLE, "pandapower not installed")
class GridMapDigitalTwinSimulatorTests(unittest.TestCase):
    def test_run_power_flow_returns_absolute_battery_voltage_kv(self):
        result = run_power_flow(
            battery_p_mw=-0.8,
            battery_q_mvar=-0.2,
            timestamp_iso="2026-03-23T00:30:00+01:00",
        )

        package_dir = Path("grid_map_digital_twin")
        metadata = json.loads((package_dir / "package_metadata.json").read_text(encoding="utf-8"))
        net = pp.from_pickle(str(package_dir / "net_digital_twin.p"))
        battery_bus = int(metadata["battery_bus"])
        expected_kv = float(result["battery_bus_vm_pu"]) * float(net.bus.at[battery_bus, "vn_kv"])

        self.assertIn("battery_bus_vm_kv", result)
        self.assertAlmostEqual(result["battery_bus_vm_kv"], expected_kv, places=6)


if __name__ == "__main__":
    unittest.main()

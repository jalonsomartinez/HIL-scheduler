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
    def test_run_power_flow_matches_mirrored_package_contract(self):
        result = run_power_flow(
            battery_p_mw=-0.8,
            battery_q_mvar=-0.2,
            timestamp_iso="2026-03-23T00:30:00+01:00",
        )

        package_dir = Path("grid_map_digital_twin")
        metadata = json.loads((package_dir / "package_metadata.json").read_text(encoding="utf-8"))
        battery_bus = int(metadata["battery_bus"])
        result_bus_table = result["results_tables"]["res_bus"]
        battery_bus_vm = float(result_bus_table.at[battery_bus, "vm_pu"])

        self.assertNotIn("battery_bus_vm_kv", result)
        self.assertIn("battery_bus_vm_pu", result)
        self.assertIn("max_line_loading_pct", result)
        self.assertIn("results_tables", result)
        self.assertAlmostEqual(float(result["battery_bus_vm_pu"]), battery_bus_vm, places=6)


if __name__ == "__main__":
    unittest.main()

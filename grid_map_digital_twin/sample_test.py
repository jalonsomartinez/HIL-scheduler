"""Simple runtime check for the digital twin simulator."""

from __future__ import annotations

import json
from pathlib import Path

from simulator import run_power_flow


def main() -> None:
    # 00:30 local triggers the configured previous-hour fallback to 00:00.
    result = run_power_flow(
        battery_p_mw=-0.8,
        battery_q_mvar=-0.8,
        timestamp_iso="2026-03-23T00:30:00+01:00",
    )

    required_fields = [
        "battery_bus_vm_pu",
        "battery_bus_vm_kv",
        "num_overloaded_lines",
        "num_voltage_violations",
        "max_voltage_pu",
        "min_voltage_pu",
        "max_line_loading_pct",
        "results_tables",
    ]
    missing = [field for field in required_fields if field not in result]
    assert not missing, f"Missing expected result fields: {missing}"

    assert result["used_previous_hour_fallback"] is True
    assert "res_bus" in result["results_tables"], "res_bus table missing in full results."
    assert not result["results_tables"]["res_bus"].empty, "res_bus is empty after runpp."
    metadata = json.loads((Path(__file__).resolve().parent / "package_metadata.json").read_text())
    battery_bus = int(metadata["battery_bus"])
    hub_bus = int(metadata["hub_bus"])
    battery_bus_vm = float(result["results_tables"]["res_bus"].at[battery_bus, "vm_pu"])
    hub_bus_vm = float(result["results_tables"]["res_bus"].at[hub_bus, "vm_pu"])
    assert result["battery_bus_vm_pu"] == battery_bus_vm, "Returned voltage does not match battery bus."
    assert battery_bus != hub_bus, "Battery bus and hub bus should be distinct."

    print("Sample test passed.")
    print(f"Selected local timestamp: {result['selected_timestamp_local']}")
    print(f"Battery bus voltage [pu]: {result['battery_bus_vm_pu']:.6f}")
    print(f"Battery bus voltage [kV]: {result['battery_bus_vm_kv']:.6f}")
    print(f"Hub bus voltage [pu]: {hub_bus_vm:.6f}")
    print(f"Max line loading [%]: {result['max_line_loading_pct']:.3f}")


if __name__ == "__main__":
    main()

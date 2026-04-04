"""Simple runtime check for the digital twin simulator."""

from __future__ import annotations

from simulator import run_power_flow


def main() -> None:
    # 00:30 local triggers the configured previous-hour fallback to 00:00.
    result = run_power_flow(
        battery_p_mw=-0.8,
        battery_q_mvar=-0.8,
        timestamp_iso="2026-03-23T00:30:00+01:00",
    )

    required_fields = [
        "ext_grid_bus_vm_pu",
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

    print("Sample test passed.")
    print(f"Selected local timestamp: {result['selected_timestamp_local']}")
    print(f"Ext-grid bus voltage [pu]: {result['ext_grid_bus_vm_pu']:.6f}")
    print(f"Max line loading [%]: {result['max_line_loading_pct']:.3f}")


if __name__ == "__main__":
    main()

# Digital Twin Package

Self-contained package for running one-hour power-flow simulations with a battery setpoint.

## Files

- `timeseries_input_2026-03-23_to_2026-05-31.csv`: generated simulator input data.
- `net_digital_twin.p`: modified pandapower model.
- `device_mapping.json`: device-code to model target mapping used at runtime.
- `package_metadata.json`: topology and runtime metadata (battery index, hub bus, etc.).
- `simulator.py`: portable function API.
- `sample_test.py`: simple execution test.
- `requirements.txt`: install dependencies in destination repo.

## Install

```bash
python3 -m pip install -r requirements.txt
```

## API

```python
from simulator import run_power_flow

result = run_power_flow(
    battery_p_mw=0.05,
    battery_q_mvar=0.01,
    timestamp_iso="2026-04-10T13:00:00+02:00",
    exclude_exported=False,
)
```

Function signature:

```python
run_power_flow(
    battery_p_mw: float,
    battery_q_mvar: float,
    timestamp_iso: str,
    exclude_exported: bool = False,
) -> dict
```

Notes:
- `timestamp_iso` must include timezone (ISO8601).
- `exclude_exported=True` ignores `ENERGY_EXPORTED (W)` and applies import-only load semantics.
- If an exact timestamp is missing, the function uses the nearest previous available hour.
- Positive battery active power means charging (load). Negative means discharging.
- Added synthetic buses in the packaged network include display-only `geo` coordinates derived from hub bus `840`.

The return includes:
- KPI fields: ext-grid voltage, overload/violation counts, voltage min/max, max line loading.
- `results_tables`: full pandapower result tables (`res_bus`, `res_line`, `res_trafo`, `res_ext_grid`, and other available `res_*` tables).

## Run sample test

```bash
python3 sample_test.py
```

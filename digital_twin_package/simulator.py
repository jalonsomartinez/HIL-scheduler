"""Portable digital-twin power-flow runner.

This module exposes a single public API, :func:`run_power_flow`, that executes one
pandapower load-flow step for the packaged digital twin.

Quick start
-----------
1. Install requirements from ``digital_twin_package/requirements.txt``.
2. Call ``run_power_flow(...)`` with battery setpoints and a timezone-aware
   timestamp string.
3. Read the KPI fields from the returned dictionary.

Example:
    from simulator import run_power_flow

    result = run_power_flow(
        battery_p_mw=0.05,                    # +P = charging (load)
        battery_q_mvar=0.01,
        timestamp_iso="2026-03-23T00:30:00+01:00",
    )

    print(result["selected_timestamp_local"])
    print(result["ext_grid_bus_vm_pu"])
    print(result["max_line_loading_pct"])

Timestamp behavior
------------------
- ``timestamp_iso`` must include timezone information (for example ``+01:00`` or
  ``Z``).
- Internally, simulation data is indexed hourly in UTC.
- If there is no exact hour match, the simulator uses the nearest previous
  available hour and sets ``used_previous_hour_fallback=True`` in the result.

Battery sign convention
-----------------------
- Positive ``battery_p_mw``: charging (consumption as load).
- Negative ``battery_p_mw``: discharging (injection as negative load).

Returned data
-------------
``run_power_flow`` returns a dictionary with:
- Convergence/timestamp fields:
  ``converged``, ``requested_timestamp_utc``, ``selected_timestamp_utc``,
  ``selected_timestamp_local``, ``used_previous_hour_fallback``.
- KPI fields:
  ``ext_grid_bus_vm_pu``, ``num_overloaded_lines``,
  ``num_voltage_violations``, ``max_voltage_pu``, ``min_voltage_pu``,
  ``max_line_loading_pct``.
- Full pandapower output tables in ``results_tables`` (all ``res_*`` DataFrames).

Design notes
------------
- Package assets are loaded once and cached on first call.
- Each simulation call deep-copies the base network to keep calls independent.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Tuple

import pandas as pd
import pandapower as pp


PACKAGE_DIR = Path(__file__).resolve().parent
DATA_CSV_PATH = PACKAGE_DIR / "timeseries_input_2026-03-23_to_2026-05-31.csv"
NET_PATH = PACKAGE_DIR / "net_digital_twin.p"
MAPPING_PATH = PACKAGE_DIR / "device_mapping.json"
METADATA_PATH = PACKAGE_DIR / "package_metadata.json"

ACTIVE_POWER_COLUMNS_BY_TABLE: Dict[str, Tuple[str, ...]] = {
    "load": ("p_mw",),
    "sgen": ("p_mw",),
    "asymmetric_load": ("p_a_mw", "p_b_mw", "p_c_mw"),
    "asymmetric_sgen": ("p_a_mw", "p_b_mw", "p_c_mw"),
}
REACTIVE_POWER_COLUMNS_BY_TABLE: Dict[str, Tuple[str, ...]] = {
    "load": ("q_mvar",),
    "sgen": ("q_mvar",),
    "asymmetric_load": ("q_a_mvar", "q_b_mvar", "q_c_mvar"),
    "asymmetric_sgen": ("q_a_mvar", "q_b_mvar", "q_c_mvar"),
}

_ASSET_CACHE: Dict[str, Any] = {}


def _safe_numeric_series(df: Any, column: str) -> pd.Series:
    if df is None or not hasattr(df, "columns") or column not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def _build_reset_plan(net: Any) -> List[Tuple[str, Any, str]]:
    plan: List[Tuple[str, Any, str]] = []
    for table, columns in ACTIVE_POWER_COLUMNS_BY_TABLE.items():
        table_df = getattr(net, table, None)
        if table_df is None or table_df.empty:
            continue
        for column in columns:
            if column not in table_df.columns:
                continue
            for index in table_df.index:
                plan.append((table, index, column))

    for table, columns in REACTIVE_POWER_COLUMNS_BY_TABLE.items():
        table_df = getattr(net, table, None)
        if table_df is None or table_df.empty:
            continue
        for column in columns:
            if column not in table_df.columns:
                continue
            for index in table_df.index:
                plan.append((table, index, column))
    return plan


def _reset_power_targets(net: Any, reset_plan: Sequence[Tuple[str, Any, str]]) -> None:
    for table, index, column in reset_plan:
        table_df = getattr(net, table, None)
        if table_df is None or index not in table_df.index or column not in table_df.columns:
            continue
        table_df.at[index, column] = 0.0


def _load_assets() -> Dict[str, Any]:
    if _ASSET_CACHE:
        return _ASSET_CACHE

    required_files = [DATA_CSV_PATH, NET_PATH, MAPPING_PATH, METADATA_PATH]
    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing required package files. Run scripts/build_digital_twin_package.py first. "
            f"Missing: {missing}"
        )

    data_df = pd.read_csv(DATA_CSV_PATH)
    data_df["timestamp_utc"] = pd.to_datetime(data_df["START_DATETIME"], utc=True, errors="coerce")
    data_df["device_code"] = data_df["DEVICE_CODE"].astype(str).str.strip().str.upper()
    data_df["imported_w"] = pd.to_numeric(data_df["ENERGY_IMPORTED (W)"], errors="coerce").fillna(0.0)
    data_df["exported_w"] = pd.to_numeric(data_df["ENERGY_EXPORTED (W)"], errors="coerce").fillna(0.0)
    data_df = data_df.dropna(subset=["timestamp_utc"])
    data_df = data_df[data_df["device_code"] != ""]

    grouped = (
        data_df.groupby(["timestamp_utc", "device_code"], as_index=False)[["imported_w", "exported_w"]]
        .sum()
        .sort_values(["timestamp_utc", "device_code"])
        .reset_index(drop=True)
    )
    grouped["device_code"] = grouped["device_code"].astype(str)
    grouped["imported_w"] = pd.to_numeric(grouped["imported_w"], errors="coerce").fillna(0.0)
    grouped["exported_w"] = pd.to_numeric(grouped["exported_w"], errors="coerce").fillna(0.0)

    hourly_lookup: Dict[pd.Timestamp, List[Tuple[str, float, float]]] = {}
    for ts, group in grouped.groupby("timestamp_utc"):
        hourly_lookup[pd.Timestamp(ts)] = list(
            group[["device_code", "imported_w", "exported_w"]].itertuples(index=False, name=None)
        )

    available_timestamps = pd.DatetimeIndex(sorted(hourly_lookup.keys()))
    available_set = set(available_timestamps.to_pydatetime())

    mapping_payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    with METADATA_PATH.open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)
    base_net = pp.from_pickle(str(NET_PATH))
    reset_plan = _build_reset_plan(base_net)

    _ASSET_CACHE.update(
        {
            "base_net": base_net,
            "reset_plan": reset_plan,
            "hourly_lookup": hourly_lookup,
            "available_timestamps": available_timestamps,
            "available_timestamp_set": available_set,
            "mapping": mapping_payload,
            "metadata": metadata,
        }
    )
    return _ASSET_CACHE


def _add_power_to_asymmetric_load(
    net: Any, load_index: int, phase: str | None, active_mw: float
) -> None:
    if load_index not in net.asymmetric_load.index:
        return

    if phase in {"A", "B", "C"}:
        column = f"p_{phase.lower()}_mw"
        if column in net.asymmetric_load.columns:
            net.asymmetric_load.at[load_index, column] = float(
                net.asymmetric_load.at[load_index, column]
            ) + active_mw
        return

    for column in ("p_a_mw", "p_b_mw", "p_c_mw"):
        if column in net.asymmetric_load.columns:
            net.asymmetric_load.at[load_index, column] = float(
                net.asymmetric_load.at[load_index, column]
            ) + (active_mw / 3.0)


def _apply_battery_setpoint(
    net: Any, battery_load_index: int, battery_p_mw: float, battery_q_mvar: float
) -> None:
    if battery_load_index not in net.asymmetric_load.index:
        raise KeyError(f"Battery asymmetric_load index not found in net: {battery_load_index}")

    p_share = battery_p_mw / 3.0
    q_share = battery_q_mvar / 3.0

    for p_col in ("p_a_mw", "p_b_mw", "p_c_mw"):
        if p_col in net.asymmetric_load.columns:
            net.asymmetric_load.at[battery_load_index, p_col] = float(
                net.asymmetric_load.at[battery_load_index, p_col]
            ) + p_share

    for q_col in ("q_a_mvar", "q_b_mvar", "q_c_mvar"):
        if q_col in net.asymmetric_load.columns:
            net.asymmetric_load.at[battery_load_index, q_col] = float(
                net.asymmetric_load.at[battery_load_index, q_col]
            ) + q_share


def _extract_results_tables(net: Any) -> Dict[str, pd.DataFrame]:
    tables: Dict[str, pd.DataFrame] = {}
    for key, value in net.items():
        if not isinstance(key, str) or not key.startswith("res_"):
            continue
        if isinstance(value, pd.DataFrame):
            tables[key] = value.copy()
    return tables


def _select_timestamp(
    requested_timestamp_iso: str,
    available_timestamps: pd.DatetimeIndex,
    available_timestamp_set: set,
) -> Tuple[pd.Timestamp, pd.Timestamp, bool]:
    requested = pd.Timestamp(requested_timestamp_iso)
    if requested.tzinfo is None:
        raise ValueError("timestamp_iso must include timezone information.")

    requested_utc = requested.tz_convert("UTC")
    requested_utc = requested_utc.tz_localize(None).tz_localize("UTC")

    requested_py = requested_utc.to_pydatetime()
    if requested_py in available_timestamp_set:
        return requested_utc, requested_utc, True

    pos = int(available_timestamps.searchsorted(requested_utc, side="right")) - 1
    if pos < 0:
        raise ValueError(
            "Requested timestamp is earlier than all available simulation data. "
            f"First available UTC timestamp: {available_timestamps[0].isoformat()}"
        )
    selected = pd.Timestamp(available_timestamps[pos])
    return requested_utc, selected, False


def run_power_flow(
    battery_p_mw: float,
    battery_q_mvar: float,
    timestamp_iso: str,
    exclude_exported: bool = False,
) -> Dict[str, Any]:
    """Run one digital-twin power flow at the requested timestamp.

    Args:
        battery_p_mw:
            Active-power setpoint for the battery in MW.
            Positive means charging (modeled as load).
            Negative means discharging (modeled as negative load).
        battery_q_mvar:
            Reactive-power setpoint for the battery in MVAr.
            This value is split equally across phases A/B/C.
        timestamp_iso:
            Timezone-aware ISO8601 timestamp string (for example
            ``"2026-03-23T00:30:00+01:00"`` or ``"2026-03-22T23:30:00Z"``).
            If the exact hour does not exist in package data, the nearest previous
            available hour is selected.
        exclude_exported:
            If ``True``, ignore ``ENERGY_EXPORTED (W)`` when applying
            time-series powers (import-only semantics).

    Returns:
        Dictionary with high-level KPIs and all pandapower ``res_*`` tables.
        Main keys:
        - ``converged`` (bool): currently ``True`` when run succeeds.
        - ``requested_timestamp_utc`` / ``selected_timestamp_utc`` /
          ``selected_timestamp_local`` (str).
        - ``used_previous_hour_fallback`` (bool): ``True`` when previous-hour
          selection was applied.
        - ``ext_grid_bus_vm_pu`` (float): voltage magnitude at package hub bus.
        - ``num_overloaded_lines`` (int): count of lines with loading > 100%.
        - ``num_voltage_violations`` (int): count of buses outside 0.95..1.05 pu.
        - ``max_voltage_pu`` / ``min_voltage_pu`` / ``max_line_loading_pct``.
        - ``results_tables`` (dict[str, pandas.DataFrame]): full copied
          pandapower result tables such as ``res_bus`` and ``res_line``.

    Raises:
        FileNotFoundError:
            If required package files are missing next to this module.
        ValueError:
            If ``timestamp_iso`` has no timezone, or is earlier than all
            available data.
        KeyError:
            If battery metadata points to a missing asymmetric-load row.
    """

    assets = _load_assets()
    requested_utc, selected_utc, exact_match = _select_timestamp(
        requested_timestamp_iso=timestamp_iso,
        available_timestamps=assets["available_timestamps"],
        available_timestamp_set=assets["available_timestamp_set"],
    )

    net = copy.deepcopy(assets["base_net"])
    _reset_power_targets(net, assets["reset_plan"])

    mapping: Mapping[str, List[Dict[str, Any]]] = assets["mapping"]
    hourly_lookup: Mapping[pd.Timestamp, List[Tuple[str, float, float]]] = assets["hourly_lookup"]

    for code, imported_w, exported_w in hourly_lookup.get(selected_utc, []):
        targets = mapping.get(str(code).strip().upper(), [])
        if not targets:
            continue
        effective_exported_w = 0.0 if exclude_exported else float(exported_w)
        net_load_mw = (float(imported_w) - effective_exported_w) / 1e6
        share = net_load_mw / len(targets)
        for target in targets:
            if target.get("table") != "asymmetric_load":
                continue
            _add_power_to_asymmetric_load(
                net=net,
                load_index=int(target["index"]),
                phase=target.get("phase"),
                active_mw=share,
            )

    _apply_battery_setpoint(
        net=net,
        battery_load_index=int(assets["metadata"]["battery_load_index"]),
        battery_p_mw=float(battery_p_mw),
        battery_q_mvar=float(battery_q_mvar),
    )

    pp.runpp(net, algorithm="nr", max_iteration=500, tolerance_mva=1e-3)

    ext_grid_bus = int(assets["metadata"]["hub_bus"])
    res_bus = getattr(net, "res_bus", pd.DataFrame())
    res_line = getattr(net, "res_line", pd.DataFrame())

    vm_series = _safe_numeric_series(res_bus, "vm_pu")
    line_loading = _safe_numeric_series(res_line, "loading_percent")

    ext_grid_vm = float(res_bus.at[ext_grid_bus, "vm_pu"]) if ext_grid_bus in res_bus.index else float("nan")
    num_overloaded_lines = int((line_loading > 100.0).sum()) if not line_loading.empty else 0
    if vm_series.empty:
        num_voltage_violations = 0
        max_voltage = float("nan")
        min_voltage = float("nan")
    else:
        num_voltage_violations = int(((vm_series < 0.95) | (vm_series > 1.05)).sum())
        max_voltage = float(vm_series.max(skipna=True))
        min_voltage = float(vm_series.min(skipna=True))

    max_line_loading = float(line_loading.max(skipna=True)) if not line_loading.empty else float("nan")

    return {
        "converged": True,
        "requested_timestamp_utc": requested_utc.isoformat(),
        "selected_timestamp_utc": selected_utc.isoformat(),
        "selected_timestamp_local": selected_utc.tz_convert("Europe/Madrid").isoformat(),
        "used_previous_hour_fallback": not exact_match,
        "ext_grid_bus_vm_pu": ext_grid_vm,
        "num_overloaded_lines": num_overloaded_lines,
        "num_voltage_violations": num_voltage_violations,
        "max_voltage_pu": max_voltage,
        "min_voltage_pu": min_voltage,
        "max_line_loading_pct": max_line_loading,
        "results_tables": _extract_results_tables(net),
    }

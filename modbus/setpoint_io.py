"""Shared helpers for aggregate and per-phase Modbus setpoint dispatch."""

import logging
import time

from modbus.codec import encode_point_internal_words, read_point_words, write_point_internal

AGGREGATE_SETPOINT_MODE = "aggregate"
PER_PHASE_SETPOINT_MODE = "per_phase"
TRIGGER_POINT_NAME = "trigger"
TRIGGER_APPLY_DELAY_S = 1.0
_sleep = time.sleep

AGGREGATE_P_POINT_NAMES = ("p_setpoint",)
AGGREGATE_Q_POINT_NAMES = ("q_setpoint",)
PER_PHASE_P_POINT_NAMES = ("p_u_setpoint", "p_v_setpoint", "p_w_setpoint")
PER_PHASE_Q_POINT_NAMES = ("q_u_setpoint", "q_v_setpoint", "q_w_setpoint")


def resolve_setpoint_mode(endpoint_cfg):
    points = dict((endpoint_cfg or {}).get("points", {}) or {})
    if "p_setpoint" in points or "q_setpoint" in points:
        return AGGREGATE_SETPOINT_MODE
    return PER_PHASE_SETPOINT_MODE


def _quantity_targets(endpoint_cfg, quantity, total_internal_value):
    if quantity == "p":
        if resolve_setpoint_mode(endpoint_cfg) == AGGREGATE_SETPOINT_MODE:
            point_names = AGGREGATE_P_POINT_NAMES
            internal_values = (float(total_internal_value),)
        else:
            point_names = PER_PHASE_P_POINT_NAMES
            phase_value = float(total_internal_value) / 3.0
            internal_values = (phase_value, phase_value, phase_value)
    elif quantity == "q":
        if resolve_setpoint_mode(endpoint_cfg) == AGGREGATE_SETPOINT_MODE:
            point_names = AGGREGATE_Q_POINT_NAMES
            internal_values = (float(total_internal_value),)
        else:
            point_names = PER_PHASE_Q_POINT_NAMES
            phase_value = float(total_internal_value) / 3.0
            internal_values = (phase_value, phase_value, phase_value)
    else:
        raise ValueError(f"Unsupported setpoint quantity: {quantity!r}")

    return [
        {
            "point_name": point_name,
            "internal_value": internal_value,
            "target_words": encode_point_internal_words(endpoint_cfg, point_name, internal_value),
        }
        for point_name, internal_value in zip(point_names, internal_values)
    ]


def build_setpoint_write_plan(endpoint_cfg, p_kw, q_kvar):
    return {
        "mode": resolve_setpoint_mode(endpoint_cfg),
        "p_targets": _quantity_targets(endpoint_cfg, "p", p_kw),
        "q_targets": _quantity_targets(endpoint_cfg, "q", q_kvar),
    }


def read_setpoint_target_words(client, endpoint_cfg, targets):
    return {
        str(target["point_name"]): read_point_words(client, endpoint_cfg, target["point_name"])
        for target in (targets or [])
    }


def write_setpoint_targets(client, endpoint_cfg, targets):
    results = []
    for target in (targets or []):
        ok = bool(write_point_internal(client, endpoint_cfg, target["point_name"], target["internal_value"]))
        results.append(
            {
                "point_name": str(target["point_name"]),
                "internal_value": float(target["internal_value"]),
                "ok": bool(ok),
            }
        )
    return {
        "ok": all(result["ok"] for result in results),
        "results": results,
    }


def trigger_point_configured(endpoint_cfg):
    points = dict((endpoint_cfg or {}).get("points", {}) or {})
    return TRIGGER_POINT_NAME in points


def write_trigger_value(client, endpoint_cfg, value):
    configured = trigger_point_configured(endpoint_cfg)
    if not configured:
        return {
            "configured": False,
            "state": "skipped",
            "value": int(value),
            "message": "point_not_configured",
        }

    try:
        ok = bool(write_point_internal(client, endpoint_cfg, TRIGGER_POINT_NAME, int(value)))
        return {
            "configured": True,
            "state": "ok" if ok else "failed",
            "value": int(value),
            "message": None if ok else "write_failed",
        }
    except Exception as exc:
        logging.error("Modbus setpoint I/O: trigger write error (value=%s): %s", int(value), exc)
        return {
            "configured": True,
            "state": "failed",
            "value": int(value),
            "message": str(exc),
        }


def pulse_trigger(client, endpoint_cfg, *, delay_s=TRIGGER_APPLY_DELAY_S):
    if not trigger_point_configured(endpoint_cfg):
        return {
            "configured": False,
            "state": "skipped",
            "delay_s": float(delay_s),
            "message": "point_not_configured",
            "steps": [],
        }

    _sleep(float(delay_s))
    high_result = write_trigger_value(client, endpoint_cfg, 1)
    if str(high_result.get("state")) != "ok":
        return {
            "configured": True,
            "state": "failed",
            "delay_s": float(delay_s),
            "message": "trigger_high_failed",
            "steps": [high_result],
        }

    _sleep(float(delay_s))
    low_result = write_trigger_value(client, endpoint_cfg, 0)
    return {
        "configured": True,
        "state": "ok" if str(low_result.get("state")) == "ok" else "failed",
        "delay_s": float(delay_s),
        "message": None if str(low_result.get("state")) == "ok" else "trigger_low_failed",
        "steps": [high_result, low_result],
    }


def write_setpoint_plan_with_optional_trigger(client, endpoint_cfg, write_plan, *, delay_s=TRIGGER_APPLY_DELAY_S):
    p_result = write_setpoint_targets(client, endpoint_cfg, (write_plan or {}).get("p_targets"))
    q_result = write_setpoint_targets(client, endpoint_cfg, (write_plan or {}).get("q_targets"))
    setpoints_ok = bool(p_result["ok"] and q_result["ok"])

    if setpoints_ok:
        trigger_result = pulse_trigger(client, endpoint_cfg, delay_s=delay_s)
    else:
        trigger_result = {
            "configured": trigger_point_configured(endpoint_cfg),
            "state": "skipped",
            "delay_s": float(delay_s),
            "message": "setpoint_write_failed",
            "steps": [],
        }

    return {
        "ok": bool(setpoints_ok and str(trigger_result.get("state")) in {"ok", "skipped"}),
        "p_result": p_result,
        "q_result": q_result,
        "trigger_result": trigger_result,
    }

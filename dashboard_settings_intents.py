"""Pure helpers that map dashboard triggers to settings-engine command intents."""

import pandas as pd

import manual_schedule_manager as msm
from time_utils import normalize_timestamp_value


MANUAL_BUTTON_TRIGGER_MAP = {
    "manual-toggle-lib-p-enable-btn": ("lib_p", "activate"),
    "manual-toggle-lib-p-disable-btn": ("lib_p", "inactivate"),
    "manual-toggle-lib-p-update-btn": ("lib_p", "update"),
    "manual-toggle-lib-q-enable-btn": ("lib_q", "activate"),
    "manual-toggle-lib-q-disable-btn": ("lib_q", "inactivate"),
    "manual-toggle-lib-q-update-btn": ("lib_q", "update"),
    "manual-toggle-vrfb-p-enable-btn": ("vrfb_p", "activate"),
    "manual-toggle-vrfb-p-disable-btn": ("vrfb_p", "inactivate"),
    "manual-toggle-vrfb-p-update-btn": ("vrfb_p", "update"),
    "manual-toggle-vrfb-q-enable-btn": ("vrfb_q", "activate"),
    "manual-toggle-vrfb-q-disable-btn": ("vrfb_q", "inactivate"),
    "manual-toggle-vrfb-q-update-btn": ("vrfb_q", "update"),
}


def _serialize_manual_series_df(df, tz):
    norm = msm.ensure_manual_series_terminal_duplicate_row(df, timezone_name=getattr(tz, "key", str(tz)))
    if norm.empty:
        return []
    rows = []
    for ts, row in norm.iterrows():
        ts_norm = normalize_timestamp_value(ts, tz)
        rows.append({"datetime": pd.Timestamp(ts_norm).isoformat(), "setpoint": float(row.get("setpoint", 0.0))})
    return rows


def manual_settings_intent_from_trigger(trigger_id, *, draft_series_by_key, tz):
    mapped = MANUAL_BUTTON_TRIGGER_MAP.get(trigger_id)
    if not mapped:
        return None
    series_key, action = mapped
    if action == "activate":
        return {
            "kind": "manual.activate",
            "payload": {
                "series_key": series_key,
                "series_rows": _serialize_manual_series_df(draft_series_by_key.get(series_key), tz),
            },
            "resource_key": series_key,
            "action": action,
        }
    if action == "update":
        return {
            "kind": "manual.update",
            "payload": {
                "series_key": series_key,
                "series_rows": _serialize_manual_series_df(draft_series_by_key.get(series_key), tz),
            },
            "resource_key": series_key,
            "action": action,
        }
    return {
        "kind": "manual.inactivate",
        "payload": {"series_key": series_key},
        "resource_key": series_key,
        "action": action,
    }


def api_connection_intent_from_trigger(trigger_id, *, password_value):
    if trigger_id == "set-password-btn":
        password = str(password_value).strip() if password_value is not None else ""
        return {"kind": "api.connect", "payload": {"password": password or None}}
    if trigger_id == "disconnect-api-btn":
        return {"kind": "api.disconnect", "payload": {}}
    return None


def posting_intent_from_trigger(trigger_id):
    if trigger_id == "api-posting-enable-btn":
        return {"kind": "posting.enable", "payload": {}}
    if trigger_id == "api-posting-disable-btn":
        return {"kind": "posting.disable", "payload": {}}
    return None

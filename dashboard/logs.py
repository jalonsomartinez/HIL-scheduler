"""Dashboard log parsing and file helpers."""

import os
import re
from collections import deque
from datetime import datetime

from dash import html


def _resolve_project_dir(base_dir):
    """Accept either project root or dashboard package dir and return project root."""
    candidate = os.path.abspath(base_dir)
    parent = os.path.dirname(candidate)
    if os.path.basename(candidate) == "dashboard" and os.path.isdir(os.path.join(parent, "assets")):
        return parent
    return candidate


def parse_and_format_historical_logs(file_content):
    pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (\w+) - (.+)"
    formatted_entries = []
    for line in (file_content or "").splitlines():
        match = re.match(pattern, line.strip())
        if not match:
            continue

        timestamp, level, message = match.groups()
        level = level.upper()
        if level == "ERROR":
            color = "#ef4444"
        elif level == "WARNING":
            color = "#f97316"
        elif level == "INFO":
            color = "#22c55e"
        else:
            color = "#94a3b8"

        formatted_entries.append(
            html.Div(
                [
                    html.Span(f"[{timestamp}] ", style={"color": "#94a3b8"}),
                    html.Span(f"{level}: ", style={"color": color, "fontWeight": "600"}),
                    html.Span(message, style={"color": "#e2e8f0"}),
                ]
            )
        )
    return formatted_entries


def get_logs_dir(base_dir):
    return os.path.join(_resolve_project_dir(base_dir), "logs")


def get_today_log_file_path(base_dir, tz):
    today_str = datetime.now(tz).strftime("%Y-%m-%d")
    return os.path.join(get_logs_dir(base_dir), f"{today_str}_hil_scheduler.log")


def read_log_tail(file_path, max_lines=1000):
    if not os.path.exists(file_path):
        return ""
    with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
        tail_lines = deque(handle, maxlen=max_lines)
    return "".join(tail_lines)

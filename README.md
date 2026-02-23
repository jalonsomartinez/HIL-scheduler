# HIL Scheduler

HIL Scheduler is a Python multi-agent app for dispatching active/reactive power setpoints to two logical battery plants (`LIB` and `VRFB`) through Modbus TCP, with a live Dash dashboard for control, monitoring, and recording.

## What It Does
- Runs dual-plant scheduling (`manual` or `api` source).
- Supports `local` emulation mode and `remote` hardware mode.
- Provides per-plant Start/Stop and Record/Stop controls.
- Writes per-plant daily CSV measurements in `data/`.
- Shows API fetch and measurement-posting status in the dashboard.

## Prerequisites
- Python 3.9+ (3.10+ recommended)
- `pip`
- Network access to configured remote Modbus endpoints (only for remote mode)

## Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration
Edit `config.yaml` as needed:
- `startup.schedule_source`: `manual` or `api`
- `startup.transport_mode`: `local` or `remote`
- `startup.initial_soc_pu`: shared local-emulation startup SoC for all plants
- `plants.lib` / `plants.vrfb`: model limits, Modbus endpoints, register maps
- `time.timezone`: runtime timezone

For local testing, keep `startup.transport_mode: "local"` (default), which starts local Modbus servers for both plants.

## Run
```bash
source venv/bin/activate
python3 hil_scheduler.py
```

Open the dashboard at:
- `http://127.0.0.1:8050/`

## Basic Dashboard Workflow
1. Select source (`Manual` or `API`) and transport mode (`Local` or `Remote`).
2. Load or generate manual schedule data (or configure API password in API tab).
3. Start a plant (`LIB` and/or `VRFB`) from the Status & Plots tab.
4. Click `Record` per plant to write measurements to `data/YYYYMMDD_<plant>.csv`.
5. Use `Stop` for dispatch stop and `Stop Recording` when session capture should end.

## Outputs
- Measurements: `data/`
- Logs: `logs/YYYY-MM-DD_hil_scheduler.log`

## Quality Checks
Run these checks before pushing changes:
```bash
python3 -m py_compile *.py
./venv/bin/python -m unittest discover -s tests -v
```

## Legacy Compatibility Notes
- `schedule_manager.py` is deprecated and not part of the active runtime path.
- `config_loader.py` no longer emits legacy flat alias keys by default.
- Temporary migration fallback: set `HIL_ENABLE_LEGACY_CONFIG_ALIASES=1` to re-enable legacy alias keys.

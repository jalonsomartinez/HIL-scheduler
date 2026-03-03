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
In Beaglebone Black, it was needed to install the following packages:
```bash
sudo apt-get update
sudo apt-get install libopenblas-dev
```

## Configuration
Edit `config.yaml` as needed:
- `startup.schedule_source`: `manual` or `api`
- `startup.transport_mode`: `local` or `remote`
- `startup.initial_soc_pu`: shared local-emulation startup SoC for all plants
- `plants.lib` / `plants.vrfb`: model limits, Modbus endpoints, register maps
- `time.timezone`: runtime timezone
- `dashboard.private.host` / `dashboard.private.port`: private ops dashboard bind
- `dashboard.public_readonly.enabled`: enable separate public read-only dashboard
- `dashboard.public_readonly.host` / `dashboard.public_readonly.port`: public dashboard bind
- `dashboard.public_readonly.auth.mode`: `basic` or `none`

If `dashboard.public_readonly.enabled: true` and auth mode is `basic`, set credentials through environment variables.
You can still export them manually:
```bash
export HIL_PUBLIC_DASH_USER='your-user'
export HIL_PUBLIC_DASH_PASS='your-password'
# Optional: preload Istentore API password used by API connect/fetch/posting gates.
export HIL_API_PASSWORD='your-api-password'
# Optional: explicit Flask secret key for dashboard session support.
export HIL_FLASK_SECRET_KEY='your-long-random-secret'
```
Or use the startup scripts with local credential files:
- Linux/macOS template: `.env.public-dashboard.example` -> copy to `.env.public-dashboard`
- Windows template: `.env.public-dashboard.ps1.example` -> copy to `.env.public-dashboard.ps1`

For local testing, keep `startup.transport_mode: "local"` (default), which starts local Modbus servers for both plants.

## Run
```bash
source venv/bin/activate
python3 hil_scheduler.py
```

Or use startup scripts (activates `venv`, loads public dashboard credentials, runs app):

Linux/macOS:
```bash
cp .env.public-dashboard.example .env.public-dashboard
# edit .env.public-dashboard with real values
./scripts/start_hil_linux.sh
```

Windows PowerShell:
```powershell
Copy-Item .env.public-dashboard.ps1.example .env.public-dashboard.ps1
# edit .env.public-dashboard.ps1 with real values
.\scripts\start_hil_windows.ps1
```

Windows `cmd.exe`:
```bat
scripts\start_hil_windows.cmd
```

Open the dashboard at:
- Private ops dashboard: `http://127.0.0.1:8050/` (default)
- Public read-only dashboard: `http://127.0.0.1:8060/` (if enabled)

## Basic Dashboard Workflow
1. Select source (`Manual` or `API`) and transport mode (`Local` or `Remote`).
2. Load or generate manual schedule data (or in API tab: set password with `Save Password`, then `Connect` / `Disconnect`).
3. Start a plant (`LIB` and/or `VRFB`) from the Status & Plots tab.
4. Click `Record` per plant to write measurements to `data/YYYYMMDD_<plant>.csv`.
5. Use `Stop` for dispatch stop and `Stop Recording` when session capture should end.

## Outputs
- Measurements: `data/`
- Logs: `logs/YYYY-MM-DD_hil_scheduler.log`

## Quality Checks
Run these checks before pushing changes:
```bash
python3 -m py_compile *.py dashboard/*.py control/*.py settings/*.py measurement/*.py scheduling/*.py modbus/*.py runtime/*.py
./venv/bin/python -m unittest discover -s tests -v
```

## VRFB Remote Diagnostics Runbook
Use this diagnostics matrix on the same remote-test machine/network where VRFB failures are observed.

Prerequisite:
```bash
source venv/bin/activate
pip install -r requirements.txt
python3 -c "import pymodbus; print(pymodbus.__version__)"
```
Expected version: `3.9.2`

### Command Matrix (Repeat Each Mode 2x, Duration >= 180s)
1. `dashboard_like` read-only baseline (expected pass):
```bash
./venv/bin/python scripts/vrfb_remote_diag.py \
  --mode dashboard_like \
  --host 10.117.133.26 --port 502 --slave 1 \
  --timeout-s 2.0 --poll-s 1.0 --duration-s 180 \
  --out logs/vrfb_remote_diag_dashboard_like_readonly_run1.csv
```
2. `dashboard_like` read+write sequence (`start_command=2`, `enable=1`, `p/q` writes every 10 cycles):
```bash
./venv/bin/python scripts/vrfb_remote_diag.py \
  --mode dashboard_like \
  --host 10.117.133.26 --port 502 --slave 1 \
  --timeout-s 2.0 --poll-s 1.0 --duration-s 180 \
  --dashboard-write-every 10 --dashboard-p-kw 0 --dashboard-q-kvar 0 \
  --out logs/vrfb_remote_diag_dashboard_like_write_run1.csv
```
3. `app_like_parallel` contention probe (expected to reproduce if multi-session contention exists):
```bash
./venv/bin/python scripts/vrfb_remote_diag.py \
  --mode app_like_parallel \
  --host 10.117.133.26 --port 502 --slave 1 \
  --timeout-s 2.0 --duration-s 180 \
  --out logs/vrfb_remote_diag_app_like_parallel_run1.csv
```
4. `app_like_serial` serialized probe (expected recovery if contention is root cause):
```bash
./venv/bin/python scripts/vrfb_remote_diag.py \
  --mode app_like_serial \
  --host 10.117.133.26 --port 502 --slave 1 \
  --timeout-s 2.0 --duration-s 180 \
  --out logs/vrfb_remote_diag_app_like_serial_run1.csv
```
5. Timeout sensitivity rerun (`2.0s` vs `5.0s`) for any failing mode:
```bash
./venv/bin/python scripts/vrfb_remote_diag.py \
  --mode app_like_parallel \
  --host 10.117.133.26 --port 502 --slave 1 \
  --timeout-s 5.0 --duration-s 180 \
  --out logs/vrfb_remote_diag_app_like_parallel_timeout5_run1.csv
```

Each run produces:
- CSV operation log with columns:
  - `ts_iso,mode,client_id,op,address,count_or_value,ok,latency_ms,error_type,error_text`
- Markdown summary report next to the CSV (`.md`) with pass/fail, error distribution, latency percentiles, and next-step guidance.

### Root-Cause Classification Guide
Use results across modes:
- `dashboard_like` passes, `app_like_parallel` fails, `app_like_serial` passes:
  - Root cause likely session/concurrency contention.
- All modes fail with similar connection errors:
  - Root cause likely network path/firewall/routing or endpoint availability.
- Reads pass but writes fail across modes:
  - Root cause likely write policy/command-state/register access restrictions.
- Only specific point addresses fail while others are stable:
  - Root cause likely map/access mismatch at point level.

## Legacy Compatibility Notes
- `schedule_manager.py` was removed after runtime migration to `manual_schedule_manager.py` and `schedule_runtime.py`.
- `config_loader.py` no longer emits legacy flat alias keys by default.
- Temporary migration fallback: set `HIL_ENABLE_LEGACY_CONFIG_ALIASES=1` to re-enable legacy alias keys.

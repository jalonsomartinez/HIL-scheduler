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

## Legacy Compatibility Notes
- `schedule_manager.py` was removed after runtime migration to `manual_schedule_manager.py` and `schedule_runtime.py`.
- `config_loader.py` no longer emits legacy flat alias keys by default.
- Temporary migration fallback: set `HIL_ENABLE_LEGACY_CONFIG_ALIASES=1` to re-enable legacy alias keys.

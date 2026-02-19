# System Patterns: HIL Scheduler

## Agent Architecture

## Dual Logical Plant Parallel Pattern (2026-02-19)

- Runtime plant model is now fixed logical IDs: `lib` and `vrfb`.
- Schedule source is global:
  - `active_schedule_source` in `{'manual', 'api'}` applies to both plants.
- Transport mode is global:
  - `transport_mode` in `{'local', 'remote'}` applies to both plants.
- Scheduler dispatch gate is per plant:
  - `scheduler_running_by_plant[plant_id]`.
- Schedule dataframes are per plant:
  - `manual_schedule_df_by_plant[plant_id]`
  - `api_schedule_df_by_plant[plant_id]`
- Recording control is per plant:
  - `measurements_filename_by_plant[plant_id]` (`None` means recording off for that plant).
- Plot cache is per plant:
  - `current_file_path_by_plant[plant_id]`
  - `current_file_df_by_plant[plant_id]`
- Local emulation mode hosts two in-app Modbus servers (LIB + VRFB) simultaneously.
- Remote mode connects both logical plants to their configured remote endpoints.
- Data fetcher parses one API response and populates both schedule maps:
  - LIB net power: `lib_to_vpp_kw - vpp_to_lib_kw`
  - VRFB net power: `vrfb_to_vpp_kw - vpp_to_vrfb_kw`

## Dashboard Safe Switch + Stateful Controls Pattern (2026-02-19)

- Dashboard tabs are now: `Status & Plots`, `Manual Schedule`, `API Schedule`, `Logs`.
- Logs tab uses a dual-source pattern:
  - live session logs from `shared_data['session_logs']`,
  - historical log files selected from `logs/*.log`.
- Source-switch pattern is confirmation-first and safety-gated:
  - clicking Manual/API shows `schedule-switch-modal`,
  - on confirm: set `schedule_switching=True`, safe-stop both plants, then set `active_schedule_source`, then clear switching flag.
- Per-plant operational transition state is explicit:
  - `plant_transition_by_plant[plant_id]` tracks `starting|running|stopping|stopped|unknown`.
- Button render logic is derived from runtime transition + recording state:
  - Start/Stop disable and labels from transition state,
  - Record/Stop Recording disable and labels from `measurements_filename_by_plant[plant_id]`.
- Safe-stop helper contract returns structured outcome:
  - `{"threshold_reached": bool, "disable_ok": bool}`,
  - logs each phase for traceability.
- Plot update pattern preserves interactive zoom via stable Plotly `uirevision` keys.

## Timezone Handling Pattern (2026-02-17)

- Canonical runtime timestamp model: timezone-aware datetimes in configured timezone (`time.timezone`, default `Europe/Madrid`).
- API ingestion: parse delivery periods as UTC and convert to configured timezone before storing into `api_schedule_df`.
- Manual schedules (random + CSV): normalize naive timestamps as configured timezone; convert aware timestamps to configured timezone.
- Scheduler lookups: normalize schedule index and use timezone-aware `now` before `asof`.
- Measurement persistence: write CSV `timestamp` as ISO 8601 with timezone offset.
- Legacy compatibility: naive historical timestamps are interpreted as configured timezone when loaded.

## API Day Rollover + Stale Setpoint Pattern (2026-02-18)

- Data fetcher status is day-scoped:
  - `today_date` and `tomorrow_date` are stored alongside fetched flags/counters.
- Midnight reconciliation rule:
  - If calendar day changes and previous `tomorrow_date == new today_date` and `tomorrow_fetched=True`, promote previous tomorrow to today's fetched state.
  - Reset new tomorrow fetch state to pending when tomorrow date changes.
- Tomorrow API fetch polling remains gated by configured `istentore_api.poll_start_time`.
- API stale cutoff uses `ISTENTORE_SCHEDULE_PERIOD_MINUTES` (default 15 min):
  - Scheduler (API source only) and dashboard Start immediate-setpoint path both force `0/0` when `now - selected_row_timestamp` exceeds the window.
- Dashboard API status surfaces concrete date labels:
  - `Today (YYYY-MM-DD)` and `Tomorrow (YYYY-MM-DD)` to remove ambiguity across midnight.

## Measurement Trigger Scheduling Pattern (2026-02-18)

- Use a fixed measurement wall-clock anchor at agent startup, rounded up to the next whole second.
- Align a monotonic anchor to the same wall-clock anchor to avoid drift from variable loop/runtime delays.
- Compute measurement step index as:
  `current_step = floor((time.monotonic() - measurement_anchor_mono) / measurement_period_s)`.
- Execute at most once per step:
  trigger only when `current_step > last_executed_trigger_step`, then mark step consumed immediately.
- Skip missed intermediate steps (no catch-up burst reads).
- Persist measurement `timestamp` as the scheduled step time (`anchor_wall + step * period`), not read completion time.
- Keep recording/session semantics unchanged (null boundaries, midnight routing, periodic disk flush).

## API Measurement Posting Pattern (2026-02-18)

- Measurement posting to Istentore API is owned by `measurement_agent.py` (same source of truth as measured SoC/P/Q/V values).
- Posting cadence is independent from:
  - measurement sampling cadence (`MEASUREMENT_PERIOD_S`),
  - CSV flush cadence (`MEASUREMENTS_WRITE_PERIOD_S`).
- Posting trigger uses a fixed monotonic step scheduler:
  - `post_step = floor((time.monotonic() - post_anchor_mono) / measurement_post_period_s)`.
- At each post step, agent enqueues payloads built from the latest successful measurement sample.
- Posting is gated by runtime + config:
  - `active_schedule_source == "api"`,
  - API password exists,
  - `ISTENTORE_POST_MEASUREMENTS_IN_API_MODE` enabled.
- Payload conversion for API:
  - `soc_kwh = soc_pu * PLANT_CAPACITY_KWH`
  - `p_w = p_poi_kw * 1000`
  - `q_var = q_poi_kvar * 1000`
  - `v_v = v_poi_pu * PLANT_POI_VOLTAGE_V`
- Conversion factors (`PLANT_CAPACITY_KWH`, `PLANT_POI_VOLTAGE_V`) are parsed once during agent initialization and reused for each post tick.
- Payload validation gate:
  - each source value is parsed as float and must be finite,
  - invalid values (non-numeric, `NaN`, `inf`) are skipped with warning logs,
  - `None` values are not added to the retry queue.
- Per-variable disable:
  - if a series ID resolves to `None` (from YAML `null`), that variable is skipped and not enqueued for posting.
- Posted timestamps are strict UTC ISO (`YYYY-MM-DDTHH:MM:SS+00:00`).
- Retry strategy:
  - bounded in-memory queue (`ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN`),
  - exponential backoff between retries (`initial`/`max`),
  - queue overflow drops oldest payload with warning.
- Queue is cleared when posting mode is disabled (e.g., switch away from API source).

## API Measurement Posting Observability Pattern (2026-02-19)

- Runtime posting telemetry lives in shared state:
  - `measurement_post_status[plant_id]` for `lib` and `vrfb`.
- Per-plant status schema:
  - `posting_enabled`
  - `last_success` (`timestamp`, `metric`, `value`, `series_id`, `measurement_timestamp`)
  - `last_attempt` (`timestamp`, `metric`, `value`, `series_id`, `measurement_timestamp`, `attempt`, `result`, `error`, `next_retry_seconds`)
  - `last_error` (`timestamp`, `message`)
  - `pending_queue_count`
  - `oldest_pending_age_s`
  - `last_enqueue`
- Queue attribution pattern:
  - each queued payload now includes `plant_id` and `metric` in addition to `series_id/value/timestamp/attempt/retry`.
- Telemetry update points:
  - on enqueue: update `last_enqueue`,
  - before send attempt: write `last_attempt` with `result=attempting`,
  - on success: update `last_success`, set `last_attempt.result=success`, clear `last_error`,
  - on failure: update `last_attempt.result=failed` with error + retry ETA, and set `last_error`.
- Queue health derivation:
  - recompute `pending_queue_count` and `oldest_pending_age_s` per plant from current in-memory queue contents each loop.
- UI presentation contract (API Schedule tab):
  - summary cards per plant expose success/attempt/error/queue observability without changing posting semantics.

### Agent Base Pattern
All agents follow a consistent pattern:

```python
def agent_name(config, shared_data):
    logging.info("Agent started.")
    
    # 1. Initialization (connections, servers, state)
    initialize_resources()
    
    # 2. Main execution loop
    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()
        
        try:
            # 3. Core logic
            perform_agent_task()
            
        except Exception as e:
            logging.error(f"Error in agent: {e}")
        
        # 4. Rate limiting
        time.sleep(max(0, period_s - (time.time() - start_loop_time)))
    
    # 5. Cleanup
    cleanup_resources()
    logging.info("Agent stopped.")
```

### Agent Types and Responsibilities

| Agent | Thread | Role | Data Flow |
|-------|--------|------|-----------|
| **Director** | Main | Orchestrates lifecycle | Starts/stops all agents |
| **Data Fetcher** | Separate | API fetcher | Fetches API → api_schedule_df |
| **Scheduler** | Separate | Setpoint dispatcher | Reads active source → Plant Modbus |
| **Plant** | Separate | Merged PPC + Battery simulation | Single Modbus server, internal battery sim |
| **Measurement** | Separate | Data logger | Reads Plant Modbus → measurements.csv |
| **Dashboard** | Separate | UI server | Four tabs: Status & Plots, Manual, API, Logs |

### Shared Data Structure

```python
shared_data = {
    # Two separate schedules
    "manual_schedule_df": pd.DataFrame(),      # Dashboard writes, Scheduler reads
    "api_schedule_df": pd.DataFrame(),         # Data Fetcher writes, Scheduler reads
    
    # Schedule selection
    "active_schedule_source": "manual",        # 'manual' or 'api'
    "scheduler_running": False,                # True when scheduler should dispatch setpoints
    
    # API configuration
    "api_password": None,                      # Dashboard writes, Data Fetcher reads
    
    # Status information
    "data_fetcher_status": {
        "connected": False,
        "today_fetched": False,
        "tomorrow_fetched": False,
        "today_date": None,
        "tomorrow_date": None,
        "today_points": 0,
        "tomorrow_points": 0,
        "last_attempt": None,
        "error": None,
    },
    
    # Measurement file management
    "measurements_filename": None,             # Dashboard sets, Measurement Agent polls
    "current_file_path": None,                 # Selected plant/day file path for plot cache
    "current_file_df": pd.DataFrame(),         # In-memory selected plant/day data for plot
    "pending_rows_by_file": {},                # path -> buffered rows pending disk flush
    
    # Switching flags
    "plant_switching": False,                  # True when plant switch in progress
    "schedule_switching": False,               # True when schedule switch in progress
    
    # Existing data
    "measurements_df": pd.DataFrame(),
    "lock": threading.Lock(),
    "shutdown_event": threading.Event(),
}
```

### Daily Per-Plant Recording Pattern (Record/Stop)

Recording writes to one file per plant per day:
- `data/YYYYMMDD_<plantname>.csv` (local time)
- plant name comes from config (`modbus_local.name`, `modbus_remote.name`) and is sanitized for filesystem-safe filenames.

```python
# Dashboard (Record button)
filename = f"data/{today_yyyymmdd}_{selected_plant_name}.csv"
with shared_data['lock']:
    shared_data['measurements_filename'] = filename

# Dashboard (Record Stop button)
with shared_data['lock']:
    shared_data['measurements_filename'] = None
```

**Key Design Decisions:**
1. **Measurement agent is the single writer**: dashboard does not write measurement CSV directly in normal Record/Stop flow.
2. **Row-level timestamp routing**: each queued row is routed to destination file by its own timestamp date, so midnight split is naturally correct.
3. **Buffered disk writes**: rows are stored in `pending_rows_by_file[path]` and flushed every `MEASUREMENTS_WRITE_PERIOD_S`.
4. **In-memory plot cache**: selected plant/day file is cached in `current_file_df`, and pending rows for that file are merged in memory for immediate plotting.
5. **Null-boundary sessions**:
   - record start: sanitize historical tail (append null if latest historical row is non-null),
   - first real sample: prepend null at `first_real - measurement_period`,
   - record stop: append null at `last_real + measurement_period` (fallback null when no real sample occurred).

## Confirmation Modal Pattern (Plant/Schedule Switching)

Pattern for implementing confirmation dialogs before destructive operations:

### Modal UI Structure
```python
html.Div(id='switch-modal', className='hidden', style={
    'position': 'fixed', 'top': '0', 'left': '0', 'width': '100%', 'height': '100%',
    'backgroundColor': 'rgba(0,0,0,0.5)', 'zIndex': '1000', 'display': 'flex',
    'justifyContent': 'center', 'alignItems': 'center'
}, children=[
    html.Div(style={
        'backgroundColor': 'white', 'padding': '24px', 'borderRadius': '8px',
        'maxWidth': '400px', 'boxShadow': '0 4px 12px rgba(0,0,0,0.15)'
    }, children=[
        html.H3("Confirm Switch", style={'marginTop': '0'}),
        html.P("Description of what will happen. Continue?"),
        html.Div(style={'display': 'flex', 'gap': '12px', 'marginTop': '20px', 
                       'justifyContent': 'flex-end'}, children=[
            html.Button('Cancel', id='switch-cancel', className='btn btn-secondary'),
            html.Button('Confirm', id='switch-confirm', className='btn btn-primary'),
        ]),
    ]),
])
```

### Single Callback Pattern
Consolidate all logic into one callback (prevents conflicts):

```python
@app.callback(
    [Output('selector', 'value'),
     Output('option1-btn', 'className'),
     Output('option2-btn', 'className'),
     Output('switch-modal', 'className')],
    [Input('option1-btn', 'n_clicks'),
     Input('option2-btn', 'n_clicks'),
     Input('switch-cancel', 'n_clicks'),
     Input('switch-confirm', 'n_clicks')],
    [State('selector', 'value')],
    prevent_initial_call=True
)
def handle_selection(opt1_clicks, opt2_clicks, cancel_clicks, confirm_clicks, current_value):
    ctx = callback_context
    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    # Handle cancel - return to current state, hide modal
    if trigger_id == 'switch-cancel':
        return current_value, 'active', 'inactive', 'hidden'
    
    # Handle confirm - perform switch in background thread
    if trigger_id == 'switch-confirm':
        requested_value = 'opt2' if current_value == 'opt1' else 'opt1'
        
        def perform_switch():
            # 1. Stop system
            stop_system()
            # 2. Flush measurements
            flush_and_clear_measurements()
            # 3. Update shared_data
            with shared_data['lock']:
                shared_data['key'] = requested_value
        
        threading.Thread(target=perform_switch).start()
        return requested_value, 'inactive', 'active', 'hidden'
    
    # Handle option click - show modal if different from current
    if trigger_id == 'option2-btn' and current_value != 'opt2':
        return current_value, 'active', 'inactive', ''  # Empty class shows modal
    
    # Default: no change
    return current_value, 'active', 'inactive', 'hidden'
```

## Safe Stop and Switch Pattern

Standard procedure for stopping scheduler/plant operation:

```python
def safe_stop_plant(threshold_kw=1.0, timeout_s=30):
    # 1) Stop scheduler dispatch
    with shared_data['lock']:
        shared_data['scheduler_running'] = False

    # 2) Send zero setpoints
    send_setpoints(0.0, 0.0)

    # 3) Wait for measured battery P/Q below threshold
    reached = wait_until_battery_power_below_threshold(threshold_kw, timeout_s)

    # 4) Disable plant regardless (warn on timeout)
    if not reached:
        logging.warning("Safe stop timeout; forcing disable.")
    set_enable(0)
```

Measurement flush/clear remains separate and is only used when switching plants:

```python
def stop_recording_and_flush():
    """Flush current recording file and stop writing to disk."""
    with shared_data['lock']:
        filename = shared_data.get('measurements_filename')
        df = shared_data.get('measurements_df', pd.DataFrame()).copy()
    if filename and not df.empty:
        df.to_csv(filename, index=False)
    with shared_data['lock']:
        shared_data['measurements_filename'] = None

def flush_and_clear_measurements():
    """Flush current measurements, clear DataFrame, and clear recording filename."""
    with shared_data['lock']:
        filename = shared_data.get('measurements_filename')
        df = shared_data.get('measurements_df', pd.DataFrame()).copy()
    
    # Write to CSV if data exists
    if filename and not df.empty:
        df.to_csv(filename, index=False)
    
    # Clear DataFrame and filename
    with shared_data['lock']:
        shared_data['measurements_df'] = pd.DataFrame()
        shared_data['measurements_filename'] = None
```

**Usage in Switch Operations:**
```python
def perform_schedule_switch():
    # Stop plant safely, keep measurements as-is
    safe_stop_plant()
    with shared_data['lock']:
        shared_data['active_schedule_source'] = new_source

def perform_plant_switch():
    # Stop safely, then clear measurements to avoid mixing plant datasets
    safe_stop_plant()
    flush_and_clear_measurements()
    with shared_data['lock']:
        shared_data['selected_plant'] = new_plant
```

| Timer | Period | Purpose |
|-------|--------|---------|
| Filename poll | 1 second | Detect filename changes quickly |
| Measurement | `MEASUREMENT_PERIOD_S` | Read from Modbus at configured rate |
| CSV write | `MEASUREMENTS_WRITE_PERIOD_S` | Persist data to disk periodically |

**Benefits:**
- Clear separation between scheduler operation and recording operation
- Safer stop sequence with explicit zero setpoints + measured decay check
- Schedule switching no longer destroys measurement session
- Plant switching still protects dataset integrity by flushing/clearing

## Data Flow Patterns

### Schedule Data Flow (New Architecture)
```
Manual Schedule Path:
  Dashboard Tab 1 → manual_schedule_manager → manual_schedule_df
                                                    ↓
                                            [Scheduler reads if active]
                                                    ↓
                                            Plant Modbus Server

API Schedule Path:
  Dashboard Tab 2 (sets password) → Data Fetcher (polling loop)
                                          ↓
                                   api_schedule_df
                                          ↓
                                   [Scheduler reads if active]
                                          ↓
                                   Plant Modbus Server

Scheduler Logic:
  Reads active_schedule_source → 
    IF 'manual': use manual_schedule_df
    IF 'api': use api_schedule_df
```

### Old Schedule Data Flow (Deprecated)
```
[Random Generation] → schedule_source.csv (5min → 1min interpolated)
                           ↓
              [Data Fetcher reads]
                           ↓
              schedule_final_df (1-second resolution via forward-fill)
                           ↓
              [Scheduler reads with asof]
                           ↓
              Plant Modbus Server (setpoint register)
```

### Control Flow
```
Dashboard (User clicks Start)
           ↓
    Sets scheduler_running=True
           ↓
    Writes ENABLE=1 and immediate latest setpoint to Plant
           ↓
    Scheduler agent dispatches schedule only while scheduler_running=True
           ↓
    Plant agent reads ENABLE and setpoint registers
           ↓
    IF enabled: applies setpoint to battery
    IF disabled: sends 0kW
           ↓
    Battery simulation with SoC tracking
           ↓
    Plant model computes P/Q at POI
           ↓
    All values exposed via Modbus
```

### Measurement Flow
```
Measurement Agent (periodic)
    ├── Read Plant: original_setpoint, actual_setpoint, SoC
    ├── Read Plant: P_poi, Q_poi, V_poi
    ├── Append to measurements_df
    └── Write to active recording file if measurements_filename is set
```

## Modbus Communication Patterns

### Client Pattern (Scheduler, Measurement, Dashboard)
```python
client = ModbusClient(host=host, port=port)

# Connection with retry
if not client.is_open:
    if not client.open():
        # Log warning, sleep, retry

# Read/Write operations
regs = client.read_holding_registers(address, count)
client.write_multiple_registers(address, values)

# Cleanup on shutdown
client.close()
```

### Server Pattern (Plant Agent)
```python
server = ModbusServer(host=host, port=port, no_block=True)
server.start()

# In main loop:
# Read from own databank
regs = server.data_bank.get_holding_registers(address, count)
# Write to own databank
server.data_bank.set_holding_registers(address, values)

# Cleanup
server.stop()
```

## Thread Safety Patterns

### Shared Data Access
Minimize lock time - only hold lock for reference operations, not data processing:

```python
# WRONG: Holding lock during slow operations
with shared_data['lock']:
    schedule_df = shared_data['schedule_df']
    result = schedule_df.asof(timestamp)  # SLOW - don't hold lock!
    client.write_registers(...)  # SLOW - don't hold lock!

# CORRECT: Get reference quickly, then release lock
with shared_data['lock']:
    schedule_df = shared_data['schedule_df']  # Just get reference

# Do all work outside lock
result = schedule_df.asof(timestamp)  # DataFrame read is thread-safe
client.write_registers(...)
```

### Lock Usage Guidelines
| Operation | Lock Needed? | Notes |
|-----------|--------------|-------|
| Read dict reference | Yes (brief) | `shared_data['key']` |
| Write dict reference | Yes (brief) | `shared_data['key'] = value` |
| Read DataFrame | Yes (brief) | Get reference, then copy outside lock |
| Write DataFrame | Yes (brief) | Replace entire DataFrame reference |
| DataFrame operations | No | `asof()`, indexing, etc. |
| Modbus/network I/O | No | Never hold lock during I/O |
| Disk I/O (CSV write) | No | Copy DataFrame, write outside lock |
| DataFrame concat/merge | No | Prepare outside lock, assign with brief lock |

### Direct Access Pattern (Simplified)
Write directly to shared_data for immediate visibility. Locks are held only for microseconds:

```python
# Measurement Agent - direct write (no buffer needed)
def take_measurement():
    # ... read from Modbus ...
    
    new_row = pd.DataFrame([{
        "timestamp": datetime.now(),
        "value": measurement_value,
        # ...
    }])
    
    # Brief lock to append
    with shared_data['lock']:
        if shared_data['measurements_df'].empty:
            shared_data['measurements_df'] = new_row
        else:
            shared_data['measurements_df'] = pd.concat(
                [shared_data['measurements_df'], new_row],
                ignore_index=True
            )
```

```python
# Dashboard Agent - direct read with brief locks
@app.callback(...)
def update_graphs(n):
    # Read all shared data with brief lock
    with shared_data['lock']:
        measurements_df = shared_data.get('measurements_df', pd.DataFrame()).copy()
        schedule_df = shared_data.get('manual_schedule_df', pd.DataFrame()).copy()
        active_source = shared_data.get('active_schedule_source', 'manual')
    
    # Work with copies outside lock
    # ... create figures ...
```

### CSV Write Pattern (Avoid I/O in Lock)
```python
# WRONG: Disk I/O inside lock
with shared_data['lock']:
    shared_data['measurements_df'].to_csv('file.csv')  # Blocks all threads!

# CORRECT: Copy reference, write outside lock
with shared_data['lock']:
    df = shared_data['measurements_df'].copy()
df.to_csv('file.csv')  # Other threads not blocked
```

### Data Fetcher Pattern (Prepare Outside Lock)
```python
# Get reference with brief lock
with shared_data['lock']:
    existing_df = shared_data['api_schedule_df']

# DataFrame operations outside lock
if not existing_df.empty:
    non_overlapping = existing_df.index.difference(df.index)
    combined_df = pd.concat([existing_df.loc[non_overlapping], df])
else:
    combined_df = df

# Brief lock only for assignment
with shared_data['lock']:
    shared_data['api_schedule_df'] = combined_df
```

### Shared Data Access (Legacy Pattern - Keep for Reference)
```python
# Writing
with shared_data['lock']:
    shared_data['schedule_final_df'] = new_df

# Reading - only get reference, don't copy unless modifying
with shared_data['lock']:
    schedule_df = shared_data['schedule_df']
# Work with schedule_df outside lock
```

### Shutdown Coordination
```python
# Director signals shutdown
shared_data['shutdown_event'].set()

# Agents check in loop
while not shared_data['shutdown_event'].is_set():
    # Work

# Director waits for completion
for t in threads:
    t.join()
```

## Error Handling Patterns

### Connection Retry
```python
if not client.open():
    logging.warning("Could not connect, retrying...")
    time.sleep(2)
    continue  # Skip to next loop iteration
```

### Graceful Degradation
```python
try:
    data = read_operation()
except FileNotFoundError:
    logging.warning("File not found, waiting...")
    time.sleep(5)
except Exception as e:
    logging.error(f"Unexpected error: {e}")
```

## Battery SoC Limiting Algorithm

```python
# Calculate expected SoC after applying power
future_soc = current_soc - (power_kw * dt_hours)

# Check boundaries
if future_soc > capacity:
    # Would overcharge - limit charging
    limited_power = (current_soc - capacity) / dt_hours
    limited_power = max(power_kw, limited_power)  # Less negative
elif future_soc < 0:
    # Would over-discharge - limit discharging
    limited_power = current_soc / dt_hours
    limited_power = min(power_kw, limited_power)  # Less positive

# Track limitation state for logging
if is_limited and (was_not_limited or power_changed):
    logging.warning("Power limited due to SoC boundary")
elif was_limited and not is_limited:
    logging.info("Power limitation removed")
```

## Plant Model (Simplified - No Impedance)

The plant model has been simplified to eliminate impedance calculations. With no impedance between battery and POI, plant power equals battery power.

### Given Parameters
- P_batt: Battery active power (kW)
- Q_batt: Battery reactive power (kvar)
- V_poi: POI voltage (V, fixed from config)

### Calculation Steps

```python
# Plant power equals battery power (no losses)
P_poi_kw = P_batt_kw
Q_poi_kvar = Q_batt_kvar

# POI voltage is fixed from config (20 kV nominal)
V_poi_pu = V_poi_v / 20000.0
```

## Configuration Patterns

### YAML Configuration (Simulated Plant)
```yaml
plant:
  capacity_kwh: 50.0
  initial_soc_pu: 0.5
  power_limits:
    p_max_kw: 1000.0
    p_min_kw: -1000.0
    q_max_kvar: 600.0
    q_min_kvar: -600.0
  poi_voltage_v: 20000.0  # POI voltage in Volts (20 kV)
```

### Config Loader
Converts nested YAML to flat dictionary for backward compatibility:
```python
def load_config(path="config.yaml"):
    with open(path) as f:
        yaml_config = yaml.safe_load(f)
    
    config = {}
    config["PLANT_CAPACITY_KWH"] = yaml_config["plant"]["capacity_kwh"]
    config["PLANT_POI_VOLTAGE_V"] = yaml_config["plant"]["poi_voltage_v"]
    # ... etc
    return config
```

## Register Map Pattern

Unified register map for Plant Agent (all 16-bit registers):

| Address | Size | Type | Register Name | Config Key |
|---------|------|------|---------------|------------|
| 0 | 1 word | signed | P_SETPOINT_IN | PLANT_P_SETPOINT_REGISTER |
| 2 | 1 word | signed | P_BATTERY | PLANT_P_BATTERY_ACTUAL_REGISTER |
| 4 | 1 word | signed | Q_SETPOINT_IN | PLANT_Q_SETPOINT_REGISTER |
| 6 | 1 word | signed | Q_BATTERY | PLANT_Q_BATTERY_ACTUAL_REGISTER |
| 10 | 1 word | unsigned | ENABLE | PLANT_ENABLE_REGISTER |
| 12 | 1 word | unsigned | SOC | PLANT_SOC_REGISTER |
| 14 | 1 word | signed | P_POI | PLANT_P_POI_REGISTER |
| 16 | 1 word | signed | Q_POI | PLANT_Q_POI_REGISTER |
| 18 | 1 word | unsigned | V_POI | PLANT_V_POI_REGISTER |

**Note:** Power values are stored in hW (hectowatts) = 0.1 kW. Range: ±3276.7 kW for 16-bit signed.

# System Patterns: HIL Scheduler

## Agent Architecture

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
| **Dashboard** | Separate | UI server | Three tabs: Manual, API, Status & Plots |

### Shared Data Structure

```python
shared_data = {
    # Two separate schedules
    "manual_schedule_df": pd.DataFrame(),      # Dashboard writes, Scheduler reads
    "api_schedule_df": pd.DataFrame(),         # Data Fetcher writes, Scheduler reads
    
    # Schedule selection
    "active_schedule_source": "manual",        # 'manual' or 'api'
    
    # API configuration
    "api_password": None,                      # Dashboard writes, Data Fetcher reads
    
    # Status information
    "data_fetcher_status": {
        "connected": False,
        "today_fetched": False,
        "tomorrow_fetched": False,
        "today_points": 0,
        "tomorrow_points": 0,
        "last_attempt": None,
        "error": None,
    },
    
    # Measurement file management (NEW)
    "measurements_filename": None,             # Dashboard sets, Measurement Agent polls
    
    # Existing data
    "measurements_df": pd.DataFrame(),
    "lock": threading.Lock(),
    "shutdown_event": threading.Event(),
}
```

### Measurement Filename Pattern (New)

Dynamic filename management for measurement files:

```python
# Dashboard (Start button)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = f"data/{timestamp}_data.csv"
with shared_data['lock']:
    shared_data['measurements_filename'] = filename

# Dashboard (Stop button)
with shared_data['lock']:
    shared_data['measurements_filename'] = None

# Measurement Agent (poll every 1 second)
def poll_filename():
    with shared_data['lock']:
        new_filename = shared_data.get('measurements_filename')
    
    if new_filename != current_filename:
        if new_filename is not None:
            handle_filename_change(new_filename)
        else:
            # Flush and stop writing
            flush_buffer_to_dataframe()
            write_measurements_to_csv(current_filename)
            current_filename = None
```

**Key Design Decisions:**
1. **Filename in shared_data**: Dashboard sets it, agent polls it (no notification needed)
2. **Poll every 1 second**: Independent from measurement rate
3. **None = stop writing**: Agent stops disk I/O when filename is None
4. **Automatic rotation**: New Start → new file, old data flushed automatically
5. **data/ folder**: All measurement files stored in subdirectory

**Filename Change Handler:**
```python
def handle_filename_change(new_filename):
    # 1. Flush buffer to DataFrame
    flush_buffer_to_dataframe()
    
    # 2. Write existing DataFrame to old file
    if current_filename is not None:
        write_measurements_to_csv(current_filename)
    
    # 3. Clear DataFrame for new file
    with shared_data['lock']:
        shared_data['measurements_df'] = pd.DataFrame()
    
    # 4. Update current filename
    current_filename = new_filename
```

**Three Independent Timers in Measurement Agent:**
```python
# 1. Filename poll (every 1 second)
if current_time - last_filename_poll_time >= 1.0:
    poll_filename()

# 2. Measurement (according to config)
if current_time - last_measurement_time >= config["MEASUREMENT_PERIOD_S"]:
    take_measurement()

# 3. CSV write (according to config)
if current_time - last_write_time >= config["MEASUREMENTS_WRITE_PERIOD_S"]:
    write_measurements_to_csv(current_filename)
```

| Timer | Period | Purpose |
|-------|--------|---------|
| Filename poll | 1 second | Detect filename changes quickly |
| Measurement | `MEASUREMENT_PERIOD_S` | Read from Modbus at configured rate |
| CSV write | `MEASUREMENTS_WRITE_PERIOD_S` | Persist data to disk periodically |

**Benefits:**
- Dashboard doesn't need to notify agent of filename changes
- Agent responds to filename changes within 1 second
- Measurement rate independent from file management
- Clean separation of concerns
- Multiple Start/Stop cycles create separate files automatically

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
    Writes ENABLE=1 to Plant
           ↓
    Plant agent reads ENABLE flag
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
    └── Write to measurements.csv (periodic)
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

New unified register map for Plant Agent:

| Address | Size | Register Name | Config Key |
|---------|------|---------------|------------|
| 0-1 | 2 words | P_SETPOINT_IN | PLANT_P_SETPOINT_REGISTER |
| 2-3 | 2 words | P_BATTERY_ACTUAL | PLANT_P_BATTERY_ACTUAL_REGISTER |
| 4-5 | 2 words | Q_SETPOINT_IN | PLANT_Q_SETPOINT_REGISTER |
| 6-7 | 2 words | Q_BATTERY_ACTUAL | PLANT_Q_BATTERY_ACTUAL_REGISTER |
| 10 | 1 word | ENABLE | PLANT_ENABLE_REGISTER |
| 12 | 1 word | SOC | PLANT_SOC_REGISTER |
| 14-15 | 2 words | P_POI | PLANT_P_POI_REGISTER |
| 16-17 | 2 words | Q_POI | PLANT_Q_POI_REGISTER |
| 18 | 1 word | V_POI | PLANT_V_POI_REGISTER |

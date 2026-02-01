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
    
    # Existing data
    "measurements_df": pd.DataFrame(),
    "lock": threading.Lock(),
    "shutdown_event": threading.Event(),
}
```

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
| Read DataFrame | No | DataFrames are read-only for consumers |
| Write DataFrame | Yes | Replace entire DataFrame reference |
| DataFrame operations | No | `asof()`, indexing, etc. |
| Modbus/network I/O | No | Never hold lock during I/O |

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

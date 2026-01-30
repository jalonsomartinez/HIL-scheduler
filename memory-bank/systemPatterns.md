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
| **Data Fetcher** | Separate | Schedule provider | schedule_source.csv → schedule_final_df |
| **Scheduler** | Separate | Setpoint dispatcher | schedule_final_df → Plant Modbus |
| **Plant** | Separate | Merged PPC + Battery simulation | Single Modbus server, internal battery sim |
| **Measurement** | Separate | Data logger | Reads Plant Modbus → measurements.csv |
| **Dashboard** | Separate | UI server | Renders UI, controls Plant enable |

## Data Flow Patterns

### Schedule Data Flow
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
All access to shared DataFrames uses the lock:

```python
# Writing
with shared_data['lock']:
    shared_data['schedule_final_df'] = new_df

# Reading
with shared_data['lock']:
    local_copy = shared_data['schedule_final_df'].copy()
# Work with local_copy outside lock
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

## Plant Model (Impedance Calculation)

The plant model calculates power and voltage at the Point of Interconnection (POI) based on impedance between battery and grid.

### Given Parameters
- R: Resistance (ohms)
- X: Reactance (ohms)
- V_nom: Nominal line-to-line voltage (V)
- PF: Power factor
- P_batt: Battery active power (kW)

### Calculation Steps

```python
# 1. Calculate apparent and reactive power at battery
S_batt_kva = abs(P_batt_kw) / PF
Q_batt_kvar = sign(P_batt) * sqrt(S_batt^2 - P_batt^2)

# 2. Calculate per-phase values
V_ph_kv = V_nom / (1000 * sqrt(3))
S_ph_kva = S_batt_kva / 3

# 3. Calculate current
I_ka = S_ph_kva / V_ph_kv
phi = arccos(PF)
if P_batt < 0:
    phi = -phi  # Charging
I_complex = I_ka * exp(-j * phi)

# 4. Voltage drop across impedance
Z_ohm = R + jX
V_drop_kv = I_complex * Z_ohm / 1000

# 5. POI voltage
V_poi_kv = V_ph_kv - V_drop_kv
V_poi_pu = abs(V_poi_kv) / V_ph_kv

# 6. Power at POI
S_poi_ph = V_poi_kv * conj(I_complex)
S_poi_kva = 3 * S_poi_ph
P_poi_kw = real(S_poi_kva)
Q_poi_kvar = imag(S_poi_kva)
```

## Configuration Patterns

### YAML Configuration (Simulated Plant)
```yaml
plant:
  capacity_kwh: 50.0
  initial_soc_pu: 0.5
  impedance:
    r_ohm: 0.01
    x_ohm: 0.1
  nominal_voltage_v: 400.0
  power_factor: 1.0
```

### Config Loader
Converts nested YAML to flat dictionary for backward compatibility:
```python
def load_config(path="config.yaml"):
    with open(path) as f:
        yaml_config = yaml.safe_load(f)
    
    config = {}
    config["PLANT_CAPACITY_KWH"] = yaml_config["plant"]["capacity_kwh"]
    config["PLANT_R_OHM"] = yaml_config["plant"]["impedance"]["r_ohm"]
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

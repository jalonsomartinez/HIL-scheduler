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
| **Scheduler** | Separate | Setpoint dispatcher | schedule_final_df → PPC Modbus |
| **PPC** | Separate (local) | Setpoint forwarder | PPC server → Battery server |
| **Battery** | Separate (local) | SoC simulator | Applies power, tracks SoC |
| **Measurement** | Separate | Data logger | Reads all Modbus → measurements.csv |
| **Dashboard** | Separate | UI server | Renders UI, controls PPC enable |

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
              PPC Modbus Server
```

### Control Flow
```
Dashboard (User clicks Start)
           ↓
    Writes ENABLE=1 to PPC
           ↓
    PPC reads ENABLE flag
           ↓
    IF enabled: forwards setpoint
    IF disabled: sends 0kW
           ↓
    Battery receives setpoint
           ↓
    Applies with SoC limiting
```

### Measurement Flow
```
Measurement Agent (periodic)
    ├── Read PPC: original_setpoint
    ├── Read Battery: actual_setpoint, SoC
    ├── Append to measurements_df
    └── Write to measurements.csv (periodic)
```

## Modbus Communication Patterns

### Client Pattern (Scheduler, PPC, Measurement)
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

### Server Pattern (PPC, Battery in local mode)
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

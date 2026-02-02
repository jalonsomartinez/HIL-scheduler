# Plan: Unify All Modbus Registers to 16-bit

## Overview
Change all Modbus register operations from 32-bit to 16-bit to match the remote plant (RTDS HIL) configuration. Currently the local emulated plant uses 32-bit registers (2 words per value), but the remote plant uses 16-bit registers (1 word per value).

## Current State Analysis

### Register Type Comparison

| Register | Local (Current) | Remote (Target) | Data Type |
|----------|-----------------|-----------------|-----------|
| p_setpoint_in | 32-bit (2 words) | 16-bit (1 word) | Signed hW |
| p_battery | 32-bit (2 words) | 16-bit (1 word) | Signed hW |
| q_setpoint_in | 32-bit (2 words) | 16-bit (1 word) | Signed hW |
| q_battery | 32-bit (2 words) | 16-bit (1 word) | Signed hW |
| p_poi | 32-bit (2 words) | 16-bit (1 word) | Signed hW |
| q_poi | 32-bit (2 words) | 16-bit (1 word) | Signed hW |
| v_poi | 16-bit (1 word) | 16-bit (1 word) | Unsigned |
| enable | 16-bit (1 word) | 16-bit (1 word) | Unsigned |
| soc | 16-bit (1 word) | 16-bit (1 word) | Unsigned |

### Files Requiring Changes

1. **[`config.yaml`](../config.yaml)** - Update comments in `modbus_local` section
2. **[`scheduler_agent.py`](../scheduler_agent.py)** - Change 32-bit writes to 16-bit
3. **[`measurement_agent.py`](../measurement_agent.py)** - Change 32-bit reads to 16-bit
4. **[`plant_agent.py`](../plant_agent.py)** - Change server-side 32-bit to 16-bit
5. **[`utils.py`](../utils.py)** - Add 16-bit signed encoding/decoding functions

## Technical Details

### 16-bit Signed Integer Handling

For **16-bit signed values**, we need:
- **Encoding**: Convert signed Python int to unsigned 16-bit register value
- **Decoding**: Convert unsigned 16-bit register value back to signed Python int

```python
# Encoding: signed int → unsigned 16-bit register
def int_to_uint16(value):
    """Convert a signed integer to an unsigned 16-bit value for Modbus register."""
    value = int(value)
    if value < 0:
        return value + 65536  # Two's complement
    return value & 0xFFFF

# Decoding: unsigned 16-bit register → signed int
def uint16_to_int(value):
    """Convert an unsigned 16-bit Modbus register value to a signed integer."""
    if value >= 32768:  # If highest bit is set, it's negative
        return value - 65536
    return value
```

### Value Range Considerations

With **16-bit signed registers** using hW (hectowatts) units:
- Range: -32768 hW to +32767 hW
- In kW: -3276.8 kW to +3276.7 kW

This is sufficient for the configured power limits:
- `p_max_kw: 1000.0` → 10000 hW ✓
- `p_min_kw: -1000.0` → -10000 hW ✓
- `q_max_kvar: 600.0` → 6000 hW ✓
- `q_min_kvar: -600.0` → -6000 hW ✓

## Implementation Steps

### Step 1: Update [`utils.py`](../utils.py)
Add 16-bit signed encoding/decoding helper functions:

```python
def int_to_uint16(value):
    """Convert a signed integer to an unsigned 16-bit value for Modbus register."""
    value = int(value)
    if value < 0:
        return value + 65536  # Two's complement
    return value & 0xFFFF

def uint16_to_int(value):
    """Convert an unsigned 16-bit Modbus register value to a signed integer."""
    if value >= 32768:  # If highest bit is set, it's negative
        return value - 65536
    return value
```

### Step 2: Update [`scheduler_agent.py`](../scheduler_agent.py)

**Current code (32-bit writes):**
```python
from pyModbusTCP.utils import long_list_to_word
# ...
p_reg_val = long_list_to_word([kw_to_hw(current_p_setpoint)], big_endian=False)
client.write_multiple_registers(plant_config['p_setpoint_reg'], p_reg_val)
```

**New code (16-bit writes):**
```python
from utils import kw_to_hw, int_to_uint16
# ...
p_reg_val = int_to_uint16(kw_to_hw(current_p_setpoint))
client.write_single_register(plant_config['p_setpoint_reg'], p_reg_val)
```

### Step 3: Update [`measurement_agent.py`](../measurement_agent.py)

**Current code (32-bit reads):**
```python
from pyModbusTCP.utils import get_2comp, word_list_to_long
# ...
regs_p_setpoint = plant_client.read_holding_registers(plant_config['p_setpoint_reg'], 2)
p_setpoint_kw = hw_to_kw(get_2comp(word_list_to_long(regs_p_setpoint, big_endian=False)[0], 32))
```

**New code (16-bit reads):**
```python
from utils import hw_to_kw, uint16_to_int
# ...
regs_p_setpoint = plant_client.read_holding_registers(plant_config['p_setpoint_reg'], 1)
p_setpoint_kw = hw_to_kw(uint16_to_int(regs_p_setpoint[0]))
```

### Step 4: Update [`plant_agent.py`](../plant_agent.py)

**Current code (32-bit server operations):**
```python
from pyModbusTCP.utils import get_2comp, word_list_to_long, long_list_to_word
# ...
# Reading setpoint
original_p_kw = hw_to_kw(get_2comp(word_list_to_long(regs_p_setpoint, big_endian=False)[0], 32))

# Writing actual power
p_actual_reg = long_list_to_word([kw_to_hw(actual_p_kw)], big_endian=False)
plant_server.data_bank.set_holding_registers(config["PLANT_P_BATTERY_ACTUAL_REGISTER"], p_actual_reg)
```

**New code (16-bit server operations):**
```python
from utils import kw_to_hw, hw_to_kw, int_to_uint16, uint16_to_int
# ...
# Reading setpoint (single register)
regs_p_setpoint = plant_server.data_bank.get_holding_registers(config["PLANT_P_SETPOINT_REGISTER"], 1)
original_p_kw = hw_to_kw(uint16_to_int(regs_p_setpoint[0]))

# Writing actual power (single register)
p_actual_reg = int_to_uint16(kw_to_hw(actual_p_kw))
plant_server.data_bank.set_holding_registers(config["PLANT_P_BATTERY_ACTUAL_REGISTER"], [p_actual_reg])
```

### Step 5: Update [`config.yaml`](../config.yaml)

Update comments in the `modbus_local` section to indicate 16-bit registers:

```yaml
# Modbus Configuration - Local Plant (Emulated)
modbus_local:
  host: "localhost"
  port: 5020
  description: "Local emulated plant running in plant_agent"

  # Register Map (all 16-bit)
  registers:
    # Active Power
    p_setpoint_in: 0        # 16-bit signed: Active power setpoint from scheduler (hW)
    p_poi: 14               # 16-bit signed: Active power at POI (hW)
    p_battery: 2            # 16-bit signed: Actual battery active power after SoC limiting (hW)
    # Reactive Power
    q_setpoint_in: 4        # 16-bit signed: Reactive power setpoint from scheduler (hW)
    q_poi: 16               # 16-bit signed: Reactive power at POI (hW)
    q_battery: 6            # 16-bit signed: Actual battery reactive power (hW)
    # Voltage
    v_poi: 18               # 16-bit unsigned: Voltage at POI (per-unit x100)
    # Control and Status
    enable: 10              # 16-bit: Enable flag (0=disabled, 1=enabled)
    soc: 12                 # 16-bit unsigned: State of Charge (per-unit x10000)
```

## Register Address Mapping (After Change)

| Register | Local Address | Remote Address | Size |
|----------|---------------|----------------|------|
| p_setpoint_in | 0 | 86 | 1 word |
| p_battery | 2 | 270 | 1 word |
| q_setpoint_in | 4 | 88 | 1 word |
| q_battery | 6 | 272 | 1 word |
| enable | 10 | 1 | 1 word |
| soc | 12 | 281 | 1 word |
| p_poi | 14 | 290 | 1 word |
| q_poi | 16 | 292 | 1 word |
| v_poi | 18 | 296 | 1 word |

Note: Local addresses can remain the same since they're just indices. The key change is that each register now occupies 1 word instead of 2.

## Files Changed Summary

| File | Changes |
|------|---------|
| [`utils.py`](../utils.py) | Add `int_to_uint16()` and `uint16_to_int()` functions |
| [`scheduler_agent.py`](../scheduler_agent.py) | Replace `write_multiple_registers` + `long_list_to_word` with `write_single_register` + `int_to_uint16` |
| [`measurement_agent.py`](../measurement_agent.py) | Read 1 register instead of 2, use `uint16_to_int` instead of `word_list_to_long` + `get_2comp` |
| [`plant_agent.py`](../plant_agent.py) | Read/write 1 register instead of 2, use new util functions |
| [`config.yaml`](../config.yaml) | Update comments to indicate 16-bit registers |

## Memory Bank Updates Required

After implementation, update these memory bank files:
- `systemPatterns.md`: Update register map table
- `techContext.md`: Update Modbus register map section

## Testing Checklist

- [ ] Start system with local plant - verify scheduler can write setpoints
- [ ] Verify measurement agent can read all registers
- [ ] Verify plant agent processes setpoints correctly
- [ ] Test negative power values (charging)
- [ ] Test positive power values (discharging)
- [ ] Verify SoC, V_poi still work (already 16-bit)
- [ ] Test with remote plant to ensure compatibility

## Rollback Plan

If issues are encountered:
1. Revert all file changes
2. The pyModbusTCP library functions (`long_list_to_word`, `word_list_to_long`, `get_2comp`) will restore 32-bit behavior

# Thread Locking Analysis: HIL Scheduler

## Executive Summary

This document analyzes thread locking patterns across the HIL Scheduler codebase to identify opportunities for reducing lock contention and improving UI responsiveness. The recent fix to [`scheduler_agent.py`](scheduler_agent.py) resolved critical UI freezing issues by minimizing lock hold time.

---

## Current Locking Patterns by Agent

### 1. Scheduler Agent ([`scheduler_agent.py`](scheduler_agent.py:45)) ✅ OPTIMIZED

**Pattern:** Minimal lock time - only dictionary reference operations

```python
# Lines 45-52: Lock only for reference retrieval
with shared_data['lock']:
    active_source = shared_data.get('active_schedule_source', 'manual')
    if active_source == 'api':
        schedule_df = shared_data.get('api_schedule_df')
    else:
        schedule_df = shared_data.get('manual_schedule_df')

# Lines 70-120: All operations happen OUTSIDE the lock
# - asof() lookup (DataFrame read - thread-safe)
# - Modbus write operations (I/O - never hold lock during I/O!)
```

**Lock Duration:** Microseconds (just dict key access)
**Performance Impact:** ✅ Excellent - UI is responsive

**What Was Fixed:**
- **Before:** Lock held during `asof()` lookup and Modbus writes
- **After:** Lock only held to get schedule reference
- **Result:** Dashboard callbacks no longer blocked

---

### 2. Dashboard Agent ([`dashboard_agent.py`](dashboard_agent.py)) ✅ OPTIMIZED

**Pattern:** Local state cache with background sync

```python
# Lines 33-40: Local state to avoid locking in callbacks
local_state = {
    'manual_schedule': pd.DataFrame(),
    'api_schedule': pd.DataFrame(),
    'api_status': {},
    'active_source': 'manual',
    'measurements': pd.DataFrame(),
}

# Lines 43-57: Background thread syncs from shared data
def sync_from_shared_data():
    while not shared_data['shutdown_event'].is_set():
        with shared_data['lock']:
            local_state['manual_schedule'] = shared_data.get('manual_schedule_df', pd.DataFrame()).copy()
            local_state['api_schedule'] = shared_data.get('api_schedule_df', pd.DataFrame()).copy()
            # ... more copies
        time.sleep(1)  # Sync every second

sync_thread = threading.Thread(target=sync_from_shared_data, daemon=True)
sync_thread.start()
```

**Callbacks Use Local State (No Locks):**
- [`update_schedule_preview()`](dashboard_agent.py:484) - Line 486: Uses `local_state['manual_schedule']`
- [`update_api_status()`](dashboard_agent.py:617) - Line 619: Uses `local_state['api_status']`
- [`update_api_schedule_preview()`](dashboard_agent.py:651) - Line 653: Uses `local_state['api_schedule']`
- [`update_status_and_graphs()`](dashboard_agent.py:787) - Lines 834, 852, 858: Uses local state

**Lock Usage (Brief Operations Only):**
- Line 535: Accept schedule - `shared_data['manual_schedule_df'] = preview_df`
- Line 553: Clear schedule - `shared_data['manual_schedule_df'] = pd.DataFrame()`
- Line 580: Set API password - `shared_data['api_password'] = password`
- Line 601: Disconnect API - `shared_data['api_password'] = None`
- Line 713: Select source - `shared_data['active_schedule_source'] = 'api'`

**Lock Duration:** Microseconds for writes
**Performance Impact:** ✅ Excellent - No blocking in UI callbacks

---

### 3. Data Fetcher Agent ([`data_fetcher_agent.py`](data_fetcher_agent.py))

**Pattern:** Brief lock periods for atomic operations

```python
# Lines 35-36: Check password (brief)
with shared_data['lock']:
    password = shared_data.get('api_password')

# Lines 62-64: Check status (brief)
with shared_data['lock']:
    status = shared_data.get('data_fetcher_status', {})
    today_fetched = status.get('today_fetched', False)

# Lines 77-78: Update schedule after fetch
with shared_data['lock']:
    shared_data['api_schedule_df'] = df

# Lines 123-133: Append tomorrow's schedule
with shared_data['lock']:
    existing_df = shared_data['api_schedule_df']
    if not existing_df.empty:
        non_overlapping = existing_df.index.difference(df.index)
        existing_df = existing_df.loc[non_overlapping]
        combined_df = pd.concat([existing_df, df]).sort_index()
    else:
        combined_df = df
    shared_data['api_schedule_df'] = combined_df
```

**Lock Duration:** Microseconds to milliseconds
**Performance Impact:** ✅ Good - Brief atomic operations only

**Note:** Lines 123-133 hold lock during DataFrame `index.difference()` and `concat()` operations. These could take milliseconds for large schedules but are infrequent (every 5 minutes after initial fetch).

---

### 4. Measurement Agent ([`measurement_agent.py`](measurement_agent.py))

**Pattern:** Lock during DataFrame append and CSV write

```python
# Lines 174-181: Append new measurement (inside lock)
with shared_data['lock']:
    if shared_data['measurements_df'].empty:
        shared_data['measurements_df'] = new_row
    else:
        shared_data['measurements_df'] = pd.concat(
            [shared_data['measurements_df'], new_row],
            ignore_index=True
        )

# Lines 25-33: Write to CSV (inside lock!)
def write_measurements_to_csv():
    with shared_data['lock']:
        measurements_df = shared_data['measurements_df']
        if not measurements_df.empty:
            measurements_df.to_csv(config['MEASUREMENTS_CSV'], index=False)
```

**Analysis:**
- **Lines 174-181:** `pd.concat()` inside lock. For large DataFrames, this could take milliseconds.
- **Lines 25-33:** **`to_csv()` inside lock!** This is disk I/O while holding the lock.

**Lock Duration:** 
- Append: Milliseconds (depends on DataFrame size)
- CSV Write: **Tens to hundreds of milliseconds** (disk I/O)

**Performance Impact:** ⚠️ **POTENTIAL ISSUE** - CSV write holds lock during disk I/O

---

### 5. Plant Agent ([`plant_agent.py`](plant_agent.py))

**Pattern:** NO SHARED DATA LOCK USAGE

The plant agent is completely decoupled from shared data - it only interacts via Modbus server. This is excellent design for minimizing lock contention.

**Performance Impact:** ✅ Excellent - No lock contention possible

---

## Lock Contention Risk Assessment

| Agent | Risk Level | Primary Concern |
|-------|------------|-----------------|
| Scheduler | 🟢 Low | Already optimized |
| Dashboard | 🟢 Low | Uses local state cache |
| Data Fetcher | 🟢 Low | Brief atomic operations |
| Measurement | 🟡 Medium | CSV write during lock |
| Plant | 🟢 None | No shared data access |

---

## Identified Optimization Opportunities

### 1. Measurement Agent CSV Write (HIGH PRIORITY)

**Location:** [`measurement_agent.py`](measurement_agent.py:25-33)

**Current Code:**
```python
def write_measurements_to_csv():
    with shared_data['lock']:
        measurements_df = shared_data['measurements_df']
        if not measurements_df.empty:
            measurements_df.to_csv(config['MEASUREMENTS_CSV'], index=False)
```

**Problem:** Disk I/O during lock hold can block dashboard for tens to hundreds of milliseconds.

**Recommended Fix:**
```python
def write_measurements_to_csv():
    # Get reference briefly, then write outside lock
    with shared_data['lock']:
        measurements_df = shared_data['measurements_df'].copy()
    
    # Write outside lock - we have our own copy
    if not measurements_df.empty:
        measurements_df.to_csv(config['MEASUREMENTS_CSV'], index=False)
```

**Impact:** Eliminates disk I/O from critical section.

---

### 2. Measurement Agent DataFrame Append (MEDIUM PRIORITY)

**Location:** [`measurement_agent.py`](measurement_agent.py:174-181)

**Current Code:**
```python
with shared_data['lock']:
    if shared_data['measurements_df'].empty:
        shared_data['measurements_df'] = new_row
    else:
        shared_data['measurements_df'] = pd.concat(
            [shared_data['measurements_df'], new_row],
            ignore_index=True
        )
```

**Problem:** `pd.concat()` creates a new DataFrame each time. As `measurements_df` grows, this operation becomes slower.

**Recommended Fix - Pre-allocate with List Buffer:**
```python
# In agent initialization
measurement_buffer = []
last_buffer_flush = time.time()

# In main loop
measurement_buffer.append({
    "timestamp": datetime.now(),
    "p_setpoint_kw": p_setpoint_kw,
    # ... other fields
})

# Periodic flush to shared DataFrame (every 10 seconds or buffer size limit)
if time.time() - last_buffer_flush > 10:
    with shared_data['lock']:
        if shared_data['measurements_df'].empty:
            shared_data['measurements_df'] = pd.DataFrame(measurement_buffer)
        else:
            buffer_df = pd.DataFrame(measurement_buffer)
            shared_data['measurements_df'] = pd.concat(
                [shared_data['measurements_df'], buffer_df],
                ignore_index=True
            )
    measurement_buffer = []
    last_buffer_flush = time.time()
```

**Alternative - Use List in Shared Data:**
```python
# In shared_data initialization
"measurements_list": [],  # List of dicts instead of DataFrame

# In measurement agent
with shared_data['lock']:
    shared_data['measurements_list'].append(measurement_dict)

# In dashboard sync thread
with shared_data['lock']:
    measurements_list = shared_data['measurements_list'].copy()
    shared_data['measurements_list'] = []  # Clear after copy
# Then convert to DataFrame outside lock
```

---

### 3. Data Fetcher Schedule Append (LOW PRIORITY)

**Location:** [`data_fetcher_agent.py`](data_fetcher_agent.py:123-133)

**Current Code:**
```python
with shared_data['lock']:
    existing_df = shared_data['api_schedule_df']
    if not existing_df.empty:
        non_overlapping = existing_df.index.difference(df.index)
        existing_df = existing_df.loc[non_overlapping]
        combined_df = pd.concat([existing_df, df]).sort_index()
    else:
        combined_df = df
    shared_data['api_schedule_df'] = combined_df
```

**Problem:** DataFrame operations inside lock. However, this only happens:
- Once when fetching today's schedule
- Once when fetching tomorrow's schedule

**Assessment:** Low risk due to infrequency (every 5+ minutes), but could be optimized.

**Recommended Fix:**
```python
# Prepare combined DataFrame outside lock
with shared_data['lock']:
    existing_df = shared_data['api_schedule_df']

# DataFrame operations outside lock
if not existing_df.empty:
    non_overlapping = existing_df.index.difference(df.index)
    existing_df = existing_df.loc[non_overlapping]
    combined_df = pd.concat([existing_df, df]).sort_index()
else:
    combined_df = df

# Brief lock for assignment only
with shared_data['lock']:
    shared_data['api_schedule_df'] = combined_df
```

---

### 4. Dashboard Data Copying (LOW PRIORITY)

**Location:** [`dashboard_agent.py`](dashboard_agent.py:46-51)

**Current Code:**
```python
def sync_from_shared_data():
    while not shared_data['shutdown_event'].is_set():
        with shared_data['lock']:
            local_state['manual_schedule'] = shared_data.get('manual_schedule_df', pd.DataFrame()).copy()
            local_state['api_schedule'] = shared_data.get('api_schedule_df', pd.DataFrame()).copy()
            local_state['api_status'] = shared_data.get('data_fetcher_status', {}).copy()
            local_state['active_source'] = shared_data.get('active_schedule_source', 'manual')
            local_state['measurements'] = shared_data.get('measurements_df', pd.DataFrame()).copy()
        time.sleep(1)
```

**Problem:** Holding lock while copying multiple DataFrames. Copying large DataFrames takes time.

**Recommended Fix - Staged Release:**
```python
def sync_from_shared_data():
    while not shared_data['shutdown_event'].is_set():
        # Copy each with brief lock, allowing interleaving
        with shared_data['lock']:
            manual_df = shared_data.get('manual_schedule_df', pd.DataFrame())
        local_state['manual_schedule'] = manual_df.copy()
        
        with shared_data['lock']:
            api_df = shared_data.get('api_schedule_df', pd.DataFrame())
        local_state['api_schedule'] = api_df.copy()
        
        with shared_data['lock']:
            local_state['api_status'] = shared_data.get('data_fetcher_status', {}).copy()
            local_state['active_source'] = shared_data.get('active_schedule_source', 'manual')
        
        with shared_data['lock']:
            measurements_df = shared_data.get('measurements_df', pd.DataFrame())
        local_state['measurements'] = measurements_df.copy()
        
        time.sleep(1)
```

**Note:** This is lower priority since the sync thread runs in the background and doesn't directly block UI callbacks.

---

## Summary of Recommendations

### Immediate Action (High Priority)
1. **Fix [`measurement_agent.py`](measurement_agent.py:25-33) CSV write** - Move `to_csv()` outside the lock

### Medium Priority
2. **Optimize [`measurement_agent.py`](measurement_agent.py:174-181) DataFrame append** - Use buffer pattern to reduce lock frequency

### Low Priority (Nice to Have)
3. **Optimize [`data_fetcher_agent.py`](data_fetcher_agent.py:123-133) schedule append** - Move DataFrame operations outside lock
4. **Optimize [`dashboard_agent.py`](dashboard_agent.py:46-51) sync thread** - Staged lock release for each DataFrame

---

## Verification Strategy

After implementing changes, verify with:

1. **Load Testing:** Run system with large schedules (10,000+ points) and high measurement frequency
2. **UI Responsiveness Test:** Rapidly click between dashboard tabs, verify no "Updating" delays
3. **Lock Duration Logging:** Add timing around lock contexts to measure actual hold times:
   ```python
   import time
   start = time.time()
   with shared_data['lock']:
       duration = time.time() - start
       if duration > 0.01:  # Log if > 10ms
           logging.warning(f"Long lock hold: {duration*1000:.2f}ms")
   ```

---

## Thread Safety Reminders

| Do ✅ | Don't ❌ |
|-------|----------|
| Hold lock only for dict reference operations | Hold lock during I/O (Modbus, disk, network) |
| Do DataFrame operations outside lock | Hold lock during DataFrame `asof()`, `concat()` |
| Copy data outside lock if needed | Assume DataFrame operations are atomic |
| Use local caching for UI (like dashboard) | Block UI thread waiting for shared data |
| Keep lock duration < 1ms when possible | Hold lock across multiple operations |

---

## Architecture Decision: Why This Pattern Works

The key insight from the [`scheduler_agent.py`](scheduler_agent.py) fix:

> **Python's GIL (Global Interpreter Lock) already protects dictionary operations. The explicit lock is for logical synchronization, not memory safety.**

This means:
1. Getting a reference to a DataFrame (`shared_data['key']`) is fast and atomic
2. Reading from that DataFrame (`df.asof()`, `df.loc[]`) is thread-safe if no other thread modifies it
3. The lock only needs to protect the **dictionary reference swap**, not the data access

This is why the dashboard's local-state pattern works so well - it copies the reference briefly, then does all work on its own copy.

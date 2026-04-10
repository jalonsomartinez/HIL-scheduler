# Grid Map Geometry And Voltage-Drop Findings

Date: 2026-04-10

## Purpose

This note summarizes the investigation performed on the pandapower model used by the dashboard grid-map simulation, focusing on:

- the apparent voltage-drop inconsistency around line `831`
- the large voltage drop between buses `841 -> 553`
- whether bus-coordinate geometry and electrical line lengths are mutually consistent across the model

The checks below were performed without patching the model.

## Important Clarification About The Buses

The dashboard comparison that triggered the investigation used bus `89` and bus `843`.

That comparison is not physically local to line `831`.

- Line `831` = `L01 DEL CT ALBUÑO0340 CT-1`
  - from bus `553` = `L01 DEL CT ALBUÑO0340-CT`
  - to bus `89` = `L01 DEL CT ALBUÑO0340-1`
- Bus `843` = `BT_Transformador  630kVA CT_INSTITUTO_FERIA_CT`
  - this is not connected to line `831`
- The upstream bus that is electrically adjacent to bus `553` is bus `841` = `BT_Transformador  630kVA CT_INSTITUTO_CT`
- Line `843` = `CT Albuño340 - L01`
  - from bus `841`
  - to bus `553`

So the meaningful voltage-drop pairs are:

- `841 -> 553` across line `843`
- `553 -> 89` across line `831`

## Current-Time Simulation Snapshot

Requested simulation time:

- `2026-04-10T18:13:32.175577+02:00`

Selected power-flow timestamp:

- `2026-04-10T18:00:00+02:00`
- `used_previous_hour_fallback = true`

Battery input used for direct check:

- `battery_p_mw = 0.0`
- `battery_q_mvar = 0.0`

Selected results:

| Element | Value |
|---|---:|
| Bus `841` voltage | `0.995636 pu` |
| Bus `553` voltage | `0.930762 pu` |
| Bus `89` voltage | `0.922522 pu` |
| Line `843` loading | `185.752 %` |
| Line `843` sending-end active power | `0.089660 MW` |
| Line `843` active losses | `0.005833 MW` |
| Line `831` loading | `54.173 %` |
| Line `831` sending-end active power | `0.083819 MW` |
| Line `831` active losses | `0.000733 MW` |

Implication:

- The large drop is upstream, on line `843`
- The additional drop on line `831` is comparatively small

## Line 831 Findings

Line `831`:

- Name: `L01 DEL CT ALBUÑO0340 CT-1`
- From bus `553`: `L01 DEL CT ALBUÑO0340-CT`
- To bus `89`: `L01 DEL CT ALBUÑO0340-1`
- Model length: `116.5 m`
- Terminal straight-line distance from bus coordinates: `105.803 m`
- `r_ohm_per_km = 0.124`
- `x_ohm_per_km = 0.085`
- `max_i_ka = 0.24`

Assessment:

- This line looks geometrically plausible
- Model length and coordinate distance are close
- At about `54–57%` loading, the line-only drop is about `0.8–0.9%`, which is physically reasonable

## Line 843 Findings

Line `843`:

- Name: `CT Albuño340 - L01`
- From bus `841`: `BT_Transformador  630kVA CT_INSTITUTO_CT`
- To bus `553`: `L01 DEL CT ALBUÑO0340-CT`
- Model length: `100.0 m`
- Terminal straight-line distance from bus coordinates: `0.0 m`
- `r_ohm_per_km = 1.15`
- `x_ohm_per_km = 0.105`
- `r0_ohm_per_km = 3.45`
- `x0_ohm_per_km = 0.315`
- `c_nf_per_km = 0.0`
- `max_i_ka = 0.07`
- Total series resistance: `0.115 ohm`
- Total series reactance: `0.0105 ohm`
- Approx thermal rating at `0.4 kV`: `0.0485 MVA` (`48.5 kVA`)

Current simulation result at `2026-04-10T18:00:00+02:00`:

- Bus `841` voltage: `0.995636 pu`
- Bus `553` voltage: `0.930762 pu`
- Drop across line `843`: `0.064874 pu` = `6.487 %`
- Current: `0.130026 kA`
- Loading: `185.752 %`
- Sending-end active power: `89.660 kW`
- Active losses: `5.833 kW`

Assessment:

- The big voltage drop between buses `841 -> 553` is consistent with the electrical parameters of line `843`
- The line is badly overloaded relative to its `70 A` ampacity
- The visual geometry is inconsistent with the electrical model because both terminal buses are stored at the same XY coordinates

## Why The Drop On Line 843 Is So Large

The downstream subtree fed through line `843` is large:

- downstream bus count from bus `553`: `649`
- downstream line count including line `843`: `654`
- downstream asymmetric-load count: `184`
- downstream asymmetric-load total active power at the checked timestamp: `0.07792 MW`

That is approximately `77.92 kW` of downstream asymmetric load behind a line whose thermal rating is only about `48.5 kVA`.

So the large voltage drop on line `843` is being driven by two things at once:

1. weak line parameters
2. too much downstream load for those parameters

## Model-Wide Geometry Scan

Assumption used for this scan:

- bus `geo` coordinates are planar coordinates in meters
- straight-line terminal distance was computed directly from the stored XY values

Model-wide results:

| Metric | Count |
|---|---:|
| Total lines scanned | `852` |
| Lines with usable bus coordinates | `852` |
| Zero-distance but nonzero-length lines | `14` |
| Lines with model/straight ratio `> 2` | `64` |
| Lines with model/straight ratio `> 5` | `12` |
| Lines with absolute model-vs-straight difference `> 50 m` | `17` |

### Zero-Distance / Nonzero-Length Lines

These lines have identical terminal coordinates but a positive modeled length:

| Index | Line Name | From Terminal | To Terminal | Model Length (m) | Straight Distance (m) |
|---|---|---|---|---:|---:|
| `33` | `L04 DEL CT ALBUÑO0340 22-52` | `L04 DEL CT ALBUÑO0340-22` | `L04 DEL CT ALBUÑO0340-52` | `4.0` | `0.0` |
| `57` | `L06 DEL CT ALBUÑO0340 2-3` | `L06 DEL CT ALBUÑO0340-3` | `L06 DEL CT ALBUÑO0340-2` | `33.2` | `0.0` |
| `228` | `L01 DEL CT ALBUÑO0340 189-190` | `L01 DEL CT ALBUÑO0340-189` | `L01 DEL CT ALBUÑO0340-190` | `2.0` | `0.0` |
| `237` | `L01 DEL CT ALBUÑO0340 194-195` | `L01 DEL CT ALBUÑO0340-194` | `L01 DEL CT ALBUÑO0340-195` | `2.0` | `0.0` |
| `239` | `L01 DEL CT ALBUÑO0340 196-197` | `L01 DEL CT ALBUÑO0340-196` | `L01 DEL CT ALBUÑO0340-197` | `3.2` | `0.0` |
| `813` | `L04 DEL CT ALBUÑO0340 18-47` | `L04 DEL CT ALBUÑO0340-18` | `L04 DEL CT ALBUÑO0340-47` | `11.8` | `0.0` |
| `818` | `L04 DEL CT ALBUÑO0340 50-51` | `L04 DEL CT ALBUÑO0340-50` | `L04 DEL CT ALBUÑO0340-51` | `6.0` | `0.0` |
| `833` | `L07 DEL CT ALBUÑO0340 CT-3` | `L07 DEL CT ALBUÑO0340-CT` | `L07 DEL CT ALBUÑO0340-3` | `8.8` | `0.0` |
| `843` | `CT Albuño340 - L01` | `BT_Transformador  630kVA CT_INSTITUTO_CT` | `L01 DEL CT ALBUÑO0340-CT` | `100.0` | `0.0` |
| `844` | `CT Albuño340 - L02` | `BT_Transformador  630kVA CT_INSTITUTO_CT` | `L02 DEL CT ALBUÑO0340-CT` | `100.0` | `0.0` |
| `845` | `CT Albuño340 - L03` | `BT_Transformador  630kVA CT_INSTITUTO_CT` | `L03 DEL CT ALBUÑO0340-CT` | `100.0` | `0.0` |
| `846` | `CT Albuño340 - L04` | `BT_Transformador  630kVA CT_INSTITUTO_CT` | `L04 DEL CT ALBUÑO0340-CT` | `100.0` | `0.0` |
| `847` | `CT Albuño340 - L05` | `BT_Transformador  630kVA CT_INSTITUTO_CT` | `L05 DEL CT ALBUÑO0340-CT` | `100.0` | `0.0` |
| `850` | `DT_20KV_TIE_840_842` | `AT_Transformador  630kVA CT_INSTITUTO_CT` | `AT_Transformador  630kVA CT_INSTITUTO_FERIA_CT` | `15.0` | `0.0` |

Key observation:

- The cluster `843–847` is especially important because these are transformer-to-feeder lines with `100 m` electrical length but `0 m` geometric separation

### Very Large Model / Straight-Distance Ratios

The strongest positive outliers are:

| Index | Line Name | From Terminal | To Terminal | Model Length (m) | Straight Distance (m) | Ratio |
|---|---|---|---|---:|---:|---:|
| `59` | `L07 DEL CT ALBUÑO0340 4-5` | `L08 DEL CT ALBUÑO0340-4` | `L07 DEL CT ALBUÑO0340-5` | `4974.0` | `4.972` | `1000.37` |
| `829` | `L01 DEL CT ALBUÑO0340 550-AC2` | `L01 DEL CT ALBUÑO0340-550` | `L01 DEL CT ALBUÑO0340-AC2-262` | `100.0` | `1.236` | `80.93` |
| `849` | `DT_20KV_SOURCE_TO_HUB` | `DT_SOURCE_20KV` | `AT_Transformador  630kVA CT_INSTITUTO_CT` | `15650.0` | `300.0` | `52.17` |
| `827` | `L01 DEL CT ALBUÑO0340 383-AC1` | `L01 DEL CT ALBUÑO0340-383` | `L01 DEL CT ALBUÑO0340-AC1-260` | `100.0` | `2.013` | `49.68` |
| `840` | `L01 DEL CT ALBUÑO0340 388-AC` | `L01 DEL CT ALBUÑO0340-388` | `L01 DEL CT ALBUÑO0340-AC` | `100.0` | `2.937` | `34.05` |
| `842` | `CT Albuño340 - L08` | `BT_Transformador  630kVA CT_INSTITUTO_FERIA_CT` | `L08 DEL CT ALBUÑO0340-CT` | `100.0` | `3.338` | `29.96` |
| `841` | `CT Albuño340 - L07` | `BT_Transformador  630kVA CT_INSTITUTO_FERIA_CT` | `L07 DEL CT ALBUÑO0340-CT` | `100.0` | `7.127` | `14.03` |
| `826` | `L01 DEL CT ALBUÑO0340 376-AC1` | `L01 DEL CT ALBUÑO0340-376` | `L01 DEL CT ALBUÑO0340-AC1-259` | `100.0` | `8.035` | `12.44` |
| `825` | `L01 DEL CT ALBUÑO0340 263-AC1` | `L01 DEL CT ALBUÑO0340-263` | `L01 DEL CT ALBUÑO0340-AC1-258` | `100.0` | `8.745` | `11.43` |

### Large Negative Differences

There are also lines where the model length is much shorter than the coordinate distance:

| Index | Line Name | From Terminal | To Terminal | Model Length (m) | Straight Distance (m) | Difference (m) |
|---|---|---|---|---:|---:|---:|
| `55` | `L06 DEL CT ALBUÑO0340 3-1` | `L06 DEL CT ALBUÑO0340-3` | `L06 DEL CT ALBUÑO0340-1` | `4.1` | `108.333` | `-104.233` |
| `566` | `L01 DEL CT ALBUÑO0340 308-426` | `L01 DEL CT ALBUÑO0340-308` | `L01 DEL CT ALBUÑO0340-426` | `11.6` | `156.729` | `-145.129` |
| `834` | `L06 DEL CT ALBUÑO0340 CT-3` | `L06 DEL CT ALBUÑO0340-CT` | `L06 DEL CT ALBUÑO0340-3` | `8.8` | `108.333` | `-99.533` |

## Main Conclusions

1. The line-length / geometry inconsistency is not isolated to line `843`.
2. There are multiple classes of anomalies:
   - identical terminal coordinates with positive line length
   - model lengths much larger than coordinate distances
   - model lengths much smaller than coordinate distances
3. The transformer-to-feeder lines `843–847` are especially suspicious because:
   - they are modeled as `100 m` LV service lines
   - several have `0 m` geometric terminal separation
   - line `843` is currently driving a large voltage drop due to overload
4. The visual grid-map geometry should not currently be treated as a reliable proxy for electrical length everywhere in the model.

## Recommended Technical Review Focus

Suggested priority order for the technical team:

1. Review lines `843–847` and confirm whether the feeder-head buses should share the same coordinates as the transformer LV bus.
2. Review the intended physical meaning of `100 m` for lines `843–848`.
3. Review line `849` = `DT_20KV_SOURCE_TO_HUB`, because `15.65 km` modeled length versus `300 m` terminal distance is a very large discrepancy.
4. Review line `59` = `L07 DEL CT ALBUÑO0340 4-5`, because `4974 m` modeled length versus `4.972 m` terminal distance is an extreme outlier.
5. Review lines `55`, `566`, and `834`, where the model length is dramatically shorter than the coordinate-derived terminal distance.

## Other Current-Time Overloaded Lines

At the same selected timestamp `2026-04-10T18:00:00+02:00`, the model contains `9` overloaded lines (`loading > 100%`).

### Overloaded Lines Ranked By Loading

| Index | Line Name | From Terminal | To Terminal | Loading (%) | Drop (%) | Model Length (m) | Straight Distance (m) | `max_i_ka` |
|---|---|---|---|---:|---:|---:|---:|---:|
| `843` | `CT Albuño340 - L01` | `BT_Transformador  630kVA CT_INSTITUTO_CT` | `L01 DEL CT ALBUÑO0340-CT` | `185.752` | `6.487` | `100.0` | `0.0` | `0.07` |
| `125` | `L01 DEL CT ALBUÑO0340 5-6` | `L01 DEL CT ALBUÑO0340-5` | `L01 DEL CT ALBUÑO0340-6` | `162.521` | `0.796` | `19.4` | `19.399` | `0.08` |
| `126` | `L01 DEL CT ALBUÑO0340 6-7` | `L01 DEL CT ALBUÑO0340-6` | `L01 DEL CT ALBUÑO0340-7` | `162.521` | `0.168` | `4.1` | `4.121` | `0.08` |
| `127` | `L01 DEL CT ALBUÑO0340 7-8` | `L01 DEL CT ALBUÑO0340-7` | `L01 DEL CT ALBUÑO0340-8` | `162.521` | `0.467` | `11.4` | `11.400` | `0.08` |
| `128` | `L01 DEL CT ALBUÑO0340 8-9` | `L01 DEL CT ALBUÑO0340-8` | `L01 DEL CT ALBUÑO0340-9` | `162.521` | `0.521` | `12.7` | `12.656` | `0.08` |
| `129` | `L01 DEL CT ALBUÑO0340 9-10` | `L01 DEL CT ALBUÑO0340-9` | `L01 DEL CT ALBUÑO0340-10` | `162.520` | `0.496` | `12.1` | `12.079` | `0.08` |
| `131` | `L01 DEL CT ALBUÑO0340 11-12` | `L01 DEL CT ALBUÑO0340-11` | `L01 DEL CT ALBUÑO0340-12` | `162.518` | `0.811` | `19.8` | `13.975` | `0.08` |
| `364` | `L01 DEL CT ALBUÑO0340 281-284` | `L01 DEL CT ALBUÑO0340-281` | `L01 DEL CT ALBUÑO0340-284` | `154.014` | `0.008` | `1.0` | `0.0` | `0.03` |
| `318` | `L01 DEL CT ALBUÑO0340 252-253` | `L01 DEL CT ALBUÑO0340-252` | `L01 DEL CT ALBUÑO0340-253` | `127.085` | `0.061` | `5.7` | `5.657` | `0.05` |

### Strong Per-Line Voltage Drops

Using actual endpoint voltages from the same power-flow result:

- Only one line exceeds `2%` per-line voltage drop: line `843`
- No other current-time line shows a comparable per-line voltage-drop event

So the current-time model has:

- multiple overloaded lines
- but only one truly large per-line voltage drop

## Comparison Of Root Causes

### Line 843

Line `843` is the clearest case where the root cause looks like a combination of:

1. suspicious geometry/electrical-length mismatch
2. weak electrical parameters
3. heavy downstream loading

Evidence:

- geometric terminal distance = `0.0 m`
- modeled electrical length = `100.0 m`
- `max_i_ka = 0.07`
- current = `0.130026 kA`
- loading = `185.752 %`
- voltage drop = `6.487 %`

### Lines 125, 126, 127, 128, 129, 131

These lines are all on the same L01 feeder chain and are heavily overloaded, but they do **not** show the same geometry inconsistency pattern as line `843`.

Observations:

- their coordinate distances are broadly consistent with model lengths
- examples:
  - line `125`: `19.4 m` modeled vs `19.399 m` straight
  - line `127`: `11.4 m` modeled vs `11.400 m` straight
  - line `128`: `12.7 m` modeled vs `12.656 m` straight
  - line `129`: `12.1 m` modeled vs `12.079 m` straight
- they still have high loading because they carry about the same feeder current as line `843`, but their per-line drops remain under `1%`

Assessment:

- these lines look more like ordinary overloads on a weak LV feeder
- not like geometry-placement errors

### Lines 318 And 364

These are overloaded too, but they do not currently look like strong analogues of line `843`.

Line `318`:

- Name: `L01 DEL CT ALBUÑO0340 252-253`
- Model length: `5.7 m`
- Straight distance: `5.657 m`
- Loading: `127.085 %`
- Drop: `0.061 %`
- `max_i_ka = 0.05`

Assessment:

- geometry looks plausible
- overload appears to be driven by low ampacity rather than by a suspicious geometry mismatch

Line `364`:

- Name: `L01 DEL CT ALBUÑO0340 281-284`
- Model length: `1.0 m`
- Straight distance: `0.0 m`
- Loading: `154.014 %`
- Drop: `0.008 %`
- `max_i_ka = 0.03`

Assessment:

- geometry mismatch exists, but the electrical length is tiny
- the issue here looks more like a very low thermal limit on a short link than a voltage-drop driver

## Additional Conclusion

The overloaded-line population appears to split into two groups:

1. **Geometry-mismatch and feeder-head risk**
   - best example: line `843`
   - possible related review candidates: lines `844`, `845`, `846`, `847`

2. **Electrically plausible but thermally overloaded feeder segments**
   - lines `125`, `126`, `127`, `128`, `129`, `131`, `318`

So the reason can be similar for some lines only in the broad sense of "too much load on too little conductor", but the specific geometry inconsistency that makes line `843` especially suspicious does **not** appear to explain most of the other overloaded lines.

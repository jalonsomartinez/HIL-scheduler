# Grid Model Patch Report

Date: 2026-04-14

## Backups

Created fresh pre-change backups for the current packaged pandapower model:

- `digital_twin_package/net_digital_twin.p.backup_pre_grid_patch_20260414`
- `grid_map_digital_twin/net_digital_twin.p.backup_pre_grid_patch_20260414`

Older April 10 backups were left untouched:

- `digital_twin_package/net_digital_twin.p.backup_line843_original_20260410`
- `grid_map_digital_twin/net_digital_twin.p.backup_line843_original_20260410`

## Applied Model Changes

The authoritative source pickle was updated in `digital_twin_package/net_digital_twin.p` and then mirrored byte-for-byte into `grid_map_digital_twin/net_digital_twin.p`.

### Length changes

- line `843` (`CT Albuño340 - L01`): `10.0 m -> 5.0 m`
- line `125` (`L01 DEL CT ALBUÑO0340 5-6`): `19.4 m -> 9.7 m`
- line `131` (`L01 DEL CT ALBUÑO0340 11-12`): `19.8 m -> 9.9 m`

### Rating changes (`max_i_ka`)

- line `843`: `0.24 -> 0.36`
- line `125`: `0.08 -> 0.12`
- line `126`: `0.08 -> 0.12`
- line `127`: `0.08 -> 0.12`
- line `128`: `0.08 -> 0.12`
- line `129`: `0.08 -> 0.12`
- line `131`: `0.08 -> 0.12`
- line `318`: `0.05 -> 0.075`
- line `364`: `0.03 -> 0.045`

## Context

- Line `843` had already been partially patched before this update: `100 m -> 10 m` and `0.07 -> 0.24`.
- In the current pre-change model state, line `843` was no longer overloaded at the audit timestamp, but it was still included here because the target set was defined by the April 10 audit table `Overloaded Lines Ranked By Loading`.

## Verification

- The updated pickles in `digital_twin_package` and `grid_map_digital_twin` are byte-identical after the change.
- `tests.test_grid_map_digital_twin_sync` passes after the mirror update.
- Read-back verification confirmed the stored target values for all edited lengths and all nine updated `max_i_ka` entries.

Simulator sanity check run:

- request timestamp: `2026-04-10T18:13:32.175577+02:00`
- selected timestamp: `2026-04-10T18:00:00+02:00`
- battery input: `battery_p_mw = 0.0`, `battery_q_mvar = 0.0`
- post-change overloaded lines: `0`
- post-change max line loading: `94.087 %`
- post-change minimum voltage: `0.919478 pu`
- post-change maximum voltage: `1.000000 pu`

Relevant post-change line loading values at that snapshot:

- line `843`: `31.457 %`
- line `125`: `94.087 %`
- line `126`: `94.086 %`
- line `127`: `94.086 %`
- line `128`: `94.085 %`
- line `129`: `94.083 %`
- line `131`: `94.053 %`
- line `318`: `72.494 %`
- line `364`: `87.269 %`

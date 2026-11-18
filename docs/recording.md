# Recording and diagnostics

[← README](../README.md)

## Diagnostics

*Download diagnostics* on the integration page gives the state of the manager, devices, roles and settings (secrets and serials redacted). Per inverter there are diagnostic sensors (activity, role, flash writes, address source). The Zendure Manager counts the inverters that are charging, discharging, idle and asleep, and the P1 values received per minute.

## Recording P1 and the control state

*Configure → Logging, simulation and test data → Record P1 every second until midnight* writes three files to `<config>/zendure_ha/`. With a directly read HomeWizard P1 meter you get one line per second. The recording switches itself off at midnight.

| File | Content |
|---|---|
| `p1_<date>.csv` | `time;unix;p1_w;battery_w;source`. With *Record the control state too* (default on), the manager and device columns below follow. |
| `p1_<date>.meta.json` | Integration and HA version, time zone, P1 source, devices with their column prefix `d1`, `d2`, … (name, model, limits), fuse groups and all settings. Serials, addresses, tokens and passwords are left out. |
| `events_<date>.csv` | `time;unix;type;device;detail`, the detail as `key=value` pairs. |

### Manager columns

| Column | Meaning |
|---|---|
| `mode` | Operation mode |
| `setpoint_w` | Dispatch setpoint |
| `net_w` | P1 + batteries |
| `p1_age_ms` | Age of the P1 value when handled |
| `p1_src` | `ws_v2`, `v1` or `sensor` |
| `cloud` | Changing-clouds mode (0/1) |
| `fast_track_n` | Number of fast adjustments |
| `charge_delay_s` | Charge delay still to go (s) |
| `cycle_n`, `cycle_running`, `cycle_ms` | Control cycles, running (0/1), duration of the last |
| `cycle_fail_n`, `cycle_stop_n` | Failed cycles, cycles stopped after 20 s |
| `role_dis`, `role_chg` | Discharge and charge role |
| `target_w` | P1 target (band) |

### Device columns (prefix `d1_`, `d2_`, …)

| Column | Meaning |
|---|---|
| `soc` | State of charge (%) |
| `state` | charging / discharging / standby / sleeping / reserve / offline |
| `pwr_w` | Measured power |
| `cmd_w` | Last command |
| `ramp` | Command held back by the direction change protection (0/1) |

### Events

| Event | When |
|---|---|
| `dir_start`, `dir_end` | Change of direction per device: reason, pause, what released it, duration |
| `dir_slow` | A change of direction takes longer than 15 s (also a WARNING) |
| `mode_change` | Operation mode changed |
| `cloud_on`, `cloud_off` | Changing-clouds mode on/off |
| `cycle_timeout`, `cycle_error` | A control cycle was stopped or failed |
| `cmd_sent`, `cmd_failed` | A command with a new power; a command that could not be delivered |
| `backoff_start`, `backoff_end` | HTTP back-off of a zenSDK device |
| `online`, `offline` | A device came online / went offline |
| `role_change` | A role moved to another device |
| `stop_abrupt` | Power fell more than 800 W between two cycles with the same command |
| `stale_control` | The watchdog fired |
| `p1_jump` | P1 jumped by more than the peak detection: a control cycle at once; `direction=import` (a load switched on) or `export` (a load switched off while discharging, or the sun came out) |
| `bms_limit`, `bms_limit_end` | The battery (BMS) of a device gives at least 150 W (or 10 %) less than a command of 300 W or more for 30 s, for example a charge that tapers near full: the device gets a cap; the cap ends when the device gives the cap + 100 W for 10 s, or stops |
| `target_change` | The P1 target changed |

**File sizes:** with three devices a day at 1 s is about 14 MB (about 5 MB without the control state). Earlier days are compressed (`.csv.gz`, about one fifth); the folder is kept below 200 MB, oldest first. When the format or number of devices changes during the day, recording continues in `p1_<date>_2.csv`.

## Watchdog

In the smart modes the watchdog fires when more than 300 W is imported or exported for 10 s while nothing happens: no new command, no change of direction running, no command held back, and a device could still act (1.6.9: a device at its limit or at its BMS cap cannot). It then logs a WARNING with the control state and forces one control cycle. When the charge delay explains the situation, only an event is written.

Recordings can be replayed in the [simulation](simulation.md#replaying-a-recording).

# Changelog

All notable changes to **Zendure HA-GP013**, newest first.

Zendure HA-GP013 started from [Zendure-HA](https://github.com/Zendure/Zendure-HA) 1.4.4 by [fireson](https://github.com/fireson) and contributors. Many thanks for that foundation. Since 1.5.0 this project follows its own path, with its own control logic, settings and releases. 1.5.0 lists what differs from Zendure-HA 1.4.4. Changes in Zendure-HA after 1.4.4 are adopted where they fit, and noted here.

Effects in brackets come from the simulation (`scripts/compare_smart_mode.py`, three SolarFlow 2400 AC+ with measured ramp, latency and losses), not from measurements in a house.

## [1.6.7] - 2026-11-06

### Changed
- **A big export ends the charge delay early.** From 1000 W export the wait before charging ends after 3 s. An inverter needs 9-12 s to start from 0 W anyway, so a longer wait only exports (5 November replayed: −40 Wh export; November day: +68 Wh).
- **"Batteries full today" with a band and hold time.** It needs 10 % more sun than free room, ends only when the sun is less than the room, and a changed verdict must hold for 10 minutes. On 5 November it changed three times in 12 minutes.
- Simulation: `CALIB=1` uses the start times measured on 4 and 5 November (discharge about 9 s, charge about 12 s from 0 W, after sleep as after standby; about 10 s more in the other AC mode).

### Fixed
- No "foreign write" for a late report of one of our own limits of the last 5 minutes (false alarm after a mode change).
- No `stop_abrupt` event when the power falls after our own 0 W command.

## [1.6.6] - 2026-11-04

### Changed
- The changing clouds mode ends at 4 edges or fewer (1.6.5: 3).

## [1.6.5] - 2026-11-04

### Changed
- **Background reader for zenSDK over HTTP.**
  - Each inverter's report is read on its own rhythm: every 2 s while active, every 30 s while idle.
  - Before, the control cycle read the report and its command then waited 0.5 s, the minimum gap between two requests to the ESP32. Now a command goes out at once.
  - About 5 % less import + export on a November day, 4-5 % less with cooking and clouds.
- **Roles on the usable SoC.** The discharge role compares the SoC above minSoc, the charge role the room below socSet.
- **Role guard.**
  - A discharger less than 10 points above its lower limit hands the role over at once, without margin or hold time, to a device that leads by at least 3 points.
  - The same applies to a charger near its upper limit.
  - Together with the next item, the discharger is no longer almost empty while another inverter is clearly fuller (November day: 41 → 0 minutes).
- **No cloud reserve on an almost empty inverter.** It may charge instead.
- **Changing clouds mode with hysteresis.** It goes on at 6 edges in 10 minutes and off at 4 or fewer, or when the sun is gone. Before, it flapped between 6 and 5 edges, sometimes within seconds (about 40 % fewer on/off switches).
- **Charge delay 10 s instead of 60 s** on days the solar forecast says the batteries will not be full.

### Fixed
- The meta file of the P1 recording got an entry with kWh 0 and default limits at every restart. It is now written after the first control cycle.

## [1.6.4] - 2026-11-03

### Fixed
- Blocking call warning "read_text … manifest.json inside the event loop" when options were saved during a P1 recording. The version now comes from the integration loader.

## [1.6.3] - 2026-11-03

### Changed
- **Idle inverters sleep after 10 minutes by default** (was off).
  - A value that was saved before is kept.
  - About 64 % less own use of idle inverters, about 0.85 kWh a day in November.

### Added
- **Ready hours for the discharger** (*Multiple inverters*, e.g. `7-9,12-13,17-19`, empty = always awake).
  - Outside these hours the discharger may sleep too.
  - At the start of such an hour it is woken (smartMode 1 at 0 W).
  - About 120 Wh a day more left in the batteries in November.

## [1.6.2] - 2026-11-03

### Added
- **Solar forecast** (options menu *Solar forecast*, optional). It reads a Solcast "forecast today" sensor (hours from now added up from `detailedHourly` / `detailedForecast`) or a "remaining today" sensor.
  - **Batteries will not be full today:** no export margin during changing clouds and no negative P1 target, so every Wh is stored. Up to 150 Wh a day in winter with a −50 W target, 20-60 Wh with 0 W. Never worse in spring or summer.
  - **No more sun today:** the inverter with the charge role is made ready to discharge, so no inverter waits to charge after sunset.
  - Event `solar` when the verdict changes.

### Changed
- **Fast confirmation of 0 W after a stop** (zenSDK over HTTP). The report is read every second until it shows 0 W, so a change of direction is released sooner (about 10 % less import with cooking while charging, 4-5 % fewer commands).
- **AC mode and new limit in one command** when the inverter is at 0 W, commanded and measured. This saves 1 s and one request. While it still reports power, the mode is switched at 0 W first.

## [1.6.1] - 2026-11-03

### Added
- Sensor *P1 values per minute* (Zendure Manager, diagnostic), with the source (`ws_v2`, `v1`, `sensor`) as attribute.

## [1.6.0] - 2026-11-03

### Removed
- **The integration's own ramp limit (slew rate).** The SolarFlow 2400 AC+ ramps by itself (about 250 W/s, measured).
  - Removed settings: *Slew limiter*, *Ramp up*, *Ramp down*, *Faster ramp*. Removed entities: *Devices ramping*, *Slew factor*, *Slew active*. Migration and entity registry are cleaned up automatically.
  - What stays: a change of direction through a confirmed 0 W with its delay, and the minimum run time (menu *Direction change protection*).
  - Mode off sends 0 W at once.

### Added
- **Peak detection** (*Power control*, default 400 W, 0 = off).
  - A cycle starts at once when import or export grew by the threshold since the last cycle. This works both ways and also right after a cycle.
  - A load that builds up is seen at the first 400 W.
  - The batteries' own response never counts as a jump.
  - 9-16 % less import while cooking, about 7 % on a November day, no extra relay starts. Event `p1_jump`.
- Sensor *Devices asleep*. *Devices idle* now counts awake idle inverters only. The activity shows *Asleep*.

### Fixed
- **From manual charging to a smart mode the charging stopped and started again.** The charge delay now counts as passed when the batteries are already charging.
- A mode change runs the first cycle at once with the last P1 value, when that value is at most 2 s old.

## [1.5.9] - 2026-11-02

### Added
- **Sleep when idle** (*Multiple inverters*, minutes, 0 = off).
  - An idle inverter goes to smartMode 0: about 5 W instead of 30 W. The next command wakes it.
  - The discharger and the cloud reserve stay awake.
  - About 24 % less own use in an evening.
- **P1 meter without values.**
  - After 60 s a Repairs issue appears; the batteries keep their power.
  - After 5 minutes they fall back to the base load or surplus of the last 10 minutes, only ever less.
- **Second controller detection.** Limits changed by someone else (Zendure app energy plan, another integration) 5 times in 15 minutes give a Repairs issue naming the inverters.

### Fixed
- No control cycle for up to 11 s after switching from off to smart.

## [1.5.8] - 2026-11-02

### Added
- **P1 target (band)**, a number entity from −200 to +200 W.
  - With a negative target, small export is not corrected while charging or idle, and import is corrected at once.
  - The band never applies while discharging, so no stored energy goes to the grid.
  - About 28 % less import and 40 % fewer commands with a −50 W target on sunny days.

### Fixed
- A small command (e.g. 19 W) stayed on a standby inverter after a hand-over and blocked a quick start in the other direction.
- A change of direction that was no longer needed stayed open, with a false "slow" warning.
- The P1 recording showed all inverters offline when a line was written during a cycle.

## [1.5.7] - 2026-11-02

### Added
- **Recording with the control state.** Per line: mode, setpoint, net demand, P1 age and source, cloud mode, charge delay, cycle data and roles, plus per inverter SoC, activity, power and command.
- **Meta file** per day with versions, devices, fuse groups and settings, without secrets.
- **Events file** per day: changes of direction, commands, mode changes, cloud mode, back-off, online/offline, role changes, abrupt stops, watchdog.
- Warning for slow changes of direction (> 15 s), saying what the inverter waits for.
- **Watchdog.** More than 300 W import or export for 10 s while nothing happens logs the state and forces a cycle.
- Recordings are written in batches. Older days are compressed, and the folder is kept below 200 MB.

## [1.5.6] - 2026-11-02

### Fixed
- **A hanging control cycle stopped the control.** A cycle is now stopped after 20 s, the lock is always released, and the log says where it was waiting.

### Added
- Diagnostics section `control`: P1 values, last cycle, failed and stopped cycles, last error.

## [1.5.5] - 2026-11-02

### Changed
- **Operation modes renamed** to `off`, `manual`, `smart`, `discharge`, `charge`, `store_solar` (were `smart_discharging`, `smart_charging`). **Automations that set the old names must be updated.**

### Added
- **P1 recording as test data**, every P1 value with the battery power, until midnight (*Logging, simulation and test data*).

## [1.5.4] - 2026-11-02

### Changed
- **Cloud detection follows the sun.**
  - An edge is half of the recent peak surplus, between 300 and 800 W, so clouds are also seen with little sun.
  - Without sun (below 200 W surplus) there is no cloud mode.
- **During changing clouds:**
  - The export margin applies only while charging.
  - Charging resumes after 10 s instead of 60 s.
  - Another inverter starts only for more than 150 W.
  - About 26 % less import and 29 % less export with 1000 W of PV.

## [1.5.3] - 2026-11-02

### Fixed
- HomeWizard API v2 failed with "Missing Authority Key Identifier" on Python 3.13+.
- Device settings (fan, LED, grid reverse, …) did not move to the configuration section.

### Changed
- The settings overview shows how the P1 meter is read (API v1/v2, token, answering) in the language of Home Assistant.
- Dutch names *Opladen* / *Ontladen*.

## [1.5.2] - 2026-11-02

### Added
- **HomeWizard P1 meter read directly**: API v2 WebSocket (every telegram) or API v1 every second, with the sensor as fallback. About 53 % less import with changing clouds than with the 5 s HomeWizard integration.
- **zenSDK inverter followed to a new address**: via mDNS without the serial in its name, a manual address that no longer answers, or a search of the /24 network.

### Changed
- *Lifetime* is now *Multiple inverters (spread & roles)*, and *Load per device* is now *Spread threshold*.

## [1.5.1] - 2026-11-02

### Added
- **Fast adjustment between cycles.** Every P1 update adjusts inverters that already run in the right direction, without starts, stops or mode changes.
- **Changing clouds mode.**
  - Aims at a small export (export margin, default 100 W).
  - Keeps the discharger ready in output mode.
  - The reserve charges too at a big surplus.
  - Sensor *Changing clouds mode*.
- Role sensors show the device and what it does now. Sensors *Activity* and *Role* per inverter, and *Devices not responding*.
- About 58 % less import than Zendure-HA 1.4.4 with changing clouds.

### Changed
- Spread threshold 85 % (70 % during changing clouds): fewer inverters at part load.
- **AC mode switch in clean steps:** 0 W, then the mode, then the limit. The idle role holders are prepared in the AC mode of their role.
- A change of direction is released by a measured 0 W, at least 2 s and at most 6 s, instead of a fixed 5 s.

### Fixed
- zenSDK devices went offline because the MQTT subscriptions were missing (regression in 1.5.0).
- Offline only after HTTP failures *and* 5 minutes of MQTT silence.
- Better matching in mDNS discovery.
- Charge role with one device online.
- Counters stayed at 0.
- Repairs issues in cloud mode.
- Device address form after an upgrade.
- Separate issue for a guessed address.
- Removed the update listener (Home Assistant 2026.12 warning).
- The crossflow warning now needs 15 s instead of 5 cycles.

## [1.5.0] - 2026-11-01

First release of Zendure HA-GP013, compared to Zendure-HA 1.4.4.

### Added
- **Device life.**
  - Dynamic commands go to RAM (smartMode 1).
  - Unchanged values are not sent again, and persistent writes are coalesced.
  - The safe 0 W state is stored at most once per hour. Sensor *Flash writes*.
- **ESP32 protection.**
  - One request at a time per inverter, at least 0.5 s apart.
  - Back-off after failures, offline after 3 failures, Repairs issue after 5.
  - Idle inverters are polled every 30 s.
- **Multiple inverters.**
  - As few inverters as needed (spread threshold), with consolidation after 30 s and hand-overs in parallel.
  - A peak is answered by the ready inverter with the missing power, and fuse groups are respected.
- **Roles.**
  - The highest SoC discharges and the lowest charges.
  - A role moves only after the role margin and 5 minutes.
- **Direction change protection.**
  - Through 0 W with a pause, and a minimum run time.
  - A full or empty battery is followed.
- **Cadence.**
  - Matching follows P1 at the fast interval.
  - Manual mode at its own interval, mode off every 60 s.
- Peak shaving limit.
- Options menu with all settings.
- Fixed IP address per zenSDK inverter, optional zeroconf discovery.
- Diagnostic sensors, *Download diagnostics* (redacted), Repairs issues for unreachable inverters and MQTT.
- Translations in en, nl, de, fr, pl.

### Fixed
- Home Assistant 2026.8 deprecations (`async_get_device`, `via_device`).
- Invalid hostname for models with `+`.
- `power_get` on a failed request.
- Manual mode applied at once.
- `mqttSelect` back to cloud.
- `aggrOffGrid`.
- `p1_factor` for kW sensors.
- FuseGroup initialisation and restore order.
- Migration (exact entity ids, size limit, backups).
- Reconfigure merge.
- Time zone.
- BLE errors and non-ASCII Wi-Fi names.
- `zero_fast` in `finally`.
- No device dict at INFO.
- Temperature template.
- `ConfigEntryNotReady`.
- Non-blocking MQTT connect.
- In 1.4.4 a surplus charger was never released (sign error).

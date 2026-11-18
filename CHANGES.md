# Changelog

All notable changes to **Zendure HA-GP013**, newest first.

Zendure HA-GP013 started from [Zendure-HA](https://github.com/Zendure/Zendure-HA) 1.4.4 by [fireson](https://github.com/fireson) and contributors. Many thanks for that foundation. Since 1.5.0 this project follows its own path, with its own control logic, settings and releases. 1.5.0 lists what differs from Zendure-HA 1.4.4. Changes in Zendure-HA after 1.4.4 are adopted where they fit, and noted here.

Effects in brackets come from the simulation (`scripts/compare_smart_mode.py`, three SolarFlow 2400 AC+ with measured ramp, latency and losses), not from measurements in a house.

## [1.7.0] - 2026-11-18

From the test of 18 November 12:13-12:14 (L1 turned by hand with both limits set, only the AC mode switched: −1490 → +1960 W in 15 s, through 0 W without a pause and without a start delay).

### Added
- **Direction change per inverter** (select in the controls of each zenSDK inverter): *Via 0 W* (default, as before) or *Direct*.
  - *Direct*: a running inverter that has to change direction gets the new AC mode and limit in one command; the limit of the old direction stays as it was in that command (as in the test) and is cleared by the next one. The slew limiter does not hold the change. The inverter ramps through 0 W by itself (about 230 W/s).
  - The manager offers such an inverter to the new direction first, before an idle one: no start delay of 9-11 s. When it is not needed, it stops as before.
  - The charge delay from discharging to charging stays; the inverter stops during it as before. Keeping it running through a short delay was simulated too: more export, no less import.
  - Event `dir_direct`.
  - Simulation (`CALIB=1 CONFIRM=1`, three SolarFlow 2400 AC+): |P1| −8 % on a spring day, −4 % with a pulsing hob and clouds, −1 % with changing clouds, ±1 % on the recorded evenings of 13, 15, 16 and the morning of 18 November, **+1 % on a November day** (more small changes of direction). Off by default.

### Changed
- **Pause at 0 W 250 ms** (`FLIP_MIN`, was 2 s) once a fresh report confirms 0 W. A report within 1 s after the stop still does not count (`MEASURE_LAG`), so in practice the change is released 1-2 s after the stop. Simulated (`CALIB=1 CONFIRM=1`): with three inverters no effect, because the roles let different inverters charge and discharge (4-11 changes of direction on one inverter a day). With one inverter (`NDEV=1`): −45 Wh import on a spring day (|P1| −0.6 %), −28 Wh with a pulsing hob (−2.2 %), no change on a November day and with changing clouds. Only charging → discharging gains: discharging → charging already waits the charge delay (2-60 s).

### Simulation
- `NDEV=1` or `2` simulates fewer inverters. `DIRECT=1`, `STOP_W=<W/s>` (a stop is faster than a ramp: 1000 W/s with `CALIB=1`, measured 13-18 November) and `FLIP_MIN=<s>`. `CALIB=1` now includes `STOP_W=1000`.

## [1.6.9] - 2026-11-18

From the recordings of 13, 15 and 16 November (smart, manual and charge mode; 1.6.6 and 1.6.8).

### Changed
- **Power tolerance 15 W by default (was 5 W).** A device gets a new command only when it differs more than this from the measured power. At steady power the inverters follow their command within about ±5 W, so 5 W chased noise: on 16 November (a new installation with the default) 29 % of the commands changed less than 15 W, 949 commands an hour against about 390 on 13 and 15 November with 15 W. Replayed (13, 15, 16 November evenings): 20-30 % fewer commands, +9 to +14 Wh |P1| per evening. A saved value stays as it is.
- **Calm manual mode.** The manual power is shared by SoC, and the shares moved every cycle: 15 November 10:00-12:15, a fixed 6600 W charge, gave 183 commands an hour with steps of 20-50 W per device. Now a running device keeps its power while its new share differs less than 5 % of the manual power (at least 50 W) and the devices together deliver the manual power within that band. A new manual power, a start and a stop go through at once.
- The `p1_jump` event tells its `direction` (`import` / `export`). Jumps for a falling load already existed (83 of 211 jumps on 13-16 November were export); `p1_jump`, `bms_limit` and `bms_limit_end` are now listed as event types.

### Added
- **Changing clouds mode by hand.** The select *Changing clouds mode control* (Configuration of the Zendure Manager): *Auto* (detected, as before), *On*, *Off*. *On* / *Off* hold until *Auto* is chosen again; the detection keeps running. The binary sensor shows the mode in effect with the attributes `source` and `detected`; `cloud_on` / `cloud_off` events tell their `source`.
- **P1 target (clouds)** (Configuration, 0 to −500 W): the export margin while charging in the changing clouds mode, before the option *Export margin* in *Power control*. That option is gone from the menu; its saved value is the start value of the entity. The limits stay: at most the recent peak surplus, 0 when the batteries will not be full today. The meta file of a recording lists both P1 targets and the select under `entities`.
- **Battery (BMS) limit of a device.** When a device gives at least 150 W (or 10 %) less than a command of 300 W or more for 30 s, its measured power becomes its cap (event `bms_limit`). The manager plans with the cap: the fuse group limit, the optimal power and the start of another device use it, so the rest goes to the other devices. The device gets at most its cap + 100 W and a new command only when that changes by 100 W. The cap follows the BMS; it ends when the device gives the cap + 100 W for 10 s, reaches its limit or stops (`bms_limit_end`). 15 November 11:25-12:04 (full charge, taper from 85 % SoC) replayed: 12 cap events; no cap on 13, 15 (smart) and 16 November.

### Fixed
- **Stale control watchdog at the limit.** A device at its limit (or its BMS cap) no longer counts as "could still act". 16 November 17:39: three inverters at 2400 / 2200 / 2200 W, 892 W import, gave a WARNING and a forced cycle for nothing.

### Documentation
- README: what is new, and a visual overview of the Zendure Manager entities and all options (defaults, what they do, tips), with a diagram of the changing clouds mode control.

### Simulation
- `SM_<NAME>=<value>` sets any `SmartMode` constant, e.g. `SM_POWER_TOLERANCE=15`, `SM_RELEASE_LOOKBACK=90`.

## [1.6.8] - 2026-11-16

Taken over from Zendure-HA up to 1.4.6.

### Added
- **Fuse groups 600 W / 1200 W and 600 W / 2400 W** (Zendure-HA #1739). The options are renumbered; a saved choice is restored by its name, so it stays the same group.
- SF800 Pro2 in the list of supported devices (#1759).
- Names in all languages and icons for the raw device properties (AC/DC/PV status, OTA, IoT, smart mode, …) (#1760).

### Changed
- **Raw device properties go to the Diagnostic section** of the device (#1760). Error, grid state, max. charge limit and pack state stay in the main list. The entities keep their entity_id and stay enabled; only dashboards that are generated automatically no longer show them.
- **Store solar charges without the charge delay** (#1665). It never discharges, so there is no change of direction to wait for. A switch from store solar to another smart mode while charging starts no delay either (as from manual since 1.6.0).
- Test stack: `pytest-homeassistant[bluetooth]` 2026.11.0 instead of `pytest-homeassistant-custom-component` (#1750); ruff 0.17.3.

### Fixed
- An AB1000S on an SF800 was shown as an 8 kWh I8000 when the device reports packType 70 (#1765). packType 70 is only the I8000 on an SF4000.
- A restored number (manual power) could be overwritten with 0 right after the restore after a restart (#1764): the interface showed the old value, the manager used 0.
- A failing entity migration (target id already held by an old registry entry) no longer stops the setup of the other entities of that device (#1760).
- SoC limit state labels: 1 = full, 2 = limiting (#1738; also corrected in Dutch and French).

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

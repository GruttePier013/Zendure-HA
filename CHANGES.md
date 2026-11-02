# Zendure HA-GP013 1.5.0

Changes compared to Zendure-HA 1.4.4.

Base: fork of Zendure-HA 1.4.4. Developed and tested against Home Assistant 2026.2.3 with 100 unit tests (`pytest`) and `ruff --select F,E9,B,ASYNC` clean. **Not tested on real hardware.** All timing, slew, flash and role values are conservative defaults, not manufacturer specifications.

## Highlights

- **Longer device life**: far fewer flash writes and less load on the device RAM / Wi-Fi in every operation mode, including *off* (see "Lifetime").
- **Smarter multi-device operation**: as few devices as needed do the work (for example 3000 W of export on 2 devices, 1500 W on 1), the device with the highest SoC stays ready for peaks such as a kettle, and roles only move after a clear SoC difference and a hold time.
- **Fast P1 monitor, calm manual mode**: matching mode follows the P1 meter at the fast interval, manual mode and *off* poll much less.
- **Slew-rate protection**: large power steps and charge <-> discharge changes go through 0 W with a short pause.
- **Robust zenSDK**: serialized requests, back-off, offline detection and a Repairs issue instead of an overloaded ESP32.
- **Options menu**, **diagnostic sensors**, **Download diagnostics**, **per-device fixed IP address**, optional **zeroconf discovery**.
- **Home Assistant 2026.8 ready**: no more `async_get_device` / `via_device` deprecation warnings.
- All five translations (en, nl, de, fr, pl) are complete and checked by a test.

## Lifetime: flash wear and RAM

### Flash of the device
The zenSDK writes `inputLimit`, `outputLimit` and `acMode` to flash unless `smartMode` is 1 (documented as "write to RAM"). Every control cycle used to touch these values.

- All dynamic commands (charge / discharge / power steps) are sent with `smartMode=1` (RAM only).
- Values equal to what the device already reports (within a 60 s window) are not sent again.
- Writes of persistent properties (min SoC, SoC limit, ...) are coalesced: at most one write per property per 120 s, always with the latest value.
- **Safe state ("veilige 0")**: in the *off* mode, and after 30 minutes idle, 0 W / 0 W is stored in flash **once** (at most once per hour, switchable in *Lifetime*). A device that reboots on its own then starts at 0 W instead of restoring an old charge/discharge limit. It is never repeated while the device stays in that state.
- `switch_off` is sent once, not every cycle (`_off_sent`).
- Diagnostic sensor **Flash writes** (per device) counts the writes the integration caused, so the effect can be verified.
- Note: the zenSDK documentation does not describe `smartMode=0` in detail. The "0 = flash" behaviour is inferred from the documented "1 = RAM".

### RAM / Wi-Fi of the device (ESP32)
- One request at a time per device (`asyncio.Lock`) with a minimum interval of 0.5 s, a 5 s timeout, and `async with` so responses are always released.
- HTTP status is checked; a failed write is no longer silent.
- Exponential back-off after failures (5 s doubling up to 60 s). After 3 failures the device is marked offline, after 5 a Repairs issue is raised. The issue disappears when the device answers again.
- Idle devices are polled every 30 s instead of every 2 s; the legacy `getAll` runs only every third minute when idle.
- Devices that use the zenSDK no longer subscribe to the cloud topics.

### Host (Home Assistant) wear
- Noisy diagnostic entities (last message, commanded power, command count, ...) are disabled by default.
- Power sensors have a 2 W dead-band, so 1-2 W flicker does not create recorder rows.
- Hot-path `INFO` logs are now `DEBUG`; `lastMessage` is touched at most once per 60 s.
- The simulation log is buffered and written in the executor (flush every 60 s or 500 lines, rotation at 10 MB).
- The API storage is only saved when its content changed.

## Operation modes and cadence

| Mode | Cadence | Remarks |
|---|---|---|
| Smart matching | P1 monitored at the fast interval (`TIMEFAST`); commands at `TIMEZERO` or when the P1 value changes significantly | The P1 monitor is cheap: it only reads values. |
| Manual | `max(manual interval, TIMEZERO)`, default 15 s | A fixed power such as 2400 W does not need frequent updates. A change of the manual value is applied at once. |
| Off | 60 s | Ramp-down to 0 W first, then one `switch_off`. |

New settings (options menu): manual interval, peak shaving limit, role margin, safe state in flash, min run time.

## Roles of multiple devices (`dispatch.py`)

### Fewer devices, one stays ready (new setting *Load per device before another one starts*, default 70 %)
- Another device is only started when the running devices carry 70 % of their limit (before, it was 50 %, so 1500 W of export already started a second 2400 W device).
- **Charging consolidates**: when the others can carry the load (below 65 % of their limit for 30 s) the device with the highest SoC is released and goes back to standby. 3000 W of export therefore runs on 2 devices, 1500 W on 1. Starting uses the opposite order: lowest SoC first, so the standby device is the last to start charging.
- **A peak is answered by the standby device**: when the P1 value jumps up, the discharge-role device starts with the missing power (limited by the slew rate of 600 W/s) instead of 50 W, and it does not have to reverse direction through 0 W with the 5 s pause that a charging device would need.
- The fill level (20-100 %) is in *Lifetime (flash & roles)*. 50 % gives the old behaviour.
- **Parallel hand-over**: the devices that change direction are stopped *in parallel* with scaling the other devices (before, every device was commanded one after the other, so a slow or failing charger delayed the discharger by up to the 5 s HTTP timeout). A failing device no longer blocks the others. The discharger target already accounts for the charge power that is being removed, so the two ramps do not over- or under-shoot; the next cycle corrects from the measured values.
- **Peaks above one device** (for example 3500 W): a device that has to start is given the power the running devices cannot deliver as its target, up to its limit, and the next device takes the rest; all new devices start at the same time. Every device still follows its own slew rate, direction-change pause and minimum run time: a standby device ramps 600 W per second towards its target, it does not jump. Before, a second device started at 50 W and was only scaled up in the next cycle. The same applies to charging when the export exceeds what the running chargers can take.
- **Fuse groups**: a starting device only gets the headroom left in its fuse group after the other devices of that group; when the group is already at its limit the device is not started.
- **Scaling back after a peak**: discharging now consolidates the same way as charging. When the remaining devices can carry the load (below 65 % for 30 s) the device with the lowest SoC (the one kept ready to charge) is released. A kettle that switches on and off restarts the timer, so the second device does not flap. A full device that passes solar through is never released. This replaces the old rule that only released a device below 15 % of its limit.
- **Passing clouds**: a device is only released when the remaining devices can also carry the highest load of the last 3 minutes. Otherwise a device is released in the shadow of a cloud and started again when the sun returns a minute later.
- **A few W is standby**: a device with less than the power tolerance (5 W) of charge or discharge counts as idle, so it can be started for a peak; before, a residual charge command of a few W could keep all devices 'charging' with nothing reacting to a peak.
- **No start/stop flapping after a release**: a released device can still report some output for a moment (report lag). It now stays in standby for 30 s unless the start rule needs it; before, it was counted as running again and started and stopped every 30-40 s.
- **Cross-flow check**: one device charging while another discharges is normal for a cycle or two during a hand-over. When it lasts 5 cycles a warning names the devices (a device did not follow its command and one battery is charging the other).
- Bug fixed on the way: in 1.4.4 the release of a surplus **charging** device (`pwr_low` against `charge_optimal`) could never trigger because of a sign mismatch (both limits are negative for charging), so all devices that had started to charge kept charging at a low power until the export ended. The discharge side did work.

### Roles
- The device with the **highest SoC** is the discharger, the **lowest SoC** (another device) the charger.
- A role moves only when another device leads by **role margin** (default 10 SoC points) and the role was held for at least 5 minutes. Roles are shown as diagnostic sensors *Discharge device* / *Charge device* and in Download diagnostics.
- The start margin of an additional device is now configurable (no more hard-coded +3).
- *Peak shaving*: optional limit (W) for the grid import that is accepted before the batteries discharge; solar pass-through is always delivered.
- The P1 decision logic (`P1Tracker`), peak shaving and roles are pure Python without Home Assistant imports and can be replayed in tests.

## Added

### Slew rate / direction change protection (`slew.py`)
- Commanded power changes at most **600 W/s** up and **1200 W/s** down; small corrections are never held back.
- **One command is at most one second of ramp** (600 W up / 1200 W down). This also holds for a device that starts from standby: before, a device that had not been commanded for a while was credited up to 10 s, so it could jump straight to 2400 W. While a device is ramping the manager sends the next step after about 1 s instead of waiting for the next P1 update.
- A change of direction always passes **through 0 W** and is released after **5 s**.
- A **minimum run time** (default 10 s, 50 W hold) prevents a freshly started device from being switched off again immediately.
- Switching to **OFF** while charging/discharging ramps down to 0 W first.
- ±10 W keep-alive values are not modified.
- **Faster start of the ready device** (default **1200 W/s**, setting *Faster start of the device kept ready*): the device with the discharge role may ramp up faster when it starts from standby, so a 2200 W kettle is covered in about 2 s instead of 4 s. It only applies when the device starts in the same direction as before, or after at least 30 s at 0 W; a device that charged a moment ago keeps the 5 s pause and the normal 600 W/s. The faster rate ends when the target is reached or goes down. It also applies while the ready device is already discharging in the same direction (for example covering the base load when the kettle starts): its relay is closed, only the power goes up. All other devices keep the normal rate.
- **Full or empty battery**: a full battery stops charging on its own (an empty one stops discharging). The slew limiter now continues from the measured power, and the old limit is cleared with one 0 W command (RAM). Before, the limiter still assumed the device was charging, so the next peak waited for a direction change pause that was not needed, and the device could jump back to its old limit (up to 2400 W at once) as soon as the SoC dropped below the limit.
- **Per-device factor** (config number *Slew rate factor*, 10-300 %) for devices that tolerate different steps (a SolarFlow 800 versus a Hyper 2000).

### Options menu (`config_flow.py`)
Overview of the current values and a menu: Account / token, MQTT & Wi-Fi, P1 meter, Power control (incl. manual interval and peak limit), Slew rate & direction change, **Lifetime (flash & roles)**, **Device addresses**, Logging & simulation, Save and close. Each step saves directly. Secrets are not copied into `options`.

### Fixed address per zenSDK device
*Device addresses* lets you enter an IP address or hostname per serial number. It overrides the cloud / mDNS address (fixes the empty IP after a VLAN migration). Optional zeroconf discovery (`_zendure._tcp.local.`, `discovery.py`) keeps the address up to date for devices without a fixed address.

### Diagnostics
- Manager (diagnostic category): cycle duration, interval, count, last cycle, P1 value, dispatch setpoint, devices online / charging / discharging / idle / ramping, cloud and local MQTT connected, discharge and charge device.
- Device: last message, commanded power, power commands sent, ramping, flash writes; zenSDK: HTTP errors, IP address.
- **Download diagnostics** (`diagnostics.py`) with tokens, passwords and serial numbers redacted; roles by device name.
- **Repairs**: unreachable zenSDK device, cloud MQTT not connected, local MQTT not connected.

## Fixed

- **HA 2026.8 deprecations**: `async_get_device(identifiers=...)` -> `async_get_device_by_identifier`, `via_device` -> `via_device_id` (with fallback for older versions).
- **Invalid hostname** for models with a `+` (SolarFlow 2400 AC+).
- **`power_get` over zenSDK** processed `{}` from a failed request and marked the device as online.
- **Manual mode** now picks up the value immediately when enabled or changed.
- `mqttSelect`: switching back to cloud works (case 0 subscribes, case 2 unsubscribes).
- `aggrOffGrid` no longer raises on models without off-grid.
- `p1_factor` is reset and determined per state (kW sensors that appear later no longer give a x1000 error).
- `FuseGroup`: separate initialisation flags for charge and discharge; restore order of the *Part of* select no longer depends on entity restore order.
- Migration: whole-entity-id replacement (`sensor.x_power` no longer matches `sensor.x_power_limit`), 5 MB size limit, `.zendure-backup` copies, correct `except` handler.
- Reconfigure merges into the existing data (keeps `simulation` and `auto_mqtt_user`).
- Time zone follows Home Assistant instead of a hard-coded `GMT+01:00`; BLE errors are raised instead of swallowed; non-ASCII Wi-Fi names work; Wi-Fi password and token are redacted in the BLE log.
- `zero_fast` is reset in a `finally`, `is_stopping` is checked, and `_async_update_data` has a try/except per device.
- Device dict with serial numbers is no longer logged at INFO.
- Temperature template for °C corrected.
- Setup: `ConfigEntryNotReady` when the API or MQTT is not reachable; the manager is loaded before the platforms are forwarded; MQTT clients are stopped on unload; no blocking `connect()` on the event loop; API timeout.

## Status of the 27 suggestions

| # | Suggestion | Status |
|---|---|---|
| 1 | Serialize zenSDK requests | Done (lock, 0.5 s minimum interval, `async with`) |
| 2 | Back-off / offline after N failures | Done |
| 3 | Check HTTP status, surface failed writes | Done |
| 4 | Configurable IP per device | Done (options menu) |
| 5 | `ConfigEntryNotReady` | Done |
| 6 | MQTT cleanup, non-blocking connect, API timeout | Done |
| 7 | `mqttSelect` | Done |
| 8 | `aggrOffGrid` | Done |
| 9 | `p1_factor` | Done |
| 10 | `operation` before online check | Kept on purpose: the mode is remembered and restored at startup; only the message is clearer |
| 11 | `FuseGroup` `initPower` | Done |
| 12 | Fuse-group select restore order | Done |
| 13 | Migration | Done |
| 14 | Reconfigure merge | Done |
| 15 | Time zone / BLE / non-ASCII | Done |
| 16 | `zero_fast` / `is_stopping` / per-device try | Done |
| 17 | No full device dict at INFO | Done |
| 18 | Random MQTT password | **Not possible**: the firmware derives the password from the device id (`md5(deviceId)[8:24]`). Documented; the local broker should stay `local_only`. |
| 19 | Complete translations | Done (en, nl, de, fr, pl; test checks keys and placeholders) |
| 20 | `manifest.json` `iot_class` / `hacs.json` | `iot_class` is `local_push`, `zeroconf` added as dependency. `hacs.json` minimum HA version left at 2025.4.0 (see below) |
| 21 | Unused imports, ruff + pytest in CI | Done (CI runs `ruff --select F,E9,B,ASYNC` and pytest) |
| 22 | Replace class-level `.add` callbacks | Done (`EntityAdder` queue) |
| 23 | Pure dispatch module | Done (`dispatch.py`, `slew.py`) |
| 24 | Per-device slew, peak shaving | Done |
| 25 | Repairs issues | Done |
| 26 | `diagnostics.py` | Done |
| 27 | Zeroconf / discovery | Done (optional, failures are swallowed) |

## Simulation against 1.4.4 smart mode

`scripts/compare_smart_mode.py` runs the real `powerChanged` / `power_charge` / `power_discharge` of both versions on the same simulated house: 3 inverters of 2400 W / 2.88 kWh (SoC 35 / 50 / 65 %), base load with fridge, kettle, coffee machine, washing machine and oven, P1 reporting every second. The inverters follow a command after 0.5 s with a 0.8 s time constant; both versions use the same model. Device-level command handling of each version is reproduced (1.4.4: tolerance and a direct command, smartMode 0 on a 0 W command; 1.5.0: slew limiter, tolerance, smartMode 1). Three scenarios (`SCENARIO=base|ramp|clouds`):

### Base (4 h, sun with a few clouds)

| | 1.4.4 | 1.5.0 |
|---|---:|---:|
| Grid import (Wh) | 58 | 61 |
| Export (Wh) | 3099 | 3110 |
| Average time until a peak is covered (s) | 5.2 | 3.5 |
| Largest step per command (W) | 2152 | 1200 |
| Steps > 600 W | 57 | 43 |
| Flash writes | 13 | 0 |
| Direction changes | 6 | 7 |
| Starts from 0 W (relay) | 16 | 18 |
| Commands | 3699 | 3554 |
| Inverter hours active | 6.6 | 6.24 |
| Reserve while charging (%) | 29.6 | 49.9 |
| Charging and discharging at the same time (s) | 0 | 3 |

| Peak | 1.4.4 (s) | 1.5.0 (s) |
|---|---:|---:|
| 20min kettle 2200W | 6.9 | 1.7 |
| 40min coffee 1400W | 2.2 | 2.2 |
| 75min kettle 2200W | 6.0 | 6.0 |
| 90min washing machine 2000W | 6.6 | 6.6 |
| 100min coffee 1400W | 2.4 | 2.4 |
| 130min kettle 2200W | 1.1 | 1.1 |
| 160min oven 3500W | 10.8 | 3.1 |
| 170min kettle 2200W | 2.6 | 2.0 |
| 195min kettle 2200W | 5.3 | 4.9 |
| 215min oven 3500W | 7.3 | 2.4 |
| 220min coffee 1400W | 6.1 | 6.1 |

### PV build-up and decline (8 h, 0 → 4500 → 0 W, no clouds)

| | 1.4.4 | 1.5.0 |
|---|---:|---:|
| Grid import (Wh) | 360 | 56 |
| Export (Wh) | 12777 | 12794 |
| Average time until a peak is covered (s) | 21.8 | 3.3 |
| Largest step per command (W) | 940 | 1205 |
| Steps > 600 W | 25 | 24 |
| Flash writes | 9 | 0 |
| Direction changes | 8 | 7 |
| Starts from 0 W (relay) | 24 | 17 |
| Commands | 3628 | 3632 |
| Inverter hours active | 6.34 | 6.0 |
| Reserve while charging (%) | 54.6 | 90.6 |
| Charging and discharging at the same time (s) | 0 | 2 |

| Peak | 1.4.4 (s) | 1.5.0 (s) |
|---|---:|---:|
| 30min kettle (low PV) 2200W | 7.2 | 1.8 |
| 75min coffee (PV rising) 1400W | 5.6 | 5.6 |
| 120min washing machine (PV rising) 2000W | 5.9 | 4.7 |
| 180min kettle (high PV) 2200W | 1.1 | 1.1 |
| 300min kettle (PV falling) 2200W | 1.1 | 1.1 |
| 380min oven (low PV) 3500W | 11.7 | 6.7 |
| 430min coffee (after sunset) 1400W | not covered | 1.8 |

### Fast changing clouds (4 h, sun 20-120 s / cloud 15-90 s)

| | 1.4.4 | 1.5.0 |
|---|---:|---:|
| Grid import (Wh) | 160 | 181 |
| Export (Wh) | 6046 | 6066 |
| Average time until a peak is covered (s) | 5.1 | 3.7 |
| Largest step per command (W) | 2195 | 1200 |
| Steps > 600 W | 142 | 73 |
| Flash writes | 48 | 0 |
| Direction changes | 32 | 32 |
| Starts from 0 W (relay) | 53 | 53 |
| Commands | 3225 | 3349 |
| Inverter hours active | 5.2 | 5.11 |
| Reserve while charging (%) | 7.6 | 8.0 |
| Charging and discharging at the same time (s) | 0 | 15 |

| Peak | 1.4.4 (s) | 1.5.0 (s) |
|---|---:|---:|
| 25min kettle 2200W | 20.1 | 10.0 |
| 70min coffee 1400W | 2.3 | 5.3 |
| 100min washing machine 2000W | 1.1 | 1.1 |
| 160min kettle 2200W | 1.1 | 1.1 |
| 200min oven 3500W | 1.1 | 1.1 |

Notes:
- Single peaks differ by a few seconds in both directions because a peak can fall just before or after the 2.2 s fast window; the average is the better measure.
- In the PV build-up/decline run 1.4.4 got stuck after sunset: every inverter was left with a 1-4 W charge command, so all were classified as charging, the 0 W stop command fell within the 5 W tolerance and no idle inverter was left to start. A real inverter probably reports 0 W at such values, so the 360 Wh import of 1.4.4 there is likely too pessimistic; 1.5.0 now counts a few W as standby (see below).
- With fast changing clouds most of the time all three inverters are needed, so there is little reserve in both versions; 1.5.0 then has the same number of relay starts as 1.4.4 and roughly half the large steps.
- Not modelled: communication failures, report delays and the real (undocumented) ramp behaviour of the firmware.

The simulations found these problems in the 1.5.0 code, all fixed: start/stop flapping after a release, the full-battery limit, the missing faster ramp of a ready device that was already running, releasing a device just before the sun returns (a device is now only released when the others can also carry the highest load of the last 3 minutes; with passing clouds relay starts went from 95 to 53), and a few W of residual power that kept a device classified as charging.

## Known limitations / not changed

- ESP32 drops and `/properties/report` timeouts on the SF2400 AC+ are **mitigated** (less load, serialization, back-off) but not proven solved without real hardware.
- "Manual -1000 W charges at 2400 W when min SoC > SoC" is probably firmware behaviour (forced charging below the minimum SoC).
- The repo's own `.ruff.toml` reports about 1100 pre-existing style findings; CI only enforces correctness rules (`F,E9,B,ASYNC`).
- `hacs.json` still says Home Assistant 2025.4.0 while the tests run on 2026.2.3. Raise it if you rely on 2026 APIs only.
- `manifest.json` `version` is `1.0.0`; the release workflow should set `1.5.0`.
- The values for slew, role margin, hold times and flash intervals are defaults and not firmware specifications. Watch the *Flash writes* sensor and tune on your own hardware.

## Ideas for later
- C-rate taper near full / empty to avoid sudden power drops.
- Learn per-device ramp behaviour from the measured response.
- Use `DataUpdateCoordinator` per device for the remaining polling.

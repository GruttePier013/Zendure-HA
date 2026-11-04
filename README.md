# Zendure HA-GP013

**A Home Assistant integration for Zendure home batteries that follows your P1 meter closely, uses as few inverters as needed and is easy on your hardware.**

Zendure HA-GP013 connects Zendure SolarFlow, Hyper, Hub, AIO and Ace devices to Home Assistant and steers them toward zero at the grid connection (*nul op de meter*). It is an independent integration that started from [Zendure-HA](https://github.com/Zendure/Zendure-HA) 1.4.4 and has since gone its own way, with its own control logic, settings and release cycle.

> **Not an official Zendure product.** This integration is not affiliated with or endorsed by Zendure. For the original integration, see [Zendure-HA](https://github.com/Zendure/Zendure-HA).

---

## Origin and thanks

This project would not exist without **[Zendure-HA](https://github.com/Zendure/Zendure-HA)** by [fireson](https://github.com/fireson) and its contributors. Their work on device support, the Zendure cloud and MQTT protocols, zenSDK and the first smart-matching manager is the foundation that everything here is built on. Thank you!

Version 1.4.4 of Zendure-HA was the starting point. Since then, the control part has been largely rewritten: direction change protection, roles, multi-inverter spreading, cloud handling, direct P1 reading, discovery, options menu and diagnostics are new. This fork now differs so much that its changes cannot easily go back upstream, so it continues as a separate project:

- It keeps the domain `zendure_ha`, so it **replaces** Zendure-HA. Your devices, entities and settings carry over, but the two cannot be installed side by side.
- Problems with this integration belong in [this repository's issues][issues], not with the Zendure-HA project.
- Device support, protocol fixes and new models from Zendure-HA are adopted where they fit.

If you want to support the original author: [![Buy Me a Coffee][buymecoffeebadge]][buymecoffee]

---

## Features

### Following the P1 meter
- **Smart matching** steers the batteries so the grid connection stays around 0 W. Between two control cycles, the power of devices that are already running is adjusted on every P1 update without switching relays.
- **Peaks at once.** When the import or export grew by 400 W or more since the last control cycle (a kettle or hob switching on, or off while the batteries discharge), the next cycle starts at once, also right after a cycle. A load that builds up is seen at the first 400 W, not when it is complete. The response of the batteries never counts as a jump. The threshold is a setting (*Power control*, 0 = off).
- **P1 every second.** A HomeWizard P1 meter can be read directly over API v2 (WebSocket, the meter pushes every telegram) or API v1. The HomeWizard integration updates only about every 5 seconds. With changing clouds the simulation shows 67 Wh instead of 142 Wh grid import. The Home Assistant sensor stays as the fallback.
- **Changing clouds.** Fast-changing clouds are detected from the P1 meter (without the effect of the batteries), with a threshold that follows the sun, so also with less than 1000 W of PV. While they last, charging aims for a small export instead of import, charging resumes after 10 s instead of 60 s, a short small shortage does not start an inverter, and one inverter stays ready to discharge.

### Multiple inverters
- **Spread threshold.** Charging and discharging use as few inverters as needed for better efficiency: for example 3000 W on 2 inverters and 1500 W on 1. Above the threshold the power is spread over one more inverter. Below it, the power is merged back onto fewer inverters.
- **Roles.** One inverter is ready to charge and one is ready to discharge. The one with the highest SoC covers peaks such as a kettle right away. Roles only swap after a clear SoC difference and a hold time, and hand-overs run in parallel.

### Easy on the hardware
- **Device flash:** power commands go to RAM (smartMode 1). A safe 0 W state is stored in flash at most once per hour.
- **Relays and the fuse box:**
  - The inverter ramps its power by itself (about 250 W/s for a SolarFlow 2400 AC+); the integration does not add a limit.
  - A change of direction always passes through a confirmed 0 W and a mode-switch pause.
  - Inverters have a minimum run time.
  - Fuse group limits are respected when a device starts.
- **Sleep when idle:** an idle inverter uses about 30 W while it waits for commands. After 10 minutes at 0 W (adjustable, 0 = off) it is put to sleep (smartMode 0, about 5 W). The inverter with the discharge role stays awake, if you want only in set hours (e.g. 7-9, 12-13, 17-19); it is woken at the start of such an hour.
- **Device Wi-Fi/ESP32:** zenSDK requests are serialized, with back-off, deduplicated writes and calm polling while idle, in manual mode and when off.

### Robust and transparent
- **Following devices to new addresses:**
  - A fixed IP address can be set per device.
  - mDNS discovery identifies a device by its own report.
  - A manual address that no longer answers is updated automatically.
  - When mDNS does not work at all, the integration searches the local network.
- **Safe without a P1 meter:** when no P1 value arrives for a minute, a Repairs issue appears and the batteries keep their power. After 5 minutes they fall back to the base load of the last 10 minutes (only ever less).
- **A second controller is noticed:** when the power limits are changed by something else (an energy plan in the Zendure app, another integration), a Repairs issue names the inverters.
- **Problem reporting:** offline detection, Repairs issues, *Download diagnostics* and diagnostic sensors per inverter (activity, role, flash writes, address source). The Zendure Manager counts the inverters that are charging, discharging, idle (awake, ready for a command) and asleep, and the P1 values received per minute.
- **Settings:** one options menu covers the P1 meter, power control, direction change, multiple inverters, device addresses and logging. Translations are available in English, Dutch, German, French and Polish.

Every change to the control logic is measured in a simulation against Zendure-HA 1.4.4 (same house, same inverters). See [CHANGES.md](CHANGES.md#simulation-against-144-smart-mode). You can repeat the simulation with `scripts/compare_smart_mode.py`.

## Supported devices

Ace 1500, AIO 2400, Hyper 2000, Hub 1200, Hub 2000, SolarFlow 800 / 800 Pro / 800 Plus, SolarFlow 1600 AC+, SolarFlow 2400 AC / AC+ / Pro, SuperBase V6400 (untested). SuperBase V4600 is not supported via the token yet.

The smart-matching features are developed on three SolarFlow 2400 AC+ devices on zenSDK with a local MQTT broker.

## Requirements

- Home Assistant 2025.5 or newer
- A Zendure app token (see the [installation guide](https://github.com/Zendure/Zendure-HA/wiki/Installation) of Zendure-HA; the steps are the same)
- For smart matching: a P1 sensor in W. A HomeWizard P1 meter can also be read directly.

## Installation

### HACS (custom repository)

1. If Zendure-HA is installed through HACS, remove it in HACS first. Keep the integration in *Settings → Devices & services*, because this integration takes over its devices and settings.
2. In HACS, open the menu (three dots) → *Custom repositories*. Add `https://github.com/GruttePier013/Zendure-HA-GP013` with the type *Integration*.
3. Search for **Zendure HA-GP013** and select *Download*.
4. Restart Home Assistant.
5. If this is a new installation, add the integration:

   [![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zendure_ha)

### Manual

Copy `custom_components/zendure_ha` from the [latest release][release] (`zendure_ha.zip`) into the `custom_components` folder of your Home Assistant configuration, then restart Home Assistant.

## Configuration

All settings are under *Settings → Devices & services → Zendure HA-GP013 → Configure*:

| Menu | What it sets |
|---|---|
| Account / token | Zendure app token |
| MQTT & Wi-Fi | Cloud or local MQTT broker |
| P1 meter | P1 sensor and, optionally, the HomeWizard P1 meter that is read every second |
| Power control | Control cycle, start power, tolerance, manual interval, peak limit, export margin during changing clouds, peak detection |
| Direction change | Delay before a change of direction, minimum run time |
| Multiple inverters | Spread threshold, role switch margin, safe state in flash, sleep when idle, ready hours of the discharger |
| Solar forecast | Sensor with the solar forecast of today (optional) |
| Device addresses | Fixed IP address per zenSDK device |
| Logging, simulation and test data | MQTT logging, simulation log, P1 recording every second until midnight |

### Solar forecast (optional)

*Configure → Solar forecast*: a sensor with the solar forecast of today, for example `sensor.solcast_pv_forecast_forecast_today` (the hours from now on are added up) or `sensor.solcast_pv_forecast_forecast_remaining_today`.

- **The batteries will not be full today:** the export margin during changing clouds and a negative P1 target are not used, so every Wh of sun is stored. This is worth most in winter; in the simulation it was up to 150 Wh a day with a −50 W target.
- **No more sun today:** the inverter with the charge role is made ready to discharge. No inverter waits to charge after sunset.

### Reading a HomeWizard P1 meter directly

1. Enter the IP address of the P1 meter in *Configure → P1 meter*. The address from the HomeWizard integration is suggested.
2. If the HomeWizard integration already uses API v2, its token is reused. Otherwise, press the button on the P1 meter and select *Submit* within 30 seconds.
3. Alternatively, tick *API v1*. You then need to turn on *Local API* in the HomeWizard app.

### Recording P1 and the control state

*Configure → Logging, simulation and test data → Record P1 every second until midnight* writes three files to `<config>/zendure_ha/`. With a directly read HomeWizard P1 meter, `p1_<YYYY-MM-DD>.csv` gets one line per second. The recording switches itself off at midnight.

| File | Content |
|---|---|
| `p1_<date>.csv` | `time;unix;p1_w;battery_w;source` (the 1.5.5 format). With *Record the control state too* (default on), the manager and every device follow, see the column tables below. |
| `p1_<date>.meta.json` | Integration and Home Assistant version, time zone, P1 source, devices with their column prefix `d1`, `d2`, … (name, model, limits), fuse groups and all settings. A new entry is added when the settings change. Serial numbers, addresses, tokens and passwords are left out. |
| `events_<date>.csv` | `time;unix;type;device;detail`, with the detail as `key=value` pairs. See the event table below. |

**Manager columns**

| Column | Meaning |
|---|---|
| `mode` | Operation mode |
| `setpoint_w` | Dispatch setpoint |
| `net_w` | P1 + batteries |
| `p1_age_ms` | Age of the P1 value when it was handled |
| `p1_src` | Where the P1 value came from: `ws_v2`, `v1` or `sensor` |
| `cloud` | Changing-clouds mode (0/1) |
| `fast_track_n` | Number of fast adjustments |
| `charge_delay_s` | Charge delay still to go (s) |
| `cycle_n` | Number of control cycles |
| `cycle_running` | A control cycle is running (0/1) |
| `cycle_ms` | Duration of the last cycle |
| `cycle_fail_n`, `cycle_stop_n` | Failed cycles, and cycles stopped after 20 s |
| `role_dis`, `role_chg` | Discharge and charge role |
| `target_w` | P1 target (band), from 1.5.8 |

**Device columns**, one set per device with the prefix `d1_`, `d2_`, …

| Column | Meaning |
|---|---|
| `soc` | State of charge (%) |
| `state` | charging / discharging / standby / sleeping / reserve / offline |
| `pwr_w` | Measured power |
| `cmd_w` | Last command |
| `ramp` | Command held back by the direction change protection (0/1); the name is kept from 1.5.x |

**Events**

| Event | When |
|---|---|
| `dir_start`, `dir_end` | A change of direction per device, with the reason, the pause, whether a measurement or the timeout released it, and the duration |
| `dir_slow` | A change of direction takes longer than 15 s (also a WARNING in the log) |
| `mode_change` | The operation mode changed |
| `cloud_on`, `cloud_off` | The changing-clouds mode switched on or off |
| `cycle_timeout`, `cycle_error` | A control cycle was stopped or failed |
| `cmd_sent` | A command with another power than before |
| `cmd_failed` | A command could not be delivered |
| `backoff_start`, `backoff_end` | HTTP back-off of a zenSDK device |
| `online`, `offline` | A device came online or went offline |
| `role_change` | A role moved to another device |
| `stop_abrupt` | The measured power fell by more than 800 W between two control cycles while the command stayed the same, with the SoC |
| `stale_control` | The watchdog fired, see below |
| `target_change` | The P1 target changed |

**File sizes:** with three devices a line is about 160 bytes, so a day at 1 s is about 14 MB, against about 5 MB without the control state. Files of earlier days are compressed (`.csv.gz`, about one fifth). The folder is kept below 200 MB by removing the oldest files first. When the format or the number of devices changes during the day, the recording continues in `p1_<date>_2.csv`.

**Watchdog:** in the smart modes, the watchdog fires when more than 300 W is imported or exported for 10 s while nothing happens. Nothing happening means:

- no new command is sent,
- no change of direction is running,
- no command is held back,
- and a device could still act.

The watchdog then logs a WARNING with the state of the control and forces one control cycle. When the charge delay explains the situation, only an event is written.

**Replaying a recording:** the simulation reads both formats, also `.csv.gz`. With the control state it takes the start SoC of the devices from the file and compares its setpoint with the recorded one (`setpoint_vs_recording_w`).

```
SCENARIO=file P1FILE=p1_2026-11-02.csv FROM=10:00 TO=16:00 python scripts/compare_smart_mode.py . 1.5.7 out.json
```

### P1 target (band)

*Zendure Manager → Configuration → P1 target (band)*: −200 to +200 W, in steps of 5 W. The default 0 W aims at zero at the meter, as before. A negative value, for example −50 W, allows a little export while the batteries charge or are idle:

- Between 50 W export and 0 W nothing is corrected. That means fewer commands and room for small changes.
- More export than the band brings the control back to −50 W.
- Import is corrected at once.
- While the batteries discharge, the band does not apply and the control aims at 0 W: otherwise stored energy would go to the grid.
- The target never starts an inverter by itself. A positive value is the mirror: a little import is allowed while the batteries discharge.

Because it is an entity, an automation can change it, for example by solar forecast or feed-in price.

### Simulating a pulsing hob

```
SCENARIO=hob PV_LEVEL=1500 LATENCY=1.5 RAMP_W=250 REPORT_S=3 python scripts/compare_smart_mode.py . 1.6.3 out.json
```

`PV_LEVEL` is the sun in W. `HOB_CLOUDS=1` adds changing clouds. `VARIANT=ready2` keeps two inverters sharing around cooking. `LATENCY`, `RAMP_W` and `REPORT_S` model the inverters; the values above are close to a SolarFlow 2400 AC+.

### Simulating a whole day with sleeping inverters

```
SCENARIO=day SEASON=nov SLEEP_AFTER=10 READY_HOURS=7-9,12-13,17-19 LATENCY=1.5 RAMP_W=250 REPORT_S=3 python scripts/compare_smart_mode.py . 1.6.3 out.json
```

24 hours from midnight: a November day (`SEASON=summer`: a summer day), a hob at lunch and dinner and appliances through the day. `SLEEP_AFTER` is the sleep setting in minutes (0 = off). `READY_HOURS` keeps the inverter with the discharge role awake only in those hours (`none`: never). `IDLE_AWAKE_W` / `IDLE_ASLEEP_W` (30 / 5 W) set the own use of an idle inverter and `WAKE_S` (2 s) the extra start delay of a sleeping one. The result shows the own use of the idle inverters (`idle_own_use_wh`) next to the grid import and export.

### Tips

- Give each inverter and the P1 meter a fixed address (a DHCP reservation) in your router.
- With several inverters, a spread threshold of 85 % (the default) keeps one SolarFlow 2400 running up to about 1900–2000 W.

## Further reading

The guides of Zendure-HA also apply to this integration:

- [Installation and Zendure app token](https://github.com/Zendure/Zendure-HA/wiki/Installation)
- [Fuse group](https://github.com/Zendure/Zendure-HA/wiki/Fuse-Group)
- [Local MQTT (legacy devices)](https://github.com/Zendure/Zendure-HA/wiki/Local-Mqtt-(Legacy-Devices))
- [Troubleshooting Hyper 2000](https://github.com/Zendure/Zendure-HA/wiki/Troubleshooting)
- Tutorials: [Domotica & IoT](https://iotdomotica.nl/tutorial/install-zendure-home-assistant-integration-tutorial) 🇬🇧, [twoenter](https://www.twoenter.nl/blog/home-assistant-nl/zendure-thuisaccu-integratie-met-home-assistant/) 🇳🇱, [Kieft-C](https://github.com/Kieft-C/Zendure-BKW-PV/wiki/Installation-Zendure-Home-Assistant-integration-%E2%80%93-Tutorial) 🇩🇪

## Reporting problems

Open an [issue][issues] and attach *Download diagnostics* from the integration page. Secrets and serial numbers are redacted. Please do not report problems with this integration to the Zendure-HA project.

## Contributing

Pull requests are welcome. The tests run with `pytest` (`requirements_test.txt`). Changes to the control logic should come with a simulation run of `scripts/compare_smart_mode.py`.

## License

MIT License, see [LICENSE](LICENSE). The copyright notice of Zendure-HA is kept.

[buymecoffee]: https://www.buymeacoffee.com/fireson
[buymecoffeebadge]: https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png
[license-shield]: https://img.shields.io/github/license/GruttePier013/Zendure-HA-GP013.svg?style=for-the-badge
[hacs]: https://github.com/GruttePier013/Zendure-HA-GP013
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[release]: https://github.com/GruttePier013/Zendure-HA-GP013/releases
[releasebadge]: https://img.shields.io/github/v/release/GruttePier013/Zendure-HA-GP013?style=for-the-badge
[issues]: https://github.com/GruttePier013/Zendure-HA-GP013/issues

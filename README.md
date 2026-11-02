# Zendure HA-GP013

[![hacs][hacsbadge]][hacs]

**A Home Assistant integration for Zendure home batteries that follows your P1 meter closely, uses as few inverters as needed and is easy on your hardware.**
**Refactored to make more efficient use of multi inverter setups**

Zendure HA-GP013 connects Zendure SolarFlow, Hyper, Hub, AIO and Ace devices to Home Assistant and steers them toward zero at the grid connection (*nul op de meter*). It is an independent integration that started from [Zendure-HA](https://github.com/Zendure/Zendure-HA) 1.4.4 and has since gone its own way, with its own control logic, settings and release cycle.

> **Not an official Zendure product.** This integration is not affiliated with or endorsed by Zendure. For the original integration, see [Zendure-HA](https://github.com/Zendure/Zendure-HA).

---

## Origin and thanks

This project would not exist without **[Zendure-HA](https://github.com/Zendure/Zendure-HA)** by [fireson](https://github.com/fireson) and its contributors. Their work on device support, the Zendure cloud and MQTT protocols, zenSDK and the first smart-matching manager is the foundation that everything here is built on. Thank you!

Version 1.4.4 of Zendure-HA was the starting point. Since then, the control part has been largely rewritten: the slew limiter, roles, multi-inverter spreading, cloud handling, direct P1 reading, discovery, options menu and diagnostics are new. This fork now differs so much that its changes cannot easily go back upstream, so it continues as a separate project:

- It keeps the domain `zendure_ha`, so it **replaces** Zendure-HA. Your devices, entities and settings carry over, but the two cannot be installed side by side.
- Problems with this integration belong in [this repository's issues][issues], not with the Zendure-HA project.
- Device support, protocol fixes and new models from Zendure-HA are adopted where they fit.
- 
---

## Features

### Following the P1 meter
- **Smart matching** steers the batteries so the grid connection stays around 0 W. Between two control cycles, the power of devices that are already running is adjusted on every P1 update without switching relays.
- **P1 every second.** A HomeWizard P1 meter can be read directly over API v2 (WebSocket, the meter pushes every telegram) or API v1. The HomeWizard integration updates only about every 5 seconds. With changing clouds the simulation shows 67 Wh instead of 142 Wh grid import. The Home Assistant sensor stays as the fallback.
- **Changing clouds.** Fast-changing clouds are detected from the P1 meter (without the effect of the batteries), with a threshold that follows the sun, so also with less than 1000 W of PV. While they last, charging aims for a small export instead of import, charging resumes after 10 s instead of 60 s, a short small shortage does not start an inverter, and one inverter stays ready to discharge.

### Multiple inverters
- **Spread threshold.** Charging and discharging use as few inverters as needed for better efficiency: for example 3000 W on 2 inverters and 1500 W on 1. Above the threshold the power is spread over one more inverter. Below it, the power is merged back onto fewer inverters.
- **Roles.** One inverter is ready to charge and one is ready to discharge. The one with the highest SoC covers peaks such as a kettle right away. Roles only swap after a clear SoC difference and a hold time, and hand-overs run in parallel.

### Easy on the hardware
- **Device flash:** power commands go to RAM (smartMode 1). A safe 0 W state is stored in flash at most once per hour.
- **Relays and the fuse box:**
  - Power changes by at most 600 W/s up and 1200 W/s down per inverter.
  - A change of direction always passes through a confirmed 0 W and a mode-switch pause.
  - Inverters have a minimum run time.
  - Fuse group limits are respected when a device starts.
- **Device Wi-Fi/ESP32:** zenSDK requests are serialized, with back-off, deduplicated writes and calm polling while idle, in manual mode and when off.

### Robust and transparent
- **Following devices to new addresses:**
  - A fixed IP address can be set per device.
  - mDNS discovery identifies a device by its own report.
  - A manual address that no longer answers is updated automatically.
  - When mDNS does not work at all, the integration searches the local network.
- **Problem reporting:** offline detection, Repairs issues, *Download diagnostics* and diagnostic sensors per inverter (activity, role, flash writes, address source).
- **Settings:** one options menu covers the P1 meter, power control, slew rate, multiple inverters, device addresses and logging. Translations are available in English, Dutch, German, French and Polish.

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
| Power control | Control cycle, start power, tolerance, manual interval, peak limit, export margin during changing clouds |
| Slew rate | Ramp up / down, faster ramp for running inverters, direction change delay, minimum run time |
| Multiple inverters | Spread threshold, role switch margin, safe state in flash |
| Device addresses | Fixed IP address per zenSDK device |
| Logging, simulation and test data | MQTT logging, simulation log, P1 recording every second until midnight |

### Reading a HomeWizard P1 meter directly
1. Enter the IP address of the P1 meter in *Configure → P1 meter*. The address from the HomeWizard integration is suggested.
2. If the HomeWizard integration already uses API v2, its token is reused. Otherwise, press the button on the P1 meter and select *Submit* within 30 seconds.
3. Alternatively, tick *API v1*. You then need to turn on *Local API* in the HomeWizard app.

### Recording P1 as test data
*Configure → Logging, simulation and test data → Record P1 every second until midnight* writes every P1 value with the battery power to `<config>/zendure_ha/p1_<YYYY-MM-DD>.csv`. When the HomeWizard P1 meter is read directly, that is one value per second. The recording switches itself off at midnight. A day is about 3 MB. The simulation can replay such a file:

```
SCENARIO=file P1FILE=p1_2026-11-02.csv FROM=10:00 TO=16:00 python scripts/compare_smart_mode.py . 1.5.5 out.json
```

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


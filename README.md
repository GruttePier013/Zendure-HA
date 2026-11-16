# Zendure HA-GP013

[![HACS Custom][hacs-badge]][hacs-add]
[![Release][release-badge]][release]
[![Home Assistant][ha-badge]](hacs.json)
[![License][license-badge]](LICENSE)

**Zero at the meter with Zendure batteries: follows your P1 meter every second, runs as few inverters as needed and is easy on your hardware.**

Zendure HA-GP013 steers Zendure SolarFlow, Hyper, Hub, AIO and Ace devices toward 0 W at the grid connection (*nul op de meter*). It replaces [Zendure-HA](https://github.com/Zendure/Zendure-HA) and keeps your devices, entities and settings.

> Not an official Zendure product.

## Why this one instead of Zendure-HA?

| | Zendure-HA | Zendure HA-GP013 |
|---|---|---|
| P1 meter | HA sensor, about every 5 s with HomeWizard | **HomeWizard read directly, every second** (API v2 WebSocket or v1), sensor as fallback |
| Peaks (kettle, hob) | Next control cycle | **New cycle at once** from a 400 W jump; running inverters follow every P1 update |
| Changing clouds | No special handling | **Detected**: aims at a small export, shorter charge delay, one inverter kept ready |
| Several inverters | Spread over all | **As few as needed** (e.g. 3000 W on 2, 1500 W on 1), charge and discharge **roles**, swap only after a clear SoC difference |
| Hardware | | Commands to RAM (flash at most once per hour), confirmed 0 W on every change of direction, minimum run time, **idle inverters sleep** (≈30 W → 5 W) |
| Robustness | | Follows devices to new IP addresses, safe fallback without P1, Repairs issue when another controller changes the limits, watchdog |
| Insight | | One options menu, diagnostics per inverter, P1 + control state recording, replayable simulation |

In the simulation (three SolarFlow 2400 AC+, measured ramp and latency) this gives about **58 % less grid import with changing clouds** than Zendure-HA 1.4.4. Every change to the control logic is measured this way; see [Simulation](docs/simulation.md) and [CHANGES.md](CHANGES.md).

Changes in Zendure-HA (new devices, protocol fixes) are taken over where they fit; the latest is noted in [CHANGES.md](CHANGES.md).

## Installation

[![Open in HACS][hacs-open-badge]][hacs-add]

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/GruttePier013/Zendure-HA-GP013`, type *Integration*.
2. Download **Zendure HA-GP013** and restart Home Assistant.
3. New installation: [![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zendure_ha)

**Coming from Zendure-HA?** Both use the domain `zendure_ha`, so they cannot run side by side. Follow [Switching from Zendure-HA](docs/migration.md): your settings and entities stay.

Requirements: Home Assistant 2025.4 or newer, a [Zendure app token](https://github.com/Zendure/Zendure-HA/wiki/Installation), and for smart matching a P1 power sensor (W) or a HomeWizard P1 meter.

## Supported devices

Ace 1500, AIO 2400, Hyper 2000, Hub 1200, Hub 2000, SolarFlow 800 / 800 Pro / 800 Pro2 / 800 Plus, SolarFlow 1600 AC+, SolarFlow 2400 AC / AC+ / Pro, SuperBase V6400 (untested). SuperBase V4600 is not supported via the token yet. Developed on three SolarFlow 2400 AC+ on zenSDK with a local MQTT broker.

## Documentation

- [Configuration](docs/configuration.md): all settings, P1 target, solar forecast, HomeWizard
- [Switching from Zendure-HA](docs/migration.md): keep your settings, HACS
- [Recording and diagnostics](docs/recording.md): P1/control recording, events, watchdog
- [Simulation](docs/simulation.md): compare and replay
- [CHANGES.md](CHANGES.md): what changed per version
- Zendure-HA wiki, also valid here: [fuse groups](https://github.com/Zendure/Zendure-HA/wiki/Fuse-Group), [local MQTT](https://github.com/Zendure/Zendure-HA/wiki/Local-Mqtt-(Legacy-Devices)), [Hyper 2000](https://github.com/Zendure/Zendure-HA/wiki/Troubleshooting)

## Problems and contributing

Open an [issue][issues] with *Download diagnostics* from the integration page attached (secrets and serials are redacted). Please do not report problems with this integration to Zendure-HA.

Pull requests are welcome. Tests: `pytest` with `requirements_test.txt`. Changes to the control logic come with a run of `scripts/compare_smart_mode.py`.

## Origin and thanks

Started from [Zendure-HA](https://github.com/Zendure/Zendure-HA) 1.4.4 by [fireson](https://github.com/fireson) and contributors, whose device and protocol work is the foundation of this project. Thank you! [![Buy Me a Coffee](https://img.shields.io/badge/buy%20fireson%20a%20coffee-ffdd00?logo=buymeacoffee&logoColor=black&style=flat-square)](https://www.buymeacoffee.com/fireson)

MIT License, see [LICENSE](LICENSE); the copyright notice of Zendure-HA is kept.

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white
[hacs-open-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[hacs-add]: https://my.home-assistant.io/redirect/hacs_repository/?owner=GruttePier013&repository=Zendure-HA-GP013&category=integration
[release-badge]: https://img.shields.io/github/v/release/GruttePier013/Zendure-HA-GP013?style=for-the-badge
[release]: https://github.com/GruttePier013/Zendure-HA-GP013/releases
[ha-badge]: https://img.shields.io/badge/Home%20Assistant-2025.4%2B-18BCF2?style=for-the-badge&logo=homeassistant&logoColor=white
[license-badge]: https://img.shields.io/github/license/GruttePier013/Zendure-HA-GP013?style=for-the-badge
[issues]: https://github.com/GruttePier013/Zendure-HA-GP013/issues

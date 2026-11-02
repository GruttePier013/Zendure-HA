<p align="center">
  <img src="https://zendure.com/cdn/shop/files/logo.svg" alt="Logo">
</p>

# Zendure HA-GP013

**Improved on: device lifetime · multi-inverter control · robustness**

> Zendure HA-GP013 is a fork of the Zendure Home Assistant integration 1.4.4 that controls your Zendure batteries more gently and more cleverly.
> Power commands stay in RAM instead of the device flash, every inverter ramps smoothly with a pause at 0 W on a change of direction, and only as many inverters run as the load needs while one stays ready for peaks such as a kettle.
> It also adds a complete settings menu, diagnostics and repair messages, a fixed IP address per device and more robust local (zenSDK) communication.

*Unofficial community fork of [Zendure-HA](https://github.com/Zendure/Zendure-HA) 1.4.4, not affiliated with or endorsed by Zendure. It is a drop-in replacement: same domain `zendure_ha`, your devices, entities and settings are kept. It cannot be installed next to the original integration. All changes: [CHANGES.md](CHANGES.md).*

This Home Assistant integration connects your Zendure devices to Home Assistant, making all reported parameters available as entities. You can track battery levels, power input/output, manage charging settings, and integrate your Zendure devices into your home automation routines. The integration also provides a power manager feature that can help balance energy usage across multiple devices when a P1 meter entity is supplied.


[![hacs][hacsbadge]][hacs] [![releasebadge]][release] [![License][license-shield]](LICENSE)

## What is different from 1.4.4

- **Lifetime**: dynamic power commands in RAM (smartMode 1), a safe 0 W state stored in flash at most once per hour, fewer and deduplicated writes, calmer polling when idle, in manual mode and when off.
- **Gentle on relays and the fuse box**: at most 600 W per second up and 1200 W down per inverter, always through 0 W with a 5 s pause on a change of direction, a minimum run time, fuse group limits respected when a device starts.
- **Smarter with several inverters**: charging and discharging use as few inverters as needed (3000 W on 2, 1500 W on 1), the one with the highest SoC stays ready and starts faster for peaks, roles only swap after a clear SoC difference; hand-overs run in parallel.
- **Robust**: serialized zenSDK requests with back-off, offline detection, Repairs issues, `Download diagnostics`, fixed IP per device, optional zeroconf discovery, Home Assistant 2026.8 ready.
- **Settings**: everything in one options menu (P1, power control, slew rate, lifetime, device addresses, logging), complete translations in English, Dutch, German, French and Polish.

A simulation against the 1.4.4 smart mode (same house, same inverters) is in [CHANGES.md](CHANGES.md#simulation-against-144-smart-mode) and can be repeated with `scripts/compare_smart_mode.py`.

## Overview

The guides below are from the original project and apply to this fork as well.

- **[Installation and ZendureApp Token](https://github.com/Zendure/Zendure-HA/wiki/Installation)**
  - [Troubleshooting Hyper2000](https://github.com/Zendure/Zendure-HA/wiki/Troubleshooting)
  - Tutorials
    - [Domotica & IoT](https://iotdomotica.nl/tutorial/install-zendure-home-assistant-integration-tutorial) 🇬🇧
    - [twoenter blog](https://www.twoenter.nl/blog/en/smarthome-en/zendure-home-battery-home-assistant-integration/) 🇬🇧 or [twoenter blog](https://www.twoenter.nl/blog/home-assistant-nl/zendure-thuisaccu-integratie-met-home-assistant/) 🇳🇱
    - [@Kieft-C](https://github.com/Kieft-C/Zendure-BKW-PV/wiki/Installation-Zendure-Home-Assistant-integration-%E2%80%93-Tutorial) 🇩🇪
  - Troubleshooting with few general hints
    - [Kieft-C](https://github.com/Kieft-C/Zendure-BKW-PV/wiki/Zendure-HA-integration-%E2%80%93-Troubleshoot-&-Mini-Anleitung) 🇩🇪
  - Solution for Protocol Mosquitto local MQTT Error after Update Zendure Device
    - [Kieft-C](https://github.com/Kieft-C/Zendure-BKW-PV/wiki/%E2%80%9EProtocol-error%22-im-Mosquitto%E2%80%90Log-nach-Zendure%E2%80%90Firmware%E2%80%90Update-(KW26-27-in-2026))🇩🇪
- **Configuration:**
  - [Fuse Group](https://github.com/Zendure/Zendure-HA/wiki/Fuse-Group)
  - Zendure Manager
    - [Power distribution strategy](https://github.com/Zendure/Zendure-HA/wiki/Power-distribution-strategy)
  - [Local Mqtt (Legacy devices)](https://github.com/Zendure/Zendure-HA/wiki/Local-Mqtt-(Legacy-Devices))
  - Home Assistant Energy Dashboard

- **Supported devices:**
  - Ace1500
  - Aio2400
  - Hyper2000
  - Hub1200 [German](https://github.com/Zendure/Zendure-HA/wiki/SolarFlow-Hub1200-German)
  - Hub2000
  - [SF800](https://github.com/Zendure/Zendure-HA/wiki/SolarFlow-800)
  - SF800 Pro
  - SF800 Plus
  - SF1600 AC+
  - SF2400 AC
  - SF2400 AC+
  - SF2400 Pro
  - SuperBase V6400 (?)
  - SuperBase V4600 not yet supported using the token

- **Device Automation:**
  - Cheap hours.

## Minimum Requirements
- [Home Assistant](https://github.com/home-assistant/core) 2025.5+

## Installation

### HACS (Home Assistant Community Store)

This fork is not in the HACS default list; add it as a custom repository:

1. Remove the original Zendure integration from HACS first (keep the configuration in Home Assistant: the fork uses the same domain and takes over your devices and settings).
2. In HACS open the menu (three dots) -> "Custom repositories", add `https://github.com/OWNER/Zendure-HA-GP013` with type "Integration".
3. Search for "Zendure HA-GP013" and select "Download".
4. Restart Home Assistant.
5. Go to "Settings" in the Home Assistant sidebar, then select "Devices and Services".
6. Click the blue [+ Add Integration] button at the bottom right, search for "Zendure", and install it.

   [![Set up a new integration in Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zendure_ha)


## Contributing

Contributions are welcome! If you're interested in contributing, please review our [Contribution Guidelines](CONTRIBUTING.md) before submitting a pull request or issue.

## Credits

Based on [Zendure-HA](https://github.com/Zendure/Zendure-HA) by the original authors and contributors. If you want to support the original author: [![Buy Me a Coffee][buymecoffeebadge]][buymecoffee]

---

[buymecoffee]: https://www.buymeacoffee.com/fireson
[buymecoffeebadge]: https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png
[license-shield]: https://img.shields.io/github/license/OWNER/Zendure-HA-GP013.svg?style=for-the-badge
[hacs]: https://github.com/OWNER/Zendure-HA-GP013
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[release]: https://github.com/OWNER/Zendure-HA-GP013/releases
[releasebadge]: https://img.shields.io/github/v/release/OWNER/Zendure-HA-GP013?style=for-the-badge


## License

MIT License, see [LICENSE](LICENSE). The copyright notice of the original project is kept.

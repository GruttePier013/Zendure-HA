# Changelog

All notable changes to **Zendure HA-GP013** are listed here, newest first.

Zendure HA-GP013 started from [Zendure-HA](https://github.com/Zendure/Zendure-HA) 1.4.4 by [fireson](https://github.com/fireson) and contributors. Many thanks for that foundation. Since 1.5.0 this project has followed its own path, with its own control logic, settings and releases. Version 1.5.0 lists everything that differs from Zendure-HA 1.4.4. Changes in Zendure-HA after 1.4.4 are not included automatically. They are adopted where they fit, and when that happens it is noted here.

## [1.5.6] - 2026-11-02

### Fixed
- **A control cycle that hangs no longer stops the control.** A diagnostics file showed 1 control cycle while the P1 meter delivered values every second: the devices stayed at their last state (all *OFFLINE* in the diagnostics, no roles), and a device kept charging at 49 W. A cycle that waits for something that never comes keeps the update lock, so every following cycle was skipped. A cycle is now stopped after 20 s (`SmartMode.CYCLE_TIMEOUT`). The lock is always released, and the log says exactly where it was waiting (ERROR "Control cycle does not finish within 20 s … it waits in: …").
- A failing cycle is logged with its traceback in one ERROR message, instead of two separate lines.

### Added
- *Download diagnostics* has a `control` section: the number of P1 values handled, whether a cycle is running, the time of the last cycle, the number of failed or stopped cycles, the last error, and where the last stopped cycle was waiting.

## [1.5.5] - 2026-11-02

### Changed
- **Operation modes renamed**: the options of *Operation Mode* (`select.zendure_manager_operation`) are now `off`, `manual`, `smart`, `discharge`, `charge` and `store_solar` (were `smart_discharging` and `smart_charging`). A mode that was stored under its old name is restored under the new name after the update. **Automations or scripts that set `smart_discharging` or `smart_charging` must use `discharge` or `charge` now.**
- *Logging & simulation* in the options menu is now *Logging, simulation and test data*.

### Added
- **P1 recording as test data**: *Configure → Logging, simulation and test data → Record P1 every second until midnight*.
  - Every P1 value is written with the power of the batteries and its source to `<config>/zendure_ha/p1_<YYYY-MM-DD>.csv` (`time;unix;p1_w;battery_w;source`). When the HomeWizard P1 meter is read directly, that is one value per second; otherwise every update of the P1 sensor is written.
  - The recording switches itself off at midnight (local time). When Home Assistant was not running at midnight, it is switched off at the next start.
  - Lines are written in batches every 60 s to spare the storage of the host. A day is about 3 MB.
  - The settings overview and *Download diagnostics* (`p1_recording`) show the running recording.
- **Simulation with a recording**: `SCENARIO=file P1FILE=<p1_YYYY-MM-DD.csv>` (optionally `FROM=HH:MM TO=HH:MM`) replays the net demand of the house (P1 + batteries) of a recorded day with the control code of any version.

## [1.5.4] - 2026-11-02

### Changed: cloud detection follows the sun
Changing clouds are detected from the net demand: the P1 value plus what the batteries deliver or take. That is the P1 meter without the effect of the batteries, so their own reactions do not look like clouds. Up to 1.5.3 a cloud edge had to be a change of at least 800 W within 15 s. With little sun (below about 1000 W) a cloud cannot take away that much, so the cloud mode hardly ever switched on, exactly when every cloud turned a surplus into a shortage (the charger stops, the discharger starts, and back).

- **Edge threshold relative to the sun**: an edge is now half of the largest surplus (PV minus house) in the last 15 minutes, at least 300 W and at most 800 W. With 750 W of surplus an edge is 375 W, with 1600 W or more it stays 800 W as before.
- **No cloud mode without sun**: below 200 W of surplus in the last 15 minutes there is no cloud mode. In the evening and at night, appliances switching on and off (oven, washing machine) no longer start it.
- When the cloud mode switches on or off, the log shows the number of edges, the threshold and the peak surplus (INFO). *Download diagnostics* shows them under `clouds`.

### Changed: what the cloud mode does
- **Export margin only while charging**: while discharging, the margin made the batteries deliver 100 W more than the house used, so stored energy went to the grid. With little sun the house often needs the batteries during a cloud, and the margin cost more stored energy than it saved import. The margin now only reduces charging. The setting is renamed *Export margin while charging during changing clouds*.
- **Charging again after 10 s instead of 60 s**: the charge delay of 1.4.4 waits 60 s before charging again when charging started within the last 5 minutes. During changing clouds the sun is often back within a minute, so up to a minute of surplus went to the grid after every cloud. In cloud mode the delay is now 10 s. Outside cloud mode it is unchanged.
- **Another device only for a real shortage**: in cloud mode a device is started only for a shortage or surplus of more than 150 W (otherwise 50 W). A short shortage under a cloud no longer starts the discharger at an inefficient 80-130 W for a few seconds.

### Simulation
Changing clouds for 4 h, 3 × SolarFlow 2400 AC+, different PV peaks. *Stored* is the sum of the SoC of the three batteries at the end.

| PV peak, P1 interval | Version | Import | Export | Relay starts | Direction changes | Stored |
|---|---|---:|---:|---:|---:|---:|
| 1000 W, 1 s | 1.4.4 | 96 Wh | 666 Wh | 116 | 11 | 141 % |
| 1000 W, 1 s | 1.5.3 | 72 Wh | 604 Wh | 107 | 4 | 142 % |
| 1000 W, 1 s | 1.5.4 | 53 Wh | 429 Wh | 97 | 5 | 148 % |
| 1000 W, 5 s | 1.4.4 | 106 Wh | 608 Wh | 103 | 14 | 144 % |
| 1000 W, 5 s | 1.5.3 | 107 Wh | 605 Wh | 102 | 4 | 144 % |
| 1000 W, 5 s | 1.5.4 | 80 Wh | 422 Wh | 94 | 5 | 149 % |
| 2000 W, 1 s | 1.4.4 | 176 Wh | 250 Wh | 48 | 21 | 257 % |
| 2000 W, 1 s | 1.5.3 | 46 Wh | 721 Wh | 55 | 13 | 236 % |
| 2000 W, 1 s | 1.5.4 | 59 Wh | 470 Wh | 51 | 12 | 245 % |
| 4500 W, 1 s | 1.4.4 | 160 Wh | 6046 Wh | 53 | 32 | 300 % |
| 4500 W, 1 s | 1.5.3 | 67 Wh | 6043 Wh | 43 | 15 | 297 % |
| 4500 W, 1 s | 1.5.4 | 74 Wh | 6044 Wh | 43 | 15 | 297 % |
| 4500 W, 5 s | 1.5.3 | 142 Wh | 6163 Wh | 64 | 17 | 295 % |
| 4500 W, 5 s | 1.5.4 | 139 Wh | 6134 Wh | 65 | 15 | 296 % |

Base (sun with a few clouds) and PV build-up/decline are unchanged (43 and 50 Wh import), because the cloud mode does not switch on there. With 2000 W of PV, 1.4.4 stores more and exports less than 1.5.x, but imports three times as much. The difference is the export margin while charging (setting, default 100 W). Each part was measured separately, with `VARIANT=oldcloud`, `nodis`, `chyst10` and `cstart150`. The variants show:
- With only the new detection, the discharge margin exported battery energy: 1085 instead of 604 Wh export at 1000 W PV.
- Without that margin, the 10 s charge delay stored the most.
- The start threshold of 150 W gave the fewest starts (97 instead of 129).

Every cloud with little sun still means a switch between charging and discharging. Fewer starts than this are only possible by accepting more import.

## [1.5.3] - 2026-11-02

### Fixed
- **HomeWizard P1 meter via API v2: `certificate verify failed: Missing Authority Key Identifier`.** The certificates of HomeWizard devices have no X.509 v3 extensions. Since Python 3.13 (Home Assistant 2025.2 and newer), the default SSL context checks strictly (`VERIFY_X509_STRICT`), so it refused the certificate of the meter. Getting a token and the WebSocket therefore failed even after the button was pressed. Strict checking is now switched off for this one connection. The certificate is still verified against the HomeWizard CA.

- **Device settings did not move to the configuration section**: *Fan mode*, *Fan speed*, *LED*, *Grid reverse* and others stayed under the controls when they were created while Home Assistant was running (the usual case: they appear with the first device report). Home Assistant adds a new entity immediately and writes its registry entry with the category it has at that moment; 1.5.1 set the category just after that. The category is now set in the constructor, so existing entities move to the configuration section after an update and restart. *Fan switch* is a setting too now.

### Changed
- Dutch: *Activity* and *Role* per device, and the role sensors, say *Opladen* / *Ontladen* (was *Laadt* / *Ontlaadt*, *Klaar om te laden* / *Klaar om te ontladen*).
- The charge delay inherited from 1.4.4 (2 s, or 60 s when charging already started within 5 minutes) is now a set of constants (`SmartMode.CHARGE_DELAY*`), so the simulation can vary it (`VARIANT=hyst10`, `start100`, `edge400`). The behaviour is unchanged.
- **Settings overview**: shows how the HomeWizard P1 meter is read right now: API v1 or API v2 (own token, or the token of the HomeWizard integration), and whether it answers ("every second", "connecting", "not answering, the sensor is used"). Before, it showed only "direct" without a token of its own. The words in the overview now follow the language of Home Assistant, and numbers are shown without a needless `.0`.
- README and changelog rewritten. They now credit Zendure-HA 1.4.4 and explain that this integration continues as its own project on that base.

## [1.5.2] - 2026-11-02

### Added: P1 meter every second (HomeWizard)

The HomeWizard integration of Home Assistant updates the P1 power about every 5 seconds, while a DSMR 5 meter (for example a Kaifa / AIFA meter) sends a telegram every second. The manager can now read a HomeWizard P1 meter itself (*Configure → P1 meter → Address of the HomeWizard P1 meter*, optional):

- **API v2 (preferred)**: a WebSocket (`wss://<address>/api/ws`, topic `measurement`, field `power_w`). The meter pushes every new measurement, so there is no polling. A token is needed: the token of the HomeWizard integration is used when that integration runs on API v2, otherwise the options flow asks to press the button on the meter and gets a token of its own (`local/zendure_ha`). The certificate of the meter is checked against the HomeWizard CA.
- **API v1**: `GET http://<address>/api/v1/data` every second (field `active_power_w`); "Local API" must be switched on in the HomeWizard app. HomeWizard advises not to poll faster than every 500 ms.
- The address of the HomeWizard integration is suggested. When the meter does not answer three times in a row, the Home Assistant sensor is used again (and the meter is tried every 30 s); when the HomeWizard integration knows a new address of the meter (DHCP), that address is taken over.
- Only the newest value is handed to the manager: when it is busy (for example during an AC mode switch), values that arrive meanwhile are not queued.

Simulation with changing clouds (3 × SolarFlow 2400 AC+, same control code, only the P1 interval differs):

| P1 interval | grid import | max import | relay starts | steps > 600 W |
|---|---|---|---|---|
| 5 s (HomeWizard integration) | 142 Wh | 3930 W | 64 | 337 |
| 1 s (read directly) | 67 Wh | 2729 W | 43 | 125 |
| 1.4.4 with 5 s, for comparison | 184 Wh | 3762 W | 52 | 211 |

### Added: following a zenSDK device to a new address

Up to 1.5.1 a device was only followed to a new address (DHCP) when mDNS found it **and** its service name or TXT records contained the serial number, and a manually entered address was never changed. Now:

- **mDNS service without the serial**: the device at the found address is asked for its report (`GET /properties/report`), which contains its serial number. The service name is remembered, so this is asked only once.
- **Manual address that no longer answers**: when the device is found at another address (mDNS or the search below) while its manual address fails (3 times in a row), the new address is used and stored as the manual address (logged as a warning).
- **mDNS does not work at all** (VLAN, mDNS blocked): a zenSDK device that no longer answers is looked for in the /24 network of its last address (one `GET /properties/report` per address, 32 at a time, nearest addresses first), at most once every 15 minutes. Its MQTT reports usually keep arriving meanwhile, so it stays online.
- A fixed address (DHCP reservation) in the router remains the most reliable solution.

### Changed: clearer names

- *Lifetime (flash & roles)* is now **Multiple inverters (spread & roles)**.
- *Load per device before another one starts* is now **Spread threshold (%)**, with an explanation: above the threshold the power is spread over one more inverter, below it it is merged onto fewer (85 %: one SolarFlow 2400 up to about 1900-2000 W; 70 % while clouds change quickly). The setting itself (`fill_level`) is unchanged.

## [1.5.1] - 2026-11-02

Bugfix release for 1.5.0.

### Fixed

- **zenSDK devices offline (regression in 1.5.0)**: 1.5.0 stopped subscribing zenSDK devices to their MQTT topics. Devices whose HTTP address does not work (for example because the cloud returns no IP and the guessed `zendure-<model>-<serial>.local` name does not exist) then received no data at all and all went OFFLINE. The subscriptions are back as in 1.4.4: the reports over the (local) MQTT broker keep the device online and its sensors up to date.
- **Offline after HTTP failures**: a device is only marked offline after repeated HTTP failures when it is also silent over MQTT for 5 minutes.
- **Zeroconf discovery**: a device is now also matched by the serial number in the mDNS TXT records (the suffix of the service name is not always the serial). A Zendure device that is found but cannot be matched is logged at INFO with its name and IP address, so the address can be entered in *Configure → Device addresses*.
- **Charge role with one device online**: showed the same device as the discharge role; it now shows "-" (no role split with one device).
- **Counters**: the control cycle and command counters stayed at 0 because their diagnostic entities are disabled by default and a disabled entity does not keep a value. They now count internally (also in *Download diagnostics*).
- **Repairs issues in cloud mode**: the start-up HTTP read also runs for devices on the cloud connection (as in 1.4.4). With a wrong address it raised the zenSDK "does not respond" issue, which then never went away in cloud mode. Issues are now only raised while a device is on zenSDK, and removed when it is switched to cloud.
- **AC mode of the device kept ready** ("acMode" stayed on *output* for the device with the charge role): the AC mode was only sent together with a real charge or discharge command. An idle device with the charge role now gets `acMode` *input* with 0 W limits, and the idle device with the discharge role `acMode` *output*, so it is ready when the export or the peak comes. One RAM command (smartMode 1), only when the mode differs, not while the device runs and not within the 5 s direction-change pause after it ran.
- **Device addresses after an upgrade from 1.4.4**: the form was empty, without any hint which address was in use. It now lists every device with its current address and where it comes from (cloud, manual, mDNS, or a GUESSED name that probably does not exist), and prefills the addresses that came from the cloud or mDNS. Entering an address resets the HTTP back-off and the issue of that device.
- **Guessed address**: when the cloud returns no IP and the constructed `.local` name does not answer, a separate Repairs issue "No address known" explains to enter the IP address; it appears after 3 failed requests instead of 5.
- **Download diagnostics** now include per device: address source, reported and last sent AC mode, input/output limit, home input/output and the commanded power.
- **Home Assistant 2026.12 warning "has an update listener and should use it for scheduling a reload"**: the integration had an update listener while the options menu (and reconfigure) also requested the reload. The update listener is removed; settings that need no reload (P1 meter, power control, slew, lifetime, addresses, logging) are applied directly when a menu step is saved, and *Save and close* schedules one reload only after a token or MQTT change.
- **AC mode switch in clean steps**: 1.4.4 (and 1.5.0) sent the new AC mode together with the new limit in one command. A change between charging and discharging now always goes: (1) the old limit to 0 W in the old mode (normally already done by the slew limiter, which passes 0 W with a 5 s pause), (2) the new AC mode with both limits at 0 W, (3) after 1 s the new limit. Nothing extra is sent when the mode does not change. A device that was already prepared for its role (see above) starts with a single command, so the ready device does not lose this second at a peak. Applies to the zenSDK command set (SolarFlow 800/1600/2400 family, also on the cloud connection); the older hub/ACE/AIO models use another command set without an AC mode.
- **Direction change confirmed by measurement instead of a fixed 5 s**: the opposite direction is released as soon as a fresh measurement (HTTP report or MQTT message, taken at least 1 s after the stop, so not a report from before it) confirms 0 W, but never before 2 s. Without a fresh measurement it waits the maximum pause (default now 6 s, setting *Maximum pause at 0 W when changing direction*). When the device still reports power after the stop it is waited for, at most 15 s. A full or empty battery that stopped by itself counts as confirmed. A device that has to reverse itself can now change direction after about 2 s instead of 5 s.
- **Cross-flow warning by time**: the warning "charging while discharging" was given after 5 cycles; with the 1 s follow-up steps that could be a normal hand-over of 5 s. It is now given after 15 s, once per episode.
- `scripts/compare_smart_mode.py`: recognises every 1.5.x version as the new code (was only "1.5.0"), and measures for every cloud edge (sun changes > 800 W within 15 s, batteries not full) how long P1 needs to be within +/-150 W again.

### Following P1 and changing clouds (new in 1.5.1)

Cloud edges and load changes were followed with a delay of several seconds: each control cycle acted on a P1 value that was already ~3 s old, and between two cycles nothing was done. Several options were built and each was measured in the simulation (`scripts/compare_smart_mode.py`, P1 every 1 s and every 5 s); these are kept:

- **Fast adjustment within the current AC mode** (`fast_track`): every P1 update between two control cycles adjusts the power of the devices that already run in the right direction. Nothing is started, stopped or switched there, so no relay switches and no AC mode change; starting, stopping, roles and direction changes stay with the control cycle, with the confirmed 0 W and the mode steps. Power that was commanded but is not measured yet is counted, and a device is only adjusted when a report newer than its last command has arrived, so it does not work against the control cycle. It never goes below 50 W or above the limit, ignores differences below 50 W and adjusts a device at most once per second (ESP32 load). It works with any P1 update rate.
- **Export margin during changing clouds (C)**: in cloud mode the control aims at a small export instead of 0 W (setting *Export margin during changing clouds*, default 100 W, 0 = off), without changing the direction of the batteries.
- **Faster ramp for running devices (D)**: a device that already runs in the same direction ramps up at the faster rate (default 1200 W/s): its relay is closed and its AC mode set. A start from 0 W and a direction change keep 600 W/s.
- **Cloud mode with a ready discharger (E)**: during changing clouds at most all devices but one charge; the device with the discharge role stays idle in output mode, so a cloud or an appliance is covered at once without a direction change. Cloud mode switches on after 6 changes of more than 800 W within 15 s in 10 minutes (an appliance on and off counts as 2) and off after 10 quiet minutes. New diagnostic sensor *Changing clouds mode*.

Tried and not kept:
- **A control cycle every second at a cloud edge (A)**: more device starts and not less import, because starts and stops reacted to every intermediate value. The fast adjustment above does the same for the power of running devices only, without starts.
- **Trend feed-forward (B)**: following a P1 trend ahead. Next to the fast adjustment it gave only a little less import (58 instead of 67 Wh with changing clouds) but 23 more device starts; with a P1 sensor that updates every 5 s it overshot (196 instead of 53 Wh import), because such a sensor shows an edge only when it is over. While testing it also showed that a trend on the raw P1 value mistakes the effect of the batteries' own changes for a trend.
- **Reading the P1 meter directly from HomeWizard**: at that time the P1 sensor was assumed to update every second, so there seemed to be nothing to gain. The HomeWizard integration updates only about every 5 seconds: added in 1.5.2.

Simulation against 1.4.4 (4 h, PV build-up/decline 8 h; three 2400 W devices):

| Scenario | P1 | Version | Import (Wh) | Export (Wh) | Peak covered (s) | Import 1st min of peaks (Wh) | Starts | Direction changes | Largest step (W) | Commands | Flash |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Base | 1 s | 1.4.4 | 58 | 3099 | 5.2 | 20.9 | 16 | 6 | 2152 | 3699 | 13 |
| Base | 1 s | 1.5.1 | 43 | 3082 | 2.7 | 10.0 | 21 | 7 | 1219 | 4599 | 0 |
| Base | 5 s | 1.4.4 | 46 | 3082 | 3.6 | 12.9 | 16 | 6 | 2167 | 3405 | 13 |
| Base | 5 s | 1.5.1 | 44 | 3085 | 2.1 | 9.2 | 21 | 7 | 1200 | 3071 | 0 |
| PV build-up/decline | 1 s | 1.4.4 | 360 | 12777 | 21.8 | 42.0 | 24 | 8 | 940 | 3628 | 9 |
| PV build-up/decline | 1 s | 1.5.1 | 50 | 12784 | 3.0 | 8.7 | 19 | 7 | 1200 | 4551 | 0 |
| PV build-up/decline | 5 s | 1.4.4 | 52 | 12785 | 3.4 | 9.6 | 24 | 10 | 1378 | 4035 | 13 |
| PV build-up/decline | 5 s | 1.5.1 | 49 | 12788 | 1.9 | 4.9 | 21 | 7 | 1205 | 3179 | 0 |
| Changing clouds | 1 s | 1.4.4 | 160 | 6046 | 5.1 | 15.5 | 53 | 32 | 2195 | 3225 | 48 |
| Changing clouds | 1 s | 1.5.1 | 67 | 6043 | 2.0 | 4.9 | 43 | 15 | 1200 | 3741 | 0 |
| Changing clouds | 5 s | 1.4.4 | 184 | 6070 | 4.4 | 14.3 | 52 | 30 | 2350 | 2766 | 41 |
| Changing clouds | 5 s | 1.5.1 | 142 | 6163 | 1.4 | 6.0 | 64 | 17 | 1205 | 2126 | 0 |

- **Fill level 85 % (was 70 %), 70 % in cloud mode**: two 2400 W devices are now consolidated to one below about 1920 W (and a second one starts above about 2040 W), so at part load one device runs at a higher, usually more efficient load instead of two at a low load. Simulation: inverter hours 6.15 -> 5.71 (base) and 5.99 -> 5.57 (PV build-up/decline), import unchanged; a few more starts (21 -> 25 in the base run). In cloud mode the fill level is 70 %, because there the load is shared sooner to follow the clouds (with 85 % in cloud mode the simulation gave 117 instead of 99 Wh import and 87 instead of 68 starts). A fill level saved before in *Lifetime* keeps its value. The real efficiency curve of the devices is not documented; the simulation does not model it.
- **Cloud reserve charges too at a big surplus**: in cloud mode the device with the discharge role also charges when the measured surplus stays above 95 % of what the other devices can take together for 10 s (e.g. above ~4560 W with two other 2400 W devices); it is kept out again after 30 s below 85 %. The measured surplus is used (without the export margin), and the delay ignores the short overshoot while chargers ramp (the P1 value lags the device readings by up to a second, so the surplus briefly looks 1000 W larger). Simulation with a 6500 W PV peak and changing clouds: all batteries are full at the end with and without this rule, so it stores no extra energy there; it costs 65 -> 100 Wh import and 48 -> 68 starts because the reserve is charging when a cloud comes. It helps when the batteries would not get full with the other devices alone (short sunny periods, large batteries).
- `scripts/compare_smart_mode.py`: `PV_PEAK` sets the PV peak of the clouds scenario, `P1_INTERVAL` the update interval of the P1 sensor (default 1 s), `VARIANT=noFast,noC,noE` switches single features off.
- **Role sensors match the device count sensors**: the role sensors are renamed to *Charge role* / *Discharge role* and show the device together with what it does now, e.g. `L2 SolarFlow 2400AC (standby)`, `(charging)`, `(discharging)`, `(standby, cloud reserve)` or `(not responding)`, in the language of Home Assistant. A role means "ready for": the device with the charge role is the first to charge, it does not have to charge now, which looked inconsistent next to *Devices charging: 0*. The activity comes from the same classification as the count sensors.
- **Device names on the count sensors**: *Devices online / charging / discharging / idle* have an attribute `devices` with the names.
- **New sensor *Devices not responding*** (count, with the names as attribute).
- **Activity and Role per device** (diagnostics of every device): *Activity* (charging, discharging, standby, standby as cloud reserve, not responding) and *Role* (ready to charge, ready to discharge, no role), as translated enum sensors, updated every control cycle from the same classification as the manager sensors.
- **Device settings in the configuration section**: *Grid reverse*, *Fuse group*, *Connection*, *Fan speed*, *Smart fan* and *LED* are now configuration entities instead of controls.
- `manifest.json` version set to 1.5.1.

### Note for zenSDK devices without an IP from the cloud

When the log shows `MDNS lookup failed` for `zendure-...local` (or the Repairs issue "No address known" appears), enter the IP address of each device (Zendure app → device information, or your router) in *Configure → Device addresses*. A fixed DHCP lease in the router is recommended. The Repairs issue disappears when the device answers.

## [1.5.0] - 2026-11-01

First release of Zendure HA-GP013. Changes compared to Zendure-HA 1.4.4.

Base: fork of Zendure-HA 1.4.4. Developed and tested against Home Assistant 2026.2.3 with 100 unit tests (`pytest`) and `ruff --select F,E9,B,ASYNC` clean. **Not tested on real hardware.** All timing, slew, flash and role values are conservative defaults, not manufacturer specifications.

### Highlights

- **Longer device life**: far fewer flash writes and less load on the device RAM / Wi-Fi in every operation mode, including *off* (see "Lifetime").
- **Smarter multi-device operation**: as few devices as needed do the work (for example 3000 W of export on 2 devices, 1500 W on 1), the device with the highest SoC stays ready for peaks such as a kettle, and roles only move after a clear SoC difference and a hold time.
- **Fast P1 monitor, calm manual mode**: matching mode follows the P1 meter at the fast interval, manual mode and *off* poll much less.
- **Slew-rate protection**: large power steps and charge <-> discharge changes go through 0 W with a short pause.
- **Robust zenSDK**: serialized requests, back-off, offline detection and a Repairs issue instead of an overloaded ESP32.
- **Options menu**, **diagnostic sensors**, **Download diagnostics**, **per-device fixed IP address**, optional **zeroconf discovery**.
- **Home Assistant 2026.8 ready**: no more `async_get_device` / `via_device` deprecation warnings.
- All five translations (en, nl, de, fr, pl) are complete and checked by a test.

### Lifetime: flash wear and RAM

#### Flash of the device
The zenSDK writes `inputLimit`, `outputLimit` and `acMode` to flash unless `smartMode` is 1 (documented as "write to RAM"). Every control cycle used to touch these values.

- All dynamic commands (charge / discharge / power steps) are sent with `smartMode=1` (RAM only).
- Values equal to what the device already reports (within a 60 s window) are not sent again.
- Writes of persistent properties (min SoC, SoC limit, ...) are coalesced: at most one write per property per 120 s, always with the latest value.
- **Safe state ("veilige 0")**: in the *off* mode, and after 30 minutes idle, 0 W / 0 W is stored in flash **once** (at most once per hour, switchable in *Lifetime*). A device that reboots on its own then starts at 0 W instead of restoring an old charge/discharge limit. It is never repeated while the device stays in that state.
- `switch_off` is sent once, not every cycle (`_off_sent`).
- Diagnostic sensor **Flash writes** (per device) counts the writes the integration caused, so the effect can be verified.
- Note: the zenSDK documentation does not describe `smartMode=0` in detail. The "0 = flash" behaviour is inferred from the documented "1 = RAM".

#### RAM / Wi-Fi of the device (ESP32)
- One request at a time per device (`asyncio.Lock`) with a minimum interval of 0.5 s, a 5 s timeout, and `async with` so responses are always released.
- HTTP status is checked; a failed write is no longer silent.
- Exponential back-off after failures (5 s doubling up to 60 s). After 3 failures the device is marked offline, after 5 a Repairs issue is raised. The issue disappears when the device answers again.
- Idle devices are polled every 30 s instead of every 2 s; the legacy `getAll` runs only every third minute when idle.
- Devices that use the zenSDK no longer subscribe to the cloud topics.

#### Host (Home Assistant) wear
- Noisy diagnostic entities (last message, commanded power, command count, ...) are disabled by default.
- Power sensors have a 2 W dead-band, so 1-2 W flicker does not create recorder rows.
- Hot-path `INFO` logs are now `DEBUG`; `lastMessage` is touched at most once per 60 s.
- The simulation log is buffered and written in the executor (flush every 60 s or 500 lines, rotation at 10 MB).
- The API storage is only saved when its content changed.

### Operation modes and cadence

| Mode | Cadence | Remarks |
|---|---|---|
| Smart matching | P1 monitored at the fast interval (`TIMEFAST`); commands at `TIMEZERO` or when the P1 value changes significantly | The P1 monitor is cheap: it only reads values. |
| Manual | `max(manual interval, TIMEZERO)`, default 15 s | A fixed power such as 2400 W does not need frequent updates. A change of the manual value is applied at once. |
| Off | 60 s | Ramp-down to 0 W first, then one `switch_off`. |

New settings (options menu): manual interval, peak shaving limit, role margin, safe state in flash, min run time.

### Roles of multiple devices (`dispatch.py`)

#### Fewer devices, one stays ready (new setting *Load per device before another one starts*, default 70 %, 85 % since 1.5.1)
- Another device is only started when the running devices carry 70 % (1.5.1: 85 %) of their limit (before, it was 50 %, so 1500 W of export already started a second 2400 W device).
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

#### Roles
- The device with the **highest SoC** is the discharger, the **lowest SoC** (another device) the charger.
- A role moves only when another device leads by **role margin** (default 10 SoC points) and the role was held for at least 5 minutes. Roles are shown as diagnostic sensors *Discharge device* / *Charge device* and in Download diagnostics.
- The start margin of an additional device is now configurable (no more hard-coded +3).
- *Peak shaving*: optional limit (W) for the grid import that is accepted before the batteries discharge; solar pass-through is always delivered.
- The P1 decision logic (`P1Tracker`), peak shaving and roles are pure Python without Home Assistant imports and can be replayed in tests.

### Added

#### Slew rate / direction change protection (`slew.py`)
- Commanded power changes at most **600 W/s** up and **1200 W/s** down; small corrections are never held back.
- **One command is at most one second of ramp** (600 W up / 1200 W down). This also holds for a device that starts from standby: before, a device that had not been commanded for a while was credited up to 10 s, so it could jump straight to 2400 W. While a device is ramping the manager sends the next step after about 1 s instead of waiting for the next P1 update.
- A change of direction always passes **through 0 W**; it is released after **2 s** once the device reports 0 W, otherwise after **6 s** (1.5.1).
- A **minimum run time** (default 10 s, 50 W hold) prevents a freshly started device from being switched off again immediately.
- Switching to **OFF** while charging/discharging ramps down to 0 W first.
- ±10 W keep-alive values are not modified.
- **Faster start of the ready device** (default **1200 W/s**, setting *Faster start of the device kept ready*): the device with the discharge role may ramp up faster when it starts from standby, so a 2200 W kettle is covered in about 2 s instead of 4 s. It only applies when the device starts in the same direction as before, or after at least 30 s at 0 W; a device that charged a moment ago keeps the 5 s pause and the normal 600 W/s. The faster rate ends when the target is reached or goes down. It also applies while the ready device is already discharging in the same direction (for example covering the base load when the kettle starts): its relay is closed, only the power goes up. All other devices keep the normal rate.
- **Full or empty battery**: a full battery stops charging on its own (an empty one stops discharging). The slew limiter now continues from the measured power, and the old limit is cleared with one 0 W command (RAM). Before, the limiter still assumed the device was charging, so the next peak waited for a direction change pause that was not needed, and the device could jump back to its old limit (up to 2400 W at once) as soon as the SoC dropped below the limit.
- **Per-device factor** (config number *Slew rate factor*, 10-300 %) for devices that tolerate different steps (a SolarFlow 800 versus a Hyper 2000).

#### Options menu (`config_flow.py`)
Overview of the current values and a menu: Account / token, MQTT & Wi-Fi, P1 meter, Power control (incl. manual interval and peak limit), Slew rate & direction change, **Lifetime (flash & roles)** (since 1.5.2: *Multiple inverters (spread & roles)*), **Device addresses**, Logging & simulation, Save and close. Each step saves directly. Secrets are not copied into `options`.

#### Fixed address per zenSDK device
*Device addresses* lets you enter an IP address or hostname per serial number. It overrides the cloud / mDNS address (fixes the empty IP after a VLAN migration). Optional zeroconf discovery (`_zendure._tcp.local.`, `discovery.py`) keeps the address up to date for devices without a fixed address.

#### Diagnostics
- Manager (diagnostic category): cycle duration, interval, count, last cycle, P1 value, dispatch setpoint, devices online / charging / discharging / idle / ramping, cloud and local MQTT connected, discharge and charge device.
- Device: last message, commanded power, power commands sent, ramping, flash writes; zenSDK: HTTP errors, IP address.
- **Download diagnostics** (`diagnostics.py`) with tokens, passwords and serial numbers redacted; roles by device name.
- **Repairs**: unreachable zenSDK device, cloud MQTT not connected, local MQTT not connected.

### Fixed

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

### Status of the 27 suggestions

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

### Simulation against 1.4.4 smart mode

`scripts/compare_smart_mode.py` runs the real `powerChanged` / `power_charge` / `power_discharge` of both versions on the same simulated house: 3 inverters of 2400 W / 2.88 kWh (SoC 35 / 50 / 65 %), base load with fridge, kettle, coffee machine, washing machine and oven, P1 reporting every second. The inverters follow a command after 0.5 s with a 0.8 s time constant; both versions use the same model. Device-level command handling of each version is reproduced (1.4.4: tolerance and a direct command, smartMode 0 on a 0 W command; 1.5.0: slew limiter, tolerance, smartMode 1). Three scenarios (`SCENARIO=base|ramp|clouds`):

#### Base (4 h, sun with a few clouds)

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

#### PV build-up and decline (8 h, 0 → 4500 → 0 W, no clouds)

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

#### Fast changing clouds (4 h, sun 20-120 s / cloud 15-90 s)

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

### Known limitations / not changed

- ESP32 drops and `/properties/report` timeouts on the SF2400 AC+ are **mitigated** (less load, serialization, back-off) but not proven solved without real hardware.
- "Manual -1000 W charges at 2400 W when min SoC > SoC" is probably firmware behaviour (forced charging below the minimum SoC).
- The repo's own `.ruff.toml` reports about 1100 pre-existing style findings; CI only enforces correctness rules (`F,E9,B,ASYNC`).
- `hacs.json` still says Home Assistant 2025.4.0 while the tests run on 2026.2.3. Raise it if you rely on 2026 APIs only.
- `manifest.json` `version` is `1.0.0`; the release workflow should set `1.5.0`.
- The values for slew, role margin, hold times and flash intervals are defaults and not firmware specifications. Watch the *Flash writes* sensor and tune on your own hardware.

### Ideas for later
- C-rate taper near full / empty to avoid sudden power drops.
- Learn per-device ramp behaviour from the measured response.
- Use `DataUpdateCoordinator` per device for the remaining polling.

# Configuration

[← README](../README.md)

All settings are under *Settings → Devices & services → Zendure HA-GP013 → Configure*.

| Menu | What it sets |
|---|---|
| Account / token | Zendure app token |
| MQTT & Wi-Fi | Cloud or local MQTT broker |
| P1 meter | P1 sensor and, optionally, the HomeWizard P1 meter that is read every second |
| Power control | Control cycle, start power, tolerance, manual interval, peak limit, peak detection |
| Direction change | Delay before a change of direction, minimum run time |
| Multiple inverters | Spread threshold, role switch margin, safe state in flash, sleep when idle, ready hours of the discharger |
| Solar forecast | Sensor with the solar forecast of today (optional) |
| Device addresses | Fixed IP address per zenSDK device |
| Logging, simulation and test data | MQTT logging, simulation log, P1 recording every second until midnight ([Recording](recording.md)) |

## How the control works

- **Smart matching** keeps the grid connection around 0 W. Between two control cycles, inverters that already run in the right direction are adjusted on every P1 update, without switching relays.
- **Peaks at once.** When import or export grew by 400 W or more since the last cycle, the next cycle starts at once. A load that builds up is seen at the first 400 W. The response of the batteries never counts as a jump. Threshold: *Power control* (0 = off).
- **Changing clouds** are detected from the P1 meter (without the batteries), with a threshold that follows the sun. The mode can also be set by hand (1.6.9, see below). While it lasts, charging aims at a small export (*P1 target (clouds)*), charging resumes after 10 s instead of 60 s, a short small shortage does not start an inverter, and one inverter stays ready to discharge.
- **Spread threshold.** As few inverters as needed (e.g. 3000 W on 2, 1500 W on 1). Above the threshold one more inverter is added, below it the power is merged back.
- **Roles.** One inverter is ready to charge, one to discharge; the one with the highest SoC covers peaks. Roles swap only after a clear SoC difference and a hold time.
- **Easy on the hardware.** Power commands go to RAM (smartMode 1); a safe 0 W state is stored in flash at most once per hour. A change of direction always passes a confirmed 0 W and a mode-switch pause; inverters have a minimum run time; fuse group limits apply when a device starts. zenSDK requests are serialized with back-off and calm polling when idle, in manual mode and when off.
- **Sleep when idle.** After 10 minutes at 0 W (adjustable, 0 = off) an idle inverter goes to sleep (≈30 W → 5 W). The discharger stays awake, optionally only in set hours (e.g. 7-9, 12-13, 17-19).
- **Direction change per inverter (1.7.0).** Select *Direction change* in the controls of a zenSDK inverter. *Via 0 W* (default): stop, a measured 0 W (at least 2 s), then a start in the other direction, which costs the inverter 9–11 s. *Direct*: a running inverter only switches its AC mode, in one command with both limits set, and ramps through 0 W by itself (about 230 W/s); the manager turns such an inverter before it starts another one. The charge delay from discharging to charging stays; during it the inverter stops as before.
- **Battery limits (1.6.9).** When the battery (BMS) of an inverter gives clearly less than its command for 30 s, for example a charge that tapers near full or a cold battery, the manager plans with what it really gives: the rest goes to the other inverters, and the inverter gets at most its cap + 100 W, a new command only when the cap moves 100 W. When it gives that extra 100 W for 10 s, the cap ends.
- **Calm manual mode (1.6.9).** The manual power is shared out again only when an inverter's share would change by more than 5 % of the manual power (at least 50 W), or when the inverters together are more than that off. A new manual power, a start and a stop go through at once.
- **Safe without P1.** No P1 value for a minute: a Repairs issue, the batteries keep their power. After 5 minutes they fall back to the base load of the last 10 minutes (only ever less).
- **A second controller is noticed.** When something else changes the limits (Zendure app energy plan, another integration), a Repairs issue names the inverters.
- **Devices at new addresses** are found again: fixed IP per device, mDNS by the device's own report, automatic update of a manual address that no longer answers, and a network search when mDNS does not work.

## Changing clouds mode and P1 target (clouds)

*Zendure Manager → Configuration* (1.6.9):

- **Changing clouds mode control**: *Auto* (default, the manager detects it), *On* or *Off*. With *On* or *Off* the manager does not switch the mode until you set *Auto* again; the detection keeps running. The binary sensor *Changing clouds mode* shows the mode in effect, with the attributes `source` (`auto` / `user`) and `detected`.
- *On* switches everything the mode does: the export margin below, charging resumes after 10 s instead of 60 s, a shortage below 150 W does not start an inverter, the spread threshold is 70 %, and one inverter stays ready to discharge.
- **P1 target (clouds)**: 0 to −500 W, steps of 10 W, default −100 W. The export the control aims at while charging in the changing clouds mode. Before 1.6.9 this was the option *Export margin* in *Power control*; its value is the start value. It is at most the recent peak surplus of the sun, and 0 on a day the solar forecast says the batteries will not be full. With a negative P1 target (band) the larger export counts; they do not add up.

## P1 target (band)

*Zendure Manager → Configuration → P1 target (band)*: −200 to +200 W, steps of 5 W. Default 0 W. A negative value, e.g. −50 W, allows a little export while the batteries charge or are idle:

- Between 50 W export and 0 W nothing is corrected (fewer commands).
- More export than the band brings the control back to −50 W; import is corrected at once.
- While discharging the band does not apply and the control aims at 0 W, so stored energy does not go to the grid.
- The target never starts an inverter by itself. A positive value is the mirror: a little import while discharging.

It is an entity, so an automation can change it (e.g. by solar forecast or feed-in price).

## Solar forecast (optional)

*Configure → Solar forecast*: a sensor with today's forecast, e.g. `sensor.solcast_pv_forecast_forecast_today` (the hours from now on are added up) or `sensor.solcast_pv_forecast_forecast_remaining_today`.

- **Batteries will not be full today:** the export margin during changing clouds and a negative P1 target are not used, so every Wh of sun is stored (worth most in winter).
- **No more sun today:** the inverter with the charge role is made ready to discharge.

## Reading a HomeWizard P1 meter directly

1. Enter the IP address of the P1 meter in *Configure → P1 meter* (the address from the HomeWizard integration is suggested).
2. If the HomeWizard integration already uses API v2, its token is reused. Otherwise press the button on the P1 meter and select *Submit* within 30 seconds.
3. Alternatively tick *API v1* and turn on *Local API* in the HomeWizard app.

## Fuse groups

Per device under its configuration: *unused*, *own circuit* (3600 W), or a group of 600/800/1200/2000/2400/3600/4000/5000 W (some with a separate charge limit). Devices on one group share its limit. See the [Zendure-HA wiki](https://github.com/Zendure/Zendure-HA/wiki/Fuse-Group).

## Tips

- Give each inverter and the P1 meter a fixed address (DHCP reservation).
- With several inverters, the default spread threshold of 85 % keeps one SolarFlow 2400 running up to about 1900–2000 W.

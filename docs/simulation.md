# Simulation

[← README](../README.md)

`scripts/compare_smart_mode.py` runs the control logic of this integration against Zendure-HA 1.4.4 in the same house with the same inverters (three SolarFlow 2400 AC+ with measured ramp, latency and losses). Every change to the control logic is measured this way; the effects in [CHANGES.md](../CHANGES.md) come from it, not from measurements in a house.

General form:

```
SCENARIO=<scenario> [options] python scripts/compare_smart_mode.py . <version> out.json
```

Inverter model options: `LATENCY`, `RAMP_W`, `REPORT_S` (e.g. `LATENCY=1.5 RAMP_W=250 REPORT_S=3`, close to a SolarFlow 2400 AC+). `CALIB=1` uses the start times measured in November 2026 (discharge about 9 s, charge about 12 s from 0 W).

## Pulsing hob

```
SCENARIO=hob PV_LEVEL=1500 LATENCY=1.5 RAMP_W=250 REPORT_S=3 python scripts/compare_smart_mode.py . 1.6.3 out.json
```

`PV_LEVEL` is the sun in W. `HOB_CLOUDS=1` adds changing clouds. `VARIANT=ready2` keeps two inverters sharing around cooking.

## A whole day with sleeping inverters

```
SCENARIO=day SEASON=nov SLEEP_AFTER=10 READY_HOURS=7-9,12-13,17-19 LATENCY=1.5 RAMP_W=250 REPORT_S=3 python scripts/compare_smart_mode.py . 1.6.3 out.json
```

24 hours from midnight: a November day (`SEASON=summer` for summer), a hob at lunch and dinner and appliances through the day. `SLEEP_AFTER` is the sleep setting in minutes (0 = off). `READY_HOURS` keeps the discharger awake only in those hours (`none`: never). `IDLE_AWAKE_W` / `IDLE_ASLEEP_W` (30 / 5 W) set the own use of an idle inverter and `WAKE_S` (2 s) the extra start delay of a sleeping one. The result shows `idle_own_use_wh` next to grid import and export.

## Replaying a recording

The simulation reads [recordings](recording.md) in both formats, also `.csv.gz`. With the control state it takes the start SoC of the devices from the file and compares its setpoint with the recorded one (`setpoint_vs_recording_w`).

```
SCENARIO=file P1FILE=p1_2026-11-02.csv FROM=10:00 TO=16:00 python scripts/compare_smart_mode.py . 1.5.7 out.json
```

Any `SmartMode` constant can be set with `SM_<NAME>=<value>` (1.6.9), e.g. a tolerance and release sweep on a recorded evening with the measured inverter model and 5.28 kWh batteries:

```
SM_POWER_TOLERANCE=15 SM_RELEASE_LOOKBACK=90 SCENARIO=file P1FILE=p1_2026-11-16.csv FROM=15:00 CAPACITY=5280 LATENCY=1.5 RAMP_W=250 REPORT_S=3 python scripts/compare_smart_mode.py . 1.6.9 out.json
```

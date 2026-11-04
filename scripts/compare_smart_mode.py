"""Compare the smart matching of Zendure-HA 1.4.4 and the 1.5.0 fork on the same simulated house.

Run once per version, pointing at that version's source tree (the folder that holds custom_components):
    python scripts/compare_smart_mode.py <path to 1.4.4 tree> 1.4.4 r144.json
    python scripts/compare_smart_mode.py . 1.5.0 r150.json
SCENARIO=base (default) | ramp (8 h PV build-up and decline, no clouds) | clouds (4 h, fast changing clouds)
| hob (3 h, a pulsing hob at lunch and dinner, PV_LEVEL=<W> sun, HOB_CLOUDS=1 clouds on it,
HOB_SHIFT=<s> shifts the pulses against the control cycles: average several shifts, one shift can be lucky or unlucky)
| day (24 h from midnight, SEASON=nov (default, 1500 W sun peak), spring (3000 W) or summer (4500 W), with clouds, a hob at lunch
and dinner and appliances through the day; batteries of 5.28 kWh; DAY_WEATHER=mixed: hours of clear sky, overcast and changing
clouds instead of changing clouds all day; DAY_SOC=<a>,<b>,<c> start SoC)
Solar forecast (Solcast-like, day scenario): FC_BIAS=<f> forecast = f x the real PV, p10 / p90 = 0.6 / 1.4 x the forecast;
FC_FILL=1 switches the export margin and a negative P1 target off when the p10 forecast (FC_FILL_Q=1: the normal forecast)
cannot fill the batteries today;
FC_CLOUD=early|gate|both uses the forecast confidence per half hour for the changing clouds mode (FC_CONF_ERR=<0..1> part of
the half hours with a wrong confidence)
| file (P1FILE=<p1_YYYY-MM-DD.csv[.gz]> recorded by the integration, optionally FROM=HH:MM TO=HH:MM: the net demand of the house
is replayed; a 1.5.7 recording with the control state also gives the start SoC and the recorded setpoint for comparison);
PV_PEAK=<W> sets the PV peak of the clouds scenario (default 4500).
P1_TARGET=<W> sets the P1 target band (1.5.8, e.g. -50). VARIANT=ready2 keeps two inverters sharing around cooking (hob).
LATENCY, TAU, RAMP_W, REPORT_S set the inverter model (default 0.5 s, 0.8 s, no ramp limit, every cycle);
LATENCY=1.5 RAMP_W=250 REPORT_S=3 is close to what the SolarFlow 2400 AC+ did on 2 November 2026.
AC mode of the inverters (1.6.2): MODE_SWITCH=<s> extra delay when a command needs another AC mode (default 1.0, the
mode at 0 W, a pause, then the limit, as up to 1.6.1; 0 = mode and limit in one command); CONFIRM=<s> reads the inverter
every that many s for 6 s after a stop (a fast confirmation of 0 W; 0 = only the normal reports); FILL=<pct> spread threshold
(FILL=1: all inverters share, so none stays ready); SUN_RULE=1 (day scenario) sets "no more sun today" from the forecast.
LOSS (default on, LOSS=0 off): the inverters lose energy as measured on a SolarFlow 3000 Mix AC+ (hassio-zendure-restapi): discharging 33 W + 2.6 %,
charging 4.8 %, from the battery; idle IDLE_AWAKE_W / IDLE_ASLEEP_W from the grid (it shows in P1 and the control covers it).
ASSUME_ZERO=<s>: a change of direction no longer waits for a measured 0 W, it is released that many s after the stop command.
Settings of the integration: ROLE_MARGIN, TIMEZERO, TIMEFAST, EXPORT_BIAS, FILL, P1_TARGET, SLEEP_AFTER, READY_HOURS.
Own use of an idle inverter (1.5.9): IDLE_AWAKE_W (default 30) awake, IDLE_ASLEEP_W (default 5) asleep. SLEEP_AFTER=<min>
lets idle inverters sleep (the setting of the integration), WAKE_S (default 2) is the extra delay of a sleeping inverter
before it follows a command. READY_HOURS=7-9,12-13,17-19 keeps the inverter with the discharge role awake only in those hours
(day scenario; READY_HOURS=none: never).
Optional: CROSS=1 prints every moment one device charges while another discharges, TRACE=<from s>,<to s> prints the P1 value and every device per 0.5 s in that window,
STEPS=1 prints every command that changes the power of a device by more than 1200 W.

The real ZendureManager.powerChanged / power_charge / power_discharge of the version are used. Only the
physical world is simulated, identically for both versions:
  * house load (base load + appliances), solar production, P1 meter reporting every second
  * 3 inverters (2400 W charge/discharge, 2.88 kWh) whose real power follows the command after 0.5 s
    latency with a 0.8 s time constant; SoC limits 10..100 %
The device-level command handling of each version is reproduced (1.4.4: tolerance + direct command,
smartMode 0 on a 0 W command; 1.5.0: slew limiter, tolerance, smartMode 1).
"""

from __future__ import annotations

import asyncio
import json
import os
import math
import random
import sys
from collections import deque
from datetime import datetime, timedelta
from math import sqrt
from types import SimpleNamespace

TREE, VERSION, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, TREE)

from custom_components.zendure_ha.const import DeviceState, ManagerMode, SmartMode  # noqa: E402
from custom_components.zendure_ha.fusegroup import FuseGroup  # noqa: E402
from custom_components.zendure_ha.manager import ZendureManager  # noqa: E402

NEW = not VERSION.startswith("1.4")  # 1.5.x and later: slew limiter, smartMode 1
if NEW:
    from custom_components.zendure_ha.dispatch import RoleSelector
    from custom_components.zendure_ha.slew import SlewLimiter

DT = 0.1
SCENARIO = os.environ.get("SCENARIO", "base")  # base | ramp | clouds | file
PV_PEAK = float(os.environ.get("PV_PEAK", "4500"))
P1_INTERVAL = float(os.environ.get("P1_INTERVAL", "1"))  # s between two P1 updates in Home Assistant  # W, peak of the PV in the clouds scenario
T0 = datetime(2026, 6, 1, 10, 0, 0)


FILE_INFO: dict[str, object] = {"socs": [], "setpoint": []}


def _read_p1_file(path: str) -> list[float]:
    """Net demand of the house per second (P1 + batteries) from a P1 recording (p1_<date>.csv or .csv.gz).

    The columns are read by name, so the 1.5.5 format (time;unix;p1_w;battery_w;source) and the 1.5.7 format
    with the control state both work. With the control state the start SoC of the devices (d1_soc, ...) and the
    recorded setpoint (setpoint_w, compared with the simulated one) are used too.
    FROM / TO (HH:MM, local time of the recording) select a part of the day.
    """
    import gzip

    global T0
    begin, end = os.environ.get("FROM"), os.environ.get("TO")
    rows: list[tuple[datetime, float, float | None]] = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        names = f.readline().strip().split(";")
        col = {n: i for i, n in enumerate(names)}
        for line in f:
            parts = line.rstrip("\n").split(";")
            if len(parts) < len(names):
                continue
            when = datetime.fromisoformat(parts[col["time"]]).replace(tzinfo=None)
            if (begin and when.strftime("%H:%M") < begin) or (end and when.strftime("%H:%M") >= end):
                continue
            if "net_w" in col and parts[col["net_w"]] != "":
                demand = float(parts[col["net_w"]])
            else:
                demand = float(parts[col["p1_w"]]) + float(parts[col["battery_w"]])
            setpoint = float(parts[col["setpoint_w"]]) if "setpoint_w" in col and parts[col["setpoint_w"]] != "" else None
            if not rows:
                FILE_INFO["socs"] = [float(parts[col[f"d{i}_soc"]]) for i in range(1, 10) if f"d{i}_soc" in col and parts[col[f"d{i}_soc"]] != ""]
            rows.append((when, demand, setpoint))
    if not rows:
        raise SystemExit(f"no P1 values in {path} (FROM={begin}, TO={end})")
    T0 = rows[0][0]
    out: list[float] = []
    setpoints: list[float | None] = []
    for when, demand, setpoint in rows:  # one value per second; a gap keeps the last value
        second = int((when - T0).total_seconds())
        while len(out) <= second:
            out.append(demand)
            setpoints.append(setpoint)
        out[second] = demand
        setpoints[second] = setpoint
    FILE_INFO["setpoint"] = setpoints
    return out


FILE_DEMAND = _read_p1_file(os.environ["P1FILE"]) if SCENARIO == "file" else []
DURATION = len(FILE_DEMAND) if SCENARIO == "file" else {"base": 4, "ramp": 8, "clouds": 4, "hob": 3, "day": 24}[SCENARIO] * 3600
if SCENARIO == "day":
    T0 = datetime(2026, 11, 4, 0, 0, 0)
SEASON = os.environ.get("SEASON", "nov")
# inverter model: LATENCY s until a command takes effect, then a first order response (TAU s), at most RAMP_W W/s
# (0 = no limit) and a report every REPORT_S s (0 = at every cycle). Measured on 2 November 2026 (SolarFlow
# 2400 AC+): about LATENCY=1.5 RAMP_W=250 REPORT_S=3.
LATENCY = float(os.environ.get("LATENCY", "0.5"))
TAU = float(os.environ.get("TAU", "0.8"))
RAMP_W = float(os.environ.get("RAMP_W", "0"))
REPORT_S = float(os.environ.get("REPORT_S", "0"))
CAPACITY_WH = float(os.environ.get("CAPACITY", "5280" if SCENARIO == "day" else "2880"))
IDLE_AWAKE_W = float(os.environ.get("IDLE_AWAKE_W", "30"))
IDLE_ASLEEP_W = float(os.environ.get("IDLE_ASLEEP_W", "5"))
SLEEP_AFTER = float(os.environ.get("SLEEP_AFTER", "0"))  # minutes, 0 = off
WAKE_S = float(os.environ.get("WAKE_S", "2"))
READY_HOURS = [] if os.environ.get("READY_HOURS", "") in ("", "none") else [tuple(int(h) for h in w.split("-")) for w in os.environ["READY_HOURS"].split(",")]
READY_NEVER = os.environ.get("READY_HOURS", "") == "none"
MODE_SWITCH = float(os.environ.get("MODE_SWITCH", "1.0"))
CONFIRM = float(os.environ.get("CONFIRM", "0"))
LOSS = os.environ.get("LOSS", "1") != "0"  # inverter losses are on by default since 1.6.2 (LOSS=0: as before)
ASSUME_ZERO = float(os.environ.get("ASSUME_ZERO", "0"))
# 1.6.0: a P1 jump of JUMP_W W since the last cycle starts a cycle at once (0 = off, as before 1.6.0);
# JUMP_SPLIT=<W> shares a jump of at least that much over two inverters
if "JUMP_W" in os.environ:
    SmartMode.JUMP_W = int(os.environ["JUMP_W"])
if "JUMP_SPLIT" in os.environ:
    SmartMode.JUMP_SPLIT_W = int(os.environ["JUMP_SPLIT"])
SIM = {"t": 0.0}
STATS = {"idle_wh": 0.0, "asleep_s": 0.0, "awake_idle_s": 0.0, "wakes": 0, "loss_wh": 0.0, "reversals_under_load": 0, "reversal_w": 0.0}
TRACE = tuple(float(x) for x in os.environ["TRACE"].split(",")) if os.environ.get("TRACE") else None


def clock() -> float:
    return SIM["t"]


# ----------------------------------------------------------------------------------------------
# the house
# ----------------------------------------------------------------------------------------------
rng = random.Random(42)
NOISE = [rng.gauss(0, 20) for _ in range(DURATION + 2)]

# (start minute, duration minutes, watt, name)
BASE_EVENTS = [
    (20, 3, 2200, "waterkoker"),
    (40, 1, 1400, "koffie"),
    (75, 3, 2200, "waterkoker"),
    (90, 15, 2000, "wasmachine"),
    (100, 1, 1400, "koffie"),
    (130, 3, 2200, "waterkoker"),
    (160, 15, 3500, "oven"),
    (170, 3, 2200, "waterkoker"),
    (195, 3, 2200, "waterkoker"),
    (215, 12, 3500, "oven"),
    (220, 1, 1400, "koffie"),
]
CLOUDS = [(55, 3), (68, 2), (110, 4), (122, 1), (140, 3)]  # (start minute, minutes)

# ramp: 8 hours, PV builds up slowly from 0 to 4500 W (morning) and goes down again (afternoon), no clouds;
# appliances at low, medium and high PV
RAMP_EVENTS = [
    (30, 3, 2200, "waterkoker (PV laag)"),
    (75, 1, 1400, "koffie (PV opbouwend)"),
    (120, 15, 2000, "wasmachine (PV opbouwend)"),
    (180, 3, 2200, "waterkoker (PV hoog)"),
    (300, 3, 2200, "waterkoker (PV afbouwend)"),
    (380, 12, 3500, "oven (PV laag)"),
    (430, 1, 1400, "koffie (na zonsondergang)"),
]
# clouds: 4 hours of PV around midday with fast changing clouds (sun 20-120 s, cloud 15-90 s, 3-15 s edges)
CLOUD_EVENTS = [
    (25, 3, 2200, "waterkoker"),
    (70, 1, 1400, "koffie"),
    (100, 15, 2000, "wasmachine"),
    (160, 3, 2200, "waterkoker"),
    (200, 12, 3500, "oven"),
]
# day: 24 hours from midnight (start minute of the day, minutes, W, name); the hob pulses at lunch and dinner come on top
DAY_EVENTS = [
    (7 * 60 + 10, 3, 2200, "waterkoker"), (7 * 60 + 30, 1, 1400, "koffie"), (7 * 60 + 45, 3, 900, "broodrooster"),
    (8 * 60 + 15, 1, 1400, "koffie"), (10 * 60, 15, 2000, "wasmachine"), (10 * 60 + 45, 1, 1400, "koffie"),
    (12 * 60 + 45, 3, 2200, "waterkoker"), (14 * 60, 20, 1200, "stofzuiger"), (15 * 60 + 30, 3, 2200, "waterkoker"),
    (16 * 60 + 15, 1, 1400, "koffie"), (18 * 60, 30, 2000, "oven"), (19 * 60 + 30, 20, 2000, "vaatwasser"),
    (21 * 60, 3, 2200, "waterkoker"),
]  # fmt: skip
EVENTS = {"base": BASE_EVENTS, "ramp": RAMP_EVENTS, "clouds": CLOUD_EVENTS, "file": [], "hob": [], "day": DAY_EVENTS}[SCENARIO]
# hob: 3 hours, a hob at half power (2200 W, 25-35 s on in every minute) for half an hour at lunch and at dinner,
# PV_LEVEL W of sun (0 = evening), with HOB_CLOUDS=1 changing clouds on that sun
HOB_SHIFT = float(os.environ.get("HOB_SHIFT", "0"))  # s: shifts the hob pulses against the control cycles
HOB_WINDOWS = [(12 * 3600 + 600, 12 * 3600 + 2400), (17 * 3600 + 1800, 18 * 3600 + 900)] if SCENARIO == "day" else [(20 * 60, 50 * 60), (110 * 60, 140 * 60)]
HOB_WINDOWS = [(a + HOB_SHIFT, b + HOB_SHIFT) for a, b in HOB_WINDOWS]
PV_LEVEL = float(os.environ.get("PV_LEVEL", "0"))


def hob(t: float) -> float:
    for a, b in HOB_WINDOWS:
        if a <= t < b:
            k = int((t - a) // 60)
            on = 25 + (k * 37 % 11)
            return 2200.0 if (t - a) % 60 < on else 0.0
    return 0.0


# Forecast of a load (a "power state" sensor of the hob or oven that announces the power before it is drawn):
# FORECAST=pulse (every hob pulse and the oven are announced) or session (only the first pulse of a cooking session
# and the oven), FORECAST_LEAD=<s> or <min>-<max> (random per event), FORECAST_OFF=1 also announces the end, FORECAST_SCALE=<0..1>
# uses only that part of the announced power.
# The announced power is added to the P1 value the manager sees until the load is really there (feedforward).
def _forecast() -> list[float]:
    mode = os.environ.get("FORECAST", "")
    out = [0.0] * (DURATION + 2)
    if not mode:
        return out
    lead_txt = os.environ.get("FORECAST_LEAD", "10")
    lo, hi = (float(x) for x in lead_txt.split("-")) if "-" in lead_txt else (float(lead_txt), float(lead_txt))
    r = random.Random(11)
    scale = float(os.environ.get("FORECAST_SCALE", "1"))  # use only this part of the announced power
    loads: list[tuple[float, float, float]] = []  # (on, off, W)
    for a, b in HOB_WINDOWS:
        pulses = []
        t = a
        while t < b:
            k = int((t - a) // 60)
            pulses.append((t, min(b, t + 25 + (k * 37 % 11)), 2200.0))
            t += 60
        loads += pulses if mode == "pulse" else pulses[:1]
    loads += [(start * 60, (start + dur) * 60, watt) for start, dur, watt, name in EVENTS if name == "oven"]
    for on, off, watt in loads:
        lead = r.uniform(lo, hi)
        for sec in range(max(0, int(on - lead)), min(len(out), int(on))):
            out[sec] += watt * scale  # announced, not drawn yet
        if os.environ.get("FORECAST_OFF"):
            lead = r.uniform(lo, hi)
            for sec in range(max(0, int(off - lead)), min(len(out), int(off))):
                out[sec] -= watt * scale  # the end is announced, still drawn
    return out


def _cloud_profile() -> list[float]:
    """Sun factor per second: alternating sun and cloud with ramps at the cloud edges."""
    r = random.Random(7)
    out: list[float] = []
    level = 1.0
    while len(out) < DURATION + 2:
        sunny = level < 0.6
        target = 1.0 if sunny else r.uniform(0.15, 0.45)
        edge = r.randint(3, 15)
        for k in range(edge):
            out.append(level + (target - level) * (k + 1) / edge)
        level = target
        out.extend([level] * (r.randint(20, 120) if sunny else r.randint(15, 90)))
    return out


FORECAST: list[float] = []
DAY_SUN = {
    "nov": (8.25, 16.75, float(os.environ.get("PV_PEAK", "1500"))),
    "spring": (6.5, 20.5, float(os.environ.get("PV_PEAK", "3000"))),
    "summer": (6.0, 21.5, float(os.environ.get("PV_PEAK", "4500"))),
}
SUN = _cloud_profile() if SCENARIO in ("clouds", "day") or (SCENARIO == "hob" and os.environ.get("HOB_CLOUDS")) else []


def _weather() -> tuple[list[float], list[str]]:
    """Sun factor per second for the day scenario and the regime per half hour (clear / overcast / broken)."""
    n = DURATION + 2
    if SCENARIO != "day":
        return [], []
    if os.environ.get("DAY_WEATHER", "") != "mixed":
        return [0.4 + 0.6 * SUN[k] for k in range(n)], ["broken"] * (n // 1800 + 1)
    r = random.Random(23)
    regimes: list[str] = []
    out: list[float] = []
    regime = "clear"
    for half in range(n // 1800 + 1):
        if half % 2 == 0:  # the weather changes at most every hour
            regime = r.choices(["clear", "overcast", "broken"], weights=[4, 3, 3])[0]
        regimes.append(regime)
        for k in range(1800):
            t = half * 1800 + k
            if t >= n:
                break
            if regime == "clear":
                out.append(1.0)
            elif regime == "overcast":
                out.append(0.25 + 0.05 * math.sin(t / 600))
            else:
                out.append(0.4 + 0.6 * SUN[t])
    return out, regimes


WEATHER, REGIMES = _weather()
FC_BIAS = float(os.environ.get("FC_BIAS", "1"))
FC_FILL = bool(os.environ.get("FC_FILL"))
FC_CLOUD = os.environ.get("FC_CLOUD", "")
_r_conf = random.Random(31)
_err = float(os.environ.get("FC_CONF_ERR", "0"))
# forecast confidence per half hour (like the Solcast analysis.intervals): low for changing clouds
CONFIDENCE = []
for _reg in REGIMES:
    _c = {"clear": 0.85, "overcast": 0.7, "broken": 0.2}[_reg]
    if _r_conf.random() < _err:
        _c = 0.9 if _c < 0.5 else 0.2  # a wrong forecast
    CONFIDENCE.append(_c)
PV_HOURS: list[float] = []  # Wh of real PV per hour (the forecast is FC_BIAS x this)


def forecast_remaining(t: float, factor: float) -> float:
    """Forecast PV (Wh) from t to the end of the day, factor 0.6 = p10, 1.4 = p90 (hourly values, the current hour pro rata)."""
    if not PV_HOURS:
        PV_HOURS.extend(sum(solar(h * 3600 + k) for k in range(0, 3600, 10)) * 10 / 3600 for h in range(24))
    h = int(t // 3600)
    if h >= 24:
        return 0.0
    rest = PV_HOURS[h] * (1 - (t % 3600) / 3600) + sum(PV_HOURS[h + 1 :])
    return rest * FC_BIAS * factor


def forecast_confidence(t: float) -> float:
    return CONFIDENCE[min(int(t // 1800), len(CONFIDENCE) - 1)] if CONFIDENCE else 1.0


def load(t: float) -> float:
    if SCENARIO == "day":
        h = t / 3600
        w = (180 if h < 7 or h >= 23 else 400 if h >= 19 else 250) + NOISE[int(t)] + hob(t)
        if int(t // 600) % 2 == 0 and (t % 600) < 240:  # fridge
            w += 90
        for start, dur, watt, _ in EVENTS:
            if start * 60 <= t < (start + dur) * 60:
                w += watt
        return max(0.0, w)
    if SCENARIO == "hob":
        return 300 + NOISE[int(t)] + hob(t)
    if SCENARIO == "file":
        return max(0.0, FILE_DEMAND[min(int(t), len(FILE_DEMAND) - 1)])
    w = 250 + NOISE[int(t)]
    if int(t // 600) % 2 == 0 and (t % 600) < 240:  # fridge
        w += 90
    for start, dur, watt, _ in EVENTS:
        if start * 60 <= t < (start + dur) * 60:
            w += watt
    return max(0.0, w)


def solar(t: float) -> float:
    if SCENARIO == "day":
        rise, setting, peak = DAY_SUN[SEASON]
        h = t / 3600
        if not rise <= h < setting:
            return 0.0
        return peak * math.sin(math.pi * (h - rise) / (setting - rise)) * WEATHER[int(t)]
    if SCENARIO == "hob":
        return PV_LEVEL * (SUN[int(t)] if SUN else 1.0)
    if SCENARIO == "file":
        return max(0.0, -FILE_DEMAND[min(int(t), len(FILE_DEMAND) - 1)])
    if SCENARIO == "ramp":
        return 4500 * max(0.0, math.sin(math.pi * t / (7 * 3600)))  # 0 -> 4500 -> 0 W in 7 hours, then dark
    if SCENARIO == "clouds":
        return PV_PEAK * SUN[int(t)]
    m = t / 60
    if m < 60:
        w = 4500 * m / 60
    elif m < 150:
        w = 4500
    elif m < 180:
        w = 4500 * (180 - m) / 30
    else:
        w = 0
    for start, dur in CLOUDS:
        if start <= m < start + dur:
            w *= 0.3
    return w


# ----------------------------------------------------------------------------------------------
# devices
# ----------------------------------------------------------------------------------------------
class Value:
    def __init__(self, v: float = 0) -> None:
        self.v = v

    @property
    def asInt(self) -> int:  # noqa: N802
        return int(self.v)

    @property
    def asNumber(self) -> float:  # noqa: N802
        return self.v

    def update_value(self, v: float) -> None:
        self.v = v


class Device:
    def __init__(self, name: str, soc: float) -> None:
        self.name = self.deviceId = name
        self.soc_wh = CAPACITY_WH * soc / 100
        self.actual = 0.0
        self.queue: deque[tuple[float, float]] = deque()
        self.target = 0.0
        self.command_value = 0
        self.effective = 0.0
        # what powerChanged reads
        self.homeInput, self.homeOutput = Value(), Value()
        self.batteryInput, self.batteryOutput = Value(), Value()
        self.solarInput = Value()
        self.electricLevel = Value(soc)
        self.byPass = Value()
        self.limitInput, self.limitOutput = Value(), Value()
        self.pwr_offgrid = 0
        self.pwr_produced = 0
        self.exports_bypass = False
        self.kWh = CAPACITY_WH / 1000
        self.charge_limit, self.discharge_limit = -2400, 2400
        self.charge_optimal, self.discharge_optimal = -600, 600
        self.charge_start, self.discharge_start = -240, 240
        self.pwr_max = 0
        self.state = DeviceState.INACTIVE
        # statistics
        self.commands = 0
        self.flash = 0
        self.max_step = 0
        self.big_steps = 0
        self.reversals = 0
        self.starts = 0
        self._last_cmd_dir = 0
        self.start_times: list[float] = []
        self.slew = SlewLimiter(clock) if NEW else SimpleNamespace(pending=False)
        if NEW and hasattr(self.slew, "ready_up"):
            self.slew.ready_up = 1200  # 1.5.x: the integration still limited the ramp
        self.asleep = False
        self.sleeps = 0
        self.minSoc, self.socSet = Value(10), Value(100)  # the SoC limits of the simulated battery (10..100 %)
        self.ac_mode: int | None = None  # 1 input, 2 output (None: not known yet)
        self.confirm_until = -1.0
        self.mode_switches = 0

    # -- physics --------------------------------------------------------------------------
    def step(self) -> None:
        while self.queue and self.queue[0][0] <= SIM["t"]:
            self.target = self.queue.popleft()[1]
        soc = self.soc_wh / CAPACITY_WH * 100
        target = self.target
        if target > 0 and soc <= 10:
            target = 0
        if target < 0 and soc >= 100:
            target = 0
        self.effective = target
        before = self.actual
        self.actual += (target - self.actual) * (1 - math.exp(-DT / TAU))
        if RAMP_W:
            self.actual = before + max(-RAMP_W * DT, min(RAMP_W * DT, self.actual - before))
        if abs(before) < 10 <= abs(self.actual):
            self.starts += 1
            self.start_times.append(round(SIM["t"] / 60, 1))
        # a new command in the other direction while the inverter still delivers power: the relay may switch under load
        if self.target * self.actual < 0 and abs(self.actual) > 50 and self.target != getattr(self, "_reversal_target", None):
            self._reversal_target = self.target
            STATS["reversals_under_load"] += 1
            STATS["reversal_w"] += abs(self.actual)
        self.ac_draw = 0.0
        if LOSS and abs(self.actual) >= 10:
            a = abs(self.actual)
            loss = 33 + 0.026 * a if self.actual > 0 else 0.048 * a
            STATS["loss_wh"] += loss * DT / 3600
            self.soc_wh = min(CAPACITY_WH, max(0.0, self.soc_wh - (self.actual + loss) * DT / 3600))
        else:
            self.soc_wh = min(CAPACITY_WH, max(0.0, self.soc_wh - self.actual * DT / 3600))
        if abs(self.actual) < 10:  # own use of an idle inverter
            idle = IDLE_ASLEEP_W if self.asleep else IDLE_AWAKE_W
            STATS["idle_wh"] += idle * DT / 3600
            STATS["asleep_s" if self.asleep else "awake_idle_s"] += DT
            if LOSS:
                self.ac_draw = idle  # from the grid: it shows in P1
                STATS["loss_wh"] += idle * DT / 3600

    def read(self) -> None:
        since = SIM["t"] - getattr(self, "_read_t", -1e9)
        fast = CONFIRM and SIM["t"] < self.confirm_until and since >= CONFIRM
        if REPORT_S and since < REPORT_S and not fast:
            return  # the device reports only every REPORT_S s
        self._read_t = SIM["t"]
        self.mqtt_last = SIM["t"]  # time of the last report (like ZendureDevice.mqttProperties)
        a = int(round(self.actual))
        self.homeOutput.v, self.homeInput.v = max(0, a), max(0, -a)
        self.batteryOutput.v, self.batteryInput.v = self.homeOutput.v, self.homeInput.v
        soc = self.soc_wh / CAPACITY_WH * 100
        self.electricLevel.v = int(soc)
        self.state = DeviceState.SOCFULL if soc >= 100 else DeviceState.SOCEMPTY if soc <= 10 else DeviceState.ACTIVE if abs(a) > 0 else DeviceState.INACTIVE

    @property
    def actualKwh(self) -> float:  # noqa: N802
        return self.soc_wh / 1000

    async def power_get(self) -> bool:
        self.read()
        if NEW:
            measured = self.homeOutput.asInt - self.homeInput.asInt
            self.slew.sync(measured)
            self.slew.measure(measured)
            # same as ZendureDevice._follow_battery_limit in 1.5.0
            if self.state == DeviceState.SOCFULL and measured > -10 and (self.slew.current <= -10 or self.limitInput.asInt > 0):
                self.slew.follow(measured)
                if self.limitInput.asInt > 0:
                    await self.charge(0)
            elif self.state == DeviceState.SOCEMPTY and measured < 10 and (self.slew.current >= 10 or self.limitOutput.asInt > 0):
                self.slew.follow(measured)
                if self.limitOutput.asInt > 0:
                    await self.discharge(0)
        return True

    # -- commands (device level of each version) -------------------------------------------
    def _send(self, power: int, input_: int, output: int) -> int:
        self.commands += 1
        soc = self.soc_wh / CAPACITY_WH * 100
        will_run = 0 if (power < 0 and soc >= 100) or (power > 0 and soc <= 10) else power
        # what the device really does: a full battery stops charging on its own, that is no command step
        step = abs(will_run - self.effective) if (will_run == power and self.effective == self.target) else 0
        self.max_step = max(self.max_step, int(step))
        if step > 1200 and os.environ.get("STEPS"):
            print(f"step {self.name} t={SIM['t']:.1f} {self.command_value} -> {power} actual={self.actual:.0f} eff={self.effective:.0f} soc={self.soc_wh / CAPACITY_WH * 100:.3f} state={self.state} slew={getattr(self.slew, 'current', None)}")
        if step > 600:
            self.big_steps += 1
        direction = (power > 0) - (power < 0)
        if direction and self._last_cmd_dir and direction != self._last_cmd_dir:
            self.reversals += 1
        if direction:
            self._last_cmd_dir = direction
        self.command_value = power
        self.command_time = SIM["t"]
        self.limitInput.v, self.limitOutput.v = input_, output
        wake = 0.0
        direction = (power > 0) - (power < 0)
        if direction:
            mode = 2 if direction > 0 else 1
            if self.ac_mode is not None and self.ac_mode != mode:
                wake += MODE_SWITCH  # the AC mode changes before the new limit applies
                self.mode_switches += 1
            self.ac_mode = mode
        elif abs(self.actual) >= 10:
            self.confirm_until = SIM["t"] + 6  # a stop: read again soon to confirm 0 W (CONFIRM)
        if self.asleep:
            self.asleep = False  # every command sets smartMode 1 again
            if power:
                wake = WAKE_S
                STATS["wakes"] += 1
        # 1.4.4 writes a 0 W command with smartMode 0 (flash); 1.5.0 always smartMode 1 (RAM)
        if not NEW and power == 0:
            self.flash += 1
        self.queue.append((SIM["t"] + LATENCY + wake, power))
        return power

    async def prepare(self, direction: int) -> bool:
        """Like ZendureZenSdk.prepare: the AC mode of the role, at 0 W, only when idle."""
        mode = 1 if direction < 0 else 2
        if abs(self.actual) >= 10 or self.command_value or self.slew.pending or not self.slew.flip_released() or self.ac_mode == mode:
            return False
        self.ac_mode = mode
        self.commands += 1
        return True

    async def wake(self) -> bool:
        """Like ZendureZenSdk.wake (1.6.3): smartMode 1 at 0 W."""
        if not self.asleep:
            return False
        self.asleep = False
        self.commands += 1
        STATS["ready_wakes"] = STATS.get("ready_wakes", 0) + 1
        return True

    async def sleep(self) -> bool:
        """Like ZendureZenSdk.sleep (1.5.9): smartMode 0 for an idle inverter."""
        if self.asleep or self.command_value:
            return False
        self.asleep = True
        self.sleeps += 1
        return True

    async def charge(self, power: int) -> int:
        if power == -SmartMode.POWER_START and self.limitInput.asInt >= SmartMode.POWER_START and self.homeInput.asInt == 0:
            power = -min(self.limitInput.asInt + 4, 2 * SmartMode.POWER_START)
        return self._send(power, -power, 0)

    async def discharge(self, power: int) -> int:
        if power == SmartMode.POWER_START and self.limitOutput.asInt >= SmartMode.POWER_START and self.homeOutput.asInt == 0:
            power = min(self.limitOutput.asInt + 4, 2 * SmartMode.POWER_START)
        return self._send(power, 0, power)

    async def _apply_charge(self, power: int) -> int:
        if abs(power + self.homeInput.asInt - self.homeOutput.asInt) <= SmartMode.POWER_TOLERANCE:
            return -self.homeInput.asInt
        return await self.charge(power)

    async def _apply_discharge(self, power: int) -> int:
        if abs(power - self.homeOutput.asInt + self.homeInput.asInt) <= SmartMode.POWER_TOLERANCE:
            return self.homeOutput.asInt
        return await self.discharge(power)

    async def power_charge(self, power: int) -> int:
        power = min(0, max(power, self.charge_limit))
        if NEW:
            power = self.slew.limit(power)
            if power > 0:
                await self._apply_discharge(power)
                return 0
        return await self._apply_charge(power)

    async def power_discharge(self, power: int, ready: bool = False) -> int:
        power = max(0, min(power, self.discharge_limit))
        if NEW:
            power = self.slew.limit(power, ready=ready) if hasattr(self.slew, "ready_up") else self.slew.limit(power)
            if power < 0:
                await self._apply_charge(power)
                return 0
        return await self._apply_discharge(power)


# ----------------------------------------------------------------------------------------------
# manager harness: the real methods of the version on a bare object
# ----------------------------------------------------------------------------------------------
def make_manager(devices: list[Device]) -> ZendureManager:
    m = object.__new__(ZendureManager)
    for name in (
        "operationstate", "power", "availableKwh", "globalSoc", "devicesOnline", "devicesCharging", "devicesDischarging",
        "devicesIdle", "dispatchSetpoint", "roleDischarge", "roleCharge",
    ):  # fmt: skip
        setattr(m, name, Value())
    m.devices = devices
    m.fuseGroups = [FuseGroup(d.name, 2400, -2400, [d]) for d in devices]
    m.operation = ManagerMode.MATCHING
    m.charge, m.discharge, m.idle = [], [], []
    m.charge_time, m.charge_last = datetime.max, datetime.min
    m.pwr_low = 0
    if NEW:
        from custom_components.zendure_ha.dispatch import P1Tracker

        m.p1tracker = P1Tracker()
        m.last_cycle = None
        m.roles = RoleSelector(10, 300, clock)
        m.role_margin = 10
        m.peak_limit = 0
    return m


def reset(m: ZendureManager) -> None:
    m.charge.clear()
    m.charge_limit = m.charge_optimal = m.charge_weight = 0
    m.discharge.clear()
    m.discharge_bypass = m.discharge_limit = m.discharge_optimal = m.discharge_produced = m.discharge_weight = 0
    m.idle.clear()
    m.idle_lvlmax, m.idle_lvlmin = 0, 100
    m.produced = 0
    for fg in m.fuseGroups:
        fg.initPower = True


async def main() -> None:
    socs = list(FILE_INFO["socs"]) if SCENARIO == "file" else []  # start SoC from a recording with the control state
    if os.environ.get("DAY_SOC"):
        socs = [float(x) for x in os.environ["DAY_SOC"].split(",")]
    devices = [Device(name, socs[i] if i < len(socs) else soc) for i, (name, soc) in enumerate((("A", 35), ("B", 50), ("C", 65)))]
    m = make_manager(devices)
    if NEW and "ready2" in os.environ.get("VARIANT", "").split(","):
        # two inverters share the load (fill level 1 %) from 5 minutes before to 5 minutes after cooking (hob scenario)
        from custom_components.zendure_ha.manager import ZendureManager as _Manager

        _fill = _Manager.effective_fill

        def _ready2(self: _Manager) -> int:
            if any(a - 300 <= SIM["t"] < b + 300 for a, b in HOB_WINDOWS):
                return 1
            return _fill.fget(self)

        _Manager.effective_fill = property(_ready2)
    # VARIANT=noC,noE,noFast switches single options off, to measure what each one adds
    variant = set(os.environ.get("VARIANT", "").split(","))
    if NEW:
        m.p1_target = int(os.environ.get("P1_TARGET", "0"))  # W, the P1 target band (1.5.8)
        m.sleep_after = SLEEP_AFTER * 60
        m.cloud_reserve = getattr(m, "cloud_reserve", None)
        if SLEEP_AFTER:
            import custom_components.zendure_ha.manager as manager_module

            manager_module.monotonic = clock  # _sleep_idle compares with the command time of the simulation
    for v in variant:
        if NEW and v.startswith("hyst"):  # hystN: charge delay N s instead of 60 s
            SmartMode.CHARGE_DELAY = int(v[4:])
        if NEW and v.startswith("chyst"):  # chystN: charge delay N s in cloud mode
            SmartMode.CLOUD_CHARGE_DELAY = int(v[5:])
        if NEW and v.startswith("mshare"):  # mshareN: export margin at most N % of the peak surplus
            SmartMode.CLOUD_MARGIN_SHARE = int(v[6:]) / 100
        if NEW and v.startswith("cstart"):  # cstartN: start threshold N W in cloud mode
            SmartMode.CLOUD_POWER_START = int(v[6:])
        if NEW and v == "nodis":  # no export margin while discharging
            SmartMode.CLOUD_MARGIN_DISCHARGE = False
        if NEW and v == "oldcloud":  # 1.5.3 detection: fixed 800 W edges, also without sun
            import custom_components.zendure_ha.dispatch as dispatch_module

            dispatch_module.EDGE_MIN = 800
            dispatch_module.SUN_MIN = 0
        if NEW and v.startswith("start"):  # startN: POWER_START N W
            SmartMode.POWER_START = int(v[5:])
        if NEW and v.startswith("edge"):  # edgeN: a cloud edge from N W
            import custom_components.zendure_ha.dispatch as dispatch_module

            dispatch_module.VOLATILE_EDGE = int(v[4:])
    if NEW and "fast1" in variant:
        SmartMode.FAST_INTERVAL = 1.0
    if NEW and "fill70" in variant:
        m.fill_level = 70
    if NEW and ASSUME_ZERO:
        def _released(self: SlewLimiter, now: float | None = None) -> bool:
            if self._zero_since is None:
                return True
            return (self._clock() if now is None else now) - self._zero_since >= ASSUME_ZERO

        SlewLimiter.flip_released = _released
    if NEW and os.environ.get("CLOUD_OFF"):  # 1.6.5: edges at or below which the changing clouds mode ends
        import custom_components.zendure_ha.dispatch as _dispatch

        _dispatch.VOLATILE_OFF_EDGES = int(os.environ["CLOUD_OFF"])
    if NEW and os.environ.get("ROLE_MARGIN"):
        m.roles.margin = m.role_margin = int(os.environ["ROLE_MARGIN"])
    if os.environ.get("TIMEZERO"):
        SmartMode.TIMEZERO = float(os.environ["TIMEZERO"])
    if os.environ.get("TIMEFAST"):
        SmartMode.TIMEFAST = float(os.environ["TIMEFAST"])
    if NEW and os.environ.get("EXPORT_BIAS"):
        m.export_bias = int(os.environ["EXPORT_BIAS"])
    if NEW and os.environ.get("FILL"):
        m.fill_level = int(os.environ["FILL"])
    if NEW and "oldE" in variant:
        # 1.5.1 before the big-surplus rule: the reserve never charges in cloud mode
        async def _always_reserve(*_args: object) -> None:
            m.cloud_reserve = m.roles.discharger if m.cloud_mode else None
            if m.cloud_reserve is None or len(m.charge) + len(m.idle) < 2:
                return
            for d in list(m.charge):
                if d.deviceId == m.cloud_reserve and d.state != DeviceState.SOCEMPTY:
                    m.charge.remove(d)
                    m.idle.append(d)
                    m.charge_limit -= d.pwr_max
                    m.charge_optimal -= d.charge_optimal
                    m.charge_weight -= d.pwr_max * (100 - d.electricLevel.asInt)
                    await d.power_charge(0)

        m._keep_cloud_reserve = _always_reserve
    if NEW and "noC" in variant:
        m.export_bias = 0
    if NEW and "noE" in variant:
        async def _no_reserve() -> None:
            m.cloud_reserve = None

        m._keep_cloud_reserve = _no_reserve
    FORECAST[:] = _forecast()
    history: deque[int] = deque([25, -25], maxlen=8)
    base_bias, base_target = getattr(m, "export_bias", 100), getattr(m, "p1_target", 0)
    if NEW and FC_CLOUD:
        from custom_components.zendure_ha.manager import ZendureManager as _Manager

        def _cloud(self: _Manager) -> bool:
            detected = self._volatility().active
            conf = forecast_confidence(SIM["t"])
            if FC_CLOUD == "early":
                return detected or conf < 0.4  # on as soon as changing clouds are forecast
            if FC_CLOUD == "gate":
                return detected and conf < 0.6  # never in a forecast clear or overcast half hour
            return conf < 0.4 or (detected and conf < 0.6)  # both

        _Manager.cloud_mode = property(_cloud)
    zero_next = zero_fast = -1.0
    followup = None
    p1 = 0.0
    p1_reported = 0

    stats = {"import_wh": 0.0, "export_wh": 0.0, "abs_wh": 0.0, "cycles": 0, "crossflow_s": 0.0, "active_device_s": 0.0,
             "charging_s": 0.0, "reserve_s": 0.0, "max_import": 0.0}  # fmt: skip
    p1_trace: list[float] = []
    room_trace: list[bool] = []

    async def run(isfast: bool) -> None:
        nonlocal zero_next, zero_fast, followup
        reset(m)
        if NEW and FC_FILL:
            # will the batteries be full today? p10 forecast minus what the house uses meanwhile, against the room
            daylight = max(0.0, min(24 * 3600, (DAY_SUN[SEASON][1]) * 3600) - SIM["t"]) / 3600
            surplus = forecast_remaining(SIM["t"], float(os.environ.get("FC_FILL_Q", "0.6"))) - 300 * daylight
            room = sum(CAPACITY_WH - d.soc_wh for d in devices)
            fills = surplus >= room
            if hasattr(m, "solar_fills"):
                m.solar_fills = fills  # 1.6.2: the integration switches the margins off itself
            else:
                m.export_bias = base_bias if fills else 0
                m.p1_target = base_target if fills else max(0, base_target)
            stats["fc_fill_s"] = stats.get("fc_fill_s", 0) + (1 if fills else 0)
        if NEW and os.environ.get("SUN_RULE") and SCENARIO == "day":
            m.sun_low = forecast_remaining(SIM["t"], 1.0) < 200
        m.last_cycle = T0 + timedelta(seconds=SIM["t"])
        await m.powerChanged(p1_reported, isfast, T0 + timedelta(seconds=SIM["t"]))
        stats["cycles"] += 1
        if NEW and SLEEP_AFTER and hasattr(m, "_sleep_idle"):
            # like _run_power_update: idle inverters may sleep; READY_HOURS keeps the discharger awake only then
            when = T0 + timedelta(seconds=SIM["t"])
            if hasattr(m, "ready_hours") and not READY_NEVER and os.environ.get("READY_OLD") is None:
                m.ready_hours = [(float(a), float(b)) for a, b in READY_HOURS]  # 1.6.3: the integration does it
                await m._sleep_idle(when)
            else:
                keep = not READY_NEVER and (not READY_HOURS or any(a <= when.hour < b for a, b in READY_HOURS))
                discharger = m.roles.discharger
                if not keep:
                    m.roles.discharger = None
                try:
                    await m._sleep_idle(when) if "time" in m._sleep_idle.__code__.co_varnames else await m._sleep_idle()
                finally:
                    m.roles.discharger = discharger
        recorded = FILE_INFO["setpoint"]
        if NEW and recorded and int(SIM["t"]) < len(recorded) and recorded[int(SIM["t"])] is not None:
            diff = abs(getattr(m, "last_setpoint", 0) - recorded[int(SIM["t"])])
            stats["setpoint_diff_sum"] = stats.get("setpoint_diff_sum", 0.0) + diff
            stats["setpoint_diff_n"] = stats.get("setpoint_diff_n", 0) + 1
        zero_next, zero_fast = SIM["t"] + SmartMode.TIMEZERO, SIM["t"] + SmartMode.TIMEFAST
        if NEW and followup is None:
            wants = m.followup_needed(p1_reported) if hasattr(m, "followup_needed") else any(d.slew.pending for d in devices)
            if wants:
                followup = SIM["t"] + SmartMode.RAMP_INTERVAL

    def jump(p1_value: int, now: float) -> bool:
        """Like ZendureManager._handle_p1 (1.6.0): a big jump starts a cycle at once, also inside the fast window."""
        if NEW and hasattr(m, "jump_check") and m.jump_check(p1_value, T0 + timedelta(seconds=now)):
            history.clear()
            history.append(p1_value)
            return True
        return False

    async def fast(p1_value: int, now: float) -> None:
        """Like ZendureManager._p1_changed: every P1 update without a control cycle goes to fast_track."""
        if NEW and hasattr(m, "fast_track") and "noFast" not in variant:
            await m.fast_track(p1_value, T0 + timedelta(seconds=now))

    m.zero_fast = datetime.min  # the simulation decides itself when a cycle runs
    steps = int(DURATION / DT)
    for i in range(steps):
        t = i * DT
        SIM["t"] = t
        for d in devices:
            d.step()
            if NEW and i % int(2 / DT) == 0:
                d.read()
        p1 = load(t) - solar(t) - sum(d.actual for d in devices) + sum(getattr(d, "ac_draw", 0.0) for d in devices)
        p1_trace.append(p1)
        if i % int(1 / DT) == 0:
            # can the batteries still take a rising sun? (needed to judge the rising cloud edges)
            room_trace.append(sum(CAPACITY_WH - d.soc_wh for d in devices) > 300)
        if TRACE and TRACE[0] <= t < TRACE[1] and i % 5 == 0:
            print(f"{t:7.1f} load {load(t):5.0f} sun {solar(t):5.0f} p1 {p1:6.0f} | " + " ".join(f"{d.name}:{d.actual:6.0f}/{d.command_value:6d}" for d in devices)
                  + (f" | dis={getattr(m.roles, 'discharger', None)} chg={getattr(m.roles, 'charger', None)}" if NEW else ""))
        stats["import_wh"] += max(0, p1) * DT / 3600
        stats["export_wh"] += max(0, -p1) * DT / 3600
        buckets = stats.setdefault("export_by_10min", [0.0] * (DURATION // 600 + 1))
        buckets[int(t // 600)] += max(0, -p1) * DT / 3600
        stats["abs_wh"] += abs(p1) * DT / 3600
        if NEW and i % int(1 / DT) == 0:
            dis = getattr(m.roles, "discharger", None)
            if dis != stats.get("_dis"):
                stats["role_changes"] = stats.get("role_changes", 0) + (1 if stats.get("_dis") is not None else 0)
                stats["_dis"] = dis
            on = bool(getattr(m, "cloud_mode", False))
            if on != stats.get("_cloud", False):
                stats["cloud_toggles"] = stats.get("cloud_toggles", 0) + 1
                stats["_cloud"] = on
            holder = next((d for d in devices if d.deviceId == dis), None)
            if holder is not None:
                soc = holder.soc_wh / CAPACITY_WH * 100
                best = max(d.soc_wh / CAPACITY_WH * 100 for d in devices)
                if soc < 20 and best - soc >= 5:
                    stats["reserve_low_s"] = stats.get("reserve_low_s", 0) + 1
        stats["max_import"] = max(stats["max_import"], p1)
        if NEW and getattr(m, "cloud_mode", False):
            stats["cloud_mode_s"] = stats.get("cloud_mode_s", 0.0) + DT
        charging = [d for d in devices if d.actual < -50]
        discharging = [d for d in devices if d.actual > 50]
        if charging and discharging:
            if os.environ.get("CROSS") and t - stats.get("_cross_last", -9.0) > 1:
                print(f"crossflow t={t:.1f} " + " ".join(f"{d.name}:{d.actual:.0f}/{d.command_value}" for d in devices))
            stats["_cross_last"] = t
            stats["crossflow_s"] += DT
        stats["active_device_s"] += DT * (len(charging) + len(discharging))
        if charging:
            stats["charging_s"] += DT
            if any(abs(d.actual) < 50 and d.soc_wh / CAPACITY_WH > 0.15 for d in devices):
                stats["reserve_s"] += DT

        # follow-up of a ramp (1.5.0)
        if followup is not None and t >= followup - 1e-9:
            followup = None
            await run(True)  # like _followup_run: it does not wait for the fast window

        # P1 meter reports every second (DSMR 5)
        if i % int(P1_INTERVAL / DT) == 0:
            p1_reported = int(p1 + FORECAST[min(int(t), len(FORECAST) - 1)])
            if t < zero_fast:
                history.append(p1_reported)
                if jump(p1_reported, t):
                    await run(True)
                    continue
                await fast(p1_reported, t)
                continue
            isfast = False
            if len(history) > 1:
                avg = int(sum(history) / len(history))
                stddev = SmartMode.P1_STDDEV_FACTOR * max(SmartMode.P1_STDDEV_MIN, sqrt(sum((x - avg) ** 2 for x in history) / len(history)))
                if abs(p1_reported - avg) > stddev or abs(p1_reported - history[0]) > stddev:
                    isfast = True
                    history.clear()
            history.append(p1_reported)
            if isfast or t > zero_next:
                await run(isfast)
            elif jump(p1_reported, t):
                await run(True)
            else:
                await fast(p1_reported, t)

    # peak events: time until the import is covered again, and the imported energy in the first minute
    peaks = []
    for start, dur, watt, name in EVENTS:
        s = int(start * 60 / DT)
        window = p1_trace[s : s + int(min(dur * 60, 120) / DT)]
        covered = next((k * DT for k, v in enumerate(window) if k * DT > 1 and v < 150), None)
        first_min = sum(max(0, v) for v in window[: int(60 / DT)]) * DT / 3600
        peaks.append({"event": f"{start}min {name} {watt}W", "covered_s": covered, "import_first_min_wh": round(first_min, 1)})

    # cloud edges: the sun changes by more than 800 W within 15 s; how fast is P1 back within +/-150 W?
    edges = {"falling": [], "rising": []}
    t = 0
    while t < DURATION - 75:
        delta = solar(t + 15) - solar(t)
        if abs(delta) > 800:
            kind = "falling" if delta < 0 else "rising"
            s0 = int(t / DT)
            window = p1_trace[s0 : s0 + int(75 / DT)]
            if room_trace[int(t)]:
                # from the moment P1 leaves +/-150 W until it stays within +/-150 W for 3 s again
                start = next((k for k, v in enumerate(window) if abs(v) >= 150), None)
                settle = 0.0
                if start is not None:
                    settle = None
                    for k in range(start, len(window) - int(3 / DT)):
                        if all(abs(v) < 150 for v in window[k : k + int(3 / DT)]):
                            settle = (k - start) * DT
                            break
                edges[kind].append({"t": round(t), "settle_s": settle, "dev_wh": round(sum(abs(v) for v in window[: int(60 / DT)]) * DT / 3600, 1)})
            t += 30
        else:
            t += 1

    out = {
        "version": VERSION,
        "edges": edges,
        "grid_import_wh": round(stats["import_wh"]),
        "grid_export_wh": round(stats["export_wh"]),
        "abs_p1_wh": round(stats["abs_wh"]),
        "max_import_w": round(stats["max_import"]),
        "cycles": stats["cycles"],
        "crossflow_s": round(stats["crossflow_s"]),
        "cloud_mode_min": round(stats.get("cloud_mode_s", 0) / 60),
        "active_device_h": round(stats["active_device_s"] / 3600, 2),
        "charging_s": round(stats["charging_s"]),
        "reserve_while_charging_pct": round(100 * stats["reserve_s"] / max(1, stats["charging_s"]), 1),
        "devices": {
            d.name: {
                "commands": d.commands,
                "flash_writes": d.flash,
                "max_step_w": d.max_step,
                "steps_over_600w": d.big_steps,
                "direction_changes": d.reversals,
                "relay_starts": d.starts,
                "mode_switches": d.mode_switches,
                "soc_end": round(d.soc_wh / CAPACITY_WH * 100, 1),
                "start_minutes": d.start_times,
            }
            for d in devices
        },
        "peaks": peaks,
        "setpoint_vs_recording_w": round(stats["setpoint_diff_sum"] / stats["setpoint_diff_n"]) if stats.get("setpoint_diff_n") else None,
        "idle_own_use_wh": round(STATS["idle_wh"]),
        "idle_awake_h": round(STATS["awake_idle_s"] / 3600, 2),
        "idle_asleep_h": round(STATS["asleep_s"] / 3600, 2),
        "sleeps": sum(d.sleeps for d in devices),
        "wakes": STATS["wakes"],
        "p1_jumps": getattr(m, "jump_total", 0),
        "inverter_loss_wh": round(STATS["loss_wh"]),
        "role_changes": stats.get("role_changes", 0),
        "cloud_toggles": stats.get("cloud_toggles", 0),
        "reserve_low_min": round(stats.get("reserve_low_s", 0) / 60),
        "reversals_under_load": STATS["reversals_under_load"],
        "reversal_avg_w": round(STATS["reversal_w"] / STATS["reversals_under_load"]) if STATS["reversals_under_load"] else 0,
        "export_by_10min": [round(x) for x in stats["export_by_10min"]],
    }
    with open(OUT, "w") as f:  # noqa: ASYNC230 - standalone script, nothing else runs on this loop
        json.dump(out, f, indent=1)


asyncio.run(main())

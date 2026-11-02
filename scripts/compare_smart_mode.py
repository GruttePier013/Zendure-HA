"""Compare the smart matching of Zendure-HA 1.4.4 and the 1.5.0 fork on the same simulated house.

Run once per version, pointing at that version's source tree (the folder that holds custom_components):
    python scripts/compare_smart_mode.py <path to 1.4.4 tree> 1.4.4 r144.json
    python scripts/compare_smart_mode.py . 1.5.0 r150.json
SCENARIO=base (default) | ramp (8 h PV build-up and decline, no clouds) | clouds (4 h, fast changing clouds).
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

NEW = VERSION == "1.5.0"
if NEW:
    from custom_components.zendure_ha.dispatch import RoleSelector
    from custom_components.zendure_ha.slew import SlewLimiter

DT = 0.1
SCENARIO = os.environ.get("SCENARIO", "base")  # base | ramp | clouds
DURATION = {"base": 4, "ramp": 8, "clouds": 4}[SCENARIO] * 3600
T0 = datetime(2026, 6, 1, 10, 0, 0)
LATENCY = 0.5
TAU = 0.8
CAPACITY_WH = 2880
SIM = {"t": 0.0}
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
EVENTS = {"base": BASE_EVENTS, "ramp": RAMP_EVENTS, "clouds": CLOUD_EVENTS}[SCENARIO]


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


SUN = _cloud_profile() if SCENARIO == "clouds" else []


def load(t: float) -> float:
    w = 250 + NOISE[int(t)]
    if int(t // 600) % 2 == 0 and (t % 600) < 240:  # fridge
        w += 90
    for start, dur, watt, _ in EVENTS:
        if start * 60 <= t < (start + dur) * 60:
            w += watt
    return max(0.0, w)


def solar(t: float) -> float:
    if SCENARIO == "ramp":
        return 4500 * max(0.0, math.sin(math.pi * t / (7 * 3600)))  # 0 -> 4500 -> 0 W in 7 hours, then dark
    if SCENARIO == "clouds":
        return 4500 * SUN[int(t)]
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
        if NEW:
            self.slew.ready_up = 1200

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
        if abs(before) < 10 <= abs(self.actual):
            self.starts += 1
            self.start_times.append(round(SIM["t"] / 60, 1))
        self.soc_wh = min(CAPACITY_WH, max(0.0, self.soc_wh - self.actual * DT / 3600))

    def read(self) -> None:
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
        self.limitInput.v, self.limitOutput.v = input_, output
        # 1.4.4 writes a 0 W command with smartMode 0 (flash); 1.5.0 always smartMode 1 (RAM)
        if not NEW and power == 0:
            self.flash += 1
        self.queue.append((SIM["t"] + LATENCY, power))
        return power

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
            power = self.slew.limit(power, ready=ready)
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
        m.roles = RoleSelector(10, 300, clock)
        m.role_margin = 10
        m.peak_limit = 0
        m.fill_level = 70
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
    devices = [Device("A", 35), Device("B", 50), Device("C", 65)]
    m = make_manager(devices)
    history: deque[int] = deque([25, -25], maxlen=8)
    zero_next = zero_fast = -1.0
    followup = None
    p1 = 0.0
    p1_reported = 0

    stats = {"import_wh": 0.0, "export_wh": 0.0, "abs_wh": 0.0, "cycles": 0, "crossflow_s": 0.0, "active_device_s": 0.0,
             "charging_s": 0.0, "reserve_s": 0.0, "max_import": 0.0}  # fmt: skip
    p1_trace: list[float] = []

    async def run(isfast: bool) -> None:
        nonlocal zero_next, zero_fast, followup
        reset(m)
        await m.powerChanged(p1_reported, isfast, T0 + timedelta(seconds=SIM["t"]))
        stats["cycles"] += 1
        zero_next, zero_fast = SIM["t"] + SmartMode.TIMEZERO, SIM["t"] + SmartMode.TIMEFAST
        if NEW and any(d.slew.pending for d in devices) and followup is None:
            followup = SIM["t"] + SmartMode.RAMP_INTERVAL

    steps = int(DURATION / DT)
    for i in range(steps):
        t = i * DT
        SIM["t"] = t
        for d in devices:
            d.step()
        p1 = load(t) - solar(t) - sum(d.actual for d in devices)
        p1_trace.append(p1)
        if TRACE and TRACE[0] <= t < TRACE[1] and i % 5 == 0:
            print(f"{t:7.1f} load {load(t):5.0f} sun {solar(t):5.0f} p1 {p1:6.0f} | " + " ".join(f"{d.name}:{d.actual:6.0f}/{d.command_value:6d}" for d in devices)
                  + (f" | dis={getattr(m.roles, 'discharger', None)} chg={getattr(m.roles, 'charger', None)}" if NEW else ""))
        stats["import_wh"] += max(0, p1) * DT / 3600
        stats["export_wh"] += max(0, -p1) * DT / 3600
        buckets = stats.setdefault("export_by_10min", [0.0] * (DURATION // 600 + 1))
        buckets[int(t // 600)] += max(0, -p1) * DT / 3600
        stats["abs_wh"] += abs(p1) * DT / 3600
        stats["max_import"] = max(stats["max_import"], p1)
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
        if i % int(1 / DT) == 0:
            p1_reported = int(p1)
            if t < zero_fast:
                history.append(p1_reported)
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

    # peak events: time until the import is covered again, and the imported energy in the first minute
    peaks = []
    for start, dur, watt, name in EVENTS:
        s = int(start * 60 / DT)
        window = p1_trace[s : s + int(min(dur * 60, 120) / DT)]
        covered = next((k * DT for k, v in enumerate(window) if k * DT > 1 and v < 150), None)
        first_min = sum(max(0, v) for v in window[: int(60 / DT)]) * DT / 3600
        peaks.append({"event": f"{start}min {name} {watt}W", "covered_s": covered, "import_first_min_wh": round(first_min, 1)})

    out = {
        "version": VERSION,
        "grid_import_wh": round(stats["import_wh"]),
        "grid_export_wh": round(stats["export_wh"]),
        "abs_p1_wh": round(stats["abs_wh"]),
        "max_import_w": round(stats["max_import"]),
        "cycles": stats["cycles"],
        "crossflow_s": round(stats["crossflow_s"]),
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
                "soc_end": round(d.soc_wh / CAPACITY_WH * 100, 1),
                "start_minutes": d.start_times,
            }
            for d in devices
        },
        "peaks": peaks,
        "export_by_10min": [round(x) for x in stats["export_by_10min"]],
    }
    with open(OUT, "w") as f:  # noqa: ASYNC230 - standalone script, nothing else runs on this loop
        json.dump(out, f, indent=1)


asyncio.run(main())

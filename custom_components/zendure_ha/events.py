"""Control events: what the manager and the devices did, and why.

Events are counted always (the counters are in *Download diagnostics*) and, while the P1 recording runs,
also written to ``<config>/zendure_ha/events_<YYYY-MM-DD>.csv`` (``time;unix;type;device;detail``, the
detail as ``key=value`` pairs). The writer is set by the P1 recorder; without a recording ``emit`` only
counts. Event types are listed in ``TYPES``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

TYPES = (
    "dir_start",  # a device starts a change of direction (charge <-> discharge)
    "dir_end",  # the change of direction is done (or cancelled)
    "dir_slow",  # a change of direction takes longer than DIR_SLOW s
    "mode_change",  # the operation mode of the manager changed
    "cloud_on",
    "cloud_off",
    "cycle_timeout",  # a control cycle did not finish within CYCLE_TIMEOUT
    "cycle_error",  # a control cycle failed
    "cmd_sent",  # a power command with another power or AC mode than the previous one
    "cmd_failed",  # a command could not be delivered
    "backoff_start",  # a zenSDK device does not answer: requests wait
    "backoff_end",
    "online",
    "offline",
    "role_change",
    "stop_abrupt",  # the measured power fell by more than ABRUPT_W in about a second while the command stayed
    "stale_control",  # import or export for a while without any new command (watchdog)
    "target_change",  # the P1 target (band) changed
    "solar",  # the solar forecast changed its verdict (batteries full today, no more sun)
    "sleep",  # an idle inverter was put to sleep (smartMode 0)
    "wake",  # a sleeping inverter got a power command again
    "foreign_write",  # a reported limit differs from the one we sent: another controller
    "p1_stale",  # no P1 value for P1_STALE_WARN s
    "p1_back",  # P1 values arrive again
    "p1_fallback",  # without P1 values the batteries fall back to the base load
)

DIR_SLOW = 15.0  # s: a change of direction that takes longer is logged as a warning
ABRUPT_W = 800  # W less between two control cycles (at most 5 s apart)

counters: Counter[str] = Counter()
last: dict[str, Any] = {}  # the last event: time, type, device, detail
_writer: Callable[[str, str, str], None] | None = None  # (type, device, detail), set while recording
_context: Callable[[], dict[str, Any]] | None = None  # state of the manager, added to some events


def set_writer(writer: Callable[[str, str, str], None] | None) -> None:
    global _writer
    _writer = writer


def set_context(context: Callable[[], dict[str, Any]] | None) -> None:
    global _context
    _context = context


def context() -> dict[str, Any]:
    try:
        return _context() if _context is not None else {}
    except Exception:  # noqa: BLE001 - an event must never break the control
        return {}


def detail_text(detail: dict[str, Any]) -> str:
    """key=value pairs separated by a space; no ';' (the column separator) in values."""
    return " ".join(f"{k}={str(v).replace(';', ',').replace(' ', '_')}" for k, v in detail.items() if v is not None)


def emit(event: str, device: str = "", **detail: Any) -> None:
    """Count an event and write it when a recording runs."""
    from time import time

    counters[event] += 1
    text = detail_text(detail)
    last.update({"time": round(time(), 3), "type": event, "device": device, "detail": text})
    if _writer is not None:
        try:
            _writer(event, device, text)
        except Exception:  # noqa: BLE001
            pass


def reset() -> None:
    """Forget the counters (integration unloaded)."""
    counters.clear()
    last.clear()
    set_writer(None)
    set_context(None)

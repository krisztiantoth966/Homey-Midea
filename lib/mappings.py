from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODE_INDEX_TO_ID = {
    0: "off",
    1: "auto",
    2: "cool",
    3: "dry",
    4: "heat",
    5: "fan_only",
}
MODE_ID_TO_INDEX = {value: key for key, value in MODE_INDEX_TO_ID.items() if value != "off"}

FAN_ID_TO_RAW = {
    "silent": 20,
    "low": 40,
    "medium": 60,
    "high": 80,
    "full": 100,
    "auto": 102,
}

SWING_VALUES = {"off", "vertical", "horizontal", "both"}
ENERGY_SOURCES = {"native", "integrated", "estimated", "unavailable"}


def safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mode_from_status(powered: Any, raw_mode: Any) -> str:
    if not bool(powered):
        return "off"
    return MODE_INDEX_TO_ID.get(safe_int(raw_mode), "auto")


def mode_to_raw(mode: str) -> int:
    if mode not in MODE_ID_TO_INDEX:
        raise ValueError(f"Unsupported Midea mode: {mode}")
    return MODE_ID_TO_INDEX[mode]


def fan_from_raw(raw: Any) -> str:
    value = safe_int(raw, 102)
    if value >= 101:
        return "auto"
    if value >= 90:
        return "full"
    if value >= 70:
        return "high"
    if value >= 50:
        return "medium"
    if value >= 30:
        return "low"
    return "silent"


def fan_to_raw(speed: str) -> int:
    try:
        return FAN_ID_TO_RAW[speed]
    except KeyError as exc:
        raise ValueError(f"Unsupported Midea fan speed: {speed}") from exc


def swing_from_status(vertical: Any, horizontal: Any) -> str:
    v = bool(vertical)
    h = bool(horizontal)
    if v and h:
        return "both"
    if v:
        return "vertical"
    if h:
        return "horizontal"
    return "off"


def swing_to_flags(value: str) -> tuple[bool, bool]:
    if value not in SWING_VALUES:
        raise ValueError(f"Unsupported Midea swing mode: {value}")
    return value in {"vertical", "both"}, value in {"horizontal", "both"}


@dataclass(frozen=True)
class EstimateConfig:
    cooling_power_w: float = 1000.0
    heating_power_w: float = 1100.0
    dry_power_w: float = 700.0
    fan_power_w: float = 50.0
    standby_power_w: float = 2.0
    load_factor: float = 0.65


def estimate_power_w(
    *,
    available: bool,
    powered: bool,
    mode: str,
    turbo: bool,
    eco: bool,
    config: EstimateConfig,
) -> float:
    if not available:
        return 0.0
    if not powered or mode == "off":
        return max(0.0, config.standby_power_w)
    if mode == "fan_only":
        return max(0.0, config.fan_power_w)

    if mode == "heat":
        rated = config.heating_power_w
    elif mode == "dry":
        rated = config.dry_power_w
    else:
        rated = config.cooling_power_w

    factor = 1.0 if turbo else min(1.0, max(0.1, config.load_factor))
    if eco:
        factor *= 0.80
    return max(config.fan_power_w, max(0.0, rated) * factor)

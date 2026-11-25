"""Pure helpers for the AC runtime behaviour (query mode, alerts).

Kept free of Homey / midea-lan imports so they can be unit tested anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

QUERY_MODES = ("light", "push", "full")
DEFAULT_QUERY_MODE = "light"
DEFAULT_POLL_SECONDS = 15
MIN_POLL_SECONDS = 10

# Status queries sent in "light" mode. StateQuery (0x41 -> C0 frame) carries
# power, mode, temperatures, fan/swing, error code and the filter (dust)
# flag; PowerQuery (group 4) carries live power and the energy counters.
# The capability probes (B5), properties (B1), humidity and the extra group
# queries that are linked to the 35/88 display / beep / Wi-Fi reset reports
# are not sent.
LIGHT_QUERY_CLASS_NAMES = ("StateQuery", "PowerQuery")


def normalize_query_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in QUERY_MODES else DEFAULT_QUERY_MODE


def poll_seconds(value: Any) -> int:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        seconds = DEFAULT_POLL_SECONDS
    return max(MIN_POLL_SECONDS, min(600, seconds))


def filter_dirty(status: dict[str, Any]) -> bool | None:
    """True when the AC reports that the dust filter needs cleaning."""
    if "full_dust" not in status or status.get("full_dust") is None:
        return None
    return bool(status.get("full_dust"))


@dataclass(frozen=True)
class Warning:
    key: str
    tokens: dict[str, Any]


def active_warning(*, filter_is_dirty: bool | None, error_code: int) -> Warning | None:
    """The single device warning Homey should show (errors win over filter)."""
    if error_code:
        return Warning("warnings.error_code", {"code": error_code})
    if filter_is_dirty:
        return Warning("warnings.filter", {})
    return None

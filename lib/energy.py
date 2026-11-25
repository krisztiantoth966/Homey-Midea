from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mappings import ENERGY_SOURCES, safe_float


@dataclass(frozen=True)
class EnergyReading:
    """Normalized values published to Homey."""

    power_w: float
    total_kwh: float
    source: str
    native_meter_available: bool
    power_is_estimated: bool


class EnergyTracker:
    """Create one monotonic Homey meter from imperfect Midea telemetry.

    Midea AC units can expose three different energy signals:

    * a cumulative counter (``total_energy_consumption``),
    * a counter that can reset (``current_energy_consumption``), and
    * instantaneous power (``realtime_power``).

    The previous tracker always preferred the first available counter. Some
    models expose a stale, low-resolution or incorrectly decoded cumulative
    counter while their live power remains useful. In that situation Homey
    under-counted a day even though ``measure_power`` looked plausible.

    Version 2 keeps independent cumulative candidates. In automatic mode,
    normal online intervals are integrated from live power. Native counters
    remain tracked in parallel and are used to recover gaps, or as a fallback
    when live power is unavailable. The public ``meter_power`` value never
    decreases and an existing v1 meter is preserved during migration.
    """

    STATE_VERSION = 2
    MAX_POWER_W = 30_000.0
    MAX_TOTAL_KWH = 10_000_000.0
    # A generous plausibility ceiling for a residential AC. This only rejects
    # decoder explosions; it does not try to police short compressor peaks.
    MAX_COUNTER_RATE_W = 50_000.0
    COUNTER_EPSILON_KWH = 0.000001

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        state = state if isinstance(state, dict) else {}
        version = int(self._non_negative(state.get("version"), 1.0))

        self.total_kwh = self._non_negative(state.get("total_kwh"), 0.0)

        # Each candidate starts at the last published Homey meter. This makes
        # migration and source changes continuous instead of resetting the
        # cumulative meter or jumping to a device's lifetime total.
        self.integrated_total_kwh = max(
            self.total_kwh,
            self._non_negative(state.get("integrated_total_kwh"), self.total_kwh),
        )
        self.native_total_candidate_kwh = max(
            self.total_kwh,
            self._non_negative(
                state.get("native_total_candidate_kwh"), self.total_kwh
            ),
        )
        self.native_session_candidate_kwh = max(
            self.total_kwh,
            self._non_negative(
                state.get("native_session_candidate_kwh"), self.total_kwh
            ),
        )

        # v1 stored one raw counter. Keep it as the total-counter anchor so the
        # first v2 update does not treat the full lifetime value as new energy.
        legacy_raw = safe_float(state.get("last_native_raw_kwh"))
        self.last_native_total_raw_kwh = safe_float(
            state.get("last_native_total_raw_kwh")
        )
        if self.last_native_total_raw_kwh is None and version < 2:
            self.last_native_total_raw_kwh = legacy_raw
        self.last_native_session_raw_kwh = safe_float(
            state.get("last_native_session_raw_kwh")
        )

        self.last_timestamp = self._valid_timestamp(state.get("last_timestamp"))
        self.last_power_w = self._non_negative(state.get("last_power_w"), 0.0)
        self.last_power_valid = bool(state.get("last_power_valid", False))
        self.last_power_is_estimated = bool(
            state.get("last_power_is_estimated", False)
        )

        source = str(state.get("last_source") or "unavailable")
        self.last_source = source if source in ENERGY_SOURCES else "unavailable"

    @staticmethod
    def _non_negative(value: Any, default: float) -> float:
        parsed = safe_float(value)
        return default if parsed is None else max(0.0, parsed)

    @staticmethod
    def _valid_timestamp(value: Any) -> float | None:
        parsed = safe_float(value)
        if parsed is None or parsed <= 0:
            return None
        return parsed

    @classmethod
    def _valid_native_total(cls, value: Any) -> float | None:
        parsed = safe_float(value)
        if parsed is None or parsed < 0 or parsed > cls.MAX_TOTAL_KWH:
            return None
        return parsed

    @classmethod
    def _valid_native_power(cls, value: Any) -> float | None:
        parsed = safe_float(value)
        if parsed is None or parsed < 0 or parsed > cls.MAX_POWER_W:
            return None
        return parsed

    def seed_minimum(self, value: Any) -> None:
        """Never start below the meter value already stored by Homey."""

        parsed = self._valid_native_total(value)
        if parsed is None or parsed <= self.total_kwh:
            return
        self.total_kwh = parsed
        self.integrated_total_kwh = max(self.integrated_total_kwh, parsed)
        self.native_total_candidate_kwh = max(
            self.native_total_candidate_kwh, parsed
        )
        self.native_session_candidate_kwh = max(
            self.native_session_candidate_kwh, parsed
        )

    def _elapsed(self, now: float) -> float | None:
        if self.last_timestamp is None:
            return None
        return max(0.0, now - self.last_timestamp)

    def _counter_delta(
        self,
        raw_value: Any,
        *,
        anchor_name: str,
        elapsed_seconds: float | None,
    ) -> tuple[float | None, bool]:
        """Return a safe positive counter delta and update its raw anchor.

        A lower value is treated as a device/session reset. The first sample is
        only an anchor. Implausible jumps are ignored but still become the new
        anchor so one bad frame cannot poison every later poll.
        """

        raw = self._valid_native_total(raw_value)
        if raw is None:
            return None, False

        previous = getattr(self, anchor_name)
        setattr(self, anchor_name, raw)
        if previous is None or elapsed_seconds is None:
            return None, True
        if raw + self.COUNTER_EPSILON_KWH < previous:
            return 0.0, True

        delta = max(0.0, raw - previous)
        allowed_seconds = max(1.0, elapsed_seconds)
        max_delta = max(
            0.25,
            allowed_seconds * self.MAX_COUNTER_RATE_W / 3_600_000.0,
        )
        if delta > max_delta:
            return None, True
        return delta, True

    def _integrated_delta(
        self, elapsed_seconds: float | None, max_gap_seconds: float
    ) -> float:
        """Integrate the last observed power for one trustworthy interval."""

        if (
            elapsed_seconds is None
            or elapsed_seconds <= 0
            or elapsed_seconds > max(1.0, max_gap_seconds)
            or not self.last_power_valid
        ):
            return 0.0
        return (
            max(0.0, self.last_power_w)
            * elapsed_seconds
            / 3_600_000.0
        )

    def update(
        self,
        *,
        now: float,
        available: bool,
        energy_mode: str,
        native_total_kwh: Any,
        native_session_kwh: Any,
        native_power_w: Any,
        estimated_power_w: float,
        max_gap_seconds: float = 300.0,
    ) -> EnergyReading:
        mode = (
            energy_mode
            if energy_mode in {"auto", "native", "realtime", "estimate"}
            else "auto"
        )
        elapsed = self._elapsed(now)

        total_delta, has_total_meter = self._counter_delta(
            native_total_kwh,
            anchor_name="last_native_total_raw_kwh",
            elapsed_seconds=elapsed,
        )
        session_delta, has_session_meter = self._counter_delta(
            native_session_kwh,
            anchor_name="last_native_session_raw_kwh",
            elapsed_seconds=elapsed,
        )
        native_meter_available = has_total_meter or has_session_meter

        if total_delta is not None:
            self.native_total_candidate_kwh += total_delta
        if session_delta is not None:
            self.native_session_candidate_kwh += session_delta

        # Complete the interval since the previous status sample before choosing
        # the power value that will represent the next interval.
        integrated_delta = self._integrated_delta(elapsed, max_gap_seconds)
        self.integrated_total_kwh += integrated_delta

        native_power = self._valid_native_power(native_power_w)
        estimate = max(0.0, safe_float(estimated_power_w) or 0.0)
        power_w = 0.0
        power_valid = False
        power_is_estimated = False
        source = "unavailable"

        if available:
            if mode == "native":
                if native_meter_available:
                    source = "native"
                    if native_power is not None:
                        power_w = native_power
                        power_valid = True
                    else:
                        power_w = estimate
                        power_valid = True
                        power_is_estimated = True
            elif mode == "realtime":
                if native_power is not None:
                    source = "integrated"
                    power_w = native_power
                    power_valid = True
            elif mode == "estimate":
                source = "estimated"
                power_w = estimate
                power_valid = True
                power_is_estimated = True
            else:  # auto
                if native_power is not None:
                    source = "integrated"
                    power_w = native_power
                    power_valid = True
                else:
                    # Keep a useful live tile even when the model only exposes
                    # counters. The approximation flag is updated by the device.
                    power_w = estimate
                    power_valid = True
                    power_is_estimated = True
                    source = "estimated"

        native_candidate = max(
            self.native_total_candidate_kwh,
            self.native_session_candidate_kwh,
        )
        gap = elapsed is not None and elapsed > max(1.0, max_gap_seconds)

        if mode == "native":
            selected_total = native_candidate
        elif mode in {"realtime", "estimate"}:
            selected_total = self.integrated_total_kwh
        else:
            # During normal online operation, live power is the primary source.
            # Use counters when there is no live power, or to recover a period
            # that is too long to safely extrapolate from one stale power sample.
            if native_power is None or gap:
                selected_total = max(self.integrated_total_kwh, native_candidate)
                if native_candidate > self.integrated_total_kwh + self.COUNTER_EPSILON_KWH:
                    source = "native" if available else source
            else:
                selected_total = self.integrated_total_kwh

        self.total_kwh = max(self.total_kwh, selected_total)
        # Rebase every candidate to the public meter. This is essential when the
        # user switches modes and ensures no source can make meter_power go back.
        self.integrated_total_kwh = max(
            self.integrated_total_kwh, self.total_kwh
        )
        self.native_total_candidate_kwh = max(
            self.native_total_candidate_kwh, self.total_kwh
        )
        self.native_session_candidate_kwh = max(
            self.native_session_candidate_kwh, self.total_kwh
        )

        self.last_timestamp = now
        self.last_power_w = power_w
        self.last_power_valid = power_valid
        self.last_power_is_estimated = power_is_estimated
        self.last_source = source

        return EnergyReading(
            power_w=round(power_w, 1),
            total_kwh=round(max(0.0, self.total_kwh), 5),
            source=source,
            native_meter_available=native_meter_available,
            power_is_estimated=power_is_estimated,
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "version": self.STATE_VERSION,
            "total_kwh": round(max(0.0, self.total_kwh), 8),
            "integrated_total_kwh": round(
                max(0.0, self.integrated_total_kwh), 8
            ),
            "native_total_candidate_kwh": round(
                max(0.0, self.native_total_candidate_kwh), 8
            ),
            "native_session_candidate_kwh": round(
                max(0.0, self.native_session_candidate_kwh), 8
            ),
            "last_native_total_raw_kwh": self.last_native_total_raw_kwh,
            "last_native_session_raw_kwh": self.last_native_session_raw_kwh,
            "last_timestamp": self.last_timestamp,
            "last_power_w": round(max(0.0, self.last_power_w), 4),
            "last_power_valid": self.last_power_valid,
            "last_power_is_estimated": self.last_power_is_estimated,
            "last_source": self.last_source,
        }

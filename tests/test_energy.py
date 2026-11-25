from __future__ import annotations

import unittest

from lib.energy import EnergyTracker


class EnergyTrackerTests(unittest.TestCase):
    def test_estimated_integration(self) -> None:
        tracker = EnergyTracker()
        first = tracker.update(
            now=1,
            available=True,
            energy_mode="estimate",
            native_total_kwh=None,
            native_session_kwh=None,
            native_power_w=None,
            estimated_power_w=1000,
        )
        self.assertEqual(first.total_kwh, 0)
        second = tracker.update(
            now=3601,
            available=True,
            energy_mode="estimate",
            native_total_kwh=None,
            native_session_kwh=None,
            native_power_w=None,
            estimated_power_w=1000,
            max_gap_seconds=3600,
        )
        self.assertAlmostEqual(second.total_kwh, 1.0, places=4)
        self.assertEqual(second.source, "estimated")
        self.assertTrue(second.power_is_estimated)

    def test_realtime_integration(self) -> None:
        tracker = EnergyTracker()
        tracker.update(
            now=1,
            available=True,
            energy_mode="realtime",
            native_total_kwh=None,
            native_session_kwh=None,
            native_power_w=500,
            estimated_power_w=0,
        )
        reading = tracker.update(
            now=1801,
            available=True,
            energy_mode="realtime",
            native_total_kwh=None,
            native_session_kwh=None,
            native_power_w=500,
            estimated_power_w=0,
            max_gap_seconds=1800,
        )
        self.assertAlmostEqual(reading.total_kwh, 0.25, places=4)
        self.assertEqual(reading.source, "integrated")
        self.assertFalse(reading.power_is_estimated)

    def test_auto_prefers_live_integration_over_underreporting_counter(self) -> None:
        tracker = EnergyTracker()
        tracker.update(
            now=1,
            available=True,
            energy_mode="auto",
            native_total_kwh=100.0,
            native_session_kwh=5.0,
            native_power_w=1000,
            estimated_power_w=650,
        )
        reading = tracker.update(
            now=3601,
            available=True,
            energy_mode="auto",
            native_total_kwh=100.1,
            native_session_kwh=5.1,
            native_power_w=1000,
            estimated_power_w=650,
            max_gap_seconds=3600,
        )
        # Old v1 logic published only the native +0.1 kWh delta. Automatic v2
        # integrates the plausible 1 kW live signal for the full hour.
        self.assertAlmostEqual(reading.total_kwh, 1.0, places=4)
        self.assertEqual(reading.source, "integrated")
        self.assertFalse(reading.power_is_estimated)

    def test_screenshot_scenario_uses_3_52_kwh_not_1_4_kwh_counter(self) -> None:
        tracker = EnergyTracker()
        samples = int(11.6 * 60 * 2)  # one sample every 30 seconds
        live_power_w = 3.52 * 1000 / 11.6
        tracker.update(
            now=1,
            available=True,
            energy_mode="auto",
            native_total_kwh=50.0,
            native_session_kwh=5.0,
            native_power_w=live_power_w,
            estimated_power_w=650,
        )
        reading = None
        for index in range(1, samples + 1):
            fraction = index / samples
            reading = tracker.update(
                now=1 + index * 30,
                available=True,
                energy_mode="auto",
                native_total_kwh=50.0 + 1.4 * fraction,
                native_session_kwh=5.0 + 1.4 * fraction,
                native_power_w=live_power_w,
                estimated_power_w=650,
                max_gap_seconds=300,
            )
        assert reading is not None
        self.assertAlmostEqual(reading.total_kwh, 3.52, places=2)
        self.assertEqual(reading.source, "integrated")

    def test_auto_recovers_long_gap_from_native_counter(self) -> None:
        tracker = EnergyTracker()
        tracker.update(
            now=1,
            available=True,
            energy_mode="auto",
            native_total_kwh=10.0,
            native_session_kwh=2.0,
            native_power_w=1000,
            estimated_power_w=650,
        )
        reading = tracker.update(
            now=7201,
            available=True,
            energy_mode="auto",
            native_total_kwh=12.0,
            native_session_kwh=4.0,
            native_power_w=1000,
            estimated_power_w=650,
            max_gap_seconds=300,
        )
        self.assertAlmostEqual(reading.total_kwh, 2.0, places=4)
        self.assertEqual(reading.source, "native")

    def test_session_counter_can_recover_and_survive_reset(self) -> None:
        tracker = EnergyTracker()
        tracker.update(
            now=1,
            available=True,
            energy_mode="auto",
            native_total_kwh=None,
            native_session_kwh=0.0,
            native_power_w=None,
            estimated_power_w=0,
        )
        first = tracker.update(
            now=3601,
            available=True,
            energy_mode="auto",
            native_total_kwh=None,
            native_session_kwh=1.0,
            native_power_w=None,
            estimated_power_w=0,
            max_gap_seconds=300,
        )
        self.assertAlmostEqual(first.total_kwh, 1.0, places=4)

        reset = tracker.update(
            now=7201,
            available=True,
            energy_mode="auto",
            native_total_kwh=None,
            native_session_kwh=0.2,
            native_power_w=None,
            estimated_power_w=0,
            max_gap_seconds=300,
        )
        self.assertAlmostEqual(reset.total_kwh, 1.0, places=4)

        after_reset = tracker.update(
            now=10801,
            available=True,
            energy_mode="auto",
            native_total_kwh=None,
            native_session_kwh=0.7,
            native_power_w=None,
            estimated_power_w=0,
            max_gap_seconds=300,
        )
        self.assertAlmostEqual(after_reset.total_kwh, 1.5, places=4)

    def test_native_mode_accumulates_deltas_without_lifetime_jump(self) -> None:
        tracker = EnergyTracker()
        first = tracker.update(
            now=1,
            available=True,
            energy_mode="native",
            native_total_kwh=200.0,
            native_session_kwh=None,
            native_power_w=600,
            estimated_power_w=0,
        )
        self.assertEqual(first.total_kwh, 0)
        second = tracker.update(
            now=3601,
            available=True,
            energy_mode="native",
            native_total_kwh=200.5,
            native_session_kwh=None,
            native_power_w=600,
            estimated_power_w=0,
            max_gap_seconds=3600,
        )
        self.assertAlmostEqual(second.total_kwh, 0.5, places=4)
        self.assertEqual(second.source, "native")

    def test_existing_v1_meter_is_preserved(self) -> None:
        tracker = EnergyTracker(
            {
                "version": 1,
                "total_kwh": 12.345,
                "last_native_raw_kwh": 88.0,
                "last_source": "native",
            }
        )
        reading = tracker.update(
            now=1000,
            available=True,
            energy_mode="auto",
            native_total_kwh=88.2,
            native_session_kwh=None,
            native_power_w=500,
            estimated_power_w=0,
        )
        self.assertAlmostEqual(reading.total_kwh, 12.345, places=5)
        self.assertEqual(tracker.last_native_total_raw_kwh, 88.2)

    def test_homey_capability_can_seed_higher_meter(self) -> None:
        tracker = EnergyTracker({"total_kwh": 2.0})
        tracker.seed_minimum(3.5)
        self.assertEqual(tracker.total_kwh, 3.5)
        self.assertGreaterEqual(tracker.integrated_total_kwh, 3.5)

    def test_offline_transition_finishes_previous_interval(self) -> None:
        tracker = EnergyTracker()
        tracker.update(
            now=1,
            available=True,
            energy_mode="realtime",
            native_total_kwh=None,
            native_session_kwh=None,
            native_power_w=1000,
            estimated_power_w=0,
        )
        reading = tracker.update(
            now=1801,
            available=False,
            energy_mode="realtime",
            native_total_kwh=None,
            native_session_kwh=None,
            native_power_w=None,
            estimated_power_w=0,
            max_gap_seconds=1800,
        )
        self.assertAlmostEqual(reading.total_kwh, 0.5, places=4)
        self.assertEqual(reading.source, "unavailable")
        self.assertEqual(reading.power_w, 0)

    def test_state_round_trip_preserves_recovery_anchors(self) -> None:
        tracker = EnergyTracker()
        tracker.update(
            now=100,
            available=True,
            energy_mode="auto",
            native_total_kwh=10,
            native_session_kwh=1,
            native_power_w=500,
            estimated_power_w=0,
        )
        restored = EnergyTracker(tracker.to_state())
        reading = restored.update(
            now=3700,
            available=True,
            energy_mode="auto",
            native_total_kwh=10.5,
            native_session_kwh=1.5,
            native_power_w=500,
            estimated_power_w=0,
            max_gap_seconds=300,
        )
        self.assertAlmostEqual(reading.total_kwh, 0.5, places=4)
        self.assertEqual(reading.source, "native")

    def test_auto_falls_back_to_estimate(self) -> None:
        tracker = EnergyTracker()
        reading = tracker.update(
            now=1,
            available=True,
            energy_mode="auto",
            native_total_kwh=None,
            native_session_kwh=None,
            native_power_w=None,
            estimated_power_w=700,
        )
        self.assertEqual(reading.source, "estimated")
        self.assertEqual(reading.power_w, 700)
        self.assertTrue(reading.power_is_estimated)

    def test_implausible_counter_jump_is_ignored(self) -> None:
        tracker = EnergyTracker()
        tracker.update(
            now=1,
            available=True,
            energy_mode="native",
            native_total_kwh=1,
            native_session_kwh=None,
            native_power_w=None,
            estimated_power_w=0,
        )
        reading = tracker.update(
            now=31,
            available=True,
            energy_mode="native",
            native_total_kwh=500,
            native_session_kwh=None,
            native_power_w=None,
            estimated_power_w=0,
        )
        self.assertEqual(reading.total_kwh, 0)


if __name__ == "__main__":
    unittest.main()

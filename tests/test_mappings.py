import unittest

from lib.mappings import (
    EstimateConfig,
    estimate_power_w,
    fan_from_raw,
    fan_to_raw,
    mode_from_status,
    mode_to_raw,
    swing_from_status,
    swing_to_flags,
)


class MappingTests(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(mode_from_status(False, 4), "off")
        self.assertEqual(mode_from_status(True, 2), "cool")
        self.assertEqual(mode_to_raw("heat"), 4)

    def test_fan(self):
        self.assertEqual(fan_from_raw(102), "auto")
        self.assertEqual(fan_from_raw(80), "high")
        self.assertEqual(fan_to_raw("full"), 100)

    def test_swing(self):
        self.assertEqual(swing_from_status(True, True), "both")
        self.assertEqual(swing_to_flags("horizontal"), (False, True))

    def test_estimate(self):
        cfg = EstimateConfig(cooling_power_w=1000, standby_power_w=2, load_factor=0.65)
        self.assertEqual(estimate_power_w(available=True, powered=False, mode="off", turbo=False, eco=False, config=cfg), 2)
        self.assertEqual(estimate_power_w(available=True, powered=True, mode="cool", turbo=False, eco=False, config=cfg), 650)
        self.assertEqual(estimate_power_w(available=True, powered=True, mode="cool", turbo=True, eco=False, config=cfg), 1000)


if __name__ == "__main__":
    unittest.main()

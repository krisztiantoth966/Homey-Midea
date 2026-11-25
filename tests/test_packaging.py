from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))
        self.driver = self.manifest["drivers"][0]

    def test_version_dependency_and_energy_mapping(self) -> None:
        self.assertEqual(self.manifest["version"], "1.0.4")
        self.assertIn("midea-lan==2026.9.0", self.manifest["pythonPackages"])
        self.assertEqual(
            self.driver.get("energy", {}).get("meterPowerImportedCapability"),
            "meter_power",
        )

    def test_smarthome_uses_certifi_tls_bundle(self) -> None:
        self.assertIn("certifi==2025.8.3", self.manifest.get("pythonPackages", []))
        pairing = (ROOT / "lib/pairing.py").read_text(encoding="utf-8")
        self.assertIn("ssl.create_default_context(cafile=certifi.where())", pairing)
        self.assertIn("TCPConnector(ssl=ssl_context)", pairing)
        app_py = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('os.environ["SSL_CERT_FILE"] = certifi.where()', app_py)

    def test_no_reserved_energy_setting_prefix(self) -> None:
        setting_ids = {setting["id"] for setting in self.driver.get("settings", [])}
        self.assertIn("consumption_mode", setting_ids)
        self.assertIn("power_calibration_percent", setting_ids)
        self.assertNotIn("energy_mode", setting_ids)
        self.assertFalse(any(value.startswith("energy_") for value in setting_ids))
        self.assertIn("safe_mode", setting_ids)
        safe = next(item for item in self.driver["settings"] if item.get("id") == "safe_mode")
        self.assertTrue(safe["value"])

    def test_auto_power_method_and_known_profiles(self) -> None:
        setting = next(
            item
            for item in self.driver["settings"]
            if item.get("id") == "power_analysis_method"
        )
        self.assertEqual(setting["value"], "auto")
        self.assertIn("auto", {item["id"] for item in setting["values"]})
        source = (ROOT / "drivers/ac/device.py").read_text(encoding="utf-8")
        self.assertIn('if "portasplit" in model:', source)
        self.assertIn('elif "00000q1d" in model and subtype == 524:', source)

    def test_homey_sdk_energy_field_is_not_overwritten(self) -> None:
        source = (ROOT / "drivers/ac/device.py").read_text(encoding="utf-8")
        self.assertNotIn("self._energy = EnergyTracker", source)
        self.assertIn("self._energy_tracker = EnergyTracker", source)
        self.assertIn("seed_minimum", source)
        self.assertIn("native_power < 5.0", source)

    def test_safe_mode_suppresses_ac_query_bursts(self) -> None:
        source = (ROOT / "drivers/ac/device.py").read_text(encoding="utf-8")
        self.assertIn("_disable_active_ac_queries", source)
        self.assertIn("client.build_init_query = MethodType(lambda _client: [], client)", source)
        self.assertIn("client.build_query = MethodType(lambda _client: [], client)", source)
        self.assertIn("client.set_refresh_interval(0)", source)
        self.assertIn("Safe LAN mode: manual active refresh skipped", source)
        self.assertIn("_safe_energy_loop", source)

    def test_tracker_uses_live_power_before_native_counter_in_auto(self) -> None:
        source = (ROOT / "lib/energy.py").read_text(encoding="utf-8")
        self.assertIn("normal online intervals are integrated from live power", source)
        self.assertIn("if native_power is None or gap:", source)
        self.assertEqual(2, __import__("lib.energy", fromlist=["EnergyTracker"]).EnergyTracker.STATE_VERSION)

    def test_custom_pair_view_owns_navigation(self) -> None:
        start = next(view for view in self.driver["pair"] if view["id"] == "start")
        self.assertNotIn("navigation", start)

    def test_pair_view_initialization_and_login_fields(self) -> None:
        html = (ROOT / "drivers/ac/pair/start.html").read_text(encoding="utf-8")
        self.assertNotIn("Homey.ready(function", html)
        self.assertIn("Homey.ready();", html)
        self.assertIn("SmartHome account", html)
        self.assertIn('id="account"', html)
        self.assertIn('id="password"', html)
        self.assertIn("Homey.showView('list_devices')", html)

    def test_repair_view_initialization(self) -> None:
        html = (ROOT / "drivers/ac/repair/repair.html").read_text(encoding="utf-8")
        self.assertNotIn("Homey.ready(function", html)
        self.assertIn("Homey.ready();", html)
        self.assertIn('id="account"', html)
        self.assertIn('id="password"', html)


if __name__ == "__main__":
    unittest.main()

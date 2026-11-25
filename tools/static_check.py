from __future__ import annotations

import json
import py_compile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))

assert manifest["id"] == "com.tothk.midea"
assert manifest["runtime"] == "python"
assert manifest["pythonVersion"] == "3.14"
assert "midea-lan==2026.9.0" in manifest["pythonPackages"]
assert "certifi==2025.8.3" in manifest["pythonPackages"]
assert len(manifest["drivers"]) == 1
assert manifest["version"] == "1.0.4"

driver = manifest["drivers"][0]
assert driver["id"] == "ac"
assert "measure_power" in driver["capabilities"]
assert "meter_power" in driver["capabilities"]
assert driver.get("energy", {}).get("meterPowerImportedCapability") == "meter_power"

setting_ids = {setting["id"] for setting in driver.get("settings", [])}
assert "consumption_mode" in setting_ids
assert "safe_mode" in setting_ids
assert next(s for s in driver["settings"] if s.get("id") == "safe_mode")["value"] is True
assert "power_calibration_percent" in setting_ids
assert "energy_mode" not in setting_ids
assert not any(setting_id.startswith("energy_") for setting_id in setting_ids)

method_setting = next(
    setting
    for setting in driver["settings"]
    if setting.get("id") == "power_analysis_method"
)
assert method_setting["value"] == "auto"
assert "auto" in {option["id"] for option in method_setting["values"]}

start_view = next(view for view in driver.get("pair", []) if view.get("id") == "start")
assert "navigation" not in start_view

pair_html = (ROOT / "drivers/ac/pair/start.html").read_text(encoding="utf-8")
assert "Homey.ready(function" not in pair_html
assert "Homey.ready();" in pair_html
assert "SmartHome account" in pair_html
assert "Homey.showView('list_devices')" in pair_html

pairing_py = (ROOT / "lib/pairing.py").read_text(encoding="utf-8")
assert "ssl.create_default_context(cafile=certifi.where())" in pairing_py
assert "TCPConnector(ssl=ssl_context)" in pairing_py
assert '"power_analysis_method": "auto"' in pairing_py
assert '"power_calibration_percent": 100' in pairing_py
assert '"safe_mode": True' in pairing_py

app_py = (ROOT / "app.py").read_text(encoding="utf-8")
assert 'os.environ["SSL_CERT_FILE"] = certifi.where()' in app_py

repair_html = (ROOT / "drivers/ac/repair/repair.html").read_text(encoding="utf-8")
assert "Homey.ready(function" not in repair_html
assert "Homey.ready();" in repair_html

device_py = (ROOT / "drivers/ac/device.py").read_text(encoding="utf-8")
assert "self._energy = EnergyTracker" not in device_py
assert "self._energy_tracker = EnergyTracker" in device_py
assert "seed_minimum" in device_py
assert "native_power < 5.0" in device_py
assert 'if "portasplit" in model:' in device_py
assert 'elif "00000q1d" in model and subtype == 524:' in device_py
assert 'client.build_query = MethodType(lambda _client: [], client)' in device_py
assert 'client.build_init_query = MethodType(lambda _client: [], client)' in device_py
assert 'client.set_refresh_interval(0)' in device_py
assert '_safe_energy_loop' in device_py

energy_py = (ROOT / "lib/energy.py").read_text(encoding="utf-8")
assert "STATE_VERSION = 2" in energy_py
assert "normal online intervals are integrated from live power" in energy_py
assert "native_total_candidate_kwh" in energy_py
assert "native_session_candidate_kwh" in energy_py

required = [
    "app.py",
    "drivers/ac/driver.py",
    "drivers/ac/device.py",
    "drivers/ac/pair/start.html",
    "drivers/ac/repair/repair.html",
    "lib/energy.py",
    "lib/mappings.py",
    "lib/pairing.py",
    "locales/en.json",
    "locales/sk.json",
]
for rel in required:
    assert (ROOT / rel).is_file(), rel

for path in ROOT.rglob("*.py"):
    if ".homeybuild" not in path.parts:
        py_compile.compile(str(path), doraise=True)

sizes = {
    "assets/images/small.png": (250, 175),
    "assets/images/large.png": (500, 350),
    "assets/images/xlarge.png": (1000, 700),
    "drivers/ac/assets/images/small.png": (75, 75),
    "drivers/ac/assets/images/large.png": (500, 500),
    "drivers/ac/assets/images/xlarge.png": (1000, 1000),
}
for rel, expected in sizes.items():
    with Image.open(ROOT / rel) as image:
        assert image.size == expected, (rel, image.size, expected)

print("Static checks passed")

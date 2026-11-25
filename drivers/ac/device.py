from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from types import MethodType

from homey.device import Device
from midealan.const import DeviceType, ProtocolVersion
from midealan.devices import device_selector

from app.lib.energy import EnergyTracker
from app.lib.mappings import (
    EstimateConfig,
    estimate_power_w,
    fan_from_raw,
    fan_to_raw,
    mode_from_status,
    mode_to_raw,
    safe_float,
    safe_int,
    swing_from_status,
    swing_to_flags,
)


class MideaACDevice(Device):
    _client = None
    _update_callback = None

    async def on_init(self) -> None:
        await super().on_init()
        self._loop = asyncio.get_running_loop()
        self._apply_lock = asyncio.Lock()
        self._generation = 0
        self._closing = False
        self._status: dict[str, Any] = {}
        self._last_mode: str | None = None
        self._last_energy_source: str | None = None
        self._last_power_approximated: bool | None = None
        self._last_error: int | None = None
        self._last_energy_save = 0.0
        self._last_energy_persisted_total = 0.0
        self._device_available = False
        self._have_device_state = False
        self._last_device_update_monotonic = 0.0
        self._safe_energy_task: asyncio.Task | None = None
        stored_energy = (self.get_store() or {}).get("energy_state")
        # ``Device._energy`` belongs to the Homey SDK. Using that name for our
        # tracker corrupted Homey's own energy metadata on SDK 3. Keep the
        # application tracker in a distinct attribute.
        self._energy_tracker = EnergyTracker(stored_energy)
        try:
            self._energy_tracker.seed_minimum(
                self.get_capability_value("meter_power")
            )
        except Exception:
            pass
        self._last_energy_persisted_total = self._energy_tracker.total_kwh

        self._register_capability_listeners()
        # Preserve the last live power across an app restart until the first
        # fresh device status arrives. Writing zero here created artificial
        # drops in Insights and lost part of the first integration interval.
        if self.get_capability_value("measure_power") is None:
            await self._set_capability("measure_power", 0.0)
        await self._set_capability(
            "meter_power", round(self._energy_tracker.total_kwh, 5)
        )
        await self._set_capability(
            "midea_energy_source", self._energy_tracker.last_source
        )
        await self._set_capability("alarm_generic", False)
        await self.set_unavailable("Connecting to the Midea air conditioner...")
        await self._start_client()
        self._safe_energy_task = asyncio.create_task(self._safe_energy_loop())
        self.log(
            f"Initialized {self.get_name()} at {self.get_setting('ip')} "
            f"safe_mode={self._safe_mode_enabled()}"
        )

    def _register_capability_listeners(self) -> None:
        self.register_capability_listener("onoff", self._on_power)
        self.register_capability_listener("target_temperature", self._on_target_temperature)
        self.register_capability_listener("midea_mode", self._on_mode)
        self.register_capability_listener("midea_fan_speed", self._on_fan_speed)
        self.register_capability_listener("midea_swing_mode", self._on_swing)

        attribute_map = {
            "midea_eco": "eco_mode",
            "midea_turbo": "boost_mode",
            "midea_sleep": "sleep_mode",
            "midea_display": "screen_display",
            "midea_frost_protect": "frost_protect",
            "midea_breezeless": "breezeless",
            "midea_purifier": "anion",
            "midea_beep": "prompt_tone",
            "midea_self_clean": "self_clean",
        }
        for capability, attribute in attribute_map.items():
            self.register_capability_listener(capability, self._boolean_listener(attribute))

    def _boolean_listener(self, attribute: str):
        async def listener(value: bool, **kwargs: Any) -> None:
            await self.set_boolean_feature(attribute, bool(value))
        return listener

    async def _on_power(self, value: bool, **kwargs: Any) -> None:
        await self.set_boolean_feature("power", bool(value))

    async def _on_target_temperature(self, value: float, **kwargs: Any) -> None:
        client = self._require_client()
        await asyncio.to_thread(client.set_target_temperature, float(value), None)

    async def _on_mode(self, value: str, **kwargs: Any) -> None:
        await self.set_mode(str(value))

    async def _on_fan_speed(self, value: str, **kwargs: Any) -> None:
        await self.set_fan_speed(str(value))

    async def _on_swing(self, value: str, **kwargs: Any) -> None:
        await self.set_swing(str(value))

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("Midea device is not initialized")
        return self._client

    def _setting(self, key: str, overrides: dict[str, Any] | None = None) -> Any:
        """Read a setting, preferring fresh values supplied by on_settings()."""
        if overrides is not None and key in overrides:
            return overrides[key]
        return self.get_setting(key)

    def _safe_mode_enabled(
        self, overrides: dict[str, Any] | None = None
    ) -> bool:
        value = self._setting("safe_mode", overrides)
        # Existing devices created before v1.0.4 have no stored value. Make the
        # safe behaviour the migration default rather than silently retaining
        # the query burst that can wake/beep/reset affected AC models.
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "off", "no"}
        return bool(value)

    @staticmethod
    def _disable_active_ac_queries(client: Any) -> None:
        """Keep only the base appliance handshake; suppress AC query bursts.

        midea-lan's base connection sends MessageQueryAppliance to establish the
        message protocol. AC-specific build_init_query()/build_query() then send
        B5, state, properties, power, humidity and group-data requests. Some Midea
        control boards interpret those as user/service interaction and show 35/88,
        beep, wake the display or even reset the Wi-Fi module. Returning empty
        query lists keeps authentication, command sending, heartbeat and unsolicited
        device notifications, while avoiding those AC-specific requests.
        """
        client.build_init_query = MethodType(lambda _client: [], client)
        client.build_query = MethodType(lambda _client: [], client)
        client.set_refresh_interval(0)

    async def _apply_availability_only(self, available: bool, generation: int) -> None:
        if generation != self._generation or self._closing:
            return
        self._device_available = bool(available)
        try:
            if available:
                await self.set_available()
            else:
                await self.set_unavailable("The Midea air conditioner is offline")
        except Exception:
            pass

    def _status_for_safe_energy_tick(self) -> dict[str, Any]:
        status = dict(self._status)
        # On migration/startup there may not have been an unsolicited AC status
        # frame yet. Reuse Homey's persisted state so the local energy heartbeat
        # can continue without querying the appliance.
        if "power" not in status:
            status["power"] = bool(self.get_capability_value("onoff"))
        if "mode" not in status:
            mode = str(self.get_capability_value("midea_mode") or "off")
            if mode == "off":
                status["mode"] = 0
            else:
                try:
                    status["mode"] = mode_to_raw(mode)
                except ValueError:
                    status["mode"] = 1
        if "boost_mode" not in status:
            status["boost_mode"] = bool(self.get_capability_value("midea_turbo"))
        if "eco_mode" not in status:
            status["eco_mode"] = bool(self.get_capability_value("midea_eco"))

        # Never keep calling a stale native wattage an exact live measurement.
        # After two minutes without a real device frame, EnergyTracker falls back
        # to the configured estimate while still doing zero network I/O.
        age = time.monotonic() - self._last_device_update_monotonic
        if self._last_device_update_monotonic <= 0 or age > 120:
            status.pop("realtime_power", None)
            status.pop("total_energy_consumption", None)
            status.pop("current_energy_consumption", None)
        return status

    async def _safe_energy_loop(self) -> None:
        try:
            while not self._closing:
                await asyncio.sleep(60)
                if self._closing or not self._safe_mode_enabled():
                    continue
                status = self._status_for_safe_energy_tick()
                await self._update_energy_from_status(
                    status,
                    available=self._device_available,
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.log(f"Safe energy heartbeat warning: {exc}")

    async def _stop_safe_energy_loop(self) -> None:
        task = self._safe_energy_task
        self._safe_energy_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _merged_customize(self, overrides: dict[str, Any] | None = None) -> str:
        raw = str(self._setting("customize", overrides) or "").strip()
        params: dict[str, Any] = {}
        if raw:
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    params.update(decoded)
            except (TypeError, ValueError):
                self.log("Ignoring invalid additional customization JSON")
        method_value = str(
            self._setting("power_analysis_method", overrides) or "auto"
        ).strip()
        if method_value == "auto":
            model = str(self._setting("model", overrides) or "").lower()
            subtype = safe_int(self._setting("subtype", overrides), 0)
            if "portasplit" in model:
                method = 12
            elif "00000q1d" in model and subtype == 524:
                method = 101
            else:
                method = 1
        else:
            method = safe_int(method_value, 1)
        params["power_analysis_method"] = method
        return json.dumps(params, separators=(",", ":"))

    async def _start_client(self, overrides: dict[str, Any] | None = None) -> None:
        await self._stop_client()
        self._generation += 1
        generation = self._generation

        try:
            protocol = ProtocolVersion(safe_int(self._setting("protocol", overrides), 3))
            client = device_selector(
                name=self.get_name(),
                device_id=int(str(self._setting("device_id", overrides))),
                device_type=int(DeviceType.AC),
                ip_address=str(self._setting("ip", overrides) or ""),
                port=safe_int(self._setting("port", overrides), 6444),
                token=str(self._setting("token", overrides) or ""),
                key=str(self._setting("key", overrides) or ""),
                device_protocol=protocol,
                model=str(self._setting("model", overrides) or "Unknown"),
                subtype=safe_int(self._setting("subtype", overrides), 0),
                customize=self._merged_customize(overrides),
                mac=str(self._setting("mac", overrides) or "") or None,
                serial_number=str(self._setting("serial_number", overrides) or "") or None,
            )
            if client is None:
                raise RuntimeError("midea-lan did not create an AC device handler")
            safe_mode = self._safe_mode_enabled(overrides)
            if safe_mode:
                self._disable_active_ac_queries(client)
                self.log(
                    "Safe LAN mode active: AC-specific status/capability queries are disabled; "
                    "using device push notifications and TCP heartbeat only."
                )
            else:
                client.set_refresh_interval(
                    max(15, safe_int(self._setting("poll_interval", overrides), 30))
                )

            def update_callback(status: dict[str, Any]) -> None:
                if self._closing or generation != self._generation:
                    return
                payload = dict(status or {})
                meaningful = bool(set(payload) - {"available"})

                # In Safe Mode, set_available(True) is emitted immediately after the
                # appliance handshake. At that point client.attributes still contains
                # constructor defaults (power=False, target=24, ...). Never copy those
                # defaults over Homey's persisted real state.
                if safe_mode and not meaningful:
                    available = bool(payload.get("available", True))

                    def schedule_availability() -> None:
                        if not self._closing and generation == self._generation:
                            asyncio.create_task(
                                self._apply_availability_only(available, generation)
                            )

                    try:
                        self._loop.call_soon_threadsafe(schedule_availability)
                    except RuntimeError:
                        pass
                    return

                snapshot: dict[str, Any] = {}
                try:
                    snapshot.update(client.attributes)
                except Exception:
                    pass
                snapshot.update(payload)
                if meaningful:
                    self._have_device_state = True
                    self._last_device_update_monotonic = time.monotonic()

                def schedule() -> None:
                    if not self._closing and generation == self._generation:
                        asyncio.create_task(self._apply_status(snapshot, generation))

                try:
                    self._loop.call_soon_threadsafe(schedule)
                except RuntimeError:
                    pass

            client.register_update(update_callback)
            self._client = client
            self._update_callback = update_callback
            client.open()
        except Exception as exc:
            self.error(f"Midea initialization failed: {type(exc).__name__}: {exc}")
            await self.set_unavailable(f"Midea initialization failed: {exc}")

    async def _stop_client(self) -> None:
        client = self._client
        callback = self._update_callback
        self._client = None
        self._update_callback = None
        if client is None:
            return
        if callback is not None:
            try:
                client.unregister_update(callback)
            except Exception:
                pass
        try:
            client.close()
            await asyncio.to_thread(client.join, 5.0)
        except Exception as exc:
            self.log(f"Midea client shutdown warning: {exc}")

    async def rebuild_connection(
        self, overrides: dict[str, Any] | None = None
    ) -> None:
        await self.set_unavailable("Reconnecting to the Midea air conditioner...")
        await self._start_client(overrides)

    async def on_uninit(self) -> None:
        self._closing = True
        await self._stop_safe_energy_loop()
        await self._persist_energy(force=True)
        await self._stop_client()
        await super().on_uninit()

    async def on_deleted(self) -> None:
        self._closing = True
        await self._stop_safe_energy_loop()
        await self._persist_energy(force=True)
        await self._stop_client()

    async def on_settings(
        self,
        old_settings: dict[str, Any],
        new_settings: dict[str, Any],
        changed_keys: list[str] | tuple[str, ...],
    ) -> str | None:
        keys = set(changed_keys or [])
        if keys & {"ip", "port", "device_id", "protocol", "model", "subtype", "token", "key", "mac", "serial_number", "customize", "power_analysis_method", "safe_mode"}:
            await self.rebuild_connection(new_settings)
        elif "poll_interval" in keys and self._client is not None:
            if self._safe_mode_enabled(new_settings):
                self._client.set_refresh_interval(0)
            else:
                self._client.set_refresh_interval(
                    max(15, safe_int(new_settings.get("poll_interval"), 30))
                )

        if keys & {"consumption_mode", "cooling_power_w", "heating_power_w", "dry_power_w", "fan_power_w", "standby_power_w", "estimated_load_factor", "power_calibration_percent"}:
            await self._update_energy_from_status(
                dict(self._status),
                available=bool(self._status.get("available", True)),
                settings_overrides=new_settings,
            )
        return None

    async def _set_capability(self, capability: str, value: Any) -> None:
        if value is None or not self.has_capability(capability):
            return
        try:
            if self.get_capability_value(capability) != value:
                await self.set_capability_value(capability, value)
        except Exception as exc:
            self.error(f"Could not update {capability}: {exc}")

    async def _apply_status(self, snapshot: dict[str, Any], generation: int) -> None:
        if generation != self._generation or self._closing:
            return
        async with self._apply_lock:
            self._status.update(snapshot)
            status = self._status
            available = bool(status.get("available", True))

            power = bool(status.get("power", False))
            mode = mode_from_status(power, status.get("mode"))
            fan = fan_from_raw(status.get("fan_speed"))
            swing = swing_from_status(status.get("swing_vertical"), status.get("swing_horizontal"))

            await self._set_capability("onoff", power)
            await self._set_capability("midea_mode", mode)
            await self._set_capability("target_temperature", safe_float(status.get("target_temperature")))
            await self._set_capability("measure_temperature", safe_float(status.get("indoor_temperature")))
            await self._set_capability("measure_temperature.outdoor", safe_float(status.get("outdoor_temperature")))

            humidity = safe_float(status.get("indoor_humidity"))
            if humidity in (0.0, 255.0):
                humidity = None
            await self._set_capability("measure_humidity", humidity)
            await self._set_capability("midea_fan_speed", fan)
            await self._set_capability("midea_swing_mode", swing)

            bool_map = {
                "midea_eco": "eco_mode",
                "midea_turbo": "boost_mode",
                "midea_sleep": "sleep_mode",
                "midea_display": "screen_display",
                "midea_frost_protect": "frost_protect",
                "midea_breezeless": "breezeless",
                "midea_purifier": "anion",
                "midea_beep": "prompt_tone",
                "midea_self_clean": "self_clean",
            }
            for capability, attribute in bool_map.items():
                if attribute in status and status.get(attribute) is not None:
                    await self._set_capability(capability, bool(status.get(attribute)))

            error_code = max(0, safe_int(status.get("error_code"), 0))
            await self._set_capability("midea_error_code", error_code)
            await self._set_capability("alarm_generic", error_code != 0)

            await self._update_target_range(status)
            await self._update_energy_from_status(status, available=available)
            self._device_available = available

            if available:
                try:
                    await self.set_available()
                except Exception:
                    pass
            else:
                try:
                    await self.set_unavailable("The Midea air conditioner is offline")
                except Exception:
                    pass

            if self._last_mode is not None and mode != self._last_mode:
                await self.driver.trigger_mode_changed(self, mode)
            self._last_mode = mode

            if self._last_error is not None and error_code and error_code != self._last_error:
                await self.driver.trigger_error(self, error_code)
            self._last_error = error_code

    async def _update_target_range(self, status: dict[str, Any]) -> None:
        minimum = safe_float(status.get("min_temperature"))
        maximum = safe_float(status.get("max_temperature"))
        step = safe_float(getattr(self._client, "temperature_step", None)) if self._client is not None else None
        if minimum is None or maximum is None or minimum >= maximum:
            return
        options = {"min": minimum, "max": maximum, "step": 1.0 if step == 1 else 0.5}
        try:
            await self.set_capability_options("target_temperature", options)
        except Exception:
            pass

    def _estimate_config(
        self, overrides: dict[str, Any] | None = None
    ) -> EstimateConfig:
        return EstimateConfig(
            cooling_power_w=safe_float(self._setting("cooling_power_w", overrides)) or 1000.0,
            heating_power_w=safe_float(self._setting("heating_power_w", overrides)) or 1100.0,
            dry_power_w=safe_float(self._setting("dry_power_w", overrides)) or 700.0,
            fan_power_w=safe_float(self._setting("fan_power_w", overrides)) or 50.0,
            standby_power_w=max(
                0.0, safe_float(self._setting("standby_power_w", overrides)) or 0.0
            ),
            load_factor=min(
                1.0,
                max(
                    0.1,
                    (safe_float(self._setting("estimated_load_factor", overrides)) or 65.0)
                    / 100.0,
                ),
            ),
        )

    async def _update_energy_from_status(
        self,
        status: dict[str, Any],
        *,
        available: bool,
        settings_overrides: dict[str, Any] | None = None,
    ) -> None:
        power = bool(status.get("power", False))
        mode = mode_from_status(power, status.get("mode"))
        estimated = estimate_power_w(
            available=available,
            powered=power,
            mode=mode,
            turbo=bool(status.get("boost_mode", False)),
            eco=bool(status.get("eco_mode", False)),
            config=self._estimate_config(settings_overrides),
        )
        poll_interval = max(
            15, safe_int(self._setting("poll_interval", settings_overrides), 30)
        )
        native_power = safe_float(status.get("realtime_power"))
        # A number of Wi-Fi modules expose the field but return a permanent
        # zero while the AC is running. Treat that as unavailable instead of
        # integrating 0 W for the whole day. The automatic strategy can then
        # use native counter deltas or the configured estimate.
        if power and native_power is not None and native_power < 5.0:
            native_power = None
        if native_power is not None:
            calibration = min(
                4.0,
                max(
                    0.25,
                    (safe_float(
                        self._setting("power_calibration_percent", settings_overrides)
                    ) or 100.0)
                    / 100.0,
                ),
            )
            native_power *= calibration

        reading = self._energy_tracker.update(
            now=time.time(),
            available=available,
            energy_mode=str(
                self._setting("consumption_mode", settings_overrides) or "auto"
            ),
            native_total_kwh=status.get("total_energy_consumption"),
            native_session_kwh=status.get("current_energy_consumption"),
            native_power_w=native_power,
            estimated_power_w=estimated,
            max_gap_seconds=max(300.0, poll_interval * 3.0),
        )
        await self._set_capability("measure_power", reading.power_w)
        await self._set_capability("meter_power", reading.total_kwh)
        await self._set_capability("midea_energy_source", reading.source)

        if reading.power_is_estimated != self._last_power_approximated:
            try:
                await self.set_capability_options(
                    "measure_power", {"approximated": reading.power_is_estimated}
                )
            except Exception as exc:
                self.log(f"Could not mark the power measurement approximation: {exc}")
            self._last_power_approximated = reading.power_is_estimated

        if self._last_energy_source is not None and reading.source != self._last_energy_source:
            await self.driver.trigger_energy_source_changed(self, reading.source)
        self._last_energy_source = reading.source
        await self._persist_energy()

    async def _persist_energy(self, force: bool = False) -> None:
        now = time.monotonic()
        changed = abs(
            self._energy_tracker.total_kwh - self._last_energy_persisted_total
        )
        if not force and now - self._last_energy_save < 60 and changed < 0.01:
            return
        try:
            await self.set_store_value("energy_state", self._energy_tracker.to_state())
            self._last_energy_save = now
            self._last_energy_persisted_total = self._energy_tracker.total_kwh
        except Exception as exc:
            self.log(f"Could not persist energy state: {exc}")

    async def set_boolean_feature(self, attribute: str, enabled: bool) -> None:
        client = self._require_client()
        await asyncio.to_thread(client.set_attribute, attribute, bool(enabled))

    async def set_mode(self, mode: str) -> None:
        client = self._require_client()
        if mode == "off":
            await asyncio.to_thread(client.set_attribute, "power", False)
        else:
            await asyncio.to_thread(client.set_attribute, "mode", mode_to_raw(mode))

    async def set_fan_speed(self, speed: str) -> None:
        client = self._require_client()
        await asyncio.to_thread(client.set_attribute, "fan_speed", fan_to_raw(speed))

    async def set_swing(self, swing: str) -> None:
        client = self._require_client()
        vertical, horizontal = swing_to_flags(swing)
        await asyncio.to_thread(client.set_swing, vertical, horizontal)

    async def boost_cool(self) -> None:
        client = self._require_client()

        def send_boost() -> None:
            message = client.make_message_uniq_set()
            message.power = True
            message.mode = mode_to_raw("cool")
            message.target_temperature = 16.0
            message.fan_speed = fan_to_raw("full")
            message.boost_mode = True
            client.build_send(message)

        await asyncio.to_thread(send_boost)

    async def refresh_now(self) -> None:
        client = self._require_client()
        if self._safe_mode_enabled():
            # Safe Mode deliberately performs no status query. Re-apply the latest
            # in-memory attributes only; device notifications remain authoritative.
            snapshot = dict(self._status)
            if snapshot:
                await self._apply_status(snapshot, self._generation)
            self.log("Safe LAN mode: manual active refresh skipped (zero AC queries sent).")
            return
        await asyncio.to_thread(client.refresh_status)

    def current_mode(self) -> str:
        value = self.get_capability_value("midea_mode")
        return str(value or "off")

    def energy_source(self) -> str:
        value = self.get_capability_value("midea_energy_source")
        return str(value or "unavailable")


homey_export = MideaACDevice

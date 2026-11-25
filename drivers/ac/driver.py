from __future__ import annotations

import asyncio
from typing import Any

from homey.driver import Driver

from midealan.const import DeviceType, ProtocolVersion
from midealan.discover import discover

from app.lib.pairing import PairingError, prepare_pairing_devices


class MideaACDriver(Driver):
    async def on_init(self) -> None:
        flow = self.homey.flow

        self._mode_trigger = flow.get_device_trigger_card("midea_mode_changed")
        self._energy_source_trigger = flow.get_device_trigger_card("midea_energy_source_changed")
        self._error_trigger = flow.get_device_trigger_card("midea_error_occurred")
        self._filter_dirty_trigger = flow.get_device_trigger_card("midea_filter_dirty")
        self._filter_clean_trigger = flow.get_device_trigger_card("midea_filter_cleaned")

        async def set_mode(args: dict[str, Any], **kwargs: Any) -> None:
            await args["device"].set_mode(str(args["mode"]))

        async def set_fan(args: dict[str, Any], **kwargs: Any) -> None:
            await args["device"].set_fan_speed(str(args["speed"]))

        async def set_swing(args: dict[str, Any], **kwargs: Any) -> None:
            await args["device"].set_swing(str(args["swing"]))

        async def set_eco(args: dict[str, Any], **kwargs: Any) -> None:
            await args["device"].set_boolean_feature("eco_mode", args["enabled"] == "on")

        async def set_turbo(args: dict[str, Any], **kwargs: Any) -> None:
            await args["device"].set_boolean_feature("boost_mode", args["enabled"] == "on")

        async def boost_cool(args: dict[str, Any], **kwargs: Any) -> None:
            await args["device"].boost_cool()

        async def refresh(args: dict[str, Any], **kwargs: Any) -> None:
            await args["device"].refresh_now()

        async def is_mode(args: dict[str, Any], **kwargs: Any) -> bool:
            return args["device"].current_mode() == str(args["mode"])

        async def filter_is_dirty(args: dict[str, Any], **kwargs: Any) -> bool:
            return args["device"].filter_needs_cleaning()

        async def energy_is_native(args: dict[str, Any], **kwargs: Any) -> bool:
            return args["device"].energy_source() == "native"

        flow.get_action_card("midea_set_mode").register_run_listener(set_mode)
        flow.get_action_card("midea_set_fan_speed").register_run_listener(set_fan)
        flow.get_action_card("midea_set_swing").register_run_listener(set_swing)
        flow.get_action_card("midea_set_eco").register_run_listener(set_eco)
        flow.get_action_card("midea_set_turbo").register_run_listener(set_turbo)
        flow.get_action_card("midea_boost_cool").register_run_listener(boost_cool)
        flow.get_action_card("midea_refresh_now").register_run_listener(refresh)
        flow.get_condition_card("midea_is_mode").register_run_listener(is_mode)
        flow.get_condition_card("midea_energy_is_native").register_run_listener(energy_is_native)
        flow.get_condition_card("midea_filter_is_dirty").register_run_listener(filter_is_dirty)

        self.log("Midea AC driver initialized")

    async def trigger_mode_changed(self, device, mode: str) -> None:
        await self._mode_trigger.trigger(device, {"mode": mode})

    async def trigger_energy_source_changed(self, device, source: str) -> None:
        await self._energy_source_trigger.trigger(device, {"source": source})

    async def trigger_error(self, device, code: int) -> None:
        await self._error_trigger.trigger(device, {"code": code})

    async def trigger_filter(self, device, dirty: bool) -> None:
        card = self._filter_dirty_trigger if dirty else self._filter_clean_trigger
        await card.trigger(device, {})

    def _known_keys(self) -> dict[int, tuple[str, str]]:
        """V3 keys of ACs already paired, so they can be re-added locally."""
        keys: dict[int, tuple[str, str]] = {}
        for device in self.get_devices():
            try:
                device_id = int(str(device.get_setting("device_id") or "0"))
                token = str(device.get_setting("token") or "")
                key = str(device.get_setting("key") or "")
            except Exception:
                continue
            if device_id and token and key:
                keys[device_id] = (token, key)
        return keys

    async def on_pair(self, session) -> None:
        found_devices = []

        async def login(data: dict[str, Any]) -> bool:
            nonlocal found_devices
            try:
                found_devices = await prepare_pairing_devices(
                    ip=str(data.get("ip") or ""),
                    account=str(data.get("account") or ""),
                    password=str(data.get("password") or ""),
                    manual_token=str(data.get("token") or ""),
                    manual_key=str(data.get("key") or ""),
                    known_keys=self._known_keys(),
                )
            except PairingError:
                raise
            except Exception as exc:
                self.error(f"Pairing failed: {type(exc).__name__}: {exc}")
                raise PairingError(f"Pairing failed: {exc}") from exc
            self.log(f"Pairing found {len(found_devices)} Midea AC device(s)")
            return True

        async def list_devices(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            if not found_devices:
                raise PairingError("No air conditioner is ready to add. Go back and run discovery again.")
            return [device.as_homey_device() for device in found_devices]

        async def discover_devices(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            """Broadcast search on the LAN; no cloud involved."""
            try:
                found = await asyncio.to_thread(lambda: discover([int(DeviceType.AC)]))
            except Exception as exc:
                self.error(f"Discovery failed: {type(exc).__name__}: {exc}")
                found = {}
            known = self._known_keys()
            paired: set[int] = set()
            for device in self.get_devices():
                try:
                    paired.add(int(str(device.get_setting("device_id") or "0")))
                except ValueError:
                    pass
            result = []
            for device_id, info in sorted((found or {}).items()):
                device_id = int(device_id)
                protocol = int(info.get("protocol") or 3)
                result.append({
                    "device_id": str(device_id),
                    "ip": str(info.get("ip_address") or ""),
                    "model": str(info.get("model") or ""),
                    "protocol": protocol,
                    "needs_login": protocol == int(ProtocolVersion.V3) and device_id not in known,
                    "paired": device_id in paired,
                })
            self.log(f"Discovery found {len(result)} air conditioner(s)")
            return result

        session.set_handler("discover", discover_devices)
        session.set_handler("login", login)
        session.set_handler("list_devices", list_devices)

    async def on_repair(self, session, device) -> None:
        async def get_current(data: dict[str, Any] | None = None) -> dict[str, Any]:
            return {
                "ip": str(device.get_setting("ip") or ""),
                "device_id": str(device.get_setting("device_id") or ""),
                "protocol": str(device.get_setting("protocol") or ""),
            }

        async def repair(data: dict[str, Any]) -> bool:
            current_token = str(device.get_setting("token") or "")
            current_key = str(device.get_setting("key") or "")
            found = await prepare_pairing_devices(
                ip=str(data.get("ip") or device.get_setting("ip") or ""),
                account=str(data.get("account") or ""),
                password=str(data.get("password") or ""),
                manual_token=str(data.get("token") or current_token),
                manual_key=str(data.get("key") or current_key),
                known_keys=self._known_keys(),
            )
            expected = str(device.get_setting("device_id") or "")
            match = next((item for item in found if str(item.device_id) == expected), None)
            if match is None:
                raise PairingError(f"The device at this IP has a different ID. Expected {expected}.")
            discovered_settings = match.as_homey_device()["settings"]
            connection_keys = {
                "ip", "port", "device_id", "protocol", "model", "subtype",
                "serial_number", "mac", "token", "key",
            }
            connection_settings = {
                key: value
                for key, value in discovered_settings.items()
                if key in connection_keys
            }
            await device.set_settings(connection_settings)
            await device.rebuild_connection(connection_settings)
            return True

        session.set_handler("get_current", get_current)
        session.set_handler("repair", repair)


homey_export = MideaACDriver

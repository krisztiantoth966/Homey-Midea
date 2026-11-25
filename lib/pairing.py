from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any

import certifi
from aiohttp import ClientSession, TCPConnector
from midealan.cloud import MideaCloud, get_midea_cloud
from midealan.const import DeviceType, ProtocolVersion
from midealan.device import MideaDevice
from midealan.discover import discover


class PairingError(RuntimeError):
    pass


@dataclass(frozen=True)
class PairingDevice:
    device_id: int
    name: str
    ip: str
    port: int
    protocol: int
    model: str
    subtype: int
    serial_number: str
    mac: str
    token: str
    key: str

    def as_homey_device(self) -> dict[str, Any]:
        settings = {
            "ip": self.ip,
            "port": self.port,
            "device_id": str(self.device_id),
            "protocol": str(self.protocol),
            "model": self.model,
            "subtype": self.subtype,
            "serial_number": self.serial_number,
            "mac": self.mac,
            "token": self.token,
            "key": self.key,
            "poll_interval": 15,
            "query_mode": "light",
            "power_analysis_method": "auto",
            "customize": "",
            "consumption_mode": "auto",
            "power_calibration_percent": 100,
            "cooling_power_w": 1000,
            "heating_power_w": 1100,
            "dry_power_w": 700,
            "fan_power_w": 50,
            "standby_power_w": 2,
            "estimated_load_factor": 65,
        }
        return {
            "name": self.name,
            "data": {"id": f"midea_ac_{self.device_id}"},
            "store": {"device_type": int(DeviceType.AC)},
            "settings": settings,
        }


def _validate_ip(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = ip_address(value)
    except ValueError as exc:
        raise PairingError(f"Invalid IP address: {value}") from exc
    if parsed.version != 4:
        raise PairingError("Only IPv4 addresses are supported by Midea LAN discovery.")
    return value


def _validate_hex_pair(token: str, key: str) -> tuple[str, str]:
    token = token.strip()
    key = key.strip()
    if bool(token) != bool(key):
        raise PairingError("Both token and key are required together.")
    if not token:
        return "", ""
    try:
        bytes.fromhex(token)
        bytes.fromhex(key)
    except ValueError as exc:
        raise PairingError("Token or key is not valid hexadecimal text.") from exc
    return token.lower(), key.lower()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _verify_sync(raw: dict[str, Any], token: str, key: str) -> bool:
    dm = MideaDevice(
        name="Midea verification",
        device_id=int(raw["device_id"]),
        device_type=int(DeviceType.AC),
        ip_address=str(raw["ip_address"]),
        port=int(raw.get("port") or 6444),
        token=token,
        key=key,
        device_protocol=ProtocolVersion(int(raw.get("protocol") or 3)),
        model=str(raw.get("model") or "Unknown"),
        subtype=0,
        attributes={},
        mac=str(raw.get("mac") or "") or None,
        serial_number=str(raw.get("sn") or "") or None,
    )
    try:
        return bool(dm.connect())
    finally:
        dm.close_socket()


async def _verify(raw: dict[str, Any], token: str, key: str) -> bool:
    return await asyncio.to_thread(_verify_sync, raw, token, key)


async def _discover(ip: str) -> dict[int, dict[str, Any]]:
    return await asyncio.to_thread(
        lambda: discover([int(DeviceType.AC)], ip_address=ip or None)
    )


async def prepare_pairing_devices(
    *,
    ip: str,
    account: str,
    password: str,
    manual_token: str = "",
    manual_key: str = "",
    known_keys: dict[int, tuple[str, str]] | None = None,
) -> list[PairingDevice]:
    """Discover ACs on the LAN and prepare them for Homey.

    known_keys maps device IDs to V3 (token, key) pairs Homey already has
    (devices paired before), so re-adding them needs no cloud login at all.
    """
    known_keys = {
        int(device_id): (str(pair[0]).lower(), str(pair[1]).lower())
        for device_id, pair in (known_keys or {}).items()
        if pair and pair[0] and pair[1]
    }
    ip = _validate_ip(ip)
    manual_token, manual_key = _validate_hex_pair(manual_token, manual_key)
    account = account.strip()
    password = str(password or "")
    if bool(account) != bool(password):
        raise PairingError("Enter both the SmartHome account and password.")

    found = await _discover(ip)
    if not found:
        if ip:
            raise PairingError(
                f"No Midea air conditioner answered at {ip}. Check the IP, Wi-Fi and VLAN/firewall rules."
            )
        raise PairingError(
            "No Midea air conditioner was discovered. Enter its IP address from your router and try again."
        )

    needs_cloud = any(
        int(item.get("protocol") or 3) == int(ProtocolVersion.V3)
        and int(device_id) not in known_keys
        for device_id, item in found.items()
    )
    if needs_cloud and not manual_token and (not account or not password):
        raise PairingError(
            "This air conditioner uses encrypted V3 LAN and Homey does not know its key yet. "
            "Enter the SmartHome account once (only used to fetch the local key), "
            "or a backed-up V3 token and key."
        )

    http: ClientSession | None = None
    cloud = None
    result: list[PairingDevice] = []
    try:
        if account and password:
            # Homey Python app containers do not always ship a complete system CA
            # trust store. Use certifi explicitly for SmartHome HTTPS requests so
            # mp-prod.appsmb.com can be verified without disabling TLS validation.
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            http = ClientSession(connector=TCPConnector(ssl=ssl_context))
            cloud = get_midea_cloud("SmartHome", http, account, password)
            try:
                logged_in = await cloud.login()
            except Exception as exc:
                raise PairingError(
                    f"SmartHome login failed: {type(exc).__name__}: {exc}"
                ) from exc
            if not logged_in:
                raise PairingError(
                    "SmartHome login failed. Check the account and password. "
                    "Use the same account that works in the SmartHome mobile app."
                )

        for device_id, item in found.items():
            raw = dict(item)
            raw["device_id"] = int(device_id)
            protocol = int(raw.get("protocol") or ProtocolVersion.V3)
            token = ""
            key = ""
            cloud_info: dict[str, Any] = {}

            if cloud is not None:
                try:
                    cloud_info = await cloud.get_device_info(int(device_id)) or {}
                except Exception:
                    cloud_info = {}

            if protocol == int(ProtocolVersion.V3):
                candidates: list[tuple[str, str]] = []
                if int(device_id) in known_keys:
                    candidates.append(known_keys[int(device_id)])
                if manual_token and manual_key:
                    candidates.append((manual_token, manual_key))
                if cloud is not None:
                    try:
                        cloud_keys = await cloud.get_cloud_keys(int(device_id)) or {}
                    except Exception:
                        cloud_keys = {}
                    for value in cloud_keys.values():
                        if isinstance(value, dict) and value.get("token") and value.get("key"):
                            candidates.append((str(value["token"]), str(value["key"])))
                    try:
                        defaults = await MideaCloud.get_default_keys()
                    except Exception:
                        defaults = {}
                    for value in defaults.values():
                        if isinstance(value, dict) and value.get("token") and value.get("key"):
                            candidates.append((str(value["token"]), str(value["key"])))

                seen: set[tuple[str, str]] = set()
                for candidate_token, candidate_key in candidates:
                    pair = (candidate_token.lower(), candidate_key.lower())
                    if pair in seen:
                        continue
                    seen.add(pair)
                    if await _verify(raw, pair[0], pair[1]):
                        token, key = pair
                        break
                if not token or not key:
                    raise PairingError(
                        f"The V3 token/key could not be obtained or verified for device {device_id}."
                    )
            else:
                if not await _verify(raw, "", ""):
                    raise PairingError(f"The air conditioner at {raw.get('ip_address')} did not accept a LAN connection.")

            subtype = _safe_int(cloud_info.get("model_number"), 0)
            name = str(cloud_info.get("name") or "").strip()
            if not name:
                model = str(raw.get("model") or "").strip()
                name = f"Midea AC {model}" if model else f"Midea AC {device_id}"

            result.append(
                PairingDevice(
                    device_id=int(device_id),
                    name=name,
                    ip=str(raw.get("ip_address") or ip),
                    port=int(raw.get("port") or 6444),
                    protocol=protocol,
                    model=str(raw.get("model") or "Unknown"),
                    subtype=subtype,
                    serial_number=str(raw.get("sn") or ""),
                    mac=str(raw.get("mac") or ""),
                    token=token,
                    key=key,
                )
            )
    finally:
        if http is not None:
            await http.close()

    return result

# Midea LAN for Homey

Native Homey Pro application for local control of Midea-family air conditioners. Pairing supports the **SmartHome** account backend, while normal control runs directly over the LAN.

## Main functions

- Power, Auto/Cool/Dry/Heat/Fan-only mode and 0.5 °C target temperature.
- Indoor/outdoor temperature and indoor humidity when reported by the AC.
- Fan speed, vertical/horizontal swing, Eco, Turbo, Sleep, display, 8 °C protection, Breezeless, ionizer, prompt tone and self-clean.
- Protocol V1/V2 and authenticated V3 devices through `midea-lan`.
- Homey Energy support using `measure_power` and a monotonic `meter_power` counter.
- SmartHome credentials are requested only in the pairing/repair form and are not saved in the Homey device settings.

## Pairing

1. Give the AC a DHCP reservation/static IP in the router.
2. Add **Midea LAN → Midea Air Conditioner** in Homey.
3. Enter the AC IP address.
4. Enter the same SmartHome e-mail/phone and password that work in the SmartHome mobile application.
5. For V3 devices, the app retrieves and verifies a LAN token/key. The SmartHome password is not stored after pairing; only the LAN connection details are stored.

A manually backed-up V3 token and key can be entered under **Advanced** instead of using SmartHome credentials.

## Homey Energy

The driver exposes:

- `measure_power` — current consumption in watts,
- `meter_power` — a cumulative, never-decreasing counter in kWh.

Version 1.0.4 uses a new hybrid tracker:

1. During normal online operation, Homey integrates the AC's live `realtime_power` value.
2. Native total/session counters are tracked in parallel and recover longer gaps or periods without usable live power.
3. A configurable estimate is used only when the AC cannot provide live power.
4. The existing Homey meter value is preserved during an upgrade, even if the stored tracker state is older.

This fixes models where the native cumulative LAN counter is stale, low-resolution, or decoded differently from the SmartHome cloud report. The active source is visible as **Energy source**. When the watt value is estimated, Homey marks it as approximated.

The optional **Live power calibration** should normally remain at 100%. Change it only after comparing a complete day with SmartHome or an external meter.

## Midea power decoding

Midea devices use several power-data encodings. The setting **Native power decoding method** now includes **Automatic**:

- PortaSplit → method `12`,
- model `00000Q1D`, subtype `524` → method `101`,
- other models → method `1`.

Manual methods `1`, `2`, `3`, `12`, and `101` remain available when the reported W/kWh values are clearly wrong.

## Development install

```bash
homey app dependencies install
homey app validate
homey app run
```

The dependency command prepares `midea-lan==2026.9.0` and `certifi` for the target Homey platform.

## Requirements

- Homey Pro with Homey OS compatible with Python apps (`>=13.2.1`).
- AC and Homey on a network path that allows local TCP/UDP communication.
- SmartHome credentials for most V3 Wi-Fi modules, unless token/key are entered manually.

## Credits

Protocol and device handling are provided by `wuwentao/midea-lan`, which was separated from `wuwentao/midea_ac_lan`. See `THIRD_PARTY_NOTICES.md`.


## Safe LAN Mode (v1.0.4)

Safe LAN Mode is enabled by default for both ON and OFF states. It suppresses midea-lan AC-specific periodic and startup capability/status query bursts, retaining only the base appliance handshake, encrypted TCP connection, heartbeat, control commands, and unsolicited device updates. This is intended to avoid the known 35/88 display wake, beeping and Wi-Fi reset behaviour reported with newer Midea query sets. Energy tracking continues locally every minute without querying the AC. Disable Safe LAN Mode only for troubleshooting on a model known to tolerate active polling.

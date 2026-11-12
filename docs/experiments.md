# Experiments

Flashes performed on a real TRVZB, in order. Each entry records the image, how
it differs from stock, and the outcome.

## 1. Version-strings-only image (1.4.5 / MCU 1.3.2)

**Question:** does the device accept a self-supplied OTA, and does the
application-MCU part of it actually get written?

**Image:** stock 1.4.4 with 9 bytes changed, built by
`python3 -I tools/build_ota.py work/trvzb_v1.4.5-verstest.ota --mcu-version 1.3.2
--file-version 0x1405` (reproduced byte for byte by the golden test):

- outer header: file version `0x1404` → `0x1405`; header string
  `vers:00001404,00001301` → `vers:00001405,00001302`
- both slots: the three `1.3.1` version strings → `1.3.2`

No code byte changed. SHA-512 `cc146b65…99820f56`.

**Delivery:** served from the Zigbee2MQTT data directory through
`ota.zigbee_ota_override_index_location: ota/local-index.json`. Zigbee2MQTT
offered it (`update_available: true`) and started the transfer on 2026-11-12
at 12:47 UTC, estimated at 1661 s (6645 chunks).

**Device before:** installed version 5124, battery 69 %, valve opening and
closing degree 100, idle steps 297, closing steps 343, setpoint pinned at 35 °C.

**Outcome: accepted.** Zigbee2MQTT reported the upgrade complete with status
`ok` at 13:38:50 UTC, about 51 minutes after starting: the transfer took about
30 minutes, then the Zigbee SoC installed and rejoined the network at 13:34.
The device now reports file version 5125 (from 5124); `software_build_id`
stays `1.4.4`, since the SoC image itself is unchanged.

**Device after:** all settings kept (setpoint 35 °C, opening and closing degree
100, child lock on): data flash was not touched. Battery 69 % → 65 %: one
flash costs about 4 %.

**Was the MCU part written?** Not directly observable: Zigbee2MQTT exposes no
application-MCU version. Indirect evidence says yes: the motor calibration
values changed (idle steps 297 → 314, closing steps 343 → 342), as they would
if the MCU restarted into new firmware and re-ran its travel calibration. That
is consistent with the update path committing and restarting, but it is not
proof. If the MCU did take `1.3.2`, it now refuses stock `1.3.1`.

**Still open:** an observable for the MCU version. Candidate: Zigbee2MQTT's
generic `read` converter on `genBasic` and the manufacturer cluster `0xfc11`,
looking for an attribute that carries it.

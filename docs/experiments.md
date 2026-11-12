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

No code byte changed. SHA-512:

```
cc146b65c5056c0aefc009a282b70ff4dc297424431be420043af8a398384396
024b1a5c761aa66e7e7041230d18f06c39ff288cb36d538ee1a1e03499820f56
```

Built with the tools at commit `ef0a73b`.

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

**Was the MCU part written? Unknown.** Zigbee2MQTT exposes no
application-MCU version. The motor calibration values changed (idle steps
297 → 314, closing steps 343 → 342), which shows the MCU restarted and re-ran
its travel calibration, but both outcomes restart it: on a transfer failure
(`0x53d2`–`0x53e6`) the MCU sets settings `+5`, saves, and reaches the same
reset (`0x1d9c`) as the success path. So the device runs either `1.3.1` (stock,
checksum rejected) or `1.3.2`. Until that is known, plan the next MCU version
above `1.3.2`, and do not assume stock `1.3.1` would be refused or accepted.
The knob patch's visible behaviour will be the first real observable.

**Still open:** an observable for the MCU version or the failure flag in
settings `+5`. Candidate: Zigbee2MQTT's generic `read` converter on `genBasic`
and the manufacturer cluster `0xfc11`, looking for an attribute that carries
either.

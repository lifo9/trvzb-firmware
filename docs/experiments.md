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
Zigbee2MQTT now shows installed version 5125, but that is the header of the
image it served, not a value read from the device. The SoC's own OTA client
attribute `currentFileVersion` still reads 5124, and `software_build_id`
stays `1.4.4`: the SoC application is stock and carries its version built in.

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

**Aftermath: an update retry loop (found the same day).** From the moment the
update installed (14:34) valve-1 rejoined the network every 1–5 minutes; no
other device rejoined, and it had not rejoined once in the 14 hours before.
Each rejoin wakes the screen (the network-status handler `0x5688` calls
`display_wake`) and the battery fell about 2 % an hour.

A debug capture shows the cycle: the valve sends `UpgradeEndRequest` for 5125
with status success, about 13 s later it reboots and rejoins, then after one
to five minutes asks again. Its ZCL transaction number is 14 every time, so
each cycle is a full reboot. Zigbee2MQTT had no update session left to answer
in. Pulling the batteries did not stop it, so the state is in flash.

A temporary Zigbee2MQTT extension then answered three requests with exactly
the `upgradeEndResponse` zigbee-herdsman sends (`currentTime` 0,
`upgradeTime` 1, the request's transaction number). The valve rebooted ~13 s
after each answer and asked again, as before.

While looping the valve ignored every update offer ("device didn't respond"),
never sent an image query of its own, and a factory reset (knob held while
inserting the batteries, `FA`) cleared the application MCU's settings but left
the Zigbee SoC's network and the loop untouched.

**Cause: the image header's version differs from the SoC's built-in one.**
The SoC runs Silicon Labs' standard OTA client (gecko_sdk
`plugin/ota-client/ota-client.c`). Every time the network comes up,
`determineDownloadFileStatus()` inspects the stored image: a fully downloaded
image whose header version **differs** from the running version (a constant
built into the SoC application, 5124 here) is verified and installed again
via `UpgradeEndRequest`. The image's header says 5125 while its SoC
application is stock 1.4.4, so every install boots into the same mismatch.
This explains every symptom: the fixed 5125 in each request, a full reboot per
cycle, offers ignored (Image Notify is dropped in that state, and the client
never reaches the query state), and no effect from answering or resetting.
The same loop is reported on a SONOFF ZBMINIR2 (no application MCU) after a
header-only version bump, so it says nothing about the application-MCU
transfer: **whether the MCU accepted 1.3.2 is still unknown.**

**Fix: a ZCL Default Response with status ABORT (`0x95`) to the
`UpgradeEndRequest`.** In the client, a non-success default response in
`WAITING_FOR_UPGRADE_MESSAGE` reaches `upgradeEndResponseParse()` →
`downloadAndVerifyFinish(SERVER_ABORTED)` and returns to querying instead of
installing. zigbee-herdsman sends nothing outside an update session (the
command declares a response, so no automatic default response either). A
temporary Zigbee2MQTT external extension sent the ABORT at 18:18:13 local
time; the reboots stopped (the last was 18:13:56), and the OTA client then read
`imageUpgradeStatus` 0 (normal) and `currentFileVersion` 5124. **The abort
survives a power cut:** after a battery pull (rejoin at 18:39:32) the valve
sent no `UpgradeEndRequest` for 10 minutes, where the loop always asked within
1–5 minutes of booting. So the SoC discards the stored image on a server
abort, as the ZBMINIR2 report describes, and the extension was removed.

The planned recovery, `1.4.6-recovery` (stock content, header 5126), would
have restarted the loop at 5126 and was cancelled. Re-sending stock 1.4.4
cannot clean the storage either: the client skips an offer whose version
equals its running one.

Settings lost to the factory reset were written back from the snapshot taken
before it; the motor calibration values (idle and closing steps, limit
voltages) were re-measured by the device itself.

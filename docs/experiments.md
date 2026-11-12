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

The transfer itself very likely happened. After the last image block (14:22:23
local time) the valve was nearly silent for 12 minutes, rebooted at 14:34:00
and sent its first `UpgradeEndRequest` at 14:38:35. That matches Sonoff's own
account of the SoC updating the MCU between the last block and that request
(see `update-and-recovery.md`). The checksum is less likely to have failed than
first thought: the OTA file carries no checksum for the MCU part, so the SoC must
compute the sum it notifies from the stored bytes.

**Candidate observable (2026-11-12, after the loop was cleared and a power
cut):** ZCL Discover Attributes on endpoint 1 found one undocumented attribute:
`0xfc11`/`0x0011`, a character string, here the bytes `01 02 13 02`. Neither
zigbee-herdsman-converters nor the ZHA quirk defines it. `13 02` matches MCU
1.3.2 in Sonoff's header notation (`vers:…,00001302`). It is not yet known
whether the SoC fills it from the MCU or from the image header it forwarded.
The MCU can report its version live: it answers the version query `0x0B`
with the packed version parsed from its own version string (`0x4dfe`), and its
update-success report `0x0e` carries the new version (`0x53e8`–`0x5402`). The
value survived the power cut, which favours a live readout. Nothing else on
`genBasic` (`swBuildId` 1.4.4 is the SoC), client-side `genOta` or `0xfc11`
carries a version. Read it with Zigbee2MQTT's `read` converter on cluster
`0xfc11`, attribute 17.

**Still open:** whether `0xfc11`/`0x0011` reflects the running MCU. The next
accepted or refused flash settles it.

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

## 2. Letter-L canary (1.4.7 / MCU 1.3.3)

**Question:** does the application MCU accept and run a modified image
delivered by OTA on the stock 1.4.4 SoC firmware?

**Image:** release `1.4.7-canary` (tag `release/1.4.7-canary`): stock 1.4.4
with the MCU version raised to 1.3.3, file version `0x1407`, and patch set
`patches/canary-letter-l`, which draws the letter L with every segment lit.
No code byte changed; 11 bytes differ from stock in total. SHA-512:

```
310cbcd53861b692b0a2ce2b1a3d41ffa957971ad4b0efaf943b2b2b13a1987b
5246a09cdfa2ca6eff6ba391da781f05de88156134c9a3c399d5803a4a7471e3
```

**Delivery:** local index entry from `just index 1.4.7-canary`, with the ABORT
extension (`abort-stale-ota-end.mjs`, armed for file version `0x1407`) loaded
first. `ota_update/update` at 19:11:04 local time on 2026-11-12.

**Device before:** battery 61 %, mode heat, setpoint 35 °C, child lock off.

**Timeline:** last image block 19:45:40; 11.5 minutes of silence while the
SoC fed the MCU; reboot and rejoin 19:57:19; `UpgradeEndRequest` for 5127 at
19:59:40, answered by the extension's ABORT in the same second and by
zigbee-herdsman's own `upgradeEndResponse`. No reboot followed, so the ABORT
won and the SoC did not re-install; zigbee-herdsman gave up waiting for the
announce and logged "updated from 5124 to 5127", which only echoes the header.
No further request or rejoin came.

**Outcome: accepted.** Holding the knob for child lock shows `8C` instead of
`LC`. So the application MCU accepts a modified payload: the SoC computes the
notified checksum from the stored bytes, and the version gate passed (1.3.3
over whatever experiment 1 left). Delivery on the stock SoC firmware works
with the ABORT extension in place.

**Device after:** settings kept (mode heat, setpoint 35 °C, frost protection
7 °C). The MCU re-ran its travel calibration after restarting, with clearly
different results: closing steps 631 (340 after the factory reset), idle steps
20 (314), opening and closing limit voltages 2928 and 3131 mV (2060 and 2946).
The code is stock, so this is a physical re-measurement; watch whether the
valve still regulates normally.

`0xfc11`/`0x0011` read empty from the reboot until at least 20:01 (it held
`01 02 13 02` long after the previous boot), so the SoC fills it some time
after booting. That points to a live readout from the MCU; the value should
become `01 02 13 03`.

## 3. Knob patch (1.4.8 / MCU 1.3.4)

**Image:** release `1.4.8-knob` (tag `release/1.4.8-knob`): stock 1.4.4, MCU
version 1.3.4, file version `0x1408`, patch set `patches/knob-valve-percent`.
SHA-512:

```
c5284248aba199667a763457612907673255bd222bc1f06c621d8e5553b4fb38
f49584e461cee980d1c1ca9d9b8cefbda107c47b4567fd7fd9ea42557a509a32
```

**Delivery:** as for the canary, with the ABORT extension armed for `0x1408`.
`ota_update/update` at 20:08:14 local time on 2026-11-12; last block about
20:45:20; reboot 20:56:55; `UpgradeEndRequest` for 5128 at 20:57:57, aborted
in the same second, no reboot after it. The extension was removed and the local
index emptied at 21:00.

**Device before:** battery 61 %, mode heat, setpoint 35 °C, opening 76 % (set by
the Home Assistant thermostat), child lock off.

**Outcome: accepted.** The woken screen shows the valve opening (52) instead of
the setpoint, and each detent steps it by 10 %. Zigbee2MQTT received every step
as a `valve_opening_degree` report (52 → 62 → 72 → 52), so the patch's
datapoint 119 report reaches Home Assistant.

**Device after:** settings kept. The post-update travel calibration came out
normal again (closing steps 335, idle steps 313, limit voltages 1808 and
2713 mV), so the canary's 631 / 20 was a one-off measurement.

**Home Assistant side (from 21:35):** the blueprint described in
`docs/operating-model.md` replaced the old automations, which forced heat mode
and wrote the opening in every mode. Tested on the valve the same evening:
- a detent switches to manual and Home Assistant leaves the opening alone;
- a short press returns to auto and the thermostat's value is applied at once;
- turning down to `OF` switches the thermostat off, and turning up from `OF`
  returns to auto with the thermostat on;
- an opening set remotely while in auto (as the slider does) switches to
  manual and keeps the value.

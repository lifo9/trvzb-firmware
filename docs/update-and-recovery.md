# Update path and recovery

What happens when a modified application-MCU image is flashed, and how to get
back. Statements are marked **proven** (read from the image) or **inferred**
(about parts that cannot be inspected: the factory bootloader and the encrypted
Zigbee SoC firmware).

## Delivery through Zigbee2MQTT — proven

Zigbee2MQTT (`zigbee-herdsman` `src/controller/helpers/ota.ts`) accepts a local
index via `ota.zigbee_ota_override_index_location`, resolved relative to its
data directory. Override entries are consulted before the public index.

Checks it performs on an image: the OTA magic, `total image size` equal to the
sum of the subelements, and an optional SHA-512 that the index itself supplies.
There is no vendor signature check, so a rebuilt file is served as-is.

A minimal index entry:

```json
[{
  "fileName": "trvzb_v1.4.5.ota",
  "fileVersion": 5125,
  "fileSize": 332220,
  "url": "ota/trvzb_v1.4.5.ota",
  "imageType": 8199,
  "manufacturerCode": 4742,
  "sha512": "<sha512 of the file>"
}]
```

The file version must exceed the installed one for an update to be offered.
For Sonoff image type 8199, Zigbee2MQTT allows up to an hour per update.

## The header version must equal the SoC's built-in version — inferred, confirmed on a device

The Zigbee SoC firmware is encrypted, so this rests on Silicon Labs' standard
OTA client source plus valve-1's behaviour, which matched it step for step,
including the ABORT fix. The SoC runs that standard OTA client. After every network-up
it compares the stored image's header version with the version built into the
running SoC application (`currentFileVersion`, 5124 for stock 1.4.4). If they
differ, it installs the stored image again, reboots, and repeats forever (see
the aftermath of experiment 1 in `experiments.md`). Zigbee2MQTT's
`installed_version` echoes the header it served, so read `genOta`
`currentFileVersion` (client direction) for the real value.

Consequences:

- An image whose SoC part is stock 1.4.4 must carry header version 5124.
  Zigbee2MQTT only offers it with `force` in the index entry, and the client
  then skips it as the version it already runs (client source; not tried on
  a device). **A modified application-MCU
  payload cannot be delivered on the stock 1.4.4 SoC image by OTA.**
- The way forward is a genuine Sonoff release newer than the installed one,
  keeping its header version unchanged and modifying only the MCU subelement.
  None exists as of 2026-11-12: 1.4.4 is the newest in the Zigbee2MQTT index,
  on Sonoff's own OTA server and in the official changelog.
- A device caught in the loop is released by answering its `UpgradeEndRequest`
  with a ZCL Default Response, status ABORT (`0x95`), command `0x06`, cluster
  `genOta`, the request's transaction number; a Zigbee2MQTT external extension
  can send it with `endpoint.defaultResponse(0x06, 0x95, 0x0019, tsn)`.

## The application updates itself — proven

The application MCU receives its own firmware from the Zigbee SoC over the
UART and writes it into code flash. There is no separate update program to
fall back on.

| Step | Code | What it does |
|---|---|---|
| Update notification | UART command dispatch `0x4cfc` → `0x55d8` | Receives product id, version, size and expected checksum |
| Version gate | `0x564a`–`0x5654` | Proceeds only if the notified version is **strictly higher** than its own (`0x4ed4` parses the `1.3.1` string at image `0x4f28`) |
| Data blocks | `0x4cfc` → `0x5438` | Parses the subelement's part table, picks one slot, writes each block via `0x7108` |
| Flash write | `0x7108` | Erases each new 512-byte page (`0x354`) and programs the block (`0x348`) at slot base + offset; slot bases come from the table at image `0x77a4`: `0x400`, `0x8000` |
| Checksum | `0x549c`–`0x54ac`, `0x5500`–`0x5510` | Byte-sums the received stream and compares it with the value from the notification |
| Commit | `0x53e8`–`0x540c` | Reports, then `0x7010` clears the next bit of a 64-bit update bitmap (RAM `0x200003a0`, persisted to data flash `0x5001f8`), then restarts |

Which slot it writes is chosen by `0x7094`; presumably the slot it is not
running from. The factory bootloader at `0x000`–`0x3ff` then boots a slot,
presumably chosen by the bitmap's parity — **inferred**.

### What follows

- **One slot per update.** The previous firmware remains in the other slot.
  Whether the bootloader ever falls back to it on its own is **unknown**; the
  bitmap is committed before the restart, so a new image that fails to boot is
  most likely what the bootloader keeps starting.
- **Versions only go up.** A device that has accepted MCU version `1.3.2`
  refuses stock `1.3.1`. Returning to Sonoff's code means stock code re-labelled
  with a higher version. Every flash consumes a version number.
- **Every 64th update runs unverified code.** Each successful update clears
  one bit of the 64-bit bitmap; a rejected transfer clears none. When none are
  left, a separate branch (`0x7048`–`0x7076`) erases and rewrites the bitmap,
  so 64 is not a lifetime cap, but whether that branch keeps the slot parity
  right is unverified. Flash wear is not the limit either. Experiments should
  still be frugal: each flash also costs about 4 % battery.
- **The checksum is chosen by the Zigbee SoC.** The expected sum arrives in
  the notification frame. Whether the SoC computes it over the bytes it sends
  (modified payloads pass) or carries a precomputed value (any modified payload
  fails) is unknown. Experiment 1 did not decide it: the MCU restarts on both
  outcomes. A patch with visible behaviour is the first real test.
- **The update code lives in the image being replaced.** A patched image that
  breaks booting, the UART link or the update handlers leaves no over-the-air
  path back.

## Failure modes

| Mode | Effect | Recovery | Risk |
|---|---|---|---|
| Version gate rejects the notification | Nothing is written; the running firmware stays | none needed | none |
| Transfer checksum fails | The inactive slot is already overwritten; the bitmap is not committed; settings `+5` is set, saved, and the MCU restarts | presumably boots the old slot again — **inferred** | low |
| Patched app boots but misbehaves, update path intact | MCU still answers the Zigbee SoC | flash re-labelled stock with a higher version | low |
| Patched app fails to boot, hangs early, or breaks the UART or update code | Zigbee SoC stays on the network, datapoints go stale | none over the air; SWD pads, if enabled, could restore the previous slot by rewriting the bitmap | high |
| Power loss mid-write | The slot being written is incomplete, the bitmap not yet committed | presumably the old slot keeps booting — **inferred** | low |

Settings and motor calibration live in data flash at `0x500000`, untouched by
an application update, so a patch does not force recalibration as long as it
leaves the settings layout alone.

## Rules for patches

- Patch only inside declared regions. Each patch set lists, in its
  `sites.toml`, the `analysis/symbols.toml` regions it may touch, and the build
  refuses any site outside them: today `ui_main_screen` and `code_cave`. The
  never-patch ranges (startup, scatter loader, vector copy, reset, flash driver,
  UART parser, frame send, update handlers, slot-base table, data-init table,
  RAM code and data) stay as a backstop. Patching any of those risks the only
  recovery path.
- The code cave is the 192-byte zero block at image `0x7948`–`0x7a08`. The
  scatter loader copies it to RAM `0x20000000`, and the vector-table copy at
  `0x1cb4` overwrites exactly those 192 bytes before VTOR points there, so code
  placed in it runs from flash and is never read as data. Nothing else
  references it. Both facts are tested by emulating the boot in each slot
  with the cave filled with a marker.
- Patch both slots at the same slot-relative offset. Absolute flash addresses
  differ by `0x7c00` between slots, and so does one instruction immediate
  (`0x1cc0`: `lsls r2, r2, #10` in slot A, `#15` in slot B); a patch must be
  able to carry different bytes per slot.
- Do not change image length unless the part table, the subelement length and
  the total image size are all updated together.
- Raise the version with every flash: the strings inside the image (`1.3.1` at
  image `0x4e89`, `0x4f28`, `0x78ef`) and the OTA header string, together, so
  the MCU reports what the header announces. Choose the scheme before using
  numbers up: the string is packed into 2 bits major, 2 bits minor and 4 bits
  patch, so `1.3.15` is the highest `1.3.x` and `1.4.0` packs *below* `1.3.1`.
  The string must also keep its length (`1.3.10` does not fit), which leaves
  `1.3.2`–`1.3.9`, then `2.x.y` and `3.x.y` with single-digit parts. How the
  Zigbee SoC turns a `2.x.y` header field into the notified version byte is
  untested, so use `1.3.3`–`1.3.9` first.
  `tools/build_ota.py` enforces all of this and derives both header version
  fields from its arguments.
- Test acceptance with a version-string-only image before any logic change.
- Run a patched handler in an emulator before flashing it.

# Update path and recovery

What happens when a modified application-MCU image is flashed, and how to get
back to stock. Statements are marked **proven** (from the image) or
**inferred** (about parts that cannot be inspected).

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

## The application MCU never updates itself — proven

- The flash driver (RAM routines at `0x20000100` erase, `0x20000146` program)
  is reachable only from the settings and `dev_sn` code, which write the data
  flash at `0x500000+`. The routines do not range-check addresses; the callers
  do (`0x604c`–`0x6056`).
- The UART frame dispatcher (`0x4cfc`, parser `0x57b4`) treats Tuya's
  firmware-upgrade command `0x0a` as a no-op. No code receives or stages an
  image.
- Neither slot refers to the other (no `±0x7c00` cross-references), and nothing
  writes a remap register or a boot flag.

So the **factory bootloader at `0x000`–`0x3ff`** does all MCU updating and slot
selection. It is not in any OTA file and its policy (image validation, slot
choice, update window) is unknown — **inferred, unprovable from here**.

## A/B slots do not provide rollback — proven

Every OTA carries both slots. A successful update writes patched code into
both. The pairing protects against power loss mid-write, not against a bad
image: there is no good copy to fall back on.

## Failure modes

| Mode | Effect | Recovery | Risk |
|---|---|---|---|
| Patched app boots but misbehaves | MCU still answers the Zigbee SoC | Flash stock 1.4.4 through Zigbee2MQTT as usual | low |
| Patched app fails to boot or hangs early | Zigbee SoC stays on the network, datapoints go stale; `0x1122` suggests a fault self-reset loop | Depends entirely on the bootloader opening an update window — unproven. Otherwise SWD pads, if enabled. | medium, unquantifiable |
| Power loss mid-write (battery device) | The slot being written is corrupt, the other is intact | Fine if the bootloader validates slots; otherwise as above | low-medium |

Settings and motor calibration live in data flash at `0x500000`, untouched by
an application-slot update, so a patch does not force recalibration as long as
it leaves the settings layout alone.

## Rules for patches

- Keep changes out of startup, the RAM blob (image `0x7a08`), the data-init
  table (`0x7900`), the flash driver, the data-flash layout and the UART code.
- Patch both slots at the same slot-relative offset; add `+0x7c00` in slot B to
  any absolute flash pointer the patch introduces.
- Do not change image length unless the MCU header, the subelement length and
  the total image size are all updated together.
- Bump the version strings inside the image (`1.3.1` at image `0x4e89`,
  `0x4f28`, `0x78ef`) together with the OTA header string, so the MCU reports
  the version the header announces and nothing retries an update in a loop.
- Test acceptance with a version-string-only image before flashing logic changes.

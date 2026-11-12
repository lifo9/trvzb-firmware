# Firmware layout

## Hardware

The TRVZB has two processors:

- **Zigbee SoC** — Silicon Labs EFR32 (the OTA carries a Silicon Labs GBL
  container). Runs the Zigbee stack, owns the radio, receives OTA updates.
- **Application MCU** — an ARM Cortex-M0+ class part (it has VTOR) with 64 KB
  code flash and 4 KB RAM, built by the ODM Rti-Tek (product ID `2dpplnsn`).
  Reads the knob and buttons, drives the display and the valve motor, and runs
  the thermostat. It talks to the Zigbee SoC over a UART using a Tuya-style
  `0x55 0xaa` framed datapoint protocol.

The application MCU's family is unconfirmed. Its peripheral layout (flash
controller at `0x40020000` with `0x55`/`0xaa` unlock keys, port registers at
`0x40040000` with a pin-function block at `0x40040800`, ADC at `0x40045000`,
data flash aperture at `0x00500000`) reads as Renesas-style; the marking on the
chip would settle it.

## OTA container

`trvzb_v1.4.4.ota` (332,220 bytes) is a standard Zigbee OTA file:

| Offset | Field | Value in 1.4.4 |
|---|---|---|
| `0x00` | magic | `0x0BEEF11E` |
| `0x06` | header length | 56 |
| `0x0A` | manufacturer code | 4742 |
| `0x0C` | image type | 8199 |
| `0x0E` | file version | `0x1404` |
| `0x14` | header string (32 B) | `vers:00001404,00001301\n` |
| `0x34` | total image size | 332,220 |

The header string carries both firmware versions: Zigbee SoC `1.4.04`,
application MCU `1.3.01`.

Two subelements follow the header:

| Tag | Length | Content |
|---|---|---|
| `0x0000` | 268,956 | Zigbee SoC firmware: Silicon Labs GBL, header type `0x0101` = AES-CCM encrypted and ECDSA signed. Entropy 8.0. Not modifiable. |
| `0xf000` | 63,196 | Application MCU firmware, **plain and unsigned**. |

### The application-MCU subelement

A 20-byte big-endian part table, then two complete builds of the same program:

| Field | 1.4.4 | 1.4.0 |
|---|---|---|
| part count | `2` | `2` |
| part 1 offset, length (slot A) | `0x14`, `0x7b64` | `0x14`, `0x7b5c` |
| part 2 offset, length (slot B) | `0x7b78`, `0x7b64` | `0x7b70`, `0x7b5c` |

The update handler `0x5438` parses it as a count followed by (offset, length)
pairs. The file carries no checksum: not in this table, not appended to either
slot. Integrity is checked during transfer instead, against a byte sum the
Zigbee SoC sends (see [update-and-recovery.md](update-and-recovery.md)).

The two builds differ only in link address. Slot B equals slot A with 129 words
shifted by exactly `+0x7c00` (vector table and internal pointers), a
different build-time string, one instruction immediate (`0x1cc0`: `lsls r2,
r2, #10` in slot A, `#15` in slot B, deriving the slot base) and one byte of
a flash pointer inside compressed `.data` (image `0x7b2c`). A patch therefore applies at the same
slot-relative offset in both slots; only absolute flash pointers it introduces
need `+0x7c00` in slot B.

Comparing versions: 1.4.0 carries MCU firmware `1.2.08`, 1.4.1 also `1.2.08`,
1.4.4 carries `1.3.01` (built 2026-01-04).

## Application-MCU memory map

| Range | Content |
|---|---|
| `0x0000_0000`–`0x0000_03ff` | Factory bootloader. Not in any OTA; cannot be inspected from the update file. |
| `0x0000_0400`–`0x0000_7fff` | Slot A (`0x7c00` bytes, image `0x7b64`, `0x9c` bytes slack) |
| `0x0000_8000`–`0x0000_fbff` | Slot B (same layout) |
| `0x0050_0000`+ | Data flash: settings, `dev_sn`, motor calibration |
| `0x2000_0000`–`0x2000_0fff` | RAM; initial stack pointer `0x20000fd8` |

**Addresses in these docs are image offsets** unless marked otherwise. MCU
address = image offset + `0x400` (slot A). Evidence for the `0x400` base: the
7-segment digit font sits at image offset `0x781f` and code loads it through the
literal `0x7c1f`; the datapoint type table at image `0x77ac` is loaded through
`0x7bac`; slot B's reset vector is `0x80d9` = `0x8000` + `0xd9`.

Each slot copies its own 48-entry vector table to RAM `0x20000000` and points
VTOR at it (`0x1cb4`), so the slots are self-contained.

### Startup data

The data-init table at image `0x7900` copies a 276-byte blob from image
`0x7a08` to RAM `0x20000100`: the flash erase and program routines, plus two
small interrupt handlers that must keep running while flash is busy (which is
why two vector entries point into RAM, `0x200001a1` and `0x200001b5`). `.bss`
starts at `0x20000458`, `0xb80` bytes, ending at the stack pointer.

### Key RAM structures

| Address | Role |
|---|---|
| `0x20000698` | Settings and report struct (setpoint limits, setpoints, mode, valve degrees, hysteresis thresholds) |
| `0x20000348` | Main runtime/control struct (thermostat loop enable at `+0xc`) |
| `0x200003a8` | Valve layer: target % `+6`, current % `+7`, total travel steps `+0x34` |
| `0x200002d6` | Motor layer: commanded steps `+0x1a`, steps taken `+0x1c` |
| `0x2000043e` | UI edit state: pending value `+3`, screen-awake flag `+1` |
| `0x200002c3` | Display framebuffer, 8 bytes, shadow copy at `+8` |
| `0x200005fc` | Input channels, 8-byte stride |

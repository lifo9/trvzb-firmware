# trvzb-firmware

Reverse engineering of the SONOFF TRVZB thermostatic radiator valve's
application-MCU firmware, and tooling to rebuild its OTA with a patched
application image.

The goal is a valve whose physical knob sets the valve opening percentage
(which the stock firmware cannot do: the knob only moves the temperature
setpoint, and no Zigbee datapoint commands a position).

## Why it is possible

The OTA file carries two firmwares. The Zigbee SoC's is encrypted and signed;
the application MCU's — which reads the knob, drives the display and the motor,
and runs the thermostat — is plain, unsigned code. The file carries no checksum
for it; the MCU checks a transfer sum supplied by the Zigbee SoC. See
[docs/firmware-layout.md](docs/firmware-layout.md).

## Status

- Analysis: datapoint map, thermostat → valve → motor chain, knob path and
  display decoded.
- Delivery: a rebuilt OTA can be served through Zigbee2MQTT's local index.
- Acceptance test: a real valve's Zigbee side accepted and installed a
  version-strings-only image (release `1.4.5-verstest`); whether the MCU took
  its part is not yet known — see
  [docs/experiments.md](docs/experiments.md).
- Tooling: patch sets assembled per slot with binutils, verified against
  stock, stock knob behaviour pinned under emulation.
- Patch (knob sets valve opening degree and the auto/manual/off modes of
  [docs/operating-model.md](docs/operating-model.md)): designed, not yet written.

## Setup

Nix with flakes and direnv:

```sh
direnv allow          # or: nix develop
just extract          # fetch the pinned stock 1.4.4 OTA into work/ and split the MCU slots
just summary
just dis 0x648c       # disassemble a function
just calls 0x6c18     # every call site of a function
```

Vendor firmware is never committed; `just fetch` downloads it from a pinned
zigbee-OTA commit and verifies its SHA-512.

## Addresses

Every address in the tools and docs is an **image offset** into slot A. The
image is linked at MCU address `0x400`, so MCU address = image offset +
`0x400`. RAM addresses (`0x2000_xxxx`) are absolute.

## Documents

- [docs/operating-model.md](docs/operating-model.md) — who controls the valve: HA thermostat, knob, manual setting
- [docs/firmware-layout.md](docs/firmware-layout.md) — hardware, OTA container, memory map, RAM structures
- [docs/datapoints.md](docs/datapoints.md) — datapoint table and its mapping to Zigbee attributes
- [docs/control-flow.md](docs/control-flow.md) — thermostat → valve → motor, knob, display
- [docs/update-and-recovery.md](docs/update-and-recovery.md) — how updates reach the MCU, failure modes, patch rules
- [docs/experiments.md](docs/experiments.md) — flashes performed on a real device

## Layout

- `trvzb/` — shared model: OTA file, slots and link addresses, MCU versions,
  symbols, patch sets, image building, verification, emulation
- `tools/` — command lines over it (below)
- `analysis/symbols.toml` — named functions, labels, RAM fields, tables and
  never-patch ranges, checked against the binary by `tests/test_facts.py`
- `patches/<name>/` — a patch set: `patch.S` and `sites.toml`
- `releases/<id>.toml` — what was (or will be) flashed, with its image hash
- `devices.toml` — every flashed valve and flash
- `tests/` — synthetic-OTA tests, fact tests, emulated behaviour, golden hashes

## Tools

- `tools/firmware_map.py` — function discovery, string and literal
  cross-references, struct field access map, disassembly (`summary`, `strings`,
  `dis`, `raw`, `find`, `uses`, `fields`, `callers`)
- `tools/calls.py` — exhaustive `bl` call-site scan, independent of function
  discovery
- `tools/extract_mcu_image.py` — split both MCU slots out of an OTA
- `tools/build_patch.py` — assemble a patch set per slot with binutils
- `tools/build_ota.py` — build an image: patch set plus raised versions
- `tools/build_release.py` — rebuild a release from its manifest (`just build`)
- `tools/verify_ota.py` — check a built image against stock (`just verify`)

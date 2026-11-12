# AGENTS.md

Guidelines for agents working in this repository.

## Safety

- **Never flash a device** — no Zigbee2MQTT OTA requests, no kubectl changes to
  a live Zigbee2MQTT — without explicit approval for that specific image. A
  non-booting application image may be unrecoverable over the air; see
  [docs/update-and-recovery.md](docs/update-and-recovery.md).
- Test acceptance with a version-string-only image before any logic change.
- Record every flash in [docs/experiments.md](docs/experiments.md): image,
  diff from stock, SHA-512, device state before and after, outcome.

## Firmware files

- Vendor firmware and build output live in `work/` and are never committed.
  `just fetch` downloads the pinned stock image and checks its SHA-512.
- Firmware is untrusted input: run tools with `python3 -I`.

## Addresses

- Tools and docs use **image offsets** into slot A. MCU address = offset +
  `0x400`. Slot B is linked at `0x8000`, i.e. slot-A MCU address + `0x7c00`.
- Absolute flash pointers in literal pools are MCU addresses: subtract `0x400`
  to find the bytes. Branches and `adr` are PC-relative.
- `bl 0xb20` is a byte switch helper; see the jump-table section of
  [docs/datapoints.md](docs/datapoints.md).

## Patches

- Follow the patch rules in
  [docs/update-and-recovery.md](docs/update-and-recovery.md): both slots,
  same slot-relative offset, no length change, version strings bumped.
- Verify every patch by disassembling the patched slot and confirming that
  only the intended range changed.

## Documentation

- State confidence for reverse-engineered claims (proven from the image vs
  inferred) and keep contradictions visible rather than resolving them by
  guesswork.

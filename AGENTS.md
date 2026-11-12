# AGENTS.md

Guidelines for agents working in this repository.

## Safety

- **Never flash a device** — no Zigbee2MQTT OTA requests, no kubectl changes to
  a live Zigbee2MQTT — without explicit approval for that specific image. A
  non-booting application image may be unrecoverable over the air; see
  [docs/update-and-recovery.md](docs/update-and-recovery.md).
- An image whose OTA header version differs from the version built into its
  Zigbee SoC application (5124 for stock 1.4.4) makes the SoC re-install it on
  every boot, forever. Every release on the stock SoC image does this. Flash
  one only with the ABORT extension loaded in Zigbee2MQTT, so the first
  `UpgradeEndRequest` gets an ABORT; see
  [docs/update-and-recovery.md](docs/update-and-recovery.md).
- Test acceptance with a version-string-only image before any logic change.
- Record every flash in [docs/experiments.md](docs/experiments.md): image,
  diff from stock, SHA-512, device state before and after, outcome.

## Releases and flashing

1. Write or change the patch set in `patches/<name>/` (`patch.S`, `sites.toml`)
   and its emulated tests; `just test` must pass.
2. Add `releases/<id>.toml` without `sha512`; `just build <id>` prints the
   hash. Review `just verify <id>`: every check PASS, every site listing read.
3. Record the hash in the manifest, commit, and tag `release/<id>`.
4. Flash only with explicit approval for that release. Its MCU version must be
   above the installed one on the target valve (see `devices.toml`). First load
   `tools/zigbee2mqtt/abort-ota-end.mjs`, with the valve's IEEE address and the
   release's file version filled in, through Zigbee2MQTT's
   `bridge/request/extension/save`. Then serve the image by replacing the
   local index with only the entry from `just index <id>`, which carries the
   release hash so Zigbee2MQTT refuses any other file, and start
   `ota_update/update`. Replace, do not append: the deployment pins the
   installed stock version with an index entry of its own, and Zigbee2MQTT
   uses the first matching entry, so the pin would hide the release.
5. After the `UpgradeEndRequest` has been aborted, remove the extension,
   restore the pinned index (the next deployment of the Home Assistant stack
   writes it back too) and delete the image from Zigbee2MQTT's `ota/`
   directory: a later "update" click would otherwise flash it again, this time
   without the abort.
6. Record the flash in `devices.toml` and the outcome in
   [docs/experiments.md](docs/experiments.md).

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

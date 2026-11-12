"""Rebuild a TRVZB OTA file with raised versions.

Builds an image from stock: optionally applies a logic patch set built by
tools/build_patch.py, then raises the application-MCU version strings and both
OTA header versions. The OTA header's version fields
are derived from the two arguments, so they cannot disagree with what the
image reports.

Refuses versions the valve would not accept: the MCU version must pack higher
than the image's current one (see trvzb/version.py) and keep the same string
length, since it sits in fixed-size fields; the file version must exceed the
current one for Zigbee2MQTT to offer the update. A patch set may not touch any
range analysis/symbols.toml marks protected. See trvzb/ota.py for the file
model.

Usage:
    build_ota.py <output.ota> --mcu-version 1.3.2 --file-version 0x1405 [--patch-set work/patches/x.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trvzb.build import BuildRefused, build_image  # noqa: E402
from trvzb.ota import OtaFile  # noqa: E402
from trvzb.patch import PatchSet  # noqa: E402
from trvzb.symbols import load_symbols  # noqa: E402


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--mcu-version", required=True, help="new application-MCU version, e.g. 1.3.2")
    parser.add_argument("--file-version", required=True, help="new OTA file version, e.g. 0x1405")
    parser.add_argument("--patch-set", help="patch set JSON from tools/build_patch.py")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    ota = OtaFile()
    print(f"slot length {ota.slot_length:#x}, MCU payload at {ota.mcu_payload_start:#x}")
    print("current header string:", ota.mcu_header_string())
    logic_patch = PatchSet.from_json(open(arguments.patch_set).read()) if arguments.patch_set else None
    try:
        build_image(ota, arguments.mcu_version, int(arguments.file_version, 0), logic_patch, load_symbols().protected_ranges)
    except BuildRefused as error:
        raise SystemExit(str(error)) from error
    if logic_patch is not None:
        print(f"applied patch set {logic_patch.name}: {[site.name for site in logic_patch.sites]}")
    print("new header string:    ", ota.mcu_header_string())
    changed = ota.changed_offsets_against()
    print(f"bytes changed vs input: {len(changed)} -> {[hex(offset) for offset in changed]}")
    meta = ota.write(arguments.output)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()

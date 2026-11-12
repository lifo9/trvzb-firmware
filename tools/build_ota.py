"""Rebuild a TRVZB OTA file with raised version strings.

Builds the version-test image: stock code, with the application-MCU version
strings and the OTA header versions raised. See trvzb/ota.py for the file
model.

Usage:
    build_ota.py <output.ota> [--mcu-version 1.3.2] [--file-version 0x1405]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trvzb.ota import OtaFile  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--mcu-version", default="1.3.2", help="version string written into both slots")
    parser.add_argument("--file-version", default="0x1405")
    parser.add_argument("--zigbee-header-version", default="00001405")
    parser.add_argument("--mcu-header-version", default="00001302")
    arguments = parser.parse_args()

    ota = OtaFile()
    print(f"slot length {ota.slot_length:#x}, MCU payload at {ota.mcu_payload_start:#x}")
    print("stock header string:", ota.mcu_header_string())

    stock_version = b"1.3.1"
    new_version = arguments.mcu_version.encode()
    occurrences = ota.find_in_slot_a(stock_version)
    print("version string occurrences in slot A:", [hex(offset) for offset in occurrences])
    assert occurrences, "no version string found"
    for slot_relative_offset in occurrences:
        ota.patch_both_slots(slot_relative_offset, stock_version, new_version)

    ota.set_file_version(int(arguments.file_version, 0))
    ota.set_header_versions(arguments.zigbee_header_version, arguments.mcu_header_version)
    print("new header string:   ", ota.mcu_header_string())

    changed = ota.changed_offsets_against()
    print(f"bytes changed vs stock: {len(changed)} -> {[hex(offset) for offset in changed]}")

    meta = ota.write(arguments.output)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()

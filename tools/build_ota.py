"""Rebuild a TRVZB OTA file with raised versions.

Builds the version-test image: stock code, with the application-MCU version
strings and both OTA header versions raised. The OTA header's version fields
are derived from the two arguments, so they cannot disagree with what the
image reports.

Refuses versions the valve would not accept: the MCU version must pack higher
than the image's current one (see trvzb/version.py) and keep the same string
length, since it sits in fixed-size fields; the file version must exceed the
current one for Zigbee2MQTT to offer the update. See trvzb/ota.py for the file
model.

Usage:
    build_ota.py <output.ota> --mcu-version 1.3.2 --file-version 0x1405
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trvzb.ota import FILE_VERSION_OFFSET, OtaFile  # noqa: E402
from trvzb.version import McuVersion  # noqa: E402


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--mcu-version", required=True, help="new application-MCU version, e.g. 1.3.2")
    parser.add_argument("--file-version", required=True, help="new OTA file version, e.g. 0x1405")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    ota = OtaFile()
    _, current_mcu_field = ota.header_versions()
    current_mcu_version = McuVersion.from_header_field(current_mcu_field)
    current_file_version, = struct.unpack_from("<I", ota.data, FILE_VERSION_OFFSET)
    try:
        new_mcu_version = McuVersion.parse(arguments.mcu_version)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    new_file_version = int(arguments.file_version, 0)

    if new_mcu_version.packed() <= current_mcu_version.packed():
        raise SystemExit(
            f"MCU version {new_mcu_version} (packed {new_mcu_version.packed():#04x}) is not above "
            f"the image's {current_mcu_version} (packed {current_mcu_version.packed():#04x}); the valve would refuse it"
        )
    if len(str(new_mcu_version)) != len(str(current_mcu_version)):
        raise SystemExit(
            f"MCU version {new_mcu_version} must be as long as {current_mcu_version}: "
            "the version strings sit in fixed-size fields"
        )
    if new_file_version <= current_file_version:
        raise SystemExit(
            f"file version {new_file_version:#x} is not above the image's {current_file_version:#x}; "
            "Zigbee2MQTT would not offer it"
        )

    print(f"slot length {ota.slot_length:#x}, MCU payload at {ota.mcu_payload_start:#x}")
    print("current header string:", ota.mcu_header_string())

    current_string = str(current_mcu_version).encode()
    new_string = str(new_mcu_version).encode()
    occurrences = ota.find_in_slot_a(current_string)
    print("version string occurrences in slot A:", [hex(offset) for offset in occurrences])
    if not occurrences:
        raise SystemExit(f"the image does not contain its version string {current_string!r}")
    for slot_relative_offset in occurrences:
        ota.patch_both_slots(slot_relative_offset, current_string, new_string)

    ota.set_file_version(new_file_version)
    ota.set_header_versions(f"{new_file_version:08x}", new_mcu_version.header_field())
    print("new header string:    ", ota.mcu_header_string())

    changed = ota.changed_offsets_against()
    print(f"bytes changed vs input: {len(changed)} -> {[hex(offset) for offset in changed]}")

    meta = ota.write(arguments.output)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()

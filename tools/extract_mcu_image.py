"""Extract both application-MCU slots from a TRVZB OTA file.

Writes <output-directory>/mcu_app_a.bin (linked at MCU address 0x400) and
mcu_app_b.bin (linked at 0x8000). Slot A is the one the analysis tools read.

Usage:
    extract_mcu_image.py <trvzb.ota> [<output-directory>]
"""

from __future__ import annotations

import os
import sys

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trvzb.ota import OtaFile  # noqa: E402


def main() -> None:
    ota_path = sys.argv[1]
    output_directory = sys.argv[2] if len(sys.argv) > 2 else "work"
    os.makedirs(output_directory, exist_ok=True)
    ota = OtaFile(ota_path)
    for slot_name in ("A", "B"):
        start = ota.slot_offset(slot_name)
        slot_image = bytes(ota.data[start : start + ota.slot_length])
        output_path = os.path.join(output_directory, f"mcu_app_{slot_name.lower()}.bin")
        with open(output_path, "wb") as slot_file:
            slot_file.write(slot_image)
        print(f"slot {slot_name}: {len(slot_image):#x} bytes -> {output_path}")


if __name__ == "__main__":
    main()

"""Verify a built OTA against stock before it is flashed.

Checks that the image holds exactly the reviewed site bytes, that only
declared bytes changed, that each patch site differs between
the slots only by relocation, that every `bl` in the patch lands on a stock function start
or inside the patch, and that the MCU version is consistent and raised. Prints
each patch site's listing in both slots for review. Exits non-zero on any
problem.

Usage:
    verify_ota.py <built.ota> [--patch-set work/patches/x.json]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import capstone

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from firmware_map import FirmwareMap  # noqa: E402
from trvzb.image import SLOT_A, SLOT_B  # noqa: E402
from trvzb.ota import OtaFile  # noqa: E402
from trvzb.patch import SLOT_NAMES, PatchSet  # noqa: E402
from trvzb.version import McuVersion  # noqa: E402
from trvzb.verify import (  # noqa: E402
    branch_targets_outside_functions,
    site_bytes_mismatches,
    site_ranges,
    site_slot_differences,
    slot_bytes,
    unexpected_changes,
    version_consistency,
)

SLOTS = {"A": SLOT_A, "B": SLOT_B}


def stock_function_starts(stock: OtaFile) -> set[int]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        slot_path = Path(temporary_directory) / "stock_slot_a.bin"
        slot_path.write_bytes(slot_bytes(stock, "A"))
        return set(FirmwareMap(str(slot_path)).functions)


def site_listing(patch_set: PatchSet) -> list[str]:
    disassembler = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
    disassembler.skipdata = True
    lines = []
    for site in patch_set.sites:
        for slot_name in SLOT_NAMES:
            mcu_start = SLOTS[slot_name].mcu_address(site.offset)
            lines.append(f"--- site {site.name}, slot {slot_name}, image {site.offset:#x}, MCU {mcu_start:#x}")
            for decoded in disassembler.disasm(site.patched_bytes[slot_name], mcu_start):
                lines.append(f"  {decoded.address:#07x}  {decoded.mnemonic} {decoded.op_str}")
    return lines


def objdump_listing(patch_set: PatchSet) -> list[str]:
    """Second disassembler over slot A's site bytes, when binutils is available."""
    if shutil.which("arm-none-eabi-objdump") is None:
        return ["(arm-none-eabi-objdump not on PATH; skipped)"]
    lines = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        for site in patch_set.sites:
            site_path = Path(temporary_directory) / f"{site.name}.bin"
            site_path.write_bytes(site.patched_bytes["A"])
            result = subprocess.run(
                [
                    "arm-none-eabi-objdump", "-D", "-b", "binary", "-marm", "-Mforce-thumb",
                    f"--adjust-vma={SLOT_A.mcu_address(site.offset):#x}", str(site_path),
                ],
                capture_output=True, text=True, check=True,
            )
            lines.append(f"--- objdump, site {site.name}, slot A")
            lines += [line for line in result.stdout.splitlines() if line.startswith("    ") or line.startswith("   ")]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("built")
    parser.add_argument("--patch-set", help="patch set JSON the image was built with")
    arguments = parser.parse_args()

    stock = OtaFile()
    built = OtaFile(arguments.built)
    patch_set = PatchSet.from_json(open(arguments.patch_set).read()) if arguments.patch_set else PatchSet("none", [])
    _, stock_field = stock.header_versions()
    stock_version_text = str(McuVersion.from_header_field(stock_field))
    version_offsets = stock.find_in_slot_a(stock_version_text.encode())
    allowed = site_ranges(patch_set, version_offsets, len(stock_version_text))

    checks = {
        "image holds the reviewed site bytes": site_bytes_mismatches(built, patch_set),
        "only declared bytes changed": unexpected_changes(stock, built, allowed),
        "site bytes differ between slots only by relocation": site_slot_differences(patch_set),
        "every bl lands on a function start": branch_targets_outside_functions(patch_set, stock_function_starts(stock)),
        "MCU version consistent and raised": version_consistency(stock, built, version_offsets),
    }
    for line in site_listing(patch_set) + objdump_listing(patch_set):
        print(line)
    print()
    failed = False
    for description, problems in checks.items():
        print(f"{'PASS' if not problems else 'FAIL'}  {description}")
        for problem in problems[:20]:
            print(f"      {problem}")
        failed = failed or bool(problems)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

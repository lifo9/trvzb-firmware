"""Verify a built OTA against stock before it is flashed.

Checks that the image holds exactly the reviewed site bytes, that only
declared bytes changed, that each patch site differs between
the slots only by relocation, that every `bl` in the patch lands on a stock function start
or inside the patch, and that the MCU version is consistent and raised. Prints
each patch site's listing in both slots for review. Exits non-zero on any
problem.

With --manifest, also checks that the stock image is the one the release pins
and that the image is the one the release records.

Usage:
    verify_ota.py <built.ota> [--patch-set work/patches/x.json] [--manifest releases/<id>.toml]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_release import load_manifest  # noqa: E402
from firmware_map import FirmwareMap  # noqa: E402
from trvzb.ota import STOCK_OTA_PATH, OtaFile  # noqa: E402
from trvzb.patch import PatchSet  # noqa: E402
from trvzb.version import McuVersion  # noqa: E402
from trvzb.verify import (  # noqa: E402
    branch_targets_outside_functions,
    branches_leaving_sites,
    image_calls_overlapping_sites,
    site_listing_from_image,
    site_bytes_mismatches,
    site_ranges,
    site_slot_differences,
    sites_splitting_stock_calls,
    slot_bytes,
    unexpected_changes,
    version_consistency,
)

def stock_code_map(stock: OtaFile) -> tuple[set[int], set[int]]:
    """Function starts and instruction starts of the stock slot A image."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        slot_path = Path(temporary_directory) / "stock_slot_a.bin"
        slot_path.write_bytes(slot_bytes(stock, "A"))
        firmware = FirmwareMap(str(slot_path))
    instruction_starts = {address for function in firmware.functions.values() for address in function.instructions}
    return set(firmware.functions), instruction_starts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("built")
    parser.add_argument("--patch-set", help="patch set JSON the image was built with")
    parser.add_argument("--manifest", type=Path, help="release manifest the image claims to be")
    arguments = parser.parse_args()

    stock = OtaFile()
    built = OtaFile(arguments.built)
    patch_set = PatchSet.from_json(open(arguments.patch_set).read()) if arguments.patch_set else PatchSet("none", [])
    _, stock_field = stock.header_versions()
    stock_version_text = str(McuVersion.from_header_field(stock_field))
    version_offsets = stock.find_in_slot_a(stock_version_text.encode())
    allowed = site_ranges(patch_set, version_offsets, len(stock_version_text))

    function_starts, instruction_starts = stock_code_map(stock)
    # Checks that cannot run yet, such as the release hash of a release under
    # review; shown, but not failures.
    pending: dict[str, str] = {}
    checks = {
        "image holds the reviewed site bytes": site_bytes_mismatches(built, patch_set),
        "only declared bytes changed": unexpected_changes(stock, built, allowed),
        "no site splits a stock call": sites_splitting_stock_calls(patch_set, slot_bytes(stock, "A")),
        "site bytes differ between slots only by relocation": site_slot_differences(patch_set),
        "every bl lands on a function start": branch_targets_outside_functions(patch_set, function_starts),
        "image calls overlapping a site land on a function start": image_calls_overlapping_sites(built, patch_set, function_starts),
        "branches leaving a site land on a stock instruction": branches_leaving_sites(built, patch_set, instruction_starts),
        "MCU version consistent and raised": version_consistency(stock, built, version_offsets),
    }
    if arguments.manifest:
        manifest = load_manifest(arguments.manifest)
        stock_sha512 = hashlib.sha512(open(STOCK_OTA_PATH, "rb").read()).hexdigest()
        built_sha512 = hashlib.sha512(open(arguments.built, "rb").read()).hexdigest()
        checks["stock is the image the release pins"] = (
            [] if stock_sha512 == manifest.stock_sha512 else [f"stock {stock_sha512[:16]}…, manifest pins {manifest.stock_sha512[:16]}…"]
        )
        if manifest.sha512 is None:
            pending["image is the recorded release"] = f"the manifest records no sha512 yet; this image is {built_sha512}"
        else:
            checks["image is the recorded release"] = (
                [] if built_sha512 == manifest.sha512 else [f"image {built_sha512[:16]}…, manifest records {manifest.sha512[:16]}…"]
            )
    for line in site_listing_from_image(built, patch_set):
        print(line)
    print()
    failed = False
    for description, reason in pending.items():
        print(f"PENDING  {description}")
        print(f"      {reason}")
    for description, problems in checks.items():
        print(f"{'PASS' if not problems else 'FAIL'}  {description}")
        for problem in problems[:20]:
            print(f"      {problem}")
        failed = failed or bool(problems)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

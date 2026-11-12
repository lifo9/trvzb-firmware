"""Exhaustive Thumb BL call-site scan of the app-MCU image.

Decodes the 32-bit Thumb `BL` encoding at every halfword of the image, so it
finds call sites that recursive-descent analysis misses (code reached only
through jump tables or function pointers). Data that happens to look like a
`BL` produces false positives; check a hit with `firmware_map.py raw`.

Addresses are image offsets. The image is linked at 0x400, so an MCU address
is image offset + 0x400.

Usage:
    calls.py <target> [<target> ...]   call sites of each target
    calls.py                           every call target (function starts)
"""

from __future__ import annotations

import os
import sys

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trvzb.image import iterate_call_sites  # noqa: E402

IMAGE_PATH = os.environ.get("TRVZB_IMAGE", "work/mcu_app_a.bin")


def main() -> None:
    image = open(IMAGE_PATH, "rb").read()
    wanted_targets = [int(argument, 0) for argument in sys.argv[1:]]
    if wanted_targets:
        call_sites_by_target: dict[int, list[int]] = {target: [] for target in wanted_targets}
        for call_site, target in iterate_call_sites(image):
            if target in call_sites_by_target:
                call_sites_by_target[target].append(call_site)
        for target, call_sites in call_sites_by_target.items():
            print(f"{target:#07x} called from {[hex(call_site) for call_site in call_sites]}")
    else:
        function_starts = sorted({target for _, target in iterate_call_sites(image)})
        print(" ".join(hex(start) for start in function_starts))


if __name__ == "__main__":
    main()

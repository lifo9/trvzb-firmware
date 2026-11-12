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
import struct
import sys
from collections.abc import Iterator

IMAGE_PATH = os.environ.get("TRVZB_IMAGE", "work/mcu_app_a.bin")
CODE_START = 0xC0


def decode_branch_with_link_target(address: int, first_halfword: int, second_halfword: int) -> int | None:
    is_branch_with_link = (first_halfword & 0xF800) == 0xF000 and (second_halfword & 0xD000) == 0xD000
    if not is_branch_with_link:
        return None
    sign = (first_halfword >> 10) & 1
    upper_bits = first_halfword & 0x3FF
    j1 = (second_halfword >> 13) & 1
    j2 = (second_halfword >> 11) & 1
    lower_bits = second_halfword & 0x7FF
    i1 = 1 - (j1 ^ sign)
    i2 = 1 - (j2 ^ sign)
    offset = (sign << 24) | (i1 << 23) | (i2 << 22) | (upper_bits << 12) | (lower_bits << 1)
    if sign:
        offset -= 1 << 25
    return address + 4 + offset


def iterate_call_sites(image: bytes) -> Iterator[tuple[int, int]]:
    """Yield (call_site, target) for every BL encoding found in the image."""
    for address in range(CODE_START, len(image) - 4, 2):
        first_halfword, second_halfword = struct.unpack_from("<HH", image, address)
        target = decode_branch_with_link_target(address, first_halfword, second_halfword)
        if target is not None and 0 < target < len(image):
            yield address, target


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

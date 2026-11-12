"""The application-MCU image: slot link addresses and Thumb call decoding.

Analysis works on image offsets into slot A. The two slots are the same
program linked at different addresses, so every translation between an image
offset and an MCU address goes through a Slot.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

CODE_START = 0xC0


@dataclass(frozen=True)
class Slot:
    name: str
    link_base: int

    def mcu_address(self, image_offset: int) -> int:
        return self.link_base + image_offset

    def image_offset(self, mcu_address: int) -> int:
        image_offset = mcu_address - self.link_base
        assert image_offset >= 0, f"{mcu_address:#x} lies below slot {self.name}'s base {self.link_base:#x}"
        return image_offset


SLOT_A = Slot(name="A", link_base=0x400)
SLOT_B = Slot(name="B", link_base=0x8000)
SLOT_ADDRESS_DELTA = SLOT_B.link_base - SLOT_A.link_base


def decode_branch_with_link_target(address: int, first_halfword: int, second_halfword: int) -> int | None:
    """Target of a 32-bit Thumb `BL` at `address`, or None if it is not one."""
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
    """Yield (call_site, target) for every BL encoding found in the image.

    Scans every halfword, so it also finds calls in code that recursive descent
    never reaches; data that happens to look like a BL produces false positives.
    """
    for address in range(CODE_START, len(image) - 4, 2):
        first_halfword, second_halfword = struct.unpack_from("<HH", image, address)
        target = decode_branch_with_link_target(address, first_halfword, second_halfword)
        if target is not None and 0 < target < len(image):
            yield address, target

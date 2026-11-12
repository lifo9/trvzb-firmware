"""Independent checks of a built OTA against stock before it is flashed.

Each check returns a list of problems; an empty list means it passed.
"""

from __future__ import annotations

from collections.abc import Iterable

from trvzb.image import SLOT_A, SLOT_ADDRESS_DELTA, SLOT_B, decode_branch_with_link_target, iterate_call_sites
from trvzb.ota import FILE_VERSION_OFFSET, HEADER_STRING_LENGTH, HEADER_STRING_OFFSET, OtaFile
from trvzb.patch import SLOT_NAMES, PatchSet
from trvzb.version import McuVersion

SLOTS = {"A": SLOT_A, "B": SLOT_B}
HEADER_VERSION_RANGES = ((FILE_VERSION_OFFSET, 4), (HEADER_STRING_OFFSET, HEADER_STRING_LENGTH))


def slot_bytes(ota: OtaFile, slot_name: str) -> bytes:
    start = ota.slot_offset(slot_name)
    return bytes(ota.data[start : start + ota.slot_length])


def site_ranges(patch_set: PatchSet, version_string_offsets: Iterable[int], version_length: int) -> list[tuple[int, int]]:
    """Slot-relative (offset, length) of every range a build may change."""
    ranges = [(site.offset, site.length) for site in patch_set.sites]
    ranges += [(offset, version_length) for offset in version_string_offsets]
    return ranges


def in_ranges(offset: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start <= offset < start + length for start, length in ranges)


def unexpected_changes(stock: OtaFile, built: OtaFile, allowed_slot_ranges: list[tuple[int, int]]) -> list[str]:
    """Bytes that differ from stock outside the header versions and the allowed slot ranges."""
    if len(stock.data) != len(built.data):
        return [f"length changed: {len(stock.data)} -> {len(built.data)}"]
    allowed_file_ranges = list(HEADER_VERSION_RANGES)
    for slot_name in SLOT_NAMES:
        allowed_file_ranges += [(stock.slot_offset(slot_name) + start, length) for start, length in allowed_slot_ranges]
    return [
        f"file offset {offset:#x} changed outside every declared site"
        for offset in range(len(stock.data))
        if stock.data[offset] != built.data[offset] and not in_ranges(offset, allowed_file_ranges)
    ]


def site_bytes_mismatches(built: OtaFile, patch_set: PatchSet) -> list[str]:
    """Sites whose bytes in the built image differ from the patch set under review.

    Without this, a listing reviewed from the patch set says nothing about the
    image that would be flashed.
    """
    problems = []
    for site in patch_set.sites:
        for slot_name in SLOT_NAMES:
            start = site.offset
            found = slot_bytes(built, slot_name)[start : start + site.length]
            if found != site.patched_bytes[slot_name]:
                problems.append(f"site {site.name} slot {slot_name}: image holds {found.hex()}, patch set says {site.patched_bytes[slot_name].hex()}")
    return problems


def sites_splitting_stock_calls(patch_set: PatchSet, stock_slot_a: bytes) -> list[str]:
    """Sites that start or end inside a stock 32-bit `bl`, leaving half of it behind.

    Thumb-1 code is halfword-aligned, and `bl` is the only 32-bit instruction
    this image uses in code, so a site boundary inside one splits it.
    """
    call_sites = {call_site for call_site, _ in iterate_call_sites(stock_slot_a)}
    problems = []
    for site in patch_set.sites:
        for boundary_name, boundary in (("start", site.offset), ("end", site.offset + site.length)):
            if boundary - 2 in call_sites:
                problems.append(f"site {site.name} {boundary_name} {boundary:#x} splits the stock bl at {boundary - 2:#x}")
    return problems


def site_slot_differences(patch_set: PatchSet) -> list[str]:
    """Differences between a site's slot A and slot B bytes that relocation does not explain.

    The slots are one program linked 0x7c00 apart, so a correctly linked site
    is identical in both except for aligned 4-byte words holding absolute flash
    addresses, which differ by exactly that amount. Outside the sites,
    `unexpected_changes` already holds both slots to stock.
    """
    problems = []
    for site in patch_set.sites:
        slot_a, slot_b = site.patched_bytes["A"], site.patched_bytes["B"]
        position = 0
        while position < site.length:
            word_start = position - ((site.offset + position) % 4)
            is_aligned_word = word_start == position and position + 4 <= site.length
            if is_aligned_word:
                word_a = int.from_bytes(slot_a[position : position + 4], "little")
                word_b = int.from_bytes(slot_b[position : position + 4], "little")
                if word_a != word_b and word_b - word_a != SLOT_ADDRESS_DELTA:
                    problems.append(f"site {site.name}: word at {site.offset + position:#x} is {word_a:#x} in A, {word_b:#x} in B")
                position += 4
                continue
            if slot_a[position] != slot_b[position]:
                problems.append(f"site {site.name}: byte at {site.offset + position:#x} differs between slots outside an aligned word")
            position += 1
    return problems


def branch_targets_outside_functions(
    patch_set: PatchSet, function_starts: set[int]
) -> list[str]:
    """`bl` instructions in the patch whose target is neither a stock function nor inside the patch."""
    patch_ranges = [(site.offset, site.length) for site in patch_set.sites]
    problems = []
    for site in patch_set.sites:
        for slot_name in SLOT_NAMES:
            slot = SLOTS[slot_name]
            site_code = site.patched_bytes[slot_name]
            for position in range(0, len(site_code) - 3, 2):
                first, second = int.from_bytes(site_code[position : position + 2], "little"), int.from_bytes(site_code[position + 2 : position + 4], "little")
                target = decode_branch_with_link_target(site.offset + position, first, second)
                if target is None:
                    continue
                is_known = target in function_starts or in_ranges(target, patch_ranges)
                if not is_known:
                    problems.append(
                        f"site {site.name} slot {slot_name}: bl at {site.offset + position:#x} targets {target:#x} "
                        f"(MCU {slot.mcu_address(target):#x}), not a known function start"
                    )
    return problems


def version_consistency(stock: OtaFile, built: OtaFile, version_string_offsets: list[int]) -> list[str]:
    """The header's MCU version must match the strings in both slots and pack above stock's."""
    _, built_field = built.header_versions()
    _, stock_field = stock.header_versions()
    built_version = McuVersion.from_header_field(built_field)
    stock_version = McuVersion.from_header_field(stock_field)
    problems = []
    if built_version.packed() <= stock_version.packed():
        problems.append(f"MCU version {built_version} does not pack above stock {stock_version}")
    expected = str(built_version).encode()
    for slot_name in SLOT_NAMES:
        slot = slot_bytes(built, slot_name)
        for offset in version_string_offsets:
            found = slot[offset : offset + len(expected)]
            if found != expected:
                problems.append(f"slot {slot_name} version string at {offset:#x} is {found!r}, header says {expected!r}")
    return problems

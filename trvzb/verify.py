"""Independent checks of a built OTA against stock before it is flashed.

Each check returns a list of problems; an empty list means it passed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import capstone

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
    """Sites that start or end anywhere inside a stock 32-bit `bl`.

    A boundary one, two or three bytes into a call leaves part of it behind
    and turns it into a call somewhere else. (`dsb` is the image's only other
    32-bit instruction; both sit inside the protected reset routine.)
    """
    call_sites = {call_site for call_site, _ in iterate_call_sites(stock_slot_a)}
    problems = []
    for site in patch_set.sites:
        for boundary_name, boundary in (("start", site.offset), ("end", site.offset + site.length)):
            for call_site in (boundary - 3, boundary - 2, boundary - 1):
                if call_site in call_sites:
                    problems.append(f"site {site.name} {boundary_name} {boundary:#x} falls inside the stock bl at {call_site:#x}")
    return problems


def image_calls_overlapping_sites(built: OtaFile, patch_set: PatchSet, function_starts: set[int]) -> list[str]:
    """Calls in the built image that overlap a site and do not land on a function start.

    Decoded from the image itself, so a call only partly covered by a site, or
    changed some other way, is checked as the MCU will execute it.
    """
    site_ranges_in_slot = [(site.offset, site.length) for site in patch_set.sites]
    problems = []
    for slot_name in SLOT_NAMES:
        for call_site, target in iterate_call_sites(slot_bytes(built, slot_name)):
            overlaps_site = any(call_site < start + length and start < call_site + 4 for start, length in site_ranges_in_slot)
            if overlaps_site and target not in function_starts and not in_ranges(target, site_ranges_in_slot):
                problems.append(f"slot {slot_name}: bl at {call_site:#x} overlapping a site targets {target:#x}, not a function start")
    return problems


def branches_leaving_sites(built: OtaFile, patch_set: PatchSet, instruction_starts: set[int]) -> list[str]:
    """Branches in a site whose target lies outside every site and is not a stock instruction start."""
    disassembler = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
    site_ranges_in_slot = [(site.offset, site.length) for site in patch_set.sites]
    slot_a = slot_bytes(built, "A")
    problems = []
    for site in patch_set.sites:
        for decoded in disassembler.disasm(slot_a[site.offset : site.offset + site.length], site.offset):
            is_branch = decoded.mnemonic.startswith("b") and decoded.mnemonic not in ("bl", "bx", "blx", "bics", "bkpt")
            match = re.fullmatch(r"#(0x[0-9a-f]+|\d+)", decoded.op_str.split(", ")[-1]) if is_branch else None
            if match is None:
                continue
            target = int(match.group(1), 0)
            if not in_ranges(target, site_ranges_in_slot) and target not in instruction_starts:
                problems.append(f"site {site.name}: {decoded.mnemonic} at {decoded.address:#x} leaves to {target:#x}, not a stock instruction start")
    return problems


def site_listing_from_image(built: OtaFile, patch_set: PatchSet, context_bytes: int = 4) -> list[str]:
    """Disassembly of every site as the built image holds it, with surrounding bytes, in both slots."""
    disassembler = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
    disassembler.skipdata = True
    lines = []
    for site in patch_set.sites:
        start = max(0, site.offset - context_bytes) & ~1
        end = site.offset + site.length + context_bytes
        for slot_name in SLOT_NAMES:
            slot = SLOTS[slot_name]
            lines.append(f"--- site {site.name}, slot {slot_name}, image {site.offset:#x}-{site.offset + site.length:#x}, MCU {slot.mcu_address(site.offset):#x}")
            for decoded in disassembler.disasm(slot_bytes(built, slot_name)[start:end], slot.mcu_address(start)):
                image_offset = decoded.address - slot.link_base
                marker = ">" if site.offset <= image_offset < site.offset + site.length else " "
                lines.append(f" {marker} {decoded.address:#07x}  {decoded.mnemonic} {decoded.op_str}")
    return lines


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

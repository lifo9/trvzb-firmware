"""Assemble a patch set once per slot and write it as a PatchSet JSON file.

A patch set directory holds:

- `patch.S`: Thumb assembly with one `.section .site.<name>, "ax"` per site.
  Including `stock_symbols.inc` defines every function in analysis/symbols.toml
  as a Thumb symbol at its address in the slot being built (so `bl
  setpoint_set` works), every
  table as a data symbol, every code label with `.` written as `__`, and every
  RAM struct field as `<STRUCT>_<FIELD>` (offset) plus `<STRUCT>` (address).
- `sites.toml`: `regions`, the analysis/symbols.toml regions the patch set may
  touch, and one `[[site]]` per section with `name`, `offset` (slot-A image
  offset) and `max_length` (bytes the site may overwrite). A site outside every
  listed region is refused.

Each slot is linked with the stock symbols based at its link address and every
site placed at its MCU address, so branch offsets and absolute flash addresses
come out right for that slot. The stock bytes each site replaces are
read from the stock OTA and recorded, so the result only applies to that image.

Usage:
    build_patch.py <patch-set-directory> --output work/patches/<name>.json
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trvzb.image import SLOT_A, SLOT_B, Slot  # noqa: E402
from trvzb.ota import OtaFile  # noqa: E402
from trvzb.patch import SLOT_NAMES, PatchSet, PatchSite  # noqa: E402
from trvzb.symbols import Symbols, load_symbols  # noqa: E402
from trvzb.verify import sites_splitting_stock_calls  # noqa: E402

ASSEMBLER_FLAGS = ["-mcpu=cortex-m0plus", "-mthumb"]
SLOTS = {"A": SLOT_A, "B": SLOT_B}


@dataclass(frozen=True)
class SiteSpecification:
    name: str
    offset: int
    max_length: int


def load_site_specifications(sites_path: Path) -> tuple[str, list[str], list[SiteSpecification]]:
    with open(sites_path, "rb") as sites_file:
        document = tomllib.load(sites_file)
    sites = [SiteSpecification(entry["name"], entry["offset"], entry["max_length"]) for entry in document.get("site", [])]
    if not sites:
        raise ValueError(f"{sites_path} declares no sites")
    region_names = document.get("regions", [])
    if not region_names:
        raise ValueError(f"{sites_path} declares no regions; list the symbols.toml regions this patch set may touch")
    return document["name"], region_names, sites


def sites_outside_regions(sites: list[SiteSpecification], region_names: list[str], symbols: Symbols) -> list[str]:
    """Sites whose maximum extent is not inside one of the patch set's declared regions."""
    unknown = [name for name in region_names if name not in symbols.regions]
    if unknown:
        raise ValueError(f"unknown regions {unknown}; known: {sorted(symbols.regions)}")
    regions = [symbols.regions[name] for name in region_names]
    return [
        f"site {site.name} ({site.offset:#x}, up to {site.max_length} bytes) lies outside regions {region_names}"
        for site in sites
        if not any(region.contains(site.offset, site.max_length) for region in regions)
    ]


def build_stock_symbols_include(symbols: Symbols) -> str:
    """Assembly defining the stock image's symbols relative to the image base.

    The symbols hang off a label in the empty `.stock` section, which the
    linker script places at the slot's link address. Absolute symbols would
    lose their Thumb marking, and the linker refuses a `bl` to a symbol whose
    instruction set it cannot tell.
    """
    lines = [
        "@ Generated from analysis/symbols.toml by tools/build_patch.py.",
        "",
        "    .section .stock, \"ax\"",
        "stock_image_base:",
    ]
    for name, offset in sorted(symbols.function_offsets.items()):
        lines.append(f".thumb_set {name}, stock_image_base + {offset:#x}")
    for name, offset in sorted(symbols.label_offsets.items()):
        lines.append(f".thumb_set {name.replace('.', '__')}, stock_image_base + {offset:#x}")
    for name, table in sorted(symbols.tables.items()):
        lines.append(f".set {name}, stock_image_base + {table.offset:#x}")
    for name, ram_struct in sorted(symbols.ram_structs.items()):
        lines.append(f".set {name.upper()}, {ram_struct.address:#x}")
        for field_name, field_offset in sorted(ram_struct.fields.items()):
            lines.append(f".set {name.upper()}_{field_name.upper()}, {field_offset:#x}")
    return "\n".join(lines) + "\n"


# Collects anything assembled outside a site; it must stay empty.
UNPLACED_ADDRESS = 0x10000000


def build_linker_script(slot: Slot, sites: list[SiteSpecification]) -> str:
    """Place each site's section at its MCU address in `slot`.

    Branch veneers and interworking glue go into `.unplaced` too: the linker
    always creates their sections, empty, and a non-empty one means it had to
    rewrite a call, which fails the size assertion instead of shipping.
    """
    placements = [f"  .site.{site.name} {slot.mcu_address(site.offset):#x} : {{ *(.site.{site.name}) }}" for site in sites]
    return (
        "SECTIONS\n{\n"
        f"  .stock {slot.link_base:#x} (NOLOAD) : {{ *(.stock) }}\n"
        + "\n".join(placements)
        + f"\n  .unplaced {UNPLACED_ADDRESS:#x} : {{ *(.text) *(.data) *(.bss) *(.rodata*) "
        "*(.glue_7) *(.glue_7t) *(.vfp11_veneer) *(.v4_bx) }\n"
        "  /DISCARD/ : { *(.ARM.attributes) *(.comment) *(.iplt) *(.rel.iplt) *(.igot.plt) }\n"
        "}\n"
        'ASSERT(SIZEOF(.stock) == 0, "stock_symbols.inc must only define symbols")\n'
        'ASSERT(SIZEOF(.unplaced) == 0, "code or data outside a .site section, or a linker veneer")\n'
    )


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(command)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def parse_section_headers(objdump_headers: str) -> dict[str, tuple[int, int]]:
    """Section name -> (size, alignment) from `objdump -h` output."""
    sections = {}
    for line in objdump_headers.splitlines():
        match = re.match(r"\s*\d+\s+(\S+)\s+([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+[0-9a-f]+\s+2\*\*(\d+)", line)
        if match:
            sections[match.group(1)] = (int(match.group(2), 16), 1 << int(match.group(3)))
    return sections


def assemble_slot(
    patch_directory: Path, slot_name: str, sites: list[SiteSpecification], include_text: str, work_directory: Path
) -> dict[str, bytes]:
    """Bytes of every site section for one slot.

    Refuses a site the linker would pad: ld silently inserts filler before a
    section whose alignment its address does not meet, which shifts every
    instruction and breaks any stock branch into the site.
    """
    slot = SLOTS[slot_name]
    (work_directory / "stock_symbols.inc").write_text(include_text)
    object_path = work_directory / f"patch_{slot_name}.o"
    elf_path = work_directory / f"patch_{slot_name}.elf"
    linker_script_path = work_directory / f"link_{slot_name}.ld"
    linker_script_path.write_text(build_linker_script(slot, sites))
    run([
        "arm-none-eabi-as", *ASSEMBLER_FLAGS,
        "-I", str(work_directory), str(patch_directory / "patch.S"), "-o", str(object_path),
    ])
    run(["arm-none-eabi-ld", "--orphan-handling=error", "-T", str(linker_script_path), str(object_path), "-o", str(elf_path)])
    assembled_sections = parse_section_headers(run(["arm-none-eabi-objdump", "-h", str(object_path)]))
    site_bytes = {}
    for site in sites:
        section_name = f".site.{site.name}"
        if section_name not in assembled_sections:
            raise ValueError(f"sites.toml declares {site.name}, but patch.S has no {section_name} section")
        assembled_size, alignment = assembled_sections[section_name]
        site_address = slot.mcu_address(site.offset)
        if site_address % alignment != 0:
            raise ValueError(
                f"site {site.name} needs {alignment}-byte alignment, but its address {site_address:#x} is not aligned; "
                "the linker would pad it"
            )
        section_path = work_directory / f"site_{slot_name}_{site.name}.bin"
        run(["arm-none-eabi-objcopy", "-O", "binary", f"--only-section={section_name}", str(elf_path), str(section_path)])
        site_bytes[site.name] = section_path.read_bytes()
        if len(site_bytes[site.name]) != assembled_size:
            raise ValueError(f"site {site.name} assembled to {assembled_size} bytes but linked to {len(site_bytes[site.name])}")
    return site_bytes


def build_patch_set(patch_directory: Path, stock_ota: OtaFile, symbols: Symbols) -> PatchSet:
    patch_set_name, region_names, sites = load_site_specifications(patch_directory / "sites.toml")
    outside = sites_outside_regions(sites, region_names, symbols)
    if outside:
        raise ValueError("; ".join(outside))
    include_text = build_stock_symbols_include(symbols)
    with tempfile.TemporaryDirectory() as temporary_directory:
        bytes_by_slot = {
            slot_name: assemble_slot(patch_directory, slot_name, sites, include_text, Path(temporary_directory))
            for slot_name in SLOT_NAMES
        }
    patch_sites = []
    for site in sites:
        lengths = {slot_name: len(bytes_by_slot[slot_name][site.name]) for slot_name in SLOT_NAMES}
        if len(set(lengths.values())) != 1:
            raise ValueError(f"site {site.name} assembles to different lengths per slot: {lengths}")
        length = lengths["A"]
        if not 0 < length <= site.max_length:
            raise ValueError(f"site {site.name} is {length} bytes; it may use 1 to {site.max_length}")
        stock_bytes = {}
        for slot_name in SLOT_NAMES:
            start = stock_ota.slot_offset(slot_name) + site.offset
            stock_bytes[slot_name] = bytes(stock_ota.data[start : start + length])
        patched_bytes = {slot_name: bytes_by_slot[slot_name][site.name] for slot_name in SLOT_NAMES}
        patch_sites.append(PatchSite(site.name, site.offset, stock_bytes, patched_bytes))
    patch_set = PatchSet(patch_set_name, patch_sites)
    split_calls = sites_splitting_stock_calls(patch_set, bytes(stock_ota.data[stock_ota.slot_offset("A") : stock_ota.slot_offset("A") + stock_ota.slot_length]))
    if split_calls:
        raise ValueError("; ".join(split_calls))
    return patch_set


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("patch_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    patch_set = build_patch_set(arguments.patch_directory, OtaFile(), load_symbols())
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(patch_set.to_json() + "\n")
    for site in patch_set.sites:
        unchanged = site.stock_bytes == site.patched_bytes
        print(f"{site.name}: {site.length} bytes at {site.offset:#x}{' (identical to stock)' if unchanged else ''}")
    print(f"wrote {arguments.output}")


if __name__ == "__main__":
    main()

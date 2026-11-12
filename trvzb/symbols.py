"""Named locations from analysis/symbols.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

SYMBOLS_PATH = Path(__file__).resolve().parent.parent / "analysis" / "symbols.toml"


@dataclass(frozen=True)
class RamStruct:
    name: str
    address: int
    fields: dict[str, int]


@dataclass(frozen=True)
class Table:
    name: str
    offset: int
    length: int


@dataclass(frozen=True)
class ProtectedRange:
    name: str
    start: int
    end: int

    def overlaps(self, start: int, length: int) -> bool:
        return start < self.end and self.start < start + length


@dataclass(frozen=True)
class Region:
    name: str
    start: int
    end: int

    def contains(self, start: int, length: int) -> bool:
        return self.start <= start and start + length <= self.end


@dataclass(frozen=True)
class Symbols:
    function_offsets: dict[str, int]
    label_offsets: dict[str, int]
    ram_structs: dict[str, RamStruct]
    tables: dict[str, Table]
    protected_ranges: list[ProtectedRange]
    regions: dict[str, Region]

    def offset(self, name: str) -> int:
        """Image offset of a function or label."""
        if name in self.function_offsets:
            return self.function_offsets[name]
        return self.label_offsets[name]

    def field_address(self, qualified_name: str) -> tuple[int, int]:
        """(struct address, field offset) for `struct.field`."""
        struct_name, field_name = qualified_name.split(".", 1)
        ram_struct = self.ram_structs[struct_name]
        return ram_struct.address, ram_struct.fields[field_name]

    def name_at(self, image_offset: int) -> str | None:
        for name, offset in self.function_offsets.items():
            if offset == image_offset:
                return name
        return None


def load_symbols(path: Path = SYMBOLS_PATH) -> Symbols:
    with open(path, "rb") as symbols_file:
        document = tomllib.load(symbols_file)
    function_offsets = {entry["name"]: entry["offset"] for entry in document.get("function", [])}
    label_offsets = {entry["name"]: entry["offset"] for entry in document.get("label", [])}
    duplicates = set(function_offsets) & set(label_offsets)
    if duplicates:
        raise ValueError(f"names used for both a function and a label: {sorted(duplicates)}")
    return Symbols(
        function_offsets=function_offsets,
        label_offsets=label_offsets,
        ram_structs={
            entry["name"]: RamStruct(entry["name"], entry["address"], dict(entry.get("fields", {})))
            for entry in document.get("ram", [])
        },
        tables={entry["name"]: Table(entry["name"], entry["offset"], entry["length"]) for entry in document.get("table", [])},
        protected_ranges=[
            ProtectedRange(entry["name"], entry["start"], entry["end"]) for entry in document.get("protected", [])
        ],
        regions={entry["name"]: Region(entry["name"], entry["start"], entry["end"]) for entry in document.get("region", [])},
    )

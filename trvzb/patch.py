"""Patch sets: same-length byte replacements at slot-relative offsets.

A site carries its own bytes per slot, because the two slots are the same
program linked at different addresses: any absolute flash address in a patch
differs by 0x7c00 between them, and so do a few stock instructions. Each site
also records the stock bytes it replaces in each slot, so applying a patch set
to anything but the image it was built against fails instead of corrupting it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from trvzb.ota import OtaFile
from trvzb.symbols import ProtectedRange

SLOT_NAMES = ("A", "B")


@dataclass(frozen=True)
class PatchSite:
    name: str
    offset: int
    stock_bytes: dict[str, bytes]
    patched_bytes: dict[str, bytes]

    def __post_init__(self) -> None:
        for slot_name in SLOT_NAMES:
            if slot_name not in self.stock_bytes or slot_name not in self.patched_bytes:
                raise ValueError(f"site {self.name} lacks bytes for slot {slot_name}")
            if len(self.stock_bytes[slot_name]) != len(self.patched_bytes[slot_name]):
                raise ValueError(f"site {self.name} changes length in slot {slot_name}")
        if len(self.stock_bytes["A"]) != len(self.stock_bytes["B"]):
            raise ValueError(f"site {self.name} has different lengths in the two slots")

    @property
    def length(self) -> int:
        return len(self.stock_bytes["A"])


@dataclass(frozen=True)
class PatchSet:
    name: str
    sites: list[PatchSite]

    def overlapping_site_pairs(self) -> list[tuple[str, str]]:
        ordered = sorted(self.sites, key=lambda site: site.offset)
        return [
            (earlier.name, later.name)
            for earlier, later in zip(ordered, ordered[1:])
            if earlier.offset + earlier.length > later.offset
        ]

    def apply(self, ota: OtaFile, protected_ranges: list[ProtectedRange]) -> None:
        """Validate every site first, then write; nothing is written if any check fails."""
        overlaps = self.overlapping_site_pairs()
        if overlaps:
            raise ValueError(f"patch set {self.name} has overlapping sites: {overlaps}")
        for site in self.sites:
            touched = [protected.name for protected in protected_ranges if protected.overlaps(site.offset, site.length)]
            if touched:
                raise ValueError(f"site {site.name} at {site.offset:#x} touches protected ranges {touched}")
            for slot_name in SLOT_NAMES:
                ota.check_slot_bytes(slot_name, site.offset, site.stock_bytes[slot_name])
        for site in self.sites:
            for slot_name in SLOT_NAMES:
                ota.patch_slot(slot_name, site.offset, site.stock_bytes[slot_name], site.patched_bytes[slot_name])

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "sites": [
                    {
                        "name": site.name,
                        "offset": f"{site.offset:#x}",
                        "stock": {slot_name: site.stock_bytes[slot_name].hex() for slot_name in SLOT_NAMES},
                        "patched": {slot_name: site.patched_bytes[slot_name].hex() for slot_name in SLOT_NAMES},
                    }
                    for site in self.sites
                ],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, json_text: str) -> PatchSet:
        document = json.loads(json_text)
        return cls(
            name=document["name"],
            sites=[
                PatchSite(
                    name=site["name"],
                    offset=int(site["offset"], 0),
                    stock_bytes={slot_name: bytes.fromhex(site["stock"][slot_name]) for slot_name in SLOT_NAMES},
                    patched_bytes={slot_name: bytes.fromhex(site["patched"][slot_name]) for slot_name in SLOT_NAMES},
                )
                for site in document["sites"]
            ],
        )

    @classmethod
    def load(cls, path: Path) -> PatchSet:
        return cls.from_json(path.read_text())

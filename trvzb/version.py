"""Application-MCU firmware versions and the byte the MCU compares them by.

The MCU parses its own version string (`1.3.1` in stock 1.4.4) and packs it as
`major << 6 | minor << 4 | patch` (2, 2 and 4 bits; image offset 0x4ed4). It
accepts an update only if the notified version packs strictly higher than its
own, so a version whose parts overflow those widths would wrap and be refused.
The OTA header carries the same version as an eight-digit field: `00001301`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAXIMUM_MAJOR = 3
MAXIMUM_MINOR = 3
MAXIMUM_PATCH = 15


@dataclass(frozen=True, order=True)
class McuVersion:
    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        is_representable = (
            0 <= self.major <= MAXIMUM_MAJOR and 0 <= self.minor <= MAXIMUM_MINOR and 0 <= self.patch <= MAXIMUM_PATCH
        )
        if not is_representable:
            raise ValueError(
                f"MCU version {self.major}.{self.minor}.{self.patch} does not fit the packed byte "
                f"(major and minor at most {MAXIMUM_MAJOR}, patch at most {MAXIMUM_PATCH})"
            )

    @classmethod
    def parse(cls, version_text: str) -> McuVersion:
        """Parse the `1.3.1` form the firmware stores in its image."""
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version_text)
        if match is None:
            raise ValueError(f"not an MCU version: {version_text!r}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    @classmethod
    def from_header_field(cls, header_field: str) -> McuVersion:
        """Parse the OTA header's eight-digit form, e.g. `00001301`."""
        match = re.fullmatch(r"0000(\d)(\d)(\d\d)", header_field)
        if match is None:
            raise ValueError(f"not an MCU header version field: {header_field!r}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def packed(self) -> int:
        return self.major << 6 | self.minor << 4 | self.patch

    def header_field(self) -> str:
        return f"0000{self.major}{self.minor}{self.patch:02d}"

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

"""Shared fixtures: a synthetic TRVZB-shaped OTA file, free of vendor bytes."""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "tools"))

OTA_MAGIC = 0x0BEEF11E
OTA_HEADER_LENGTH = 56
MANUFACTURER_CODE = 4742
IMAGE_TYPE = 8199
STOCK_FILE_VERSION = 0x1404
STOCK_HEADER_STRING = b"vers:00001404,00001301\n"
SYNTHETIC_SLOT_LENGTH = 0x200
VERSION_STRING_OFFSETS = (0x40, 0x100, 0x1F0)


@dataclass(frozen=True)
class SyntheticOta:
    path: Path
    data: bytes
    mcu_payload_start: int
    slot_length: int

    def slot_start(self, slot_name: str) -> int:
        return self.mcu_payload_start + 20 + {"A": 0, "B": 1}[slot_name] * self.slot_length


def build_synthetic_ota_bytes(slot_length: int = SYNTHETIC_SLOT_LENGTH) -> tuple[bytes, int]:
    """Return (file bytes, file offset of the MCU payload).

    Mirrors the real layout: Zigbee OTA header, a 0x0000 subelement standing in
    for the encrypted SoC image, and a 0xf000 subelement holding a big-endian
    part table followed by two slots. Each slot carries the stock version
    string `1.3.1` at the same offsets, like the real builds.
    """
    soc_image = bytes((index * 7 + 3) & 0xFF for index in range(0x80))

    slots = []
    for slot_index in range(2):
        slot = bytearray((index + slot_index) & 0xFF for index in range(slot_length))
        for offset in VERSION_STRING_OFFSETS:
            slot[offset : offset + 5] = b"1.3.1"
        slots.append(bytes(slot))
    part_table = struct.pack(">5I", 2, 0x14, slot_length, 0x14 + slot_length, slot_length)
    mcu_payload = part_table + slots[0] + slots[1]

    body = (
        struct.pack("<HI", 0x0000, len(soc_image)) + soc_image
        + struct.pack("<HI", 0xF000, len(mcu_payload)) + mcu_payload
    )
    total_size = OTA_HEADER_LENGTH + len(body)
    header = bytearray(OTA_HEADER_LENGTH)
    struct.pack_into("<IHHHHHIH", header, 0, OTA_MAGIC, 0x0100, OTA_HEADER_LENGTH, 0, MANUFACTURER_CODE, IMAGE_TYPE, STOCK_FILE_VERSION, 2)
    header[0x14 : 0x14 + len(STOCK_HEADER_STRING)] = STOCK_HEADER_STRING
    struct.pack_into("<I", header, 0x34, total_size)

    mcu_payload_start = OTA_HEADER_LENGTH + 6 + len(soc_image) + 6
    return bytes(header) + body, mcu_payload_start


@pytest.fixture
def synthetic_ota(tmp_path: Path) -> SyntheticOta:
    data, mcu_payload_start = build_synthetic_ota_bytes()
    path = tmp_path / "synthetic.ota"
    path.write_bytes(data)
    return SyntheticOta(path=path, data=data, mcu_payload_start=mcu_payload_start, slot_length=SYNTHETIC_SLOT_LENGTH)


@pytest.fixture
def stock_ota_path() -> Path:
    path = Path(os.environ.get("TRVZB_OTA", REPOSITORY_ROOT / "work" / "trvzb_v1.4.4.ota"))
    if not path.exists():
        pytest.skip("stock OTA not present; run `just fetch`")
    return path

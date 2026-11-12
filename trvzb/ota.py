"""The TRVZB Zigbee OTA file: header fields and the application-MCU subelement.

The OTA carries two subelements: the encrypted Zigbee SoC image (tag 0x0000,
left untouched) and the application-MCU image (tag 0xf000), which is plain
code. The MCU subelement starts with a 20-byte part table followed by two
separately linked builds of the same program -- slot A at MCU address 0x400
and slot B at 0x8000 -- so every patch must be applied to both slots at the
same slot-relative offset.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct

STOCK_OTA_PATH = os.environ.get("TRVZB_OTA", "work/trvzb_v1.4.4.ota")
MCU_SUBELEMENT_TAG = 0xF000
MCU_PART_TABLE_LENGTH = 20
FILE_VERSION_OFFSET = 0x0E
HEADER_STRING_OFFSET = 0x14
HEADER_STRING_LENGTH = 32
TOTAL_IMAGE_SIZE_OFFSET = 0x34


class OtaFile:
    def __init__(self, path: str = STOCK_OTA_PATH) -> None:
        self.data = bytearray(open(path, "rb").read())
        identifier, _, self.header_length = struct.unpack_from("<IHH", self.data, 0)
        assert identifier == 0x0BEEF11E, f"not an OTA file: {identifier:#x}"
        total_image_size, = struct.unpack_from("<I", self.data, TOTAL_IMAGE_SIZE_OFFSET)
        assert total_image_size == len(self.data), "total image size disagrees with the file length"
        self.mcu_payload_start, self.mcu_payload_length = self._locate_mcu_subelement()
        self.slot_length = (self.mcu_payload_length - MCU_PART_TABLE_LENGTH) // 2
        assert MCU_PART_TABLE_LENGTH + 2 * self.slot_length == self.mcu_payload_length, "MCU payload is not two equal slots"

    def _locate_mcu_subelement(self) -> tuple[int, int]:
        offset = self.header_length
        while offset < len(self.data) - 6:
            tag, length = struct.unpack_from("<HI", self.data, offset)
            if tag == MCU_SUBELEMENT_TAG:
                return offset + 6, length
            offset += 6 + length
        raise AssertionError("no MCU subelement found")

    def slot_offset(self, slot_name: str) -> int:
        """File offset of a slot's first byte."""
        slot_index = {"A": 0, "B": 1}[slot_name]
        return self.mcu_payload_start + MCU_PART_TABLE_LENGTH + slot_index * self.slot_length

    def patch_both_slots(self, slot_relative_offset: int, old_bytes: bytes, new_bytes: bytes) -> None:
        """Apply one same-length patch at the same offset inside both slots."""
        assert len(old_bytes) == len(new_bytes), "patches must not change the image length"
        for slot_name in ("A", "B"):
            start = self.slot_offset(slot_name) + slot_relative_offset
            found = bytes(self.data[start : start + len(old_bytes)])
            assert found == old_bytes, f"slot {slot_name} at {slot_relative_offset:#x} holds {found!r}, expected {old_bytes!r}"
            self.data[start : start + len(new_bytes)] = new_bytes

    def find_in_slot_a(self, pattern: bytes) -> list[int]:
        """Slot-relative offsets of every match inside slot A."""
        slot_start = self.slot_offset("A")
        slot = bytes(self.data[slot_start : slot_start + self.slot_length])
        return [match.start() for match in re.finditer(re.escape(pattern), slot)]

    def set_file_version(self, file_version: int) -> None:
        struct.pack_into("<I", self.data, FILE_VERSION_OFFSET, file_version)

    def set_header_versions(self, zigbee_version: str, mcu_version: str) -> None:
        """Rewrite the `vers:AAAAAAAA,BBBBBBBB` header string, keeping its length."""
        header_string = bytes(self.data[HEADER_STRING_OFFSET : HEADER_STRING_OFFSET + HEADER_STRING_LENGTH])
        match = re.match(rb"vers:(\d{8}),(\d{8})\n", header_string)
        assert match is not None, f"unexpected header string {header_string!r}"
        replacement = f"vers:{zigbee_version},{mcu_version}\n".encode()
        assert len(replacement) == match.end(), "replacement header string changes length"
        self.data[HEADER_STRING_OFFSET : HEADER_STRING_OFFSET + len(replacement)] = replacement

    def header_versions(self) -> tuple[str, str]:
        """The Zigbee and MCU version fields of the header string, e.g. ('00001404', '00001301')."""
        header_string = bytes(self.data[HEADER_STRING_OFFSET : HEADER_STRING_OFFSET + HEADER_STRING_LENGTH])
        match = re.match(rb"vers:(\d{8}),(\d{8})\n", header_string)
        assert match is not None, f"unexpected header string {header_string!r}"
        return match.group(1).decode(), match.group(2).decode()

    def mcu_header_string(self) -> bytes:
        return bytes(self.data[HEADER_STRING_OFFSET : HEADER_STRING_OFFSET + HEADER_STRING_LENGTH]).rstrip(b"\x00")

    def write(self, path: str) -> dict:
        total_image_size, = struct.unpack_from("<I", self.data, TOTAL_IMAGE_SIZE_OFFSET)
        assert total_image_size == len(self.data), "length changed; total image size would need updating"
        open(path, "wb").write(self.data)
        file_version, = struct.unpack_from("<I", self.data, FILE_VERSION_OFFSET)
        return {
            "fileName": path.rsplit("/", 1)[-1],
            "fileVersion": file_version,
            "fileSize": len(self.data),
            "imageType": struct.unpack_from("<H", self.data, 12)[0],
            "manufacturerCode": struct.unpack_from("<H", self.data, 10)[0],
            "sha512": hashlib.sha512(self.data).hexdigest(),
        }

    def changed_offsets_against(self, other_path: str = STOCK_OTA_PATH) -> list[int]:
        stock = open(other_path, "rb").read()
        return [index for index in range(len(stock)) if stock[index] != self.data[index]]

"""Behaviour of the firmware mapper against the stock 1.4.4 slot A image.

Each test pins a fact about the binary that the patch work depends on, read
through the mapper the way an analyst would.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firmware_map import FirmwareMap

RESET_HANDLER = 0xD8
UI_HANDLER_REACHED_BY_POINTER = 0x2E38
KNOB_PATH_INSTRUCTION = 0x314A
CLAMP_FUNCTION = 0x7148
VALVE_PERCENT_PRODUCER = 0x648C
SETTINGS_STRUCT = 0x20000698
VALVE_OPENING_DEGREE_FIELD = 0x15
BUILD_TIME_STRING = 0x78B4
DIGIT_FONT = 0x781F


@pytest.fixture(scope="module")
def firmware(stock_slot_a_path: Path) -> FirmwareMap:
    return FirmwareMap(str(stock_slot_a_path))


def test_vector_table__returns_image_offsets(firmware: FirmwareMap) -> None:
    assert firmware.vector_table()[0] == RESET_HANDLER


def test_functions__include_reset_handler(firmware: FirmwareMap) -> None:
    assert RESET_HANDLER in firmware.functions


def test_functions__include_handler_reached_only_through_pointer_table(firmware: FirmwareMap) -> None:
    assert UI_HANDLER_REACHED_BY_POINTER in firmware.functions


def test_functions__follow_switch_cases_into_knob_path(firmware: FirmwareMap) -> None:
    assert KNOB_PATH_INSTRUCTION in firmware.functions[UI_HANDLER_REACHED_BY_POINTER].instructions


def test_callers_of__includes_call_from_knob_path(firmware: FirmwareMap) -> None:
    assert UI_HANDLER_REACHED_BY_POINTER in firmware.callers_of[CLAMP_FUNCTION]


def test_field_accesses__follow_branches_to_valve_opening_degree_read(firmware: FirmwareMap) -> None:
    readers, _ = firmware.field_accesses(SETTINGS_STRUCT)
    assert VALVE_PERCENT_PRODUCER in readers[VALVE_OPENING_DEGREE_FIELD]


def test_string_references__resolve_literal_pool_flash_pointers(firmware: FirmwareMap) -> None:
    references = {address: readers for address, _, readers in firmware.string_references()}
    assert references[BUILD_TIME_STRING]


def test_users_of__accepts_image_offset_of_flash_data(firmware: FirmwareMap) -> None:
    assert firmware.users_of(DIGIT_FONT)


def test_raw_listing__continues_past_undecodable_data(firmware: FirmwareMap) -> None:
    listed_addresses = [int(line.split()[0], 16) for line in firmware.raw_listing(0x4FB8, 0x60)]
    assert max(listed_addresses) >= 0x4FB8 + 0x60 - 4

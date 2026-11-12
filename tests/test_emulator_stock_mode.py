"""mode_set's guards under emulation: what the knob patch's mode changes rely on.

mode_set(mode, is_local) refuses auto until the clock has been synced over
Zigbee (runtime.time_synced), and refuses any change while today's byte in
the per-weekday table at settings + 0x9d is zero. The weekday comes from the
clock reader 0x1dc4, stubbed here, which leaves index 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator
from trvzb.image import SLOT_A
from trvzb.symbols import Symbols, load_symbols

MODE_HEAT, MODE_OFF, MODE_AUTO = 0, 1, 2
WEEKDAY_TABLE_OFFSET = 0x9D
WEEKDAY_TABLE_LENGTH = 8


@pytest.fixture(scope="module")
def symbols() -> Symbols:
    return load_symbols()


@pytest.fixture(scope="module")
def function_starts(stock_slot_a_path: Path) -> set[int]:
    return set(FirmwareMap(str(stock_slot_a_path)).functions)


def mode_after_mode_set(
    stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int],
    current_mode: int, requested_mode: int, time_synced: int, weekday_enabled: int,
) -> int:
    emulator = FirmwareEmulator(
        slot=SLOT_A,
        slot_image=stock_slot_a_path.read_bytes(),
        function_starts=function_starts,
        allowed_functions={symbols.offset("mode_set")},
    )
    settings_address, mode_field = symbols.field_address("settings.mode")
    runtime_address, time_synced_field = symbols.field_address("runtime.time_synced")
    emulator.write_byte(settings_address + mode_field, current_mode)
    emulator.write_byte(runtime_address + time_synced_field, time_synced)
    for day in range(WEEKDAY_TABLE_LENGTH):
        emulator.write_byte(settings_address + WEEKDAY_TABLE_OFFSET + day, weekday_enabled)
    emulator.call_function(symbols.offset("mode_set"), (requested_mode, 1))
    return emulator.read_byte(settings_address + mode_field)


def test_mode_set__enters_auto_once_time_is_synced(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_AUTO, time_synced=1, weekday_enabled=1) == MODE_AUTO


def test_mode_set__refuses_auto_before_time_sync(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_AUTO, time_synced=0, weekday_enabled=1) == MODE_OFF


def test_mode_set__allows_heat_before_time_sync(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_HEAT, time_synced=0, weekday_enabled=1) == MODE_HEAT


def test_mode_set__refuses_any_change_when_todays_byte_is_zero(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_HEAT, time_synced=1, weekday_enabled=0) == MODE_OFF

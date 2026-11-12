"""mode_set's guards under emulation: what the knob patch's mode changes rely on.

mode_set(mode, is_local) refuses auto until the clock has been synced over
Zigbee (runtime.time_synced), and refuses any change while today's byte at
settings + 0x9d + weekday is zero. The clock reader 0x1dc4 yields weekdays
1-7, so the guard reads settings + 0x9e to + 0xa4: exactly the seven bytes
the datapoint 118 handler writes. The clock reader is stubbed here and writes
a chosen weekday into the buffer it is given.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator, outcome_for_every_flag_pattern
from trvzb.image import SLOT_A
from trvzb.symbols import Symbols, load_symbols

MODE_HEAT, MODE_OFF, MODE_AUTO = 0, 1, 2
WEEKDAY_GUARD_BASE = 0x9D
WEEKDAYS = range(1, 8)
CLOCK_WEEKDAY_BYTE = 3


@pytest.fixture(scope="module")
def symbols() -> Symbols:
    return load_symbols()


@pytest.fixture(scope="module")
def function_starts(stock_slot_a_path: Path) -> set[int]:
    return set(FirmwareMap(str(stock_slot_a_path)).functions)


def mode_after_mode_set(
    stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int],
    current_mode: int, requested_mode: int, time_synced: int, enabled_weekdays: set[int], today: int = 3,
) -> int:
    """The mode afterwards; identical under both stub flag patterns or the test fails."""
    return outcome_for_every_flag_pattern(
        lambda clobbered_flags: mode_after_mode_set_once(
            stock_slot_a_path, symbols, function_starts, current_mode, requested_mode, time_synced,
            enabled_weekdays, today, clobbered_flags,
        )
    )


def mode_after_mode_set_once(
    stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int],
    current_mode: int, requested_mode: int, time_synced: int, enabled_weekdays: set[int], today: int,
    clobbered_flags: int,
) -> int:
    emulator = FirmwareEmulator(
        slot=SLOT_A,
        slot_image=stock_slot_a_path.read_bytes(),
        function_starts=function_starts,
        allowed_functions={symbols.offset("mode_set")},
        clobbered_flags=clobbered_flags,
        stub_effects={
            symbols.offset("clock_read"): lambda emulator, arguments: emulator.write_byte(arguments[0] + CLOCK_WEEKDAY_BYTE, today)
        },
    )
    settings_address, mode_field = symbols.field_address("settings.mode")
    runtime_address, time_synced_field = symbols.field_address("runtime.time_synced")
    emulator.write_byte(settings_address + mode_field, current_mode)
    emulator.write_byte(runtime_address + time_synced_field, time_synced)
    for weekday in WEEKDAYS:
        emulator.write_byte(settings_address + WEEKDAY_GUARD_BASE + weekday, 1 if weekday in enabled_weekdays else 0)
    emulator.call_function(symbols.offset("mode_set"), (requested_mode, 1))
    return emulator.read_byte(settings_address + mode_field)


def test_mode_set__enters_auto_once_time_is_synced(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_AUTO, time_synced=1, enabled_weekdays=set(WEEKDAYS)) == MODE_AUTO


def test_mode_set__refuses_auto_before_time_sync(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_AUTO, time_synced=0, enabled_weekdays=set(WEEKDAYS)) == MODE_OFF


def test_mode_set__allows_heat_before_time_sync(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_HEAT, time_synced=0, enabled_weekdays=set(WEEKDAYS)) == MODE_HEAT


def test_mode_set__refuses_any_change_when_todays_byte_is_zero(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_HEAT, time_synced=1, enabled_weekdays=set(WEEKDAYS) - {3}) == MODE_OFF


def test_mode_set__checks_only_todays_byte(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    only_wednesday = {3}
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_HEAT, 1, only_wednesday, today=3) == MODE_HEAT
    assert mode_after_mode_set(stock_slot_a_path, symbols, function_starts, MODE_OFF, MODE_HEAT, 1, only_wednesday, today=4) == MODE_OFF

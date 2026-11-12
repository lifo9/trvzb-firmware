"""The stock knob path under emulation: the baseline the knob patch replaces.

Runs the knob branch of the main screen handler (`ui_main_screen`, entered at
0x3104 the way its event switch enters it) and records what it asks for.
Setpoints are raw settings units; the test uses minimum 10 and maximum 70.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator
from trvzb.image import SLOT_A
from trvzb.symbols import Symbols, load_symbols

KNOB_BRANCH = 0x3104
KNOB_UP_EVENT = 5
KNOB_DOWN_EVENT = 6
MODE_HEAT, MODE_OFF, MODE_AUTO = 0, 1, 2
RUNNING = 2
MINIMUM_SETPOINT = 10
MAXIMUM_SETPOINT = 70
LOCAL_CHANGE = 1


@pytest.fixture(scope="module")
def symbols() -> Symbols:
    return load_symbols()


@pytest.fixture(scope="module")
def function_starts(stock_slot_a_path: Path) -> set[int]:
    return set(FirmwareMap(str(stock_slot_a_path)).functions)


def run_knob(
    stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int], mode: int, pending: int, event: int
) -> FirmwareEmulator:
    emulator = FirmwareEmulator(
        slot=SLOT_A,
        slot_image=stock_slot_a_path.read_bytes(),
        function_starts=function_starts,
        allowed_functions={symbols.offset("clamp"), symbols.offset("switch8"), symbols.offset("operating_state_get")},
    )
    settings_address, mode_field = symbols.field_address("settings.mode")
    _, minimum_field = symbols.field_address("settings.minimum_setpoint")
    _, maximum_field = symbols.field_address("settings.maximum_setpoint")
    ui_edit_address, pending_field = symbols.field_address("ui_edit.pending_value")
    runtime_address, operating_state_field = symbols.field_address("runtime.operating_state")
    emulator.write_byte(settings_address + mode_field, mode)
    emulator.write_byte(settings_address + minimum_field, MINIMUM_SETPOINT)
    emulator.write_byte(settings_address + maximum_field, MAXIMUM_SETPOINT)
    emulator.write_byte(ui_edit_address + pending_field, pending)
    emulator.write_byte(runtime_address + operating_state_field, RUNNING)
    emulator.run_fragment_ending_in_pop(
        KNOB_BRANCH, registers={"r4": event, "r5": ui_edit_address, "r6": settings_address}, saved_register_count=5
    )
    return emulator


def setpoint_calls(emulator: FirmwareEmulator, symbols: Symbols) -> list[tuple[int, int]]:
    return [arguments[:2] for arguments in emulator.calls_to(symbols.offset("setpoint_set"))]


def mode_calls(emulator: FirmwareEmulator, symbols: Symbols) -> list[tuple[int, int]]:
    return [arguments[:2] for arguments in emulator.calls_to(symbols.offset("mode_set"))]


def test_stock_knob__up_in_heat_raises_the_setpoint(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    emulator = run_knob(stock_slot_a_path, symbols, function_starts, MODE_HEAT, pending=40, event=KNOB_UP_EVENT)
    assert setpoint_calls(emulator, symbols) == [(41, LOCAL_CHANGE)]
    assert mode_calls(emulator, symbols) == []


def test_stock_knob__down_in_heat_lowers_the_setpoint(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    emulator = run_knob(stock_slot_a_path, symbols, function_starts, MODE_HEAT, pending=40, event=KNOB_DOWN_EVENT)
    assert setpoint_calls(emulator, symbols) == [(39, LOCAL_CHANGE)]


def test_stock_knob__does_nothing_in_auto(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    emulator = run_knob(stock_slot_a_path, symbols, function_starts, MODE_AUTO, pending=40, event=KNOB_UP_EVENT)
    assert emulator.calls == []


def test_stock_knob__below_minimum_switches_off(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    emulator = run_knob(stock_slot_a_path, symbols, function_starts, MODE_HEAT, pending=MINIMUM_SETPOINT, event=KNOB_DOWN_EVENT)
    assert mode_calls(emulator, symbols) == [(MODE_OFF, LOCAL_CHANGE)]


def test_stock_knob__up_from_off_switches_to_heat_at_minimum(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    emulator = run_knob(stock_slot_a_path, symbols, function_starts, MODE_OFF, pending=MINIMUM_SETPOINT - 1, event=KNOB_UP_EVENT)
    assert mode_calls(emulator, symbols) == [(MODE_HEAT, LOCAL_CHANGE)]
    assert setpoint_calls(emulator, symbols) == [(MINIMUM_SETPOINT, LOCAL_CHANGE)]


def test_stock_knob__up_at_maximum_stays_at_maximum(stock_slot_a_path: Path, symbols: Symbols, function_starts: set[int]) -> None:
    emulator = run_knob(stock_slot_a_path, symbols, function_starts, MODE_HEAT, pending=MAXIMUM_SETPOINT, event=KNOB_UP_EVENT)
    assert setpoint_calls(emulator, symbols) == [(MAXIMUM_SETPOINT, LOCAL_CHANGE)]

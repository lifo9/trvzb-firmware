"""Stock versus patched: the whole main screen, for every event, mode and direction.

The patch must change only the knob detents and the short press in auto, and
on screen only the two digits. Everything else - long press, configuration
key, child lock, the alternate screen, every icon - must match stock.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from build_patch import build_patch_set
from conftest import REPOSITORY_ROOT
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator, outcome_for_every_flag_pattern
from trvzb.image import SLOT_A, SLOT_B
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet
from trvzb.symbols import Symbols, load_symbols

pytestmark = pytest.mark.skipif(shutil.which("arm-none-eabi-as") is None, reason="arm-none-eabi binutils not on PATH")

PATCH_DIRECTORY = REPOSITORY_ROOT / "patches" / "knob-valve-percent"
MODE_HEAT, MODE_OFF, MODE_AUTO = 0, 1, 2
SHORT_PRESS, KNOB_UP, KNOB_DOWN = 1, 5, 6
RUNNING = 2
EVENTS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12)
DIGIT_BYTES = (0, 2)
SCREEN_FUNCTIONS = (
    "ui_main_screen", "operating_state_get", "clamp", "switch8", "draw_setpoint", "draw_two_characters",
    "draw_character", "draw_value_tenths", "unsigned_divide", "signed_divide",
)
DRAW_TWO_CHARACTERS_INNER = 0x324C


@pytest.fixture(scope="module")
def symbols() -> Symbols:
    return load_symbols()


@pytest.fixture(scope="module")
def patch_set(stock_ota_path: Path, symbols: Symbols) -> PatchSet:
    return build_patch_set(PATCH_DIRECTORY, OtaFile(str(stock_ota_path)), symbols)


@pytest.fixture(scope="module")
def function_starts(stock_slot_a_path: Path) -> set[int]:
    return set(FirmwareMap(str(stock_slot_a_path)).functions)


@pytest.fixture(scope="module", params=["A", "B"])
def slot_image(request: pytest.FixtureRequest, stock_slot_a_path: Path, stock_slot_b_path: Path) -> tuple[str, bytes]:
    path = stock_slot_a_path if request.param == "A" else stock_slot_b_path
    return request.param, path.read_bytes()


def screen_pass(
    slot_name: str, image: bytes, function_starts: set[int], symbols: Symbols, patch_set: PatchSet | None,
    event: int, mode: int, child_lock: int, direction: int,
) -> tuple[tuple[tuple[int, int, int], ...], bytes]:
    """(calls with their first two arguments, framebuffer) after one main-screen pass with `event`."""
    def scenario(clobbered_flags: int) -> tuple[tuple[tuple[int, int, int], ...], bytes]:
        emulator = FirmwareEmulator(
            slot=SLOT_A if slot_name == "A" else SLOT_B,
            slot_image=image,
            function_starts=function_starts,
            allowed_functions={symbols.offset(name) for name in SCREEN_FUNCTIONS} | {DRAW_TWO_CHARACTERS_INNER},
            clobbered_flags=clobbered_flags,
            return_values={symbols.offset("input_event_producer"): event},
        )
        if patch_set is not None:
            emulator.apply_patch_set(patch_set)
        settings_address, _ = symbols.field_address("settings.mode")
        values = {"mode": mode, "child_lock": child_lock, "screen_direction": direction, "valve_opening_degree": 50,
                  "minimum_setpoint": 10, "maximum_setpoint": 70}
        for field_name, value in values.items():
            _, field_offset = symbols.field_address(f"settings.{field_name}")
            emulator.write_byte(settings_address + field_offset, value)
        runtime_address, operating_field = symbols.field_address("runtime.operating_state")
        emulator.write_byte(runtime_address + operating_field, RUNNING)
        emulator.call_function(symbols.offset("ui_main_screen"))
        calls = tuple((call.function_offset, *call.arguments[:2]) for call in emulator.calls)
        return calls, emulator.read_bytes(symbols.ram_structs["framebuffer"].address, 8)

    return outcome_for_every_flag_pattern(scenario)


def is_intended_change(event: int, mode: int) -> bool:
    knob_detent = event in (KNOB_UP, KNOB_DOWN)
    short_press_in_auto = event == SHORT_PRESS and mode == MODE_AUTO
    return knob_detent or short_press_in_auto


@pytest.mark.parametrize("direction", [0, 2])
@pytest.mark.parametrize("child_lock", [0, 1])
@pytest.mark.parametrize("mode", [MODE_HEAT, MODE_OFF, MODE_AUTO])
@pytest.mark.parametrize("event", EVENTS)
def test_main_screen__differs_from_stock_only_where_intended(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet,
    event: int, mode: int, child_lock: int, direction: int,
) -> None:
    stock_calls, stock_screen = screen_pass(*slot_image, function_starts, symbols, None, event, mode, child_lock, direction)
    patched_calls, patched_screen = screen_pass(*slot_image, function_starts, symbols, patch_set, event, mode, child_lock, direction)
    icon_bytes = [index for index in range(8) if index not in DIGIT_BYTES]
    assert [patched_screen[index] for index in icon_bytes] == [stock_screen[index] for index in icon_bytes]
    if not is_intended_change(event, mode):
        assert patched_calls == stock_calls
    if mode == MODE_OFF:
        assert patched_screen == stock_screen

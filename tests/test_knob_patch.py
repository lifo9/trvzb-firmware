"""The knob-valve-percent patch under emulation: every row of docs/operating-model.md.

Each scenario runs the built patch set in both slots and under both stub flag
patterns. Screens are compared with what stock's own renderer draws.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from build_patch import build_patch_set, load_site_specifications
from conftest import REPOSITORY_ROOT
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator, outcome_for_every_flag_pattern
from trvzb.image import SLOT_A, SLOT_B
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet
from trvzb.symbols import Symbols, load_symbols

pytestmark = pytest.mark.skipif(shutil.which("arm-none-eabi-as") is None, reason="arm-none-eabi binutils not on PATH")

PATCH_DIRECTORY = REPOSITORY_ROOT / "patches" / "knob-valve-percent"
KNOB_BRANCH = 0x3104
MODE_HEAT, MODE_OFF, MODE_AUTO = 0, 1, 2
KNOB_UP, KNOB_DOWN = 5, 6
RUNNING = 2
LOCAL = 1
VALVE_OPENING_DATAPOINT, LOCAL_REPORT, SAVE_DELAY = 119, 6, 2000
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


@pytest.fixture(params=["A", "B"])
def slot_image(request: pytest.FixtureRequest, stock_slot_a_path: Path, stock_slot_b_path: Path) -> tuple[str, bytes]:
    path = stock_slot_a_path if request.param == "A" else stock_slot_b_path
    return request.param, path.read_bytes()


def site_offset(name: str) -> int:
    _, _, sites = load_site_specifications(PATCH_DIRECTORY / "sites.toml")
    return next(site.offset for site in sites if site.name == name)


def make_emulator(
    slot_name: str, image: bytes, function_starts: set[int], symbols: Symbols, patch_set: PatchSet | None,
    clobbered_flags: int, allowed_names: tuple[str, ...], extra_allowed: set[int] = frozenset(),
    stub_effects: dict | None = None,
) -> FirmwareEmulator:
    emulator = FirmwareEmulator(
        slot=SLOT_A if slot_name == "A" else SLOT_B,
        slot_image=image,
        function_starts=function_starts,
        allowed_functions={symbols.offset(name) for name in allowed_names} | set(extra_allowed),
        clobbered_flags=clobbered_flags,
        stub_effects=stub_effects or {},
    )
    if patch_set is not None:
        emulator.apply_patch_set(patch_set)
    return emulator


# --- Knob ------------------------------------------------------------------------


def knob_outcome(
    slot_name: str, image: bytes, function_starts: set[int], symbols: Symbols, patch_set: PatchSet,
    mode: int, opening: int, event: int, auto_accepted: bool = True,
) -> tuple[int, int, tuple[tuple[str, int, int], ...]]:
    """(opening after, mode after, calls as (name, first argument, second argument))."""
    settings_address, mode_field = symbols.field_address("settings.mode")
    _, opening_field = symbols.field_address("settings.valve_opening_degree")
    ui_edit_address, _ = symbols.field_address("ui_edit.pending_value")
    runtime_address, operating_field = symbols.field_address("runtime.operating_state")

    def mode_set_effect(emulator: FirmwareEmulator, arguments: tuple[int, int, int, int]) -> None:
        if arguments[0] != MODE_AUTO or auto_accepted:
            emulator.write_byte(settings_address + mode_field, arguments[0])

    def scenario(clobbered_flags: int) -> tuple[int, int, tuple[tuple[str, int, int], ...]]:
        emulator = make_emulator(
            slot_name, image, function_starts, symbols, patch_set, clobbered_flags,
            ("clamp", "switch8", "operating_state_get"),
            stub_effects={symbols.offset("mode_set"): mode_set_effect},
        )
        emulator.write_byte(settings_address + mode_field, mode)
        emulator.write_byte(settings_address + opening_field, opening)
        emulator.write_byte(runtime_address + operating_field, RUNNING)
        emulator.run_fragment_ending_in_pop(
            KNOB_BRANCH, registers={"r4": event, "r5": ui_edit_address, "r6": settings_address}, saved_register_count=5
        )
        calls = tuple(
            (symbols.name_at(call.function_offset) or hex(call.function_offset), call.arguments[0], call.arguments[1])
            for call in emulator.calls
        )
        return emulator.read_byte(settings_address + opening_field), emulator.read_byte(settings_address + mode_field), calls

    return outcome_for_every_flag_pattern(scenario)


APPLY_OPENING = (
    ("datapoint_report_builder", VALVE_OPENING_DATAPOINT, LOCAL_REPORT),
    ("settings_save_deferred", SAVE_DELAY),
    ("thermostat_apply_or_retry",),
)


def without_second_argument_for(calls: tuple[tuple[str, int, int], ...]) -> tuple[tuple, ...]:
    """Calls with the arguments that matter: the save takes one, the apply none."""
    single_argument = {"settings_save_deferred"}
    no_argument = {"thermostat_apply_or_retry"}
    return tuple(
        (name,) if name in no_argument else (name, first) if name in single_argument else (name, first, second)
        for name, first, second in calls
    )


@pytest.mark.parametrize(
    "mode, opening, event, expected_opening",
    [
        (MODE_HEAT, 50, KNOB_UP, 60),
        (MODE_HEAT, 50, KNOB_DOWN, 40),
        (MODE_HEAT, 95, KNOB_UP, 100),
        (MODE_HEAT, 100, KNOB_UP, 100),
        (MODE_HEAT, 7, KNOB_DOWN, 0),
        (MODE_HEAT, 37, KNOB_UP, 47),
        (MODE_HEAT, 255, KNOB_DOWN, 90),
        (MODE_HEAT, 255, KNOB_UP, 100),
    ],
)
def test_knob_in_manual__steps_the_opening_and_applies_it(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet,
    mode: int, opening: int, event: int, expected_opening: int,
) -> None:
    opening_after, mode_after, calls = knob_outcome(*slot_image, function_starts, symbols, patch_set, mode, opening, event)
    assert opening_after == expected_opening
    assert mode_after == MODE_HEAT
    assert without_second_argument_for(calls) == APPLY_OPENING


def test_knob_in_auto__switches_to_manual_and_steps(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    opening_after, mode_after, calls = knob_outcome(*slot_image, function_starts, symbols, patch_set, MODE_AUTO, 50, KNOB_UP)
    assert (opening_after, mode_after) == (60, MODE_HEAT)
    assert without_second_argument_for(calls) == (("mode_set", MODE_HEAT, LOCAL),) + APPLY_OPENING


def test_knob_down_at_zero__switches_heating_off(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    opening_after, mode_after, calls = knob_outcome(*slot_image, function_starts, symbols, patch_set, MODE_HEAT, 0, KNOB_DOWN)
    assert (opening_after, mode_after) == (0, MODE_OFF)
    assert calls == (("mode_set", MODE_OFF, LOCAL),)


def test_knob_up_from_off__switches_on_in_auto(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    _, mode_after, calls = knob_outcome(*slot_image, function_starts, symbols, patch_set, MODE_OFF, 30, KNOB_UP)
    assert mode_after == MODE_AUTO
    assert calls == (("mode_set", MODE_AUTO, LOCAL),)


def test_knob_up_from_off__falls_back_to_manual_when_auto_is_refused(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    _, mode_after, calls = knob_outcome(*slot_image, function_starts, symbols, patch_set, MODE_OFF, 30, KNOB_UP, auto_accepted=False)
    assert mode_after == MODE_HEAT
    assert calls == (("mode_set", MODE_AUTO, LOCAL), ("mode_set", MODE_HEAT, LOCAL))


def test_knob_down_while_off__does_nothing(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    opening_after, mode_after, calls = knob_outcome(*slot_image, function_starts, symbols, patch_set, MODE_OFF, 30, KNOB_DOWN)
    assert (opening_after, mode_after, calls) == (30, MODE_OFF, ())


# --- Display ------------------------------------------------------------------------

DISPLAY_FUNCTIONS = (
    "draw_setpoint", "draw_two_characters", "draw_character", "draw_value_tenths",
    "operating_state_get", "unsigned_divide", "signed_divide",
)


def framebuffer_after(
    slot_name: str, image: bytes, function_starts: set[int], symbols: Symbols, patch_set: PatchSet | None,
    direction: int, mode: int, opening: int, draw: Callable[[FirmwareEmulator], None],
) -> bytes:
    def scenario(clobbered_flags: int) -> bytes:
        emulator = make_emulator(
            slot_name, image, function_starts, symbols, patch_set, clobbered_flags, DISPLAY_FUNCTIONS,
            extra_allowed={DRAW_TWO_CHARACTERS_INNER},
        )
        settings_address, mode_field = symbols.field_address("settings.mode")
        for field_name, value in (("mode", mode), ("valve_opening_degree", opening), ("screen_direction", direction)):
            _, field_offset = symbols.field_address(f"settings.{field_name}")
            emulator.write_byte(settings_address + field_offset, value)
        runtime_address, operating_field = symbols.field_address("runtime.operating_state")
        emulator.write_byte(runtime_address + operating_field, RUNNING)
        draw(emulator)
        return emulator.read_bytes(symbols.ram_structs["framebuffer"].address, 8)

    return outcome_for_every_flag_pattern(scenario)


def patched_screen(slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet,
                   direction: int, mode: int, opening: int) -> bytes:
    pending_setpoint = 70
    return framebuffer_after(
        *slot_image, function_starts, symbols, patch_set, direction, mode, opening,
        lambda emulator: emulator.call_function(site_offset("cave_draw_valve_opening"), (pending_setpoint,)),
    )


def stock_text(slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, direction: int, text: str) -> bytes:
    """What stock draws for these two characters, after an even setpoint clears the dot."""
    def draw(emulator: FirmwareEmulator) -> None:
        emulator.call_function(symbols.offset("draw_setpoint"), (70,))
        emulator.call_function(symbols.offset("draw_two_characters"), (ord(text[0]), ord(text[1])))

    return framebuffer_after(*slot_image, function_starts, symbols, None, direction, MODE_HEAT, 0, draw)


@pytest.mark.parametrize("direction", [0, 2])
@pytest.mark.parametrize(
    "mode, opening, text",
    [(MODE_HEAT, 70, "70"), (MODE_AUTO, 70, "70"), (MODE_HEAT, 5, " 5"), (MODE_HEAT, 0, " 0"),
     (MODE_HEAT, 100, "FU"), (MODE_HEAT, 255, "FU")],
)
def test_woken_screen__shows_the_valve_opening(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet,
    direction: int, mode: int, opening: int, text: str,
) -> None:
    assert patched_screen(slot_image, function_starts, symbols, patch_set, direction, mode, opening) == stock_text(
        slot_image, function_starts, symbols, direction, text
    )


@pytest.mark.parametrize("direction", [0, 2])
def test_woken_screen__keeps_stock_off_display(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet, direction: int
) -> None:
    stock_off = framebuffer_after(
        *slot_image, function_starts, symbols, None, direction, MODE_OFF, 40,
        lambda emulator: emulator.call_function(symbols.offset("draw_setpoint"), (70,)),
    )
    assert patched_screen(slot_image, function_starts, symbols, patch_set, direction, MODE_OFF, 40) == stock_off


@pytest.mark.parametrize("direction, dot_byte, dot_bit", [(0, 6, 5), (2, 4, 0)])
def test_woken_screen__clears_the_half_degree_dot(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet,
    direction: int, dot_byte: int, dot_bit: int,
) -> None:
    def draw_after_a_dotted_setpoint(emulator: FirmwareEmulator) -> None:
        emulator.call_function(symbols.offset("draw_setpoint"), (71,))
        emulator.call_function(site_offset("cave_draw_valve_opening"), (71,))

    framebuffer = framebuffer_after(
        *slot_image, function_starts, symbols, patch_set, direction, MODE_HEAT, 70, draw_after_a_dotted_setpoint
    )
    assert not framebuffer[dot_byte] >> dot_bit & 1


# --- Short press --------------------------------------------------------------------


def short_press_calls(slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols,
                      patch_set: PatchSet | None, mode: int) -> tuple[tuple[str, int, int], ...]:
    settings_address, mode_field = symbols.field_address("settings.mode")
    ui_edit_address, _ = symbols.field_address("ui_edit.pending_value")
    runtime_address, operating_field = symbols.field_address("runtime.operating_state")

    def scenario(clobbered_flags: int) -> tuple[tuple[str, int, int], ...]:
        emulator = make_emulator(*slot_image, function_starts, symbols, patch_set, clobbered_flags, ("operating_state_get",))
        emulator.write_byte(settings_address + mode_field, mode)
        emulator.write_byte(runtime_address + operating_field, RUNNING)
        emulator.run_fragment_ending_in_pop(
            symbols.offset("ui_main_screen.short_press"),
            registers={"r5": ui_edit_address, "r6": settings_address}, saved_register_count=5,
        )
        return tuple((symbols.name_at(call.function_offset) or hex(call.function_offset), *call.arguments[:2]) for call in emulator.calls)

    return outcome_for_every_flag_pattern(scenario)


def test_short_press_in_auto__changes_nothing(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    assert short_press_calls(slot_image, function_starts, symbols, patch_set, MODE_AUTO) == ()
    assert short_press_calls(slot_image, function_starts, symbols, None, MODE_AUTO) == (("mode_set", MODE_HEAT, LOCAL),)


def test_short_press_in_manual__returns_to_auto_as_stock_does(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    patched = short_press_calls(slot_image, function_starts, symbols, patch_set, MODE_HEAT)
    assert patched == (("mode_set", MODE_AUTO, LOCAL),)
    assert patched == short_press_calls(slot_image, function_starts, symbols, None, MODE_HEAT)


# --- Applying a detent while the valve moves ----------------------------------------

VALVE_TASK_STATE = 0x20000334
VALVE_MOVING = 0x04
THERMOSTAT_RETRY_TIMER, THERMOSTAT_RETRY_MILLISECONDS = 0x20000788, 3000


def detent_with_valve(
    slot_name: str, image: bytes, function_starts: set[int], symbols: Symbols, patch_set: PatchSet, valve_state: int
) -> tuple[int, tuple[tuple[str, int, int], ...]]:
    """(thermostat-loop mark after one detent up, calls), with the apply chain running for real."""
    settings_address, mode_field = symbols.field_address("settings.mode")
    _, opening_field = symbols.field_address("settings.valve_opening_degree")
    ui_edit_address, _ = symbols.field_address("ui_edit.pending_value")
    runtime_address, operating_field = symbols.field_address("runtime.operating_state")
    _, loop_mark_field = symbols.field_address("runtime.thermostat_loop_enabled")

    def scenario(clobbered_flags: int) -> tuple[int, tuple[tuple[str, int, int], ...]]:
        emulator = make_emulator(
            slot_name, image, function_starts, symbols, patch_set, clobbered_flags,
            ("clamp", "switch8", "operating_state_get", "thermostat_kick", "thermostat_apply_or_retry", "valve_task_state_get"),
        )
        emulator.write_byte(settings_address + mode_field, MODE_HEAT)
        emulator.write_byte(settings_address + opening_field, 20)
        emulator.write_byte(runtime_address + operating_field, RUNNING)
        emulator.write_byte(VALVE_TASK_STATE, valve_state)
        emulator.run_fragment_ending_in_pop(
            KNOB_BRANCH, registers={"r4": KNOB_UP, "r5": ui_edit_address, "r6": settings_address}, saved_register_count=5
        )
        calls = tuple(
            (symbols.name_at(call.function_offset) or hex(call.function_offset), call.arguments[0], call.arguments[1])
            for call in emulator.calls
        )
        return emulator.read_byte(runtime_address + loop_mark_field), calls

    return outcome_for_every_flag_pattern(scenario)


def test_detent_while_the_valve_moves__retries_instead_of_marking_the_loop(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    """Marking the loop while the valve moves made it record a target the valve dropped,
    leaving the valve behind the shown opening after a quick spin."""
    loop_mark, calls = detent_with_valve(*slot_image, function_starts, symbols, patch_set, VALVE_MOVING)
    assert loop_mark == 0
    timer_starts = [call for call in calls if call[0] == "0x61dc"]
    assert timer_starts == [("0x61dc", THERMOSTAT_RETRY_TIMER, THERMOSTAT_RETRY_MILLISECONDS)]


def test_detent_with_the_valve_idle__applies_at_once(
    slot_image: tuple[str, bytes], function_starts: set[int], symbols: Symbols, patch_set: PatchSet
) -> None:
    loop_mark, calls = detent_with_valve(*slot_image, function_starts, symbols, patch_set, 0)
    assert loop_mark == 1
    assert not [call for call in calls if call[0] == "0x61dc"]

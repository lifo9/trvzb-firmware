"""Emulator tests run the built patch set, in both slots."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from build_patch import build_patch_set
from conftest import REPOSITORY_ROOT
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator, RecordedCall, outcome_for_every_flag_pattern
from trvzb.image import SLOT_A, SLOT_B
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet
from trvzb.symbols import Symbols, load_symbols

pytestmark = pytest.mark.skipif(shutil.which("arm-none-eabi-as") is None, reason="arm-none-eabi binutils not on PATH")

COMMIT_NOT_LOCAL = REPOSITORY_ROOT / "tests" / "fixtures" / "patches" / "commit-not-local"
KNOB_BRANCH, KNOB_UP_EVENT, MODE_HEAT, RUNNING = 0x3104, 5, 0, 2


@pytest.fixture(scope="module")
def symbols() -> Symbols:
    return load_symbols()


@pytest.fixture(scope="module")
def patch_set(stock_ota_path: Path, symbols: Symbols) -> PatchSet:
    return build_patch_set(COMMIT_NOT_LOCAL, OtaFile(str(stock_ota_path)), symbols)


def knob_up_calls(slot_name: str, slot_image: bytes, function_starts: set[int], symbols: Symbols, patch_set: PatchSet | None, clobbered_flags: int) -> tuple[RecordedCall, ...]:
    emulator = FirmwareEmulator(
        slot=SLOT_A if slot_name == "A" else SLOT_B,
        slot_image=slot_image,
        function_starts=function_starts,
        allowed_functions={symbols.offset("clamp"), symbols.offset("switch8"), symbols.offset("operating_state_get")},
        clobbered_flags=clobbered_flags,
    )
    if patch_set is not None:
        emulator.apply_patch_set(patch_set)
    settings_address, mode_field = symbols.field_address("settings.mode")
    _, minimum_field = symbols.field_address("settings.minimum_setpoint")
    _, maximum_field = symbols.field_address("settings.maximum_setpoint")
    ui_edit_address, pending_field = symbols.field_address("ui_edit.pending_value")
    runtime_address, operating_state_field = symbols.field_address("runtime.operating_state")
    for address, value in ((settings_address + mode_field, MODE_HEAT), (settings_address + minimum_field, 10),
                           (settings_address + maximum_field, 70), (ui_edit_address + pending_field, 40),
                           (runtime_address + operating_state_field, RUNNING)):
        emulator.write_byte(address, value)
    emulator.run_fragment_ending_in_pop(KNOB_BRANCH, registers={"r4": KNOB_UP_EVENT, "r5": ui_edit_address, "r6": settings_address}, saved_register_count=5)
    return tuple(emulator.calls)


@pytest.mark.parametrize("slot_name", ["A", "B"])
def test_apply_patch_set__runs_the_built_patch(
    stock_slot_a_path: Path, stock_slot_b_path: Path, symbols: Symbols, patch_set: PatchSet, slot_name: str
) -> None:
    slot_image = (stock_slot_a_path if slot_name == "A" else stock_slot_b_path).read_bytes()
    function_starts = set(FirmwareMap(str(stock_slot_a_path)).functions)
    setpoint_set = symbols.offset("setpoint_set")
    for patch, expected_is_local in ((None, 1), (patch_set, 0)):
        calls = outcome_for_every_flag_pattern(
            lambda flags: knob_up_calls(slot_name, slot_image, function_starts, symbols, patch, flags)
        )
        assert [call.arguments[:2] for call in calls if call.function_offset == setpoint_set] == [(41, expected_is_local)]

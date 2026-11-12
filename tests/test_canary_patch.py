"""The canary-letter-l patch: one font byte, visible on the child-lock screen.

Built from patches/canary-letter-l exactly as a release would be, then drawn
by stock's own renderer under emulation in both slots and both screen
directions.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from build_patch import build_patch_set
from conftest import REPOSITORY_ROOT
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator
from trvzb.image import SLOT_A, SLOT_B, Slot
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet
from trvzb.symbols import Symbols, load_symbols

pytestmark = pytest.mark.skipif(shutil.which("arm-none-eabi-as") is None, reason="arm-none-eabi binutils not on PATH")

PATCH_DIRECTORY = REPOSITORY_ROOT / "patches" / "canary-letter-l"
DRAW_TWO_CHARACTERS_INNER = 0x324C
ALL_SEGMENTS = 0x7F
STOCK_LETTER_L = 0x38
# Font glyph bits: a=0 b=1 c=2 d=3 e=4 f=5 g=6. Turning a 7-segment digit
# upside down swaps a with d, b with e and c with f; g stays.
ROTATION_180 = {0: 3, 1: 4, 2: 5, 3: 0, 4: 1, 5: 2, 6: 6}


def rotate_180(glyph: int) -> int:
    return sum(1 << ROTATION_180[bit] for bit in range(7) if glyph >> bit & 1)


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
def slot_under_test(request: pytest.FixtureRequest, stock_slot_a_path: Path, stock_slot_b_path: Path) -> tuple[Slot, bytes]:
    return (SLOT_A, stock_slot_a_path.read_bytes()) if request.param == "A" else (SLOT_B, stock_slot_b_path.read_bytes())


def letter_glyph(image: bytes, symbols: Symbols, letter: str) -> int:
    return image[symbols.tables["letter_font"].offset + ord(letter) - ord("A")]


def draw(
    slot: Slot, slot_image: bytes, symbols: Symbols, function_starts: set[int], patch_set: PatchSet | None, text: str, direction: int
) -> bytes:
    emulator = FirmwareEmulator(
        slot=slot,
        slot_image=slot_image,
        function_starts=function_starts,
        allowed_functions={symbols.offset("draw_two_characters"), DRAW_TWO_CHARACTERS_INNER, symbols.offset("draw_character")},
    )
    if patch_set is not None:
        emulator.apply_patch_set(patch_set)
    settings_address, direction_field = symbols.field_address("settings.screen_direction")
    emulator.write_byte(settings_address + direction_field, direction)
    emulator.call_function(symbols.offset("draw_two_characters"), (ord(text[0]), ord(text[1])))
    assert emulator.calls == []
    return emulator.read_bytes(symbols.ram_structs["framebuffer"].address, 8)


def test_build_patch_set__replaces_only_the_letter_l_glyph_in_both_slots(
    patch_set: PatchSet, symbols: Symbols, stock_slot_a_path: Path
) -> None:
    letter_l_offset = symbols.tables["letter_font"].offset + ord("L") - ord("A")
    assert stock_slot_a_path.read_bytes()[letter_l_offset] == STOCK_LETTER_L
    assert [(site.offset, site.stock_bytes, site.patched_bytes) for site in patch_set.sites] == [
        (
            letter_l_offset,
            {"A": bytes([STOCK_LETTER_L]), "B": bytes([STOCK_LETTER_L])},
            {"A": bytes([ALL_SEGMENTS]), "B": bytes([ALL_SEGMENTS])},
        )
    ]


def test_canary__child_lock_screen_reads_8c(
    slot_under_test: tuple[Slot, bytes], symbols: Symbols, function_starts: set[int], patch_set: PatchSet
) -> None:
    slot, slot_image = slot_under_test
    framebuffer = draw(slot, slot_image, symbols, function_starts, patch_set, "LC", direction=0)
    assert framebuffer[0] == ALL_SEGMENTS
    assert framebuffer[2] == letter_glyph(slot_image, symbols, "C")


def test_canary__child_lock_screen_reads_8c_upside_down_in_direction_2(
    slot_under_test: tuple[Slot, bytes], symbols: Symbols, function_starts: set[int], patch_set: PatchSet
) -> None:
    slot, slot_image = slot_under_test
    framebuffer = draw(slot, slot_image, symbols, function_starts, patch_set, "LC", direction=2)
    assert framebuffer[0] == rotate_180(letter_glyph(slot_image, symbols, "C"))
    assert framebuffer[2] == ALL_SEGMENTS


@pytest.mark.parametrize("text", ["OF", "OP", "FU", "70"])
def test_canary__leaves_every_other_screen_as_stock_draws_it(
    slot_under_test: tuple[Slot, bytes], symbols: Symbols, function_starts: set[int], patch_set: PatchSet, text: str
) -> None:
    slot, slot_image = slot_under_test
    for direction in (0, 2):
        patched = draw(slot, slot_image, symbols, function_starts, patch_set, text, direction)
        stock = draw(slot, slot_image, symbols, function_starts, None, text, direction)
        assert patched == stock

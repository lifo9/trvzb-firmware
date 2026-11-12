"""The display renderer under emulation, in both slots and both screen directions.

Patched screens are checked against what stock's own renderer draws, so the
patch never has to predict segment bits by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from firmware_map import FirmwareMap
from trvzb.emulator import FirmwareEmulator
from trvzb.image import SLOT_A, SLOT_B, Slot
from trvzb.symbols import Symbols, load_symbols

DRAW_TWO_CHARACTERS_INNER = 0x324C
# Font glyph bits: a=0 b=1 c=2 d=3 e=4 f=5 g=6. Turning a 7-segment digit
# upside down swaps a with d, b with e and c with f; g stays.
ROTATION_180 = {0: 3, 1: 4, 2: 5, 3: 0, 4: 1, 5: 2, 6: 6}


def rotate_180(glyph: int) -> int:
    return sum(1 << ROTATION_180[bit] for bit in range(7) if glyph >> bit & 1)


@pytest.fixture(scope="module")
def symbols() -> Symbols:
    return load_symbols()


@pytest.fixture(scope="module")
def function_starts(stock_slot_a_path: Path) -> set[int]:
    return set(FirmwareMap(str(stock_slot_a_path)).functions)


def glyph(image: bytes, symbols: Symbols, character: str) -> int:
    if character.isdigit():
        return image[symbols.tables["digit_font"].offset + int(character)]
    return image[symbols.tables["letter_font"].offset + ord(character) - ord("A")]


def draw(slot: Slot, slot_image: bytes, symbols: Symbols, function_starts: set[int], text: str, direction: int) -> bytes:
    emulator = FirmwareEmulator(
        slot=slot,
        slot_image=slot_image,
        function_starts=function_starts,
        allowed_functions={symbols.offset("draw_two_characters"), DRAW_TWO_CHARACTERS_INNER, symbols.offset("draw_character")},
    )
    settings_address, direction_field = symbols.field_address("settings.screen_direction")
    emulator.write_byte(settings_address + direction_field, direction)
    emulator.call_function(symbols.offset("draw_two_characters"), (ord(text[0]), ord(text[1])))
    assert emulator.calls == []
    return emulator.read_bytes(symbols.ram_structs["framebuffer"].address, 8)


@pytest.fixture(params=["A", "B"])
def slot_under_test(request: pytest.FixtureRequest, stock_slot_a_path: Path, stock_slot_b_path: Path) -> tuple[Slot, bytes]:
    return (SLOT_A, stock_slot_a_path.read_bytes()) if request.param == "A" else (SLOT_B, stock_slot_b_path.read_bytes())


@pytest.mark.parametrize("text", ["LC", "FU", "70", "09"])
def test_draw_two_characters__writes_font_glyphs_left_in_byte_0(
    slot_under_test: tuple[Slot, bytes], symbols: Symbols, function_starts: set[int], text: str
) -> None:
    slot, slot_image = slot_under_test
    framebuffer = draw(slot, slot_image, symbols, function_starts, text, direction=0)
    assert framebuffer[0] == glyph(slot_image, symbols, text[0])
    assert framebuffer[2] == glyph(slot_image, symbols, text[1])


@pytest.mark.parametrize("text", ["LC", "FU", "70", "09"])
def test_draw_two_characters__turns_the_screen_upside_down_in_direction_2(
    slot_under_test: tuple[Slot, bytes], symbols: Symbols, function_starts: set[int], text: str
) -> None:
    slot, slot_image = slot_under_test
    framebuffer = draw(slot, slot_image, symbols, function_starts, text, direction=2)
    assert framebuffer[0] == rotate_180(glyph(slot_image, symbols, text[1]))
    assert framebuffer[2] == rotate_180(glyph(slot_image, symbols, text[0]))

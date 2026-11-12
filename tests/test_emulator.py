"""The emulator must be no kinder than the hardware."""

from __future__ import annotations

import struct

from trvzb.emulator import FirmwareEmulator
from trvzb.image import SLOT_A, decode_branch_with_link_target, encode_branch_with_link

SCRATCH_CODE = 0xF400
STUB_TARGET = 0xF480
RESULT_ADDRESS = 0x20000800
EMPTY_IMAGE = bytes(0x7B64)


def thumb(*halfwords: int) -> bytes:
    return b"".join(struct.pack("<H", halfword) for halfword in halfwords)


def test_encode_branch_with_link__round_trips_through_the_decoder() -> None:
    for address, target in ((0x31A2, 0x6C18), (0x6C18, 0x31A2), (0x100, 0x7F00)):
        first, second = struct.unpack("<HH", encode_branch_with_link(address, target))
        assert decode_branch_with_link_target(address, first, second) == target


def test_stub__destroys_registers_and_flags_a_real_call_may_destroy() -> None:
    movs_r2_7, cmp_r2_7, bne_over_store, strb_r2_to_r5, pop_r3_r7_pc = 0x2207, 0x2A07, 0xD100, 0x702A, 0xBDF8
    code = (
        thumb(movs_r2_7, cmp_r2_7)
        + encode_branch_with_link(SCRATCH_CODE + 4, STUB_TARGET)
        + thumb(bne_over_store, strb_r2_to_r5, pop_r3_r7_pc)
    )
    emulator = FirmwareEmulator(slot=SLOT_A, slot_image=EMPTY_IMAGE, function_starts={STUB_TARGET})
    emulator.write_code(SCRATCH_CODE, code)
    emulator.run_fragment_ending_in_pop(SCRATCH_CODE, registers={"r5": RESULT_ADDRESS}, saved_register_count=5)
    assert emulator.read_byte(RESULT_ADDRESS) != 7


def test_patched_function_start__runs_instead_of_being_stubbed() -> None:
    movs_r0_42, bx_lr, strb_r0_to_r5, pop_r3_r7_pc = 0x202A, 0x4770, 0x7028, 0xBDF8
    caller = encode_branch_with_link(SCRATCH_CODE, STUB_TARGET) + thumb(strb_r0_to_r5, pop_r3_r7_pc)
    for patched_ranges, expected in (([], 0), ([(STUB_TARGET, 4)], 42)):
        emulator = FirmwareEmulator(
            slot=SLOT_A, slot_image=EMPTY_IMAGE, function_starts={STUB_TARGET}, patched_ranges=patched_ranges
        )
        emulator.write_code(SCRATCH_CODE, caller)
        emulator.write_code(STUB_TARGET, thumb(movs_r0_42, bx_lr))
        emulator.run_fragment_ending_in_pop(SCRATCH_CODE, registers={"r5": RESULT_ADDRESS}, saved_register_count=5)
        assert emulator.read_byte(RESULT_ADDRESS) == expected

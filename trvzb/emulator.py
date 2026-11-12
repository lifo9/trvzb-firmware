"""Run fragments of the application-MCU image under unicorn.

Maps a slot at its link address, 4 KB of RAM and zero-filled peripheral space.
Every call to a known function start is intercepted and recorded with its
arguments, and returns a configurable value, except for functions explicitly
allowed to run (pure helpers such as the clamp) and function starts inside
patched ranges, whose new code runs. A stub is no kinder than a real callee:
it destroys the registers and flags the calling convention lets a callee
destroy, so code that wrongly relies on them fails here as it would on the
valve. A fragment that ends with a `pop {..., pc}` returns into a trap address,
which stops the run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_APSR_NZCV,
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R12,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R6,
    UC_ARM_REG_R7,
    UC_ARM_REG_SP,
)

from trvzb.image import Slot

FLASH_SIZE = 0x10000
RAM_BASE = 0x20000000
RAM_SIZE = 0x1000
STACK_TOP = 0x20000FD8
PERIPHERAL_REGIONS = ((0x40000000, 0x100000), (0xE0000000, 0x100000))
RETURN_TRAP = 0xFF00
INSTRUCTION_LIMIT = 20000
ARGUMENT_REGISTERS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
# What a stub leaves in the caller-clobbered registers: distinctive values.
CLOBBERED_REGISTER_VALUES = {UC_ARM_REG_R1: 0xBAD00001, UC_ARM_REG_R2: 0xBAD00002, UC_ARM_REG_R3: 0xBAD00003, UC_ARM_REG_R12: 0xBAD0000C}
# Condition flags a stub leaves: N=1 Z=0 C=1 V=1 and its complement. One fixed
# pattern only catches half of the mistakes, so scenarios run under both.
COMPLEMENTARY_FLAG_PATTERNS = (0xB0000000, 0x40000000)
CALLEE_SAVED_REGISTERS = {
    "r3": UC_ARM_REG_R3, "r4": UC_ARM_REG_R4, "r5": UC_ARM_REG_R5, "r6": UC_ARM_REG_R6, "r7": UC_ARM_REG_R7,
}


@dataclass(frozen=True)
class RecordedCall:
    function_offset: int
    arguments: tuple[int, int, int, int]


@dataclass
class FirmwareEmulator:
    slot: Slot
    slot_image: bytes
    function_starts: set[int]
    allowed_functions: set[int] = field(default_factory=set)
    patched_ranges: list[tuple[int, int]] = field(default_factory=list)
    clobbered_flags: int = COMPLEMENTARY_FLAG_PATTERNS[0]
    return_values: dict[int, int | Callable[[tuple[int, int, int, int]], int]] = field(default_factory=dict)
    calls: list[RecordedCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.engine = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.engine.mem_map(0, FLASH_SIZE)
        self.engine.mem_write(self.slot.link_base, self.slot_image)
        self.engine.mem_map(RAM_BASE, RAM_SIZE)
        for start, size in PERIPHERAL_REGIONS:
            self.engine.mem_map(start, size)
        self.stubbed_starts = {
            start
            for start in self.function_starts - self.allowed_functions
            if not any(patch_start <= start < patch_start + length for patch_start, length in self.patched_ranges)
        }
        self.engine.hook_add(UC_HOOK_CODE, self._intercept_calls, begin=0, end=FLASH_SIZE - 1)

    def _intercept_calls(self, engine: Uc, address: int, size: int, user_data: object) -> None:
        if address == RETURN_TRAP:
            engine.emu_stop()
            return
        image_offset = address - self.slot.link_base
        if image_offset not in self.stubbed_starts:
            return
        arguments = tuple(engine.reg_read(register) for register in ARGUMENT_REGISTERS)
        self.calls.append(RecordedCall(image_offset, arguments))
        configured = self.return_values.get(image_offset, 0)
        return_value = configured(arguments) if callable(configured) else configured
        engine.reg_write(UC_ARM_REG_R0, return_value & 0xFFFFFFFF)
        for register, clobbered_value in CLOBBERED_REGISTER_VALUES.items():
            engine.reg_write(register, clobbered_value)
        engine.reg_write(UC_ARM_REG_APSR_NZCV, self.clobbered_flags)
        engine.reg_write(UC_ARM_REG_PC, engine.reg_read(UC_ARM_REG_LR))

    def write_code(self, image_offset: int, code: bytes) -> None:
        """Place code at an image offset in this emulator's slot, e.g. a patch site."""
        self.engine.mem_write(self.slot.mcu_address(image_offset), code)

    def write_byte(self, address: int, value: int) -> None:
        self.engine.mem_write(address, bytes([value & 0xFF]))

    def read_byte(self, address: int) -> int:
        return self.engine.mem_read(address, 1)[0]

    def run_fragment_ending_in_pop(self, start_offset: int, registers: dict[str, int], saved_register_count: int) -> None:
        """Run from mid-function code whose epilogue pops `saved_register_count` registers and pc.

        Builds the frame that the function's prologue would have pushed, with
        the return address pointing at the trap.
        """
        frame = [0] * saved_register_count + [RETURN_TRAP | 1]
        stack_pointer = STACK_TOP - 4 * len(frame)
        self.engine.mem_write(stack_pointer, b"".join(word.to_bytes(4, "little") for word in frame))
        self.engine.reg_write(UC_ARM_REG_SP, stack_pointer)
        for name, value in registers.items():
            self.engine.reg_write(CALLEE_SAVED_REGISTERS[name], value)
        self.engine.emu_start(self.slot.mcu_address(start_offset) | 1, RETURN_TRAP, count=INSTRUCTION_LIMIT)
        if self.engine.reg_read(UC_ARM_REG_PC) != RETURN_TRAP:
            raise RuntimeError(f"fragment did not return to the trap; stopped at {self.engine.reg_read(UC_ARM_REG_PC):#x}")

    def call_function(self, function_offset: int, arguments: tuple[int, ...] = ()) -> int:
        """Run a whole function with up to four arguments; return r0."""
        if function_offset in self.stubbed_starts:
            raise ValueError(
                f"{function_offset:#x} is stubbed, so calling it would not run it; add it to allowed_functions"
            )
        if len(arguments) > len(ARGUMENT_REGISTERS):
            raise ValueError("at most four register arguments")
        for register, value in zip(ARGUMENT_REGISTERS, arguments):
            self.engine.reg_write(register, value & 0xFFFFFFFF)
        self.engine.reg_write(UC_ARM_REG_SP, STACK_TOP)
        self.engine.reg_write(UC_ARM_REG_LR, RETURN_TRAP | 1)
        self.engine.emu_start(self.slot.mcu_address(function_offset) | 1, RETURN_TRAP, count=INSTRUCTION_LIMIT)
        if self.engine.reg_read(UC_ARM_REG_PC) != RETURN_TRAP:
            raise RuntimeError(f"function did not return to the trap; stopped at {self.engine.reg_read(UC_ARM_REG_PC):#x}")
        return self.engine.reg_read(UC_ARM_REG_R0)

    def read_bytes(self, address: int, length: int) -> bytes:
        return bytes(self.engine.mem_read(address, length))

    def calls_to(self, function_offset: int) -> list[tuple[int, int, int, int]]:
        return [call.arguments for call in self.calls if call.function_offset == function_offset]


class FlagDependence(AssertionError):
    """A scenario's outcome depended on the condition flags a callee left."""


def outcome_for_every_flag_pattern(scenario: Callable[[int], object]) -> object:
    """Run `scenario(clobbered_flags)` under each complementary pattern; return the shared outcome.

    Raises FlagDependence if the outcomes differ: the code under test relied on
    flags surviving a call, which real callees do not guarantee.
    """
    outcomes = [scenario(pattern) for pattern in COMPLEMENTARY_FLAG_PATTERNS]
    if any(outcome != outcomes[0] for outcome in outcomes[1:]):
        raise FlagDependence(f"outcome depends on flags left by a callee: {outcomes}")
    return outcomes[0]

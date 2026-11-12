"""Static map of the TRVZB application-MCU image (Cortex-M0+, Thumb, 32 KB slot).

The image is slot A of the unencrypted 0xf000 subelement of Sonoff's TRVZB OTA
file. It is linked at MCU address 0x400 (a factory bootloader owns 0x000-0x3ff).
Every address this tool takes or prints is an image offset, except RAM and
peripheral addresses, which are absolute. Flash pointers found in the image
(vector table, literal pools, pointer tables) are MCU addresses and are
translated to image offsets through the slot model in trvzb/image.py.

Functions are found by recursive descent from four kinds of seeds: the vector
table, every `bl` target, every pointer word that lands on a `push {..., lr}`
prologue, and the case targets of the compiler's byte switch helper. Code
reached only some other way may still be missing; `raw` disassembles any range
regardless.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import capstone

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trvzb.image import CODE_START, SLOT_A, iterate_call_sites  # noqa: E402
from trvzb.symbols import SYMBOLS_PATH, Symbols, load_symbols  # noqa: E402

IMAGE_PATH = os.environ.get("TRVZB_IMAGE", "work/mcu_app_a.bin")
RAM_BASE = 0x20000000
RAM_END = 0x20001000
VECTOR_COUNT = 48

# ARM Compiler's __ARM_common_switch8 begins `push {r4, r5}; mov r4, lr;
# subs r4, r4, #1; ldrb r5, [r4]`. After a `bl` to it come one limit byte and
# then one byte per case; case n continues at (bl address + 4) + 2 * entry[n],
# and any index at or above the limit takes the last entry.
SWITCH8_SIGNATURE = bytes.fromhex("30b4 7446 641e 2578")

REGISTER_WRITING_MNEMONICS = {
    "adcs", "add", "adds", "adr", "ands", "asrs", "bics", "eors", "ldr", "ldrb", "ldrh",
    "ldrsb", "ldrsh", "lsls", "lsrs", "mov", "movs", "muls", "mvns", "negs", "orrs",
    "rev", "rev16", "revsh", "rors", "rsbs", "sbcs", "sub", "subs", "sxtb", "sxth",
    "uxtb", "uxth",
}
CALL_CLOBBERED_REGISTERS = {"r0", "r1", "r2", "r3", "r12", "lr"}


@dataclass
class Instruction:
    address: int
    size: int
    mnemonic: str
    operands: str

    @property
    def text(self) -> str:
        return f"{self.mnemonic} {self.operands}".strip()


@dataclass
class Function:
    address: int
    instructions: dict[int, Instruction] = field(default_factory=dict)
    successors: dict[int, list[int]] = field(default_factory=dict)
    called_functions: set[int] = field(default_factory=set)
    literal_values: dict[int, int] = field(default_factory=dict)
    data_references: dict[int, int] = field(default_factory=dict)

    @property
    def end_address(self) -> int:
        return max(self.instructions) + self.instructions[max(self.instructions)].size


class FirmwareMap:
    def __init__(self, image_path: str = IMAGE_PATH) -> None:
        self.image = open(image_path, "rb").read()
        self.disassembler = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        self.disassembler.detail = True
        self.functions: dict[int, Function] = {}
        self.callers_of: dict[int, set[int]] = defaultdict(set)
        self.literal_readers: dict[int, set[int]] = defaultdict(set)
        self.symbols: Symbols | None = load_symbols() if SYMBOLS_PATH.exists() else None
        self.switch8_helpers = {
            target for _, target in iterate_call_sites(self.image)
            if self.image[target : target + len(SWITCH8_SIGNATURE)] == SWITCH8_SIGNATURE
        }
        self._analyze()

    def read_word(self, address: int) -> int:
        return struct.unpack_from("<I", self.image, address)[0]

    def flash_offset(self, value: int) -> int | None:
        """Image offset a flash pointer points at, or None if it is not one."""
        image_offset = value - SLOT_A.link_base
        return image_offset if 0 <= image_offset < len(self.image) else None

    def reference_key(self, literal_value: int) -> int:
        """Key under which a literal is recorded: an image offset for flash, else absolute."""
        image_offset = self.flash_offset(literal_value)
        return image_offset if image_offset is not None else literal_value

    def vector_table(self) -> list[int]:
        """Image offsets of the exception and interrupt handlers."""
        handler_offsets = []
        for index in range(1, VECTOR_COUNT):
            word = self.read_word(index * 4)
            image_offset = self.flash_offset(word & ~1) if word & 1 else None
            if image_offset is not None:
                handler_offsets.append(image_offset)
        return handler_offsets

    def _linear_sweep_call_targets(self) -> list[int]:
        """Every `bl` target found by sweeping the image as a flat Thumb stream.

        Literal pools and tables decode as nonsense, so this over-reports; the
        recursive pass that consumes these seeds discards the ones that do not
        disassemble into a plausible function.
        """
        return sorted(
            {target for _, target in iterate_call_sites(self.image) if target >= CODE_START and target % 2 == 0}
        )

    def _pointer_seeded_functions(self) -> list[int]:
        """Functions whose address appears as a Thumb pointer word in the image.

        Reaches handlers that are only ever called through tables, such as the
        UI screen handlers. A candidate counts only if it starts with a
        `push {..., lr}` prologue, which keeps data that happens to look like a
        code pointer out.
        """
        seeds = []
        for word_offset in range(0, len(self.image) - 3, 4):
            word = self.read_word(word_offset)
            if not word & 1:
                continue
            image_offset = self.flash_offset(word & ~1)
            if image_offset is None or image_offset < CODE_START:
                continue
            first = next(self.disassembler.disasm(self.image[image_offset : image_offset + 4], image_offset, count=1), None)
            if first is not None and first.mnemonic == "push" and "lr" in first.op_str:
                seeds.append(image_offset)
        return seeds

    def _analyze(self) -> None:
        seeds = self.vector_table() + self._linear_sweep_call_targets() + self._pointer_seeded_functions()
        pending = list(dict.fromkeys(seeds))
        while pending:
            entry = pending.pop()
            if entry in self.functions:
                continue
            function = self._disassemble_function(entry)
            self.functions[entry] = function
            for callee in function.called_functions:
                self.callers_of[callee].add(entry)
                if callee not in self.functions and callee < len(self.image):
                    pending.append(callee)
            for value in function.literal_values.values():
                self.literal_readers[self.reference_key(value)].add(entry)
            for target in function.data_references.values():
                self.literal_readers[target].add(entry)

    def switch8_case_targets(self, call_address: int) -> list[int]:
        """Case targets of the byte switch table that follows a `bl` to the helper."""
        return_address = call_address + 4
        limit = self.image[return_address]
        entries = self.image[return_address + 1 : return_address + 2 + limit]
        return [return_address + 2 * entry for entry in entries]

    def _disassemble_function(self, entry: int) -> Function:
        function = Function(address=entry)
        pending_blocks = [entry]
        while pending_blocks:
            address = pending_blocks.pop()
            while True:
                if address in function.instructions or address >= len(self.image):
                    break
                decoded = next(self.disassembler.disasm(self.image[address : address + 4], address, count=1), None)
                if decoded is None:
                    break
                instruction = Instruction(address, decoded.size, decoded.mnemonic, decoded.op_str)
                function.instructions[address] = instruction
                fallthrough = address + decoded.size
                mnemonic = decoded.mnemonic
                if mnemonic == "adr" or (mnemonic == "add" and re.search(r"\bpc\b", decoded.op_str)):
                    match = re.search(r"#(-?(?:0x[0-9a-f]+|\d+))", decoded.op_str)
                    if match is not None:
                        function.data_references[address] = ((address + 4) & ~3) + int(match.group(1), 0)
                if mnemonic.startswith("ldr") and "[pc," in decoded.op_str:
                    literal_address = self._literal_address(decoded)
                    if literal_address is not None and literal_address + 4 <= len(self.image):
                        function.literal_values[address] = self.read_word(literal_address)
                if mnemonic == "bl":
                    target = self._branch_target(decoded)
                    if target is not None:
                        function.called_functions.add(target)
                    if target in self.switch8_helpers:
                        case_targets = self.switch8_case_targets(address)
                        function.successors[address] = case_targets
                        pending_blocks.extend(case_targets)
                        break
                    function.successors[address] = [fallthrough]
                elif mnemonic in ("b", "b.w"):
                    target = self._branch_target(decoded)
                    if target is not None and entry <= target < entry + 0x800:
                        function.successors[address] = [target]
                        pending_blocks.append(target)
                    else:
                        function.successors[address] = []
                        if target is not None:
                            function.called_functions.add(target)
                    break
                elif mnemonic.startswith("b") and mnemonic not in ("bx", "blx", "bkpt", "bics"):
                    target = self._branch_target(decoded)
                    function.successors[address] = [fallthrough] + ([target] if target is not None else [])
                    if target is not None:
                        pending_blocks.append(target)
                elif mnemonic in ("bx", "pop") and ("pc" in decoded.op_str or mnemonic == "bx"):
                    function.successors[address] = []
                    break
                else:
                    function.successors[address] = [fallthrough]
                address = fallthrough

        return function

    @staticmethod
    def _literal_address(decoded) -> int | None:
        match = re.search(r"\[pc, #(-?(?:0x[0-9a-f]+|\d+))\]", decoded.op_str)
        if match is None:
            return None
        return ((decoded.address + 4) & ~3) + int(match.group(1), 0)

    @staticmethod
    def _branch_target(decoded) -> int | None:
        match = re.fullmatch(r"#(0x[0-9a-f]+|-?\d+)", decoded.op_str.split(", ")[-1])
        if match is None:
            return None
        return int(match.group(1), 0) & ~1

    def strings(self, minimum_length: int = 4) -> dict[int, str]:
        found = {}
        for match in re.finditer(rb"[\x20-\x7e]{%d,}" % minimum_length, self.image):
            found[match.start()] = match.group().decode()
        return found

    def peripheral_addresses(self) -> dict[int, set[int]]:
        peripherals = defaultdict(set)
        for value, readers in self.literal_readers.items():
            if 0x40000000 <= value < 0x60000000 or 0xE0000000 <= value:
                peripherals[value] |= readers
        return peripherals

    def ram_addresses(self) -> dict[int, set[int]]:
        variables = defaultdict(set)
        for value, readers in self.literal_readers.items():
            if RAM_BASE <= value < RAM_END:
                variables[value] |= readers
        return variables

    def function_of(self, address: int) -> int | None:
        for entry, function in self.functions.items():
            if address in function.instructions:
                return entry
        return None

    def _annotate_literal(self, value: int, texts: dict[int, str]) -> str:
        annotation = f"  ; ={value:#x}"
        image_offset = self.flash_offset(value)
        if image_offset is not None:
            annotation += f" (image {image_offset:#x})"
            text = texts.get(image_offset)
            if text:
                annotation += f' "{text}"'
        return annotation

    def _annotate_call(self, mnemonic: str, operands: str) -> str:
        """` <name>` for a branch to a named function, else empty."""
        if self.symbols is None or not mnemonic.startswith("b") or mnemonic in ("bx", "blx", "bics", "bkpt"):
            return ""
        match = re.fullmatch(r"#(0x[0-9a-f]+|-?\d+)", operands.split(", ")[-1])
        name = self.symbols.name_at(int(match.group(1), 0) & ~1) if match else None
        return f" <{name}>" if name else ""

    @staticmethod
    def _annotate_data_reference(target: int, texts: dict[int, str]) -> str:
        annotation = f"  ; ->{target:#x}"
        nearby = [item for item in texts.items() if target <= item[0] <= target + 3]
        if nearby:
            annotation += f' "{nearby[0][1]}"'
        return annotation

    def disassemble(self, entry: int) -> str:
        function = self.functions.get(entry)
        if function is None:
            function = self._disassemble_function(entry)
        texts = self.strings()
        lines = []
        for address in sorted(function.instructions):
            instruction = function.instructions[address]
            annotation = ""
            if address in function.data_references:
                annotation = self._annotate_data_reference(function.data_references[address], texts)
            if address in function.literal_values:
                annotation = self._annotate_literal(function.literal_values[address], texts)
            annotation = self._annotate_call(instruction.mnemonic, instruction.operands) + annotation
            lines.append(f"{address:#07x}  {instruction.text}{annotation}")
        return "\n".join(lines)

    def string_references(self, minimum_length: int = 5) -> list[tuple[int, str, set[int]]]:
        """Every printable string with the functions that reference it.

        A reference may point up to three bytes before the string, since `adr`
        targets are word-aligned.
        """
        references = []
        for address, text in sorted(self.strings(minimum_length).items()):
            readers: set[int] = set()
            for candidate in range(address - 3, address + 1):
                readers |= self.literal_readers.get(candidate, set())
            references.append((address, text, readers))
        return references

    def field_accesses(self, struct_address: int) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
        """Functions reading and writing each field of the struct at `struct_address`.

        Tracks, along every control-flow path, which registers may hold the
        struct's base address, so an access in a branch is attributed even when
        a different path reuses the register.
        """
        readers: dict[int, set[int]] = defaultdict(set)
        writers: dict[int, set[int]] = defaultdict(set)
        for entry, function in sorted(self.functions.items()):
            for _, base_offset, is_write in self._struct_accesses(function, struct_address):
                (writers if is_write else readers)[base_offset].add(entry)
        return readers, writers

    def _struct_accesses(self, function: Function, struct_address: int) -> list[tuple[int, int, bool]]:
        """(address, field offset, is_write) of every access through the struct's base."""
        holding_at: dict[int, frozenset[str]] = {function.address: frozenset()}
        worklist = [function.address]
        accesses: set[tuple[int, int, bool]] = set()
        while worklist:
            address = worklist.pop()
            if address not in function.instructions:
                continue
            holding = set(holding_at[address])
            instruction = function.instructions[address]
            access = re.fullmatch(r"(r\d+|lr), \[(r\d+)(?:, #(0x[0-9a-f]+|\d+))?\]", instruction.operands)
            destination = instruction.operands.split(",")[0].strip()
            if function.literal_values.get(address) == struct_address:
                holding.add(destination)
            elif access is not None and access.group(2) in holding:
                offset = int(access.group(3), 0) if access.group(3) else 0
                is_write = instruction.mnemonic.startswith("str")
                accesses.add((address, offset, is_write))
                if not is_write:
                    holding.discard(destination)
            elif instruction.mnemonic in ("mov", "movs") and re.fullmatch(r"(r\d+), (r\d+)", instruction.operands):
                source = instruction.operands.split(",")[1].strip()
                if source in holding:
                    holding.add(destination)
                else:
                    holding.discard(destination)
            elif instruction.mnemonic in ("bl", "blx"):
                holding -= CALL_CLOBBERED_REGISTERS
            elif instruction.mnemonic == "pop":
                holding -= set(re.findall(r"r\d+", instruction.operands))
            elif instruction.mnemonic in REGISTER_WRITING_MNEMONICS:
                holding.discard(destination)
            frozen = frozenset(holding)
            for successor in function.successors.get(address, []):
                previous = holding_at.get(successor)
                merged = frozen if previous is None else previous | frozen
                if merged != previous:
                    holding_at[successor] = merged
                    worklist.append(successor)
        return sorted(accesses)

    def raw_listing(self, start: int, length: int) -> list[str]:
        """Linear disassembly of a byte range, independent of function discovery.

        Bytes that do not decode as an instruction are listed as data and the
        listing continues after them.
        """
        texts = self.strings()
        raw_disassembler = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        raw_disassembler.skipdata = True
        lines = []
        for decoded in raw_disassembler.disasm(self.image[start : start + length], start):
            annotation = ""
            if decoded.mnemonic.startswith("ldr") and "[pc," in decoded.op_str:
                literal_address = self._literal_address(decoded)
                if literal_address is not None and literal_address + 4 <= len(self.image):
                    annotation = self._annotate_literal(self.read_word(literal_address), texts)
            if decoded.mnemonic == "adr" or (decoded.mnemonic == "add" and "pc" in decoded.op_str):
                match = re.search(r"#(-?(?:0x[0-9a-f]+|\d+))", decoded.op_str)
                if match:
                    target = ((decoded.address + 4) & ~3) + int(match.group(1), 0)
                    annotation = self._annotate_data_reference(target, texts)
            annotation = self._annotate_call(decoded.mnemonic, decoded.op_str) + annotation
            lines.append(f"{decoded.address:#07x}  {decoded.mnemonic} {decoded.op_str}{annotation}")
        return lines

    def matching_lines(self, pattern: re.Pattern[str]) -> list[str]:
        """Disassembly lines of every function that match `pattern`."""
        matches = []
        for entry in sorted(self.functions):
            for line in self.disassemble(entry).splitlines():
                if pattern.search(line):
                    matches.append(f"fn {entry:#07x} | {line}")
        return matches

    def users_of(self, target: int) -> set[int]:
        """Functions that reference `target`: an image offset, or a RAM or peripheral address.

        A code address also matches pointers to it, which carry the Thumb bit.
        """
        wanted_keys = {target, target | 1}
        users = set()
        for entry, function in self.functions.items():
            keys = {self.reference_key(value) for value in function.literal_values.values()}
            keys |= set(function.data_references.values())
            if keys & wanted_keys:
                users.add(entry)
        return users


def main() -> None:
    firmware = FirmwareMap()
    command = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if command == "summary":
        print("functions", len(firmware.functions))
        print("vector handlers", [hex(a) for a in firmware.vector_table()])
        print("switch8 helpers", [hex(a) for a in sorted(firmware.switch8_helpers)])
        print("\nperipherals:")
        for address, readers in sorted(firmware.peripheral_addresses().items()):
            print(f"  {address:#010x}  read by {len(readers)} fn: {[hex(r) for r in sorted(readers)][:6]}")
    elif command == "strings":
        for address, text, readers in firmware.string_references():
            if re.search(r"[a-zA-Z]{4}", text):
                print(f"{address:#07x} {text!r} refs={[hex(r) for r in sorted(readers)]}")
    elif command == "dis":
        print(firmware.disassemble(int(sys.argv[2], 0)))
    elif command == "callers":
        target = int(sys.argv[2], 0)
        print([hex(a) for a in sorted(firmware.callers_of.get(target, set()))])
    elif command == "ram":
        for address, readers in sorted(firmware.ram_addresses().items()):
            print(f"{address:#010x} refs={len(readers)} {[hex(r) for r in sorted(readers)][:8]}")
    elif command == "fields":
        readers, writers = firmware.field_accesses(int(sys.argv[2], 0))
        for offset in sorted(set(readers) | set(writers)):
            print(
                f"+{offset:#04x} read={[hex(a) for a in sorted(readers[offset])]} "
                f"written={[hex(a) for a in sorted(writers[offset])]}"
            )
    elif command == "raw":
        start = int(sys.argv[2], 0)
        length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x80
        print("\n".join(firmware.raw_listing(start, length)))
    elif command == "find":
        for line in firmware.matching_lines(re.compile(sys.argv[2])):
            print(line)
    elif command == "uses":
        print([hex(a) for a in sorted(firmware.users_of(int(sys.argv[2], 0)))])
    elif command == "json":
        print(json.dumps({hex(a): [hex(c) for c in sorted(f.called_functions)] for a, f in firmware.functions.items()}, indent=1))
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main()

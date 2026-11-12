"""Static map of the TRVZB application-MCU image (Cortex-M0+, Thumb, 32 KB slot).

The image is slot A of the unencrypted 0xf000 subelement of Sonoff's TRVZB OTA
file. It is linked at MCU address 0x400 (a factory bootloader owns 0x000-0x3ff),
but every address this tool prints is an image offset: MCU address = offset +
0x400. Branches and `adr` are PC-relative and need no translation; absolute
flash pointers in literal pools do (subtract 0x400 to find the bytes).

Functions are found by recursive descent from the vector table, seeded with
every `bl` target in the image, so code reached only through jump tables may be
missing; `raw` disassembles any range regardless.
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

IMAGE_PATH = os.environ.get("TRVZB_IMAGE", "work/mcu_app_a.bin")
RAM_BASE = 0x20000000
RAM_END = 0x20001000


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
        self._analyze()

    def read_word(self, address: int) -> int:
        return struct.unpack_from("<I", self.image, address)[0]

    def vector_table(self) -> list[int]:
        handler_addresses = []
        for index in range(1, 48):
            word = self.read_word(index * 4)
            if word & 1 and word < len(self.image):
                handler_addresses.append(word & ~1)
        return handler_addresses

    def _linear_sweep_call_targets(self) -> list[int]:
        """Every `bl` target found by sweeping the image as a flat Thumb stream.

        Literal pools and tables decode as nonsense, so this over-reports; the
        recursive pass that consumes these seeds discards the ones that do not
        disassemble into a plausible function.
        """
        targets: set[int] = set()
        for address in range(0xC0, len(self.image) - 4, 2):
            first_halfword, second_halfword = struct.unpack_from("<HH", self.image, address)
            is_branch_with_link = (first_halfword & 0xF800) == 0xF000 and (second_halfword & 0xD000) == 0xD000
            if not is_branch_with_link:
                continue
            sign = (first_halfword >> 10) & 1
            upper_bits = first_halfword & 0x3FF
            j1 = (second_halfword >> 13) & 1
            j2 = (second_halfword >> 11) & 1
            lower_bits = second_halfword & 0x7FF
            i1 = 1 - (j1 ^ sign)
            i2 = 1 - (j2 ^ sign)
            offset = (sign << 24) | (i1 << 23) | (i2 << 22) | (upper_bits << 12) | (lower_bits << 1)
            if sign:
                offset -= 1 << 25
            target = address + 4 + offset
            if 0xC0 <= target < len(self.image) and target % 2 == 0:
                targets.add(target)
        return sorted(targets)

    def _analyze(self) -> None:
        pending = list(dict.fromkeys(self.vector_table() + self._linear_sweep_call_targets()))
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
                self.literal_readers[value].add(entry)
            for value in function.data_references.values():
                self.literal_readers[value].add(entry)

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
                elif mnemonic in ("b", "b.w"):
                    target = self._branch_target(decoded)
                    if target is not None and entry <= target < entry + 0x800:
                        pending_blocks.append(target)
                    elif target is not None:
                        function.called_functions.add(target)
                    break
                elif mnemonic.startswith("b") and mnemonic not in ("bx", "blx", "bkpt"):
                    target = self._branch_target(decoded)
                    if target is not None:
                        pending_blocks.append(target)
                elif mnemonic in ("bx", "pop") and ("pc" in decoded.op_str or mnemonic == "bx"):
                    break
                address += decoded.size

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

    def disassemble(self, entry: int) -> str:
        function = self.functions.get(entry)
        if function is None:
            function = self._disassemble_function(entry)
        lines = []
        for address in sorted(function.instructions):
            instruction = function.instructions[address]
            annotation = ""
            if address in function.data_references:
                target = function.data_references[address]
                annotation = f"  ; ->{target:#x}"
                nearby = [t for t in self.strings().items() if target <= t[0] <= target + 3]
                if nearby:
                    annotation += f' "{nearby[0][1]}"'
            if address in function.literal_values:
                value = function.literal_values[address]
                annotation = f"  ; ={value:#x}"
                if value < len(self.image):
                    text = self.strings().get(value)
                    if text:
                        annotation += f' "{text}"'
            lines.append(f"{address:#07x}  {instruction.text}{annotation}")
        return "\n".join(lines)


if __name__ == "__main__":
    firmware = FirmwareMap()
    command = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if command == "summary":
        print("functions", len(firmware.functions))
        print("vector handlers", [hex(a) for a in firmware.vector_table()])
        print("\nperipherals:")
        for address, readers in sorted(firmware.peripheral_addresses().items()):
            print(f"  {address:#010x}  read by {len(readers)} fn: {[hex(r) for r in sorted(readers)][:6]}")
    elif command == "strings":
        for address, text in sorted(firmware.strings(5).items()):
            if re.search(r"[a-zA-Z]{4}", text):
                readers: set[int] = set()
                for candidate in range(address - 3, address + 1):
                    readers |= firmware.literal_readers.get(candidate, set())
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
        struct_address = int(sys.argv[2], 0)
        readers: dict[int, set[int]] = defaultdict(set)
        writers: dict[int, set[int]] = defaultdict(set)
        for entry, function in sorted(firmware.functions.items()):
            holding_registers: set[str] = set()
            for address in sorted(function.instructions):
                instruction = function.instructions[address]
                destination = instruction.operands.split(",")[0].strip()
                if function.literal_values.get(address) == struct_address:
                    holding_registers.add(destination)
                    continue
                access = re.fullmatch(r"(r\d+), \[(r\d+)(?:, #(0x[0-9a-f]+|\d+))?\]", instruction.operands)
                if access is not None and access.group(2) in holding_registers:
                    offset = int(access.group(3), 0) if access.group(3) else 0
                    if instruction.mnemonic.startswith("ldr"):
                        readers[offset].add(entry)
                    elif instruction.mnemonic.startswith("str"):
                        writers[offset].add(entry)
                    continue
                if instruction.mnemonic in ("movs", "mov", "ldr", "ldrb", "adds", "subs") and destination in holding_registers:
                    holding_registers.discard(destination)
        for offset in sorted(set(readers) | set(writers)):
            print(
                f"+{offset:#04x} read={[hex(a) for a in sorted(readers[offset])]} "
                f"written={[hex(a) for a in sorted(writers[offset])]}"
            )
    elif command == "raw":
        start = int(sys.argv[2], 0)
        length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x80
        texts = firmware.strings()
        for decoded in firmware.disassembler.disasm(firmware.image[start : start + length], start):
            annotation = ""
            if decoded.mnemonic.startswith("ldr") and "[pc," in decoded.op_str:
                literal_address = FirmwareMap._literal_address(decoded)
                if literal_address is not None and literal_address + 4 <= len(firmware.image):
                    annotation = f"  ; ={firmware.read_word(literal_address):#x}"
            if decoded.mnemonic in ("adr",) or (decoded.mnemonic == "add" and "pc" in decoded.op_str):
                match = re.search(r"#(-?(?:0x[0-9a-f]+|\d+))", decoded.op_str)
                if match:
                    target = ((decoded.address + 4) & ~3) + int(match.group(1), 0)
                    annotation = f"  ; ->{target:#x}"
                    nearby = [t for t in texts.items() if target <= t[0] <= target + 3]
                    if nearby:
                        annotation += f' "{nearby[0][1]}"'
            print(f"{decoded.address:#07x}  {decoded.mnemonic} {decoded.op_str}{annotation}")
    elif command == "find":
        pattern = re.compile(sys.argv[2])
        for entry in sorted(firmware.functions):
            listing = firmware.disassemble(entry)
            for line in listing.splitlines():
                if pattern.search(line):
                    print(f"fn {entry:#07x} | {line}")
    elif command == "uses":
        target = int(sys.argv[2], 0)
        users = set()
        for entry, function in firmware.functions.items():
            if target in function.literal_values.values() or target in function.data_references.values():
                users.add(entry)
        print([hex(a) for a in sorted(users)])
    elif command == "json":
        print(json.dumps({hex(a): [hex(c) for c in sorted(f.called_functions)] for a, f in firmware.functions.items()}, indent=1))
    else:
        raise SystemExit(f"unknown command {command}")

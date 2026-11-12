"""verify_ota's checks: each must catch the defect it exists for."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from build_patch import build_patch_set
from conftest import REPOSITORY_ROOT, SyntheticOta
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet, PatchSite
from trvzb.symbols import load_symbols
from trvzb.verify import branch_targets_outside_functions, site_slot_differences, unexpected_changes, version_consistency


def encode_branch_with_link(address: int, target: int) -> bytes:
    offset = target - (address + 4)
    sign = 1 if offset < 0 else 0
    offset &= (1 << 25) - 1
    i1, i2 = (offset >> 23) & 1, (offset >> 22) & 1
    j1, j2 = 1 - (i1 ^ sign), 1 - (i2 ^ sign)
    first = 0xF000 | (sign << 10) | ((offset >> 12) & 0x3FF)
    second = 0xD000 | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF)
    return first.to_bytes(2, "little") + second.to_bytes(2, "little")


def test_unexpected_changes__flags_a_byte_outside_every_site(synthetic_ota: SyntheticOta) -> None:
    stock = OtaFile(str(synthetic_ota.path))
    built = OtaFile(str(synthetic_ota.path))
    built.data[synthetic_ota.slot_start("A") + 0x10] ^= 0xFF
    assert unexpected_changes(stock, built, allowed_slot_ranges=[(0x40, 5)])
    assert not unexpected_changes(stock, built, allowed_slot_ranges=[(0x10, 1)])


def test_site_slot_differences__accepts_only_relocated_words() -> None:
    stock = {"A": b"\x00" * 4, "B": b"\x00" * 4}
    pointer_a = (0x7C1F).to_bytes(4, "little")
    relocated = PatchSite("pointer", 0x3350, stock, {"A": pointer_a, "B": (0xF81F).to_bytes(4, "little")})
    mislinked = PatchSite("pointer", 0x3350, stock, {"A": pointer_a, "B": (0x7C20).to_bytes(4, "little")})
    assert not site_slot_differences(PatchSet("relocated", [relocated]))
    assert site_slot_differences(PatchSet("mislinked", [mislinked]))


def test_site_slot_differences__flags_differing_code() -> None:
    stock = {"A": b"\x00" * 2, "B": b"\x00" * 2}
    differing = PatchSite("code", 0x31A0, stock, {"A": bytes.fromhex("0121"), "B": bytes.fromhex("0221")})
    assert site_slot_differences(PatchSet("differing", [differing]))


def test_branch_targets_outside_functions__flags_a_call_into_mid_function() -> None:
    call_site_offset = 0x31A2
    stock = {"A": b"\x00" * 4, "B": b"\x00" * 4}
    to_function = PatchSite("good", call_site_offset, stock, {slot: encode_branch_with_link(call_site_offset, 0x6C18) for slot in ("A", "B")})
    to_middle = PatchSite("bad", call_site_offset, stock, {slot: encode_branch_with_link(call_site_offset, 0x6C1A) for slot in ("A", "B")})
    function_starts = {0x6C18}
    assert not branch_targets_outside_functions(PatchSet("good", [to_function]), function_starts)
    assert branch_targets_outside_functions(PatchSet("bad", [to_middle]), function_starts)


def test_version_consistency__flags_strings_that_disagree_with_header(synthetic_ota: SyntheticOta) -> None:
    stock = OtaFile(str(synthetic_ota.path))
    built = OtaFile(str(synthetic_ota.path))
    built.set_header_versions("00001405", "00001302")
    assert any("header says" in problem for problem in version_consistency(stock, built, [0x40, 0x100, 0x1F0]))


@pytest.mark.skipif(shutil.which("arm-none-eabi-as") is None, reason="arm-none-eabi binutils not on PATH")
def test_main__passes_an_identity_build(stock_ota_path: Path, tmp_path: Path) -> None:
    patch_set = build_patch_set(REPOSITORY_ROOT / "tests" / "fixtures" / "patches" / "identity", OtaFile(str(stock_ota_path)), load_symbols())
    patch_set_path = tmp_path / "identity.json"
    patch_set_path.write_text(patch_set.to_json())
    built_path = tmp_path / "built.ota"
    environment = {**os.environ, "TRVZB_OTA": str(stock_ota_path)}
    subprocess.run(
        [sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "build_ota.py"), str(built_path),
         "--mcu-version", "1.3.2", "--file-version", "0x1405", "--patch-set", str(patch_set_path)],
        env=environment, check=True, capture_output=True,
    )
    result = subprocess.run(
        [sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "verify_ota.py"), str(built_path), "--patch-set", str(patch_set_path)],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS") == 4

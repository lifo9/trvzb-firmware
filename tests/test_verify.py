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
from trvzb.image import encode_branch_with_link
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet, PatchSite
from trvzb.symbols import load_symbols
from trvzb.verify import (
    branch_targets_outside_functions,
    image_calls_overlapping_sites,
    site_bytes_mismatches,
    site_slot_differences,
    sites_splitting_stock_calls,
    unexpected_changes,
    version_consistency,
)


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
    assert result.stdout.count("PASS") == 8


def test_site_bytes_mismatches__flags_an_image_built_from_a_different_patch(synthetic_ota: SyntheticOta) -> None:
    stock = {"A": b"1.3.1", "B": b"1.3.1"}
    reviewed = PatchSet("reviewed", [PatchSite("version", 0x40, stock, {"A": b"1.3.2", "B": b"1.3.2"})])
    actually_built = PatchSet("built", [PatchSite("version", 0x40, stock, {"A": b"1.3.9", "B": b"1.3.2"})])
    built = OtaFile(str(synthetic_ota.path))
    actually_built.apply(built, protected_ranges=[])
    assert site_bytes_mismatches(built, reviewed)
    assert not site_bytes_mismatches(built, actually_built)


def test_sites_splitting_stock_calls__flags_a_site_ending_inside_a_call(stock_slot_a_path: Path) -> None:
    stock_slot_a = stock_slot_a_path.read_bytes()
    stock_bytes = stock_slot_a[0x31A0:0x31A4]
    half_call = PatchSite("half_call", 0x31A0, {"A": stock_bytes, "B": stock_bytes}, {"A": stock_bytes, "B": stock_bytes})
    whole_call = PatchSite("whole_call", 0x31A0, {"A": stock_slot_a[0x31A0:0x31A6], "B": stock_slot_a[0x31A0:0x31A6]},
                           {"A": stock_slot_a[0x31A0:0x31A6], "B": stock_slot_a[0x31A0:0x31A6]})
    assert sites_splitting_stock_calls(PatchSet("half", [half_call]), stock_slot_a)
    assert not sites_splitting_stock_calls(PatchSet("whole", [whole_call]), stock_slot_a)


def test_main__with_manifest_fails_an_image_that_is_not_the_recorded_release(stock_ota_path: Path, tmp_path: Path) -> None:
    built_path = tmp_path / "built.ota"
    environment = {**os.environ, "TRVZB_OTA": str(stock_ota_path)}
    subprocess.run(
        [sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "build_ota.py"), str(built_path),
         "--mcu-version", "1.3.3", "--file-version", "0x1405"],
        env=environment, check=True, capture_output=True,
    )
    result = subprocess.run(
        [sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "verify_ota.py"), str(built_path),
         "--manifest", str(REPOSITORY_ROOT / "releases" / "1.4.5-verstest.toml")],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "FAIL  image is the recorded release" in result.stdout


@pytest.mark.parametrize("site_offset, site_length", [(0x31A2, 1), (0x31A5, 1), (0x31A3, 2)])
def test_sites_splitting_stock_calls__flags_any_boundary_inside_a_call(
    stock_slot_a_path: Path, site_offset: int, site_length: int
) -> None:
    stock_slot_a = stock_slot_a_path.read_bytes()
    original = stock_slot_a[site_offset : site_offset + site_length]
    partial = PatchSite("partial", site_offset, {"A": original, "B": original}, {"A": original, "B": original})
    assert sites_splitting_stock_calls(PatchSet("partial", [partial]), stock_slot_a)


def test_image_calls_overlapping_sites__decodes_calls_from_the_built_image(synthetic_ota: SyntheticOta) -> None:
    built = OtaFile(str(synthetic_ota.path))
    call_offset = 0x100
    for slot_name in ("A", "B"):
        start = built.slot_offset(slot_name) + call_offset
        built.data[start : start + 4] = encode_branch_with_link(call_offset, 0x180)
    one_byte_site = PatchSite("one_byte", call_offset + 1, {"A": b"\x00", "B": b"\x00"}, {"A": b"\x00", "B": b"\x00"})
    patch_set = PatchSet("one_byte", [one_byte_site])
    assert image_calls_overlapping_sites(built, patch_set, function_starts={0x170})
    assert not image_calls_overlapping_sites(built, patch_set, function_starts={0x180})


def test_main__shows_an_unrecorded_release_hash_as_pending(stock_ota_path: Path, tmp_path: Path) -> None:
    manifest_text = (REPOSITORY_ROOT / "releases" / "1.4.5-verstest.toml").read_text()
    unrecorded = tmp_path / "1.4.5-verstest.toml"
    unrecorded.write_text("\n".join(line for line in manifest_text.splitlines() if not line.startswith("sha512")) + "\n")
    environment = {**os.environ, "TRVZB_OTA": str(stock_ota_path)}
    built_path = tmp_path / "built.ota"
    subprocess.run(
        [sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "build_ota.py"), str(built_path),
         "--mcu-version", "1.3.2", "--file-version", "0x1405"],
        env=environment, check=True, capture_output=True,
    )
    result = subprocess.run(
        [sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "verify_ota.py"), str(built_path), "--manifest", str(unrecorded)],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout
    assert "PENDING  image is the recorded release" in result.stdout
    assert "FAIL" not in result.stdout

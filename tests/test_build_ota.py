"""Behaviour of the OTA rebuild: what lands in the output file, byte for byte."""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from build_patch import build_patch_set
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet, PatchSite
from trvzb.symbols import load_symbols
from conftest import REPOSITORY_ROOT, VERSION_STRING_OFFSETS, SyntheticOta, build_synthetic_ota_bytes

EXPERIMENT_1_SHA512 = (
    "cc146b65c5056c0aefc009a282b70ff4dc297424431be420043af8a398384396"
    "024b1a5c761aa66e7e7041230d18f06c39ff288cb36d538ee1a1e03499820f56"
)


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    assert len(before) == len(after)
    return [offset for offset in range(len(before)) if before[offset] != after[offset]]


def test_ota_file__raises_on_wrong_magic(tmp_path: Path) -> None:
    data, _ = build_synthetic_ota_bytes()
    corrupted = bytearray(data)
    corrupted[0] ^= 0xFF
    path = tmp_path / "corrupted.ota"
    path.write_bytes(corrupted)
    with pytest.raises(ValueError, match="not an OTA file"):
        OtaFile(str(path))


def test_ota_file__raises_when_total_size_disagrees_with_file(tmp_path: Path) -> None:
    data, _ = build_synthetic_ota_bytes()
    path = tmp_path / "truncated.ota"
    path.write_bytes(data[:-1])
    with pytest.raises(ValueError, match="total image size"):
        OtaFile(str(path))


def test_ota_file__locates_both_slots(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    assert ota.slot_length == synthetic_ota.slot_length
    assert ota.slot_offset("A") == synthetic_ota.slot_start("A")
    assert ota.slot_offset("B") == synthetic_ota.slot_start("B")


def test_find_in_slot_a__returns_slot_relative_offsets(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    assert ota.find_in_slot_a(b"1.3.1") == list(VERSION_STRING_OFFSETS)


def test_patch_both_slots__writes_same_bytes_at_same_offset_in_both_slots(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    ota.patch_both_slots(0x40, b"1.3.1", b"1.3.2")
    expected = sorted(
        [synthetic_ota.slot_start("A") + 0x44, synthetic_ota.slot_start("B") + 0x44]
    )
    assert changed_offsets(synthetic_ota.data, bytes(ota.data)) == expected


def test_patch_both_slots__raises_when_old_bytes_do_not_match(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    with pytest.raises(ValueError, match="slot A"):
        ota.patch_both_slots(0x41, b"1.3.1", b"1.3.2")


def test_patch_both_slots__raises_when_length_changes(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    with pytest.raises(ValueError, match="length"):
        ota.patch_both_slots(0x40, b"1.3.1", b"1.3.10")


def test_set_file_version__writes_little_endian_at_0x0e(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    ota.set_file_version(0x1405)
    assert struct.unpack_from("<I", ota.data, 0x0E)[0] == 0x1405
    assert changed_offsets(synthetic_ota.data, bytes(ota.data)) == [0x0E]


def test_set_header_versions__rewrites_both_versions_keeping_length(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    ota.set_header_versions("00001405", "00001302")
    assert ota.mcu_header_string() == b"vers:00001405,00001302\n"
    assert len(ota.data) == len(synthetic_ota.data)


def test_set_header_versions__raises_on_wrong_length(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    with pytest.raises(ValueError):
        ota.set_header_versions("1405", "00001302")


def run_build_ota(
    stock_path: Path,
    output_path: Path,
    mcu_version: str = "1.3.2",
    file_version: str = "0x1405",
    check: bool = True,
    patch_set_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    patch_arguments = ["--patch-set", str(patch_set_path)] if patch_set_path else []
    return subprocess.run(
        [
            sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "build_ota.py"), str(output_path),
            "--mcu-version", mcu_version, "--file-version", file_version, *patch_arguments,
        ],
        env={**os.environ, "TRVZB_OTA": str(stock_path)},
        capture_output=True,
        text=True,
        check=check,
    )


def test_main__changes_only_versions_in_synthetic_ota(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    output_path = tmp_path / "out.ota"
    run_build_ota(synthetic_ota.path, output_path)
    built = output_path.read_bytes()
    version_digit_offsets = [
        synthetic_ota.slot_start(slot_name) + offset + 4
        for slot_name in ("A", "B")
        for offset in VERSION_STRING_OFFSETS
    ]
    header_offsets = [0x0E, 0x20, 0x29]
    assert changed_offsets(synthetic_ota.data, built) == sorted(header_offsets + version_digit_offsets)


def test_main__reproduces_experiment_1_image_from_stock(stock_ota_path: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "trvzb_v1.4.5-verstest.ota"
    run_build_ota(stock_ota_path, output_path)
    assert hashlib.sha512(output_path.read_bytes()).hexdigest() == EXPERIMENT_1_SHA512


def test_main__refuses_mcu_version_not_above_current(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    result = run_build_ota(synthetic_ota.path, tmp_path / "out.ota", mcu_version="1.3.1", check=False)
    assert result.returncode != 0
    assert "not above" in result.stderr
    assert not (tmp_path / "out.ota").exists()


def test_main__refuses_mcu_version_that_would_wrap(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    result = run_build_ota(synthetic_ota.path, tmp_path / "out.ota", mcu_version="1.4.0", check=False)
    assert result.returncode != 0
    assert "does not fit" in result.stderr


def test_main__refuses_mcu_version_of_different_length(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    result = run_build_ota(synthetic_ota.path, tmp_path / "out.ota", mcu_version="2.0.10", check=False)
    assert result.returncode != 0
    assert "fixed-size" in result.stderr


def test_main__refuses_file_version_not_above_current(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    result = run_build_ota(synthetic_ota.path, tmp_path / "out.ota", file_version="0x1404", check=False)
    assert result.returncode != 0
    assert "Zigbee2MQTT would not offer it" in result.stderr


def test_main__derives_header_versions_from_arguments(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    output_path = tmp_path / "out.ota"
    run_build_ota(synthetic_ota.path, output_path, mcu_version="2.0.0", file_version="0x1406")
    assert OtaFile(str(output_path)).header_versions() == ("00001406", "00002000")


def test_patch_both_slots__raises_when_patch_runs_past_slot_end(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    with pytest.raises(ValueError, match="outside"):
        ota.patch_both_slots(synthetic_ota.slot_length - 2, b"abcd", b"wxyz")


def test_main__refuses_a_patch_set_touching_a_protected_range(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    startup_site = PatchSite("startup", 0x40, {"A": b"1.3.1", "B": b"1.3.1"}, {"A": b"xxxxx", "B": b"xxxxx"})
    patch_set_path = tmp_path / "startup.json"
    patch_set_path.write_text(PatchSet("touches-startup", [startup_site]).to_json())
    result = run_build_ota(synthetic_ota.path, tmp_path / "out.ota", check=False, patch_set_path=patch_set_path)
    assert result.returncode != 0
    assert "startup_and_vectors" in result.stderr
    assert not (tmp_path / "out.ota").exists()


@pytest.mark.skipif(shutil.which("arm-none-eabi-as") is None, reason="arm-none-eabi binutils not on PATH")
def test_main__identity_patch_set_reproduces_experiment_1_image(stock_ota_path: Path, tmp_path: Path) -> None:
    patch_set = build_patch_set(REPOSITORY_ROOT / "tests" / "fixtures" / "patches" / "identity", OtaFile(str(stock_ota_path)), load_symbols())
    patch_set_path = tmp_path / "identity.json"
    patch_set_path.write_text(patch_set.to_json())
    output_path = tmp_path / "out.ota"
    run_build_ota(stock_ota_path, output_path, patch_set_path=patch_set_path)
    assert hashlib.sha512(output_path.read_bytes()).hexdigest() == EXPERIMENT_1_SHA512

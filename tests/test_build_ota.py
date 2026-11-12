"""Behaviour of the OTA rebuild: what lands in the output file, byte for byte."""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from trvzb.ota import OtaFile
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
    with pytest.raises(AssertionError, match="not an OTA file"):
        OtaFile(str(path))


def test_ota_file__raises_when_total_size_disagrees_with_file(tmp_path: Path) -> None:
    data, _ = build_synthetic_ota_bytes()
    path = tmp_path / "truncated.ota"
    path.write_bytes(data[:-1])
    with pytest.raises(AssertionError, match="total image size"):
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
    with pytest.raises(AssertionError, match="slot A"):
        ota.patch_both_slots(0x41, b"1.3.1", b"1.3.2")


def test_patch_both_slots__raises_when_length_changes(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    with pytest.raises(AssertionError, match="length"):
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
    with pytest.raises(AssertionError):
        ota.set_header_versions("1405", "00001302")


def run_build_ota(stock_path: Path, output_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(REPOSITORY_ROOT / "tools" / "build_ota.py"), str(output_path)],
        env={**os.environ, "TRVZB_OTA": str(stock_path)},
        capture_output=True,
        text=True,
        check=True,
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

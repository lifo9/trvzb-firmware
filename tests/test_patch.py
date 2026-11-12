"""Patch sets: per-slot bytes, all-or-nothing application, protected ranges."""

from __future__ import annotations

import pytest
from conftest import SyntheticOta
from trvzb.ota import OtaFile
from trvzb.patch import PatchSet, PatchSite
from trvzb.symbols import ProtectedRange


def site(name: str, offset: int, stock: bytes, patched_a: bytes, patched_b: bytes) -> PatchSite:
    return PatchSite(name=name, offset=offset, stock_bytes={"A": stock, "B": stock}, patched_bytes={"A": patched_a, "B": patched_b})


def slot_bytes(ota: OtaFile, slot_name: str, offset: int, length: int) -> bytes:
    start = ota.slot_offset(slot_name) + offset
    return bytes(ota.data[start : start + length])


def test_apply__writes_each_slot_its_own_bytes(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    PatchSet("per-slot", [site("version", 0x40, b"1.3.1", b"AAAAA", b"BBBBB")]).apply(ota, protected_ranges=[])
    assert slot_bytes(ota, "A", 0x40, 5) == b"AAAAA"
    assert slot_bytes(ota, "B", 0x40, 5) == b"BBBBB"


def test_apply__writes_nothing_when_a_later_site_does_not_match(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    patch_set = PatchSet(
        "half-valid",
        [site("good", 0x40, b"1.3.1", b"1.3.2", b"1.3.2"), site("bad", 0x101, b"1.3.1", b"1.3.2", b"1.3.2")],
    )
    with pytest.raises(ValueError, match="holds"):
        patch_set.apply(ota, protected_ranges=[])
    assert bytes(ota.data) == synthetic_ota.data


def test_apply__refuses_a_site_in_a_protected_range(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    protected = [ProtectedRange("update_path", 0x3E, 0x42)]
    with pytest.raises(ValueError, match="update_path"):
        PatchSet("protected", [site("version", 0x40, b"1.3.1", b"1.3.2", b"1.3.2")]).apply(ota, protected)
    assert bytes(ota.data) == synthetic_ota.data


def test_apply__refuses_overlapping_sites(synthetic_ota: SyntheticOta) -> None:
    ota = OtaFile(str(synthetic_ota.path))
    patch_set = PatchSet(
        "overlap",
        [site("first", 0x40, b"1.3.1", b"1.3.2", b"1.3.2"), site("second", 0x42, b"3.1", b"3.2", b"3.2")],
    )
    with pytest.raises(ValueError, match="overlapping"):
        patch_set.apply(ota, protected_ranges=[])


def test_patch_site__rejects_a_length_change() -> None:
    with pytest.raises(ValueError, match="changes length"):
        site("longer", 0x40, b"1.3.1", b"1.3.10", b"1.3.1")


def test_json__round_trips() -> None:
    patch_set = PatchSet("round-trip", [site("version", 0x40, b"1.3.1", b"AAAAA", b"BBBBB")])
    assert PatchSet.from_json(patch_set.to_json()) == patch_set

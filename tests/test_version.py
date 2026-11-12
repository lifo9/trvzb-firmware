"""MCU version packing: the byte the valve's update gate compares."""

from __future__ import annotations

import pytest
from trvzb.version import McuVersion


def test_packed__matches_stock_1_4_4_mcu_byte() -> None:
    assert McuVersion.parse("1.3.1").packed() == 0x71


def test_packed__orders_like_the_update_gate() -> None:
    assert McuVersion.parse("1.3.2").packed() > McuVersion.parse("1.3.1").packed()
    assert McuVersion.parse("1.3.15").packed() > McuVersion.parse("1.3.2").packed()


def test_mcu_version__rejects_minor_that_would_wrap_below_stock() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        McuVersion.parse("1.4.0")


def test_mcu_version__rejects_patch_above_fifteen() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        McuVersion.parse("1.3.16")


def test_parse__rejects_malformed_text() -> None:
    with pytest.raises(ValueError, match="not an MCU version"):
        McuVersion.parse("1.3")


def test_header_field__matches_stock_header_format() -> None:
    assert McuVersion.parse("1.3.1").header_field() == "00001301"
    assert McuVersion.parse("1.2.8").header_field() == "00001208"


def test_from_header_field__round_trips() -> None:
    assert McuVersion.from_header_field("00001302") == McuVersion.parse("1.3.2")

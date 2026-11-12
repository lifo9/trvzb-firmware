"""The device log stays consistent with the releases and the MCU's version rule."""

from __future__ import annotations

import tomllib

from build_release import load_manifest
from conftest import REPOSITORY_ROOT
from trvzb.version import McuVersion

STOCK_MCU_VERSION = McuVersion.parse("1.3.1")


def load_devices() -> list[dict]:
    with open(REPOSITORY_ROOT / "devices.toml", "rb") as devices_file:
        return tomllib.load(devices_file)["device"]


def test_devices__every_flash_names_an_existing_release() -> None:
    for device in load_devices():
        for flash in device["flash"]:
            assert (REPOSITORY_ROOT / "releases" / f"{flash['release']}.toml").exists(), flash["release"]


def test_devices__flash_records_the_release_mcu_version() -> None:
    for device in load_devices():
        for flash in device["flash"]:
            manifest = load_manifest(REPOSITORY_ROOT / "releases" / f"{flash['release']}.toml")
            assert flash["mcu_version"] == manifest.mcu_version, (device["label"], flash["release"])


def test_devices__mcu_versions_strictly_increase_per_device() -> None:
    for device in load_devices():
        previous = STOCK_MCU_VERSION
        for flash in device["flash"]:
            current = McuVersion.parse(flash["mcu_version"])
            assert current.packed() > previous.packed(), (device["label"], flash["release"])
            previous = current

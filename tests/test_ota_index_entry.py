"""The Zigbee2MQTT index entry is derived from a verified release only."""

from __future__ import annotations

from pathlib import Path

import pytest
from build_release import build_release, load_manifest
from conftest import REPOSITORY_ROOT
from ota_index_entry import index_entry

EXPERIMENT_1 = REPOSITORY_ROOT / "releases" / "1.4.5-verstest.toml"


def test_index_entry__describes_the_release(stock_ota_path: Path, tmp_path: Path) -> None:
    manifest = load_manifest(EXPERIMENT_1)
    image_path, _ = build_release(manifest, str(stock_ota_path), tmp_path)
    entry = index_entry(manifest, image_path)
    assert entry["sha512"] == manifest.sha512
    assert entry["fileVersion"] == 0x1405
    assert (entry["manufacturerCode"], entry["imageType"]) == (4742, 8199)
    assert entry["url"] == "ota/1.4.5-verstest.ota"


def test_index_entry__refuses_an_image_that_is_not_the_release(stock_ota_path: Path, tmp_path: Path) -> None:
    manifest = load_manifest(EXPERIMENT_1)
    image_path, _ = build_release(manifest, str(stock_ota_path), tmp_path)
    tampered = bytearray(image_path.read_bytes())
    tampered[-1] ^= 0xFF
    image_path.write_bytes(tampered)
    with pytest.raises(ValueError, match="is not release"):
        index_entry(manifest, image_path)

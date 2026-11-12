"""Releases rebuild from their manifests, from the stock image they pin only."""

from __future__ import annotations

from pathlib import Path

import pytest
from build_release import build_release, load_manifest
from conftest import REPOSITORY_ROOT, SyntheticOta
from trvzb.build import BuildRefused

RELEASES = REPOSITORY_ROOT / "releases"


def test_build_release__experiment_1_reproduces_its_manifest(stock_ota_path: Path, tmp_path: Path) -> None:
    manifest = load_manifest(RELEASES / "1.4.5-verstest.toml")
    _, built_sha512 = build_release(manifest, str(stock_ota_path), tmp_path)
    assert built_sha512 == manifest.sha512


def test_build_release__refuses_a_different_stock_image(synthetic_ota: SyntheticOta, tmp_path: Path) -> None:
    manifest = load_manifest(RELEASES / "1.4.5-verstest.toml")
    with pytest.raises(BuildRefused, match="not the stock image"):
        build_release(manifest, str(synthetic_ota.path), tmp_path)

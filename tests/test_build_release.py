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


def test_build_release__leaves_no_image_when_the_hash_does_not_match(stock_ota_path: Path, tmp_path: Path) -> None:
    manifest_text = (RELEASES / "1.4.5-verstest.toml").read_text()
    wrong_manifest = tmp_path / "1.4.5-verstest.toml"
    wrong_manifest.write_text(manifest_text.replace('sha512 = "cc146b65', 'sha512 = "00146b65'))
    output_directory = tmp_path / "releases"
    with pytest.raises(BuildRefused, match="manifest records"):
        build_release(load_manifest(wrong_manifest), str(stock_ota_path), output_directory)
    assert not (output_directory / "1.4.5-verstest.ota").exists()


def test_load_manifest__refuses_an_id_that_differs_from_the_file_name(tmp_path: Path) -> None:
    renamed = tmp_path / "something-else.toml"
    renamed.write_text((RELEASES / "1.4.5-verstest.toml").read_text())
    with pytest.raises(ValueError, match="file name"):
        load_manifest(renamed)

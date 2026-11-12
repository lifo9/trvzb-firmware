"""Building patch sets with the assembler, against the stock image."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from build_patch import SiteSpecification, build_linker_script, build_patch_set
from conftest import REPOSITORY_ROOT
from trvzb.image import SLOT_A, SLOT_B
from trvzb.ota import OtaFile
from trvzb.symbols import load_symbols

IDENTITY_PATCH = REPOSITORY_ROOT / "tests" / "fixtures" / "patches" / "identity"

requires_assembler = pytest.mark.skipif(shutil.which("arm-none-eabi-as") is None, reason="arm-none-eabi binutils not on PATH")


def test_build_linker_script__places_sites_at_each_slot_address() -> None:
    sites = [SiteSpecification("knob_commit", 0x31A0, 6)]
    assert ".site.knob_commit 0x35a0 :" in build_linker_script(SLOT_A, sites)
    assert ".site.knob_commit 0xb1a0 :" in build_linker_script(SLOT_B, sites)


@requires_assembler
def test_build_patch_set__identity_patch_reproduces_stock_in_both_slots(stock_ota_path: Path) -> None:
    patch_set = build_patch_set(IDENTITY_PATCH, OtaFile(str(stock_ota_path)), load_symbols())
    for site in patch_set.sites:
        assert site.patched_bytes == site.stock_bytes, site.name


@requires_assembler
def test_build_patch_set__gives_flash_pointers_slot_specific_bytes(stock_ota_path: Path) -> None:
    patch_set = build_patch_set(IDENTITY_PATCH, OtaFile(str(stock_ota_path)), load_symbols())
    pointer_site = next(site for site in patch_set.sites if site.name == "digit_font_pointer")
    assert int.from_bytes(pointer_site.patched_bytes["A"], "little") == 0x7C1F
    assert int.from_bytes(pointer_site.patched_bytes["B"], "little") == 0xF81F


@requires_assembler
def test_build_patch_set__refuses_a_site_longer_than_its_max_length(stock_ota_path: Path, tmp_path: Path) -> None:
    (tmp_path / "patch.S").write_text((IDENTITY_PATCH / "patch.S").read_text())
    (tmp_path / "sites.toml").write_text(
        (IDENTITY_PATCH / "sites.toml").read_text().replace("max_length = 6", "max_length = 4")
    )
    with pytest.raises(ValueError, match="knob_commit is 6 bytes"):
        build_patch_set(tmp_path, OtaFile(str(stock_ota_path)), load_symbols())


@requires_assembler
def test_build_patch_set__refuses_code_outside_a_site(stock_ota_path: Path, tmp_path: Path) -> None:
    (tmp_path / "patch.S").write_text((IDENTITY_PATCH / "patch.S").read_text() + "\n    .text\n    nop\n")
    (tmp_path / "sites.toml").write_text((IDENTITY_PATCH / "sites.toml").read_text())
    with pytest.raises(SystemExit, match="outside a .site section"):
        build_patch_set(tmp_path, OtaFile(str(stock_ota_path)), load_symbols())


@requires_assembler
def test_build_patch_set__refuses_a_site_the_linker_would_pad(stock_ota_path: Path, tmp_path: Path) -> None:
    (tmp_path / "patch.S").write_text(
        "    .syntax unified\n    .thumb\n    .include \"stock_symbols.inc\"\n"
        "    .section .site.needs_word_alignment, \"ax\"\n"
        "    ldr r0, =SETTINGS\n    bx lr\n    .ltorg\n"
    )
    (tmp_path / "sites.toml").write_text(
        'name = "misaligned"\nregions = ["ui_main_screen"]\n\n[[site]]\nname = "needs_word_alignment"\noffset = 0x314a\nmax_length = 0x20\n'
    )
    with pytest.raises(ValueError, match="alignment"):
        build_patch_set(tmp_path, OtaFile(str(stock_ota_path)), load_symbols())


@requires_assembler
def test_build_patch_set__refuses_a_site_outside_its_declared_regions(stock_ota_path: Path, tmp_path: Path) -> None:
    (tmp_path / "patch.S").write_text((IDENTITY_PATCH / "patch.S").read_text())
    (tmp_path / "sites.toml").write_text(
        (IDENTITY_PATCH / "sites.toml").read_text().replace('regions = ["ui_main_screen", "draw_character"]', 'regions = ["ui_main_screen"]')
    )
    with pytest.raises(ValueError, match="digit_font_pointer .* lies outside regions"):
        build_patch_set(tmp_path, OtaFile(str(stock_ota_path)), load_symbols())

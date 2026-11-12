"""Build a release from its manifest and check it reproduces.

A manifest in releases/<id>.toml pins the stock image (`stock_sha512`), the
MCU and file versions, an optional `patch_set` directory, and the SHA-512 of
the resulting image. The build refuses a different stock image, and fails if
the result does not match the recorded hash; a manifest without `sha512` is a
release in preparation, whose hash is printed for review.

Writes work/releases/<id>.ota and, for a patched release,
work/releases/<id>.patch.json, which tools/verify_ota.py takes.

Usage:
    build_release.py releases/<id>.toml
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_patch import build_patch_set  # noqa: E402
from trvzb.build import BuildRefused, build_image  # noqa: E402
from trvzb.ota import STOCK_OTA_PATH, OtaFile  # noqa: E402
from trvzb.symbols import load_symbols  # noqa: E402

RELEASE_OUTPUT_DIRECTORY = Path("work/releases")


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    stock_sha512: str
    mcu_version: str
    file_version: int
    patch_set_directory: Path | None
    sha512: str | None


def load_manifest(path: Path) -> ReleaseManifest:
    with open(path, "rb") as manifest_file:
        document = tomllib.load(manifest_file)
    patch_set = document.get("patch_set")
    return ReleaseManifest(
        release_id=document["id"],
        stock_sha512=document["stock_sha512"],
        mcu_version=document["mcu_version"],
        file_version=document["file_version"],
        patch_set_directory=REPOSITORY_ROOT / patch_set if patch_set else None,
        sha512=document.get("sha512"),
    )


def build_release(manifest: ReleaseManifest, stock_path: str, output_directory: Path) -> tuple[Path, str]:
    """Build the release image; return its path and SHA-512."""
    stock_bytes = open(stock_path, "rb").read()
    if hashlib.sha512(stock_bytes).hexdigest() != manifest.stock_sha512:
        raise BuildRefused(f"{stock_path} is not the stock image release {manifest.release_id} is built from")
    symbols = load_symbols()
    output_directory.mkdir(parents=True, exist_ok=True)
    logic_patch = None
    if manifest.patch_set_directory is not None:
        logic_patch = build_patch_set(manifest.patch_set_directory, OtaFile(stock_path), symbols)
        (output_directory / f"{manifest.release_id}.patch.json").write_text(logic_patch.to_json() + "\n")
    image = build_image(OtaFile(stock_path), manifest.mcu_version, manifest.file_version, logic_patch, symbols.protected_ranges)
    output_path = output_directory / f"{manifest.release_id}.ota"
    image.write(str(output_path))
    built_sha512 = hashlib.sha512(output_path.read_bytes()).hexdigest()
    if manifest.sha512 is not None and built_sha512 != manifest.sha512:
        raise BuildRefused(f"release {manifest.release_id} built to {built_sha512}, manifest records {manifest.sha512}")
    return output_path, built_sha512


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    manifest = load_manifest(arguments.manifest)
    try:
        output_path, built_sha512 = build_release(manifest, STOCK_OTA_PATH, RELEASE_OUTPUT_DIRECTORY)
    except BuildRefused as error:
        raise SystemExit(str(error)) from error
    status = "reproduces the manifest" if manifest.sha512 else "new; record this hash in the manifest after review"
    print(f"{output_path}: {status}\nsha512 {built_sha512}")


if __name__ == "__main__":
    main()

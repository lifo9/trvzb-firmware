"""Print the Zigbee2MQTT local OTA index entry for a built release.

The entry carries the release's SHA-512, so Zigbee2MQTT refuses to serve any
other file under that name. Refuses to print one unless the built image
matches the hash its manifest records.

Usage:
    ota_index_entry.py releases/<id>.toml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# The tools run under `python3 -I` because they read untrusted firmware files,
# and isolated mode leaves the repository off the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_release import RELEASE_OUTPUT_DIRECTORY, ReleaseManifest, load_manifest  # noqa: E402
from trvzb.ota import OtaFile  # noqa: E402

ZIGBEE2MQTT_OTA_DIRECTORY = "ota"


def index_entry(manifest: ReleaseManifest, image_path: Path) -> dict:
    if manifest.sha512 is None:
        raise ValueError(f"release {manifest.release_id} records no sha512; review and record it first")
    image_bytes = image_path.read_bytes()
    image_sha512 = hashlib.sha512(image_bytes).hexdigest()
    if image_sha512 != manifest.sha512:
        raise ValueError(f"{image_path} is not release {manifest.release_id}: {image_sha512[:16]}…")
    ota = OtaFile(str(image_path))
    meta = {
        "fileName": image_path.name,
        "fileVersion": int.from_bytes(ota.data[0x0E:0x12], "little"),
        "fileSize": len(image_bytes),
        "url": f"{ZIGBEE2MQTT_OTA_DIRECTORY}/{image_path.name}",
        "imageType": int.from_bytes(ota.data[0x0C:0x0E], "little"),
        "manufacturerCode": int.from_bytes(ota.data[0x0A:0x0C], "little"),
        "sha512": image_sha512,
    }
    if meta["fileVersion"] != manifest.file_version:
        raise ValueError(f"image file version {meta['fileVersion']:#x} differs from the manifest's {manifest.file_version:#x}")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    manifest = load_manifest(arguments.manifest)
    try:
        entry = index_entry(manifest, RELEASE_OUTPUT_DIRECTORY / f"{manifest.release_id}.ota")
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps([entry], indent=2))


if __name__ == "__main__":
    main()

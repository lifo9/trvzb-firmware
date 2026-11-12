"""Build an image from stock: a logic patch set, then raised versions.

Refuses anything the valve would not accept: the MCU version must pack higher
than the image's current one and keep the same string length, since it sits in
fixed-size fields; the file version must exceed the current one for Zigbee2MQTT
to offer the update; and the logic patch set may not touch a protected range.
"""

from __future__ import annotations

import struct

from trvzb.ota import FILE_VERSION_OFFSET, OtaFile
from trvzb.patch import PatchSet, PatchSite
from trvzb.symbols import ProtectedRange
from trvzb.version import McuVersion


class BuildRefused(ValueError):
    """The requested image would be rejected by the valve or Zigbee2MQTT, or would be unsafe."""


def check_file_version(ota: OtaFile, new_file_version: int) -> None:
    """Raise BuildRefused unless the file version is spellable and above the image's."""
    current_file_version, = struct.unpack_from("<I", ota.data, FILE_VERSION_OFFSET)
    if not f"{new_file_version:08x}".isdecimal():
        raise BuildRefused(
            f"file version {new_file_version:#x} must be written with decimal digits only in the header's "
            "eight-digit field (for example 0x1406-0x1409, then 0x1410)"
        )
    if new_file_version <= current_file_version:
        raise BuildRefused(
            f"file version {new_file_version:#x} is not above the image's {current_file_version:#x}; "
            "Zigbee2MQTT would not offer it"
        )


def check_versions(ota: OtaFile, new_mcu_version: McuVersion, new_file_version: int) -> McuVersion:
    """Raise BuildRefused unless both versions are acceptable; return the image's current MCU version."""
    _, current_mcu_field = ota.header_versions()
    current_mcu_version = McuVersion.from_header_field(current_mcu_field)
    if new_mcu_version.packed() <= current_mcu_version.packed():
        raise BuildRefused(
            f"MCU version {new_mcu_version} (packed {new_mcu_version.packed():#04x}) is not above "
            f"the image's {current_mcu_version} (packed {current_mcu_version.packed():#04x}); the valve would refuse it"
        )
    if len(str(new_mcu_version)) != len(str(current_mcu_version)):
        raise BuildRefused(
            f"MCU version {new_mcu_version} must be as long as {current_mcu_version}: "
            "the version strings sit in fixed-size fields"
        )
    check_file_version(ota, new_file_version)
    return current_mcu_version


def version_patch_set(ota: OtaFile, current_mcu_version: McuVersion, new_mcu_version: McuVersion) -> PatchSet:
    current_string = str(current_mcu_version).encode()
    new_string = str(new_mcu_version).encode()
    occurrences = ota.find_in_slot_a(current_string)
    if not occurrences:
        raise BuildRefused(f"the image does not contain its version string {current_string!r}")
    return PatchSet(
        name=f"mcu-version-{new_mcu_version}",
        sites=[
            PatchSite(
                name=f"version_string_{offset:#x}",
                offset=offset,
                stock_bytes={"A": current_string, "B": current_string},
                patched_bytes={"A": new_string, "B": new_string},
            )
            for offset in occurrences
        ],
    )


def build_image(
    ota: OtaFile,
    mcu_version_text: str,
    file_version: int,
    logic_patch: PatchSet | None,
    protected_ranges: list[ProtectedRange],
) -> OtaFile:
    """Apply `logic_patch` and raise the versions in `ota`, which is modified and returned."""
    try:
        new_mcu_version = McuVersion.parse(mcu_version_text)
    except ValueError as error:
        raise BuildRefused(str(error)) from error
    current_mcu_version = check_versions(ota, new_mcu_version, file_version)
    if logic_patch is not None:
        try:
            logic_patch.apply(ota, protected_ranges=protected_ranges)
        except ValueError as error:
            raise BuildRefused(f"patch set {logic_patch.name}: {error}") from error
    # The version strings are the one change the update handshake requires, and
    # one of them sits in the UART dispatcher's literal data, inside a protected
    # range; so this patch set alone is applied without the protected check.
    version_patch_set(ota, current_mcu_version, new_mcu_version).apply(ota, protected_ranges=[])
    ota.set_file_version(file_version)
    ota.set_header_versions(f"{file_version:08x}", new_mcu_version.header_field())
    return ota


def build_recovery_image(ota: OtaFile, file_version: int) -> OtaFile:
    """Raise only the file version of an otherwise unchanged stock image.

    The application-MCU payload and its version stay exactly as Sonoff built
    them, so the update offers the MCU nothing new while giving the Zigbee SoC
    a complete, consistent update to finish.
    """
    check_file_version(ota, file_version)
    _, mcu_field = ota.header_versions()
    ota.set_file_version(file_version)
    ota.set_header_versions(f"{file_version:08x}", mcu_field)
    return ota

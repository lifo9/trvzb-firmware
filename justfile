# Stock firmware this repository's analysis is based on, pinned to the
# zigbee-OTA commit that added it so the bytes can never drift.
stock_ota_url := "https://raw.githubusercontent.com/Koenkk/zigbee-OTA/b8d50733ddbd0dcf3c9c0cb483e9362a5e2313cc/images/Sonoff/trvzb_v1.4.4.ota"
stock_ota_sha512 := "c7640e2a238ab9807b77d36abc4b9e58d64c99a8fe8ac8d6246f48760bc85a2aec5c446630c94dd2e85fe3fe088011eccc992e543183b74ede7e061ae6f324b3"

default:
    @just --list

# Download the stock 1.4.4 OTA into work/ unless a verified copy is already there
fetch:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p work
    verify() { python3 -I -c 'import hashlib, sys; sys.exit(hashlib.sha512(open(sys.argv[1], "rb").read()).hexdigest() != sys.argv[2])' "$1" "{{stock_ota_sha512}}"; }
    if [ -f work/trvzb_v1.4.4.ota ] && verify work/trvzb_v1.4.4.ota; then
        echo "work/trvzb_v1.4.4.ota: present, SHA-512 verified"
        exit 0
    fi
    curl -sSfL -o work/trvzb_v1.4.4.ota.partial "{{stock_ota_url}}"
    if ! verify work/trvzb_v1.4.4.ota.partial; then
        echo "downloaded file does not match the pinned SHA-512; keeping it at work/trvzb_v1.4.4.ota.partial for inspection" >&2
        exit 1
    fi
    mv work/trvzb_v1.4.4.ota.partial work/trvzb_v1.4.4.ota
    echo "work/trvzb_v1.4.4.ota: downloaded, SHA-512 verified"

# Split the application-MCU slots out of the stock OTA
extract: fetch
    python3 -I tools/extract_mcu_image.py work/trvzb_v1.4.4.ota work

# Function count, vector table and peripheral registers of slot A
summary:
    python3 -I tools/firmware_map.py summary

# Disassemble the function at an image offset, e.g. `just dis 0x648c`
dis address:
    python3 -I tools/firmware_map.py dis {{address}}

# Call sites of one or more image offsets, e.g. `just calls 0x6c18`
calls +addresses:
    python3 -I tools/calls.py {{addresses}}

# Run the test suite (the golden-hash test needs `just fetch` first)
test:
    python3 -I -m pytest -q -p no:cacheprovider tests

# Build a release from releases/<id>.toml and check it reproduces the recorded hash
build release: fetch
    python3 -I tools/build_release.py releases/{{release}}.toml

# Verify a built release against stock: changed bytes, slot relocation, calls, versions
verify release:
    #!/usr/bin/env bash
    set -euo pipefail
    patch_arguments=()
    if [ -f work/releases/{{release}}.patch.json ]; then
        patch_arguments=(--patch-set work/releases/{{release}}.patch.json)
    fi
    python3 -I tools/verify_ota.py work/releases/{{release}}.ota --manifest releases/{{release}}.toml "${patch_arguments[@]}"

# Zigbee2MQTT local OTA index entry for a built, verified release
index release:
    python3 -I tools/ota_index_entry.py releases/{{release}}.toml

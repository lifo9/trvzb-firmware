# Stock firmware this repository's analysis is based on, pinned to the
# zigbee-OTA commit that added it so the bytes can never drift.
stock_ota_url := "https://raw.githubusercontent.com/Koenkk/zigbee-OTA/b8d50733ddbd0dcf3c9c0cb483e9362a5e2313cc/images/Sonoff/trvzb_v1.4.4.ota"
stock_ota_sha512 := "c7640e2a238ab9807b77d36abc4b9e58d64c99a8fe8ac8d6246f48760bc85a2aec5c446630c94dd2e85fe3fe088011eccc992e543183b74ede7e061ae6f324b3"

default:
    @just --list

# Download the stock 1.4.4 OTA into work/ and verify its checksum
fetch:
    mkdir -p work
    curl -sSfL -o work/trvzb_v1.4.4.ota "{{stock_ota_url}}"
    echo "{{stock_ota_sha512}}  work/trvzb_v1.4.4.ota" | shasum -a 512 -c -

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

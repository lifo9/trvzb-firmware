# Datapoints

The Zigbee SoC translates Zigbee attributes (mostly on the manufacturer cluster
`0xfc11`) into Tuya-style datapoints and sends them to the application MCU.
Frame layout as seen by the dispatcher: `[0]` datapoint id, `[1]` type,
`[2..3]` big-endian length, `[4..]` payload.

- Command dispatcher: `0x4f64`. A compare chain for 1, 2, 4, 7, 101–104 and
  111, then two GCC jump tables (see below) for 105–111 and 118–132.
- Report builder: `0x5af4`, with its own jump table based at datapoint 112.
- Type table: 42 entries at image `0x77ac`, read by `0x4e94`.

## Table

Confidence reflects the evidence: value range and scaling, the field written,
agreement between the command and report paths, and Zigbee2MQTT's attribute
list.

| dp | Type | Meaning | Handler / field | Confidence |
|---|---|---|---|---|
| 1 | bool | on/off | `0x6b54` | high |
| 2 | value | occupied heating setpoint, bounded by settings `+0xc`/`+0xd` | `0x6c18` | high |
| 3 | value | local temperature (report) | `0x6324` | high |
| 4 | enum | system mode / preset | `0x6460`, `0x6cb8` | medium-high |
| 7 | bool | child lock | settings `+0x1d` | medium-high |
| 8 | value | battery | — | medium |
| 9, 13, 14 | enum / bitmap / enum | fault and status reporting | — | low-medium |
| 101–107 | raw | weekly schedule, one 24-byte day each | `0x50ba` | high |
| 108 | value | temporary-mode trigger | `0x5078` | low |
| 109 | bool | — | settings `+0xf` | low |
| 110, 111 | value | 16-bit, scaled; 111 → settings `+0x13` | `0x5092`, `0x50a6` | low |
| 112, 113 | value | idle steps, closing steps (**report only**) | — | medium-high |
| 114, 115, 116 | value | opening-limit, closing-limit, motor-running voltages (**report only**) | — | medium-high |
| 117 | — | does not exist (absent from the type table) | — | high |
| 118 | bitmap | 7-bit per-day schedule enable mask | `0x5150` | medium |
| **119** | value | **valve opening degree**, 0–100 | settings `+0x15` | **high** |
| **120** | value | **valve closing degree**, 0–100 | settings `+0x16` | **high** |
| 121 | bool | open-window detection | `0x5192` → `0x6be8` | medium-high |
| 122 | value | external temperature input, −299..1000 in 0.1 °C | `0x6eb8` | high |
| 123 | value | duration, 1..600 in 100 ms units | settings `+0x18`/`+0x19` | medium |
| 124 | value | 1..10 | settings `+0xb` | low |
| 125 | value | heating-on hysteresis threshold, stored as `50 − v`; likely `temperature_accuracy` | settings `+7` | high (role) / medium (attribute) |
| 126 | value | heating-off hysteresis threshold, stored as `v − 50` | settings `+0x17` | high |
| 127 | enum | 0 or 2; selects one of two display icon layouts — likely screen direction | settings `+0xa` | low-medium |
| 128, 130 | raw | schedule / time payloads | `0x6cb8`, `0x6d70` | low-medium |
| 131 | raw | unresolved: the command jump table sends it to the default branch, yet a second analysis pass linked it to `0x3e64`, possibly through the report path | — | low |
| 129 | enum | — | `0x646c` | low |
| 132 | enum | — | settings `+0x12` | low |

Datapoints 112–116 are discarded by the command dispatcher, so the matching
Zigbee attributes (`0x6003`–`0x6007`) are effectively read-only even though
Zigbee2MQTT's TRVZB definition marks them writable.

## No direct valve-position datapoint

No datapoint commands the valve position. The only producer of the motor's
target percentage is `0x648c`, which takes a binary open/closed decision made
by the thermostat (see [control-flow.md](control-flow.md)). This is the gap
reported in Koenkk/zigbee2mqtt#33543.

## GCC jump tables

`bl 0xb20` is a bounds-checked byte switch helper. At the return address: one
byte holding the limit, then the table. Index = `r3`, clamped to the limit (the
last entry is the default). Target = table start + 2 × entry.

| Call | Index | Decoded |
|---|---|---|
| `0x4fbe` | dp − 105 | 105–107 → `0x50ba`, 108 → `0x5078`, 109 → `0x504e`, 110 → `0x5092`, default `0x518c` |
| `0x4fd0` | dp − 118 | 118 → `0x5150`, 119/120 → `0x516e`, 121 → `0x5192`, 122 → `0x519a`, 123 → `0x51c8`, 124 → `0x51e2`, 125 → `0x51f2`, 126 → `0x5200`, 127 → `0x520c`, 128 → `0x521c`, 130 → `0x5250`, 132 → `0x5278`, 129/131/default `0x518c` |

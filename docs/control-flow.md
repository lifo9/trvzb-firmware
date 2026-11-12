# Control flow

Addresses are image offsets (MCU address = offset + `0x400`).

## Thermostat → valve → motor

```
thermostat loop 0x6570 (every tick, while runtime +0xc is set)
  └─ decides open / closed by one of three control laws
       └─ 0x648c(open_flag) → target percent
            └─ 0x6d38(percent)            set + report
                 └─ 0x4a64(percent)       valve_set_target_percent
                      └─ valve task 0x48fc → 0x4ac0 apply
                           └─ 0x1c7c(direction, steps)   motor_move
```

### `0x648c` — the only producer of the target percentage

```
if open_flag == 1: return settings[0x15]          # valve opening degree, verbatim
else:              return 100 - settings[0x16]    # valve closing degree, complemented
```

Called only from the thermostat's decision sites (`0x67da`, `0x67e6`, `0x680c`,
`0x682a`) and from `0x6d38` (`0x6d42`). So the valve opening degree is not a
position command: it is the position used *whenever the thermostat decides to
open*. While the thermostat keeps deciding "open", it is applied continuously,
which is why pinning the setpoint above room temperature turns the opening
degree into a live position control.

### Control laws in `0x6570`

Selected by settings and runtime fields:

- runtime `+0x10` bit 2 → forces `0x6d38(0)`.
- settings `+0x1c == 1` and runtime `+4 == 2` → bang-bang with hardcoded 100 / 0:
  100 % if `setpoint × 5 > temperature`, 0 % if `setpoint × 5 + 20 ≤ temperature`.
- settings `+0x12 == 1` → hysteresis law through `0x648c`, thresholds settings
  `+7` (dp 125) and `+0x17` (dp 126).
- otherwise → PID `0x68c8(temperature, setpoint × 5)`, state at `0x2000075c`,
  clamped to 100, divided by 10.

Which law a given device runs is not proven statically; empirically, the
opening degree acts as a position control when the setpoint is pinned high.

### Valve layer — struct `0x200003a8`

`0x4a64(percent)`:

```
if valve[0xa] != 0: return              # a move is already pending
valve[6] = percent                      # target
if percent != valve[0xb]: valve[0xb] = percent; report
if valve[6] != valve[7]: valve[8] = 1; valve[0xa] = 1; kick valve task
```

`0x4ac0` converts with `steps = percent × valve[0x34] × 10 / 1000` for both target
and current position and calls `0x1c7c(2, delta)` to open or `0x1c7c(1, delta)`
to close.

Other entry points: `0x489c` get target percent, `0x4954` travel calibration
(1560 steps toward the end stop), `0x43c4`/`0x48b4` initialise and restore.
`0x1048` calls `0x4a64(80)` — a factory self-test path.

### Motor layer — struct `0x200002d6`

`0x1c7c(direction, steps)` writes the command and step count; `0x1aa4` is the
tick state machine that drives the outputs on ports `0x40040000`/`0x40040060`,
counts steps open-loop, and samples motor current/voltage on ADC channels
`0x20` and `0x16` into 4-deep rings. End-stop detection compares the filtered
voltage (`0x19bc`) against the calibrated thresholds. ADC HAL: `0x11d0`,
`0x120c`, `0x12ac`.

The exact pin mapping is unresolved: the drive-pattern tables at image
`0x7748`/`0x7750` are almost all zero, so the motor may be a DC motor with
brake/enable lines rather than a stepper.

## Knob and buttons

Input scanner `0x3e90` reads a bitmask from `0x1688` (bit = channel). Event
producer `0x3d8c`. The consumer is `0x2e38`–`0x31a6`, the combined
screen-refresh and key handler, run every tick, dispatching through the jump
table at `0x3062`:

| Event | Source | Meaning | Handler |
|---|---|---|---|
| 1 | button 0 | short press | `0x30dc` → on/off `0x6b54` |
| 2 | button 0 | hold / repeat | ignored |
| 3 | button 0 | long press | `0x30a4` → `0x6aec` |
| 4 | button 0 | very long press | ignored |
| **5** | knob channel 2 | **one detent up** | `0x3104` |
| **6** | knob channel 3 | **one detent down** | `0x3104` |
| 7–10 | button 1 | short / hold / long / very long | event 9 toggles settings `+0xa` 0 ↔ 2 |

Knob channels bypass the button debounce/hold state machine: one detent is
exactly one event, with no acceleration or repeat.

### The knob path, `0x3104`–`0x31a6`

1. If the screen is asleep (UI `+1` clear), the first detent only wakes it:
   set the flag and start a 1 s timer (`0x2a14`); the screen stays on 4 s.
2. Otherwise, at `0x314a`: read the pending value UI `+3` (re-seeded every pass
   from the setpoint getter `0x636c(0)`), increment for event 5 or decrement
   for event 6, clamp to settings `+0xc`..`+0xd` via `0x7148`, store it back.
3. Commit with `0x6c18(value, 1)`. The second argument marks a local change,
   which only delays the Zigbee report (2 s / 5 s); the setpoint applies at once.

## Display

- Framebuffer 8 bytes at `0x200002c3`, shadow at `+8`; `0xc00` compares and
  copies, `0xb9c` pushes to the panel, `0xd24` lights every segment (test).
- Renderer `0x32b0(position, character)`; `0x32a8(char_at_0, char_at_2)` draws
  two characters; `0x3398` draws a value in tenths as two digits.
- **Two 7-segment digits** (positions 0 and 2). No third digit, so 100 cannot
  be shown as a number. A half-step (`.5`) indicator exists as an icon bit.
- Segment bit map at image `0x7818`: `03 04 05 00 01 02 06` (a b c d e f g).
- Font: digits 0–9 at image `0x781f`, letters A–Z at `0x7829`, plus `-` and
  blank. Used today for `LC` (child lock), `OP` (open window), `OF` (off).
- No percent sign is driven anywhere in the firmware. Whether the glass has
  one is unknown; `0xd24`'s all-on test would show it.
- Two icon-bit layouts, chosen by settings `+0xa` (0 or 2), written by dp 127
  and toggled by button-1 event 9. This looks like a screen-direction setting.

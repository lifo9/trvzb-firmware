# Control flow

Addresses are image offsets (MCU address = offset + `0x400`).

## Thermostat → valve → motor

```
thermostat loop 0x6570 (each pass clears runtime +0xc and re-arms its timer
                         at datapoint 123 × 100 ms)
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

The motor is a stepper. `0x1aa4` loads its drive patterns through the literals
`0x7748` and `0x7750`, which are MCU addresses: the tables sit at image
`0x7348` and `0x7350` and hold the half-step sequence `08 0c 04 06 02 03 01 09`
and its reverse, one per direction.

## Knob and buttons

Physical controls: the knob (turn and press) and a configuration key under
the cover. Button 0 is the knob press, button 1 the configuration key. The
vendor manual (User Manual TRVZB EN V1.0) documents these gestures, matching
the events below:

| Gesture | Effect |
|---|---|
| knob press, short | switch auto ↔ manual |
| knob press, 3 s | child lock on/off (`LC`) |
| knob press, 3 s while the screen shows `OF` | **pairing mode for 3 minutes** |
| knob counter-clockwise past the minimum | off mode, `OF` (valve closed, frost protection on) |
| configuration key, 3 s | rotate the display 180° |
| knob press held while inserting batteries | factory reset (`FA`) |
| knob press, short, after installation | motor travel calibration ("debugging") |

Input scanner `0x3e90` reads a bitmask from `0x1688` (bit = channel). The event
producer `0x3d8c` gates input on the display state: while the screen sleeps
(`0xdf8`), events 1–6 wake it (`0xdb0`) and are dropped, and events 7–10 are
dropped without waking. So a first press or detent on a sleeping screen only
wakes it. Each UI screen calls the producer (six callers); the main screen is
`0x2e38`–`0x31a6`, the combined screen-refresh and key handler, reached only
through a pointer table and dispatching through the switch at `0x3064`:

| Event | Source | Meaning | Handler |
|---|---|---|---|
| 1 | button 0 | short press | `0x30dc` → toggles mode heat ↔ auto via `0x6b54`; never off |
| 2 | button 0 | hold / repeat | ignored |
| 3 | button 0 | long press | `0x30a4` → `0x6aec`: toggles the child lock (settings `+0x1d`) |
| 4 | button 0 | very long press | ignored |
| **5** | knob channel 2 | **one detent up** | `0x3104` |
| **6** | knob channel 3 | **one detent down** | `0x3104` |
| 7–10 | button 1 = configuration key, under the cover | short / hold / long / very long | event 9 (3 s press) toggles screen direction, settings `+0xa` 0 ↔ 2 |

`0x6b54` refuses some changes and returns without effect (verified under
emulation, `tests/test_emulator_stock_mode.py`):

- auto (2) while runtime `+0x12` (`time_synced`) is 0. Only `0x6ae0` sets it,
  from the time-sync handler, so auto is unavailable until the valve has
  received the time over Zigbee — for example right after a battery change;
- any change while today's byte in the per-weekday table at settings `+0x9d`
  is 0 (the weekday comes from the clock reader `0x1dc4`).

Knob channels bypass the button debounce/hold state machine: one detent is
exactly one event, with no acceleration or repeat.

`0x6b54(mode, is_local)` is the mode setter, writing settings `+0x1c`: 0 =
heat, 1 = off, 2 = auto (weekly schedule). Evidence: dp 1 maps on → 0 and
off → 1; the short press only ever swaps 0 and 2; and the setpoint setter
`0x6c18` keeps separate setpoints for mode 0 (`+0x1b`) and mode 2 (`+0x1a`).
Mode numbering: medium-high confidence.

### The knob path, `0x3104`–`0x31a6`

1. In auto mode the path returns at once (`0x314a`–`0x314e`): the stock knob
   does nothing in auto.
2. Otherwise: read the pending value UI `+3` (re-seeded every pass
   from the setpoint getter `0x636c(0)`), increment for event 5 or decrement
   for event 6, clamp between settings `+0xd` (minimum) and `+0xc` (maximum)
   via `0x7148`, store it back. In heat mode, turning below the minimum calls
   `0x6b54(1)`: off mode, shown as `OF`. In off mode, turning up sets the
   pending value to the minimum and calls `0x6b54(0)`: back to heat.
3. Commit with `0x6c18(value, 1)`. The second argument marks a local change:
   the setpoint is stored at once, but the settings save is deferred 2 s
   (`0x60a4`) and the thermostat re-evaluation 5 s. Applying a change at once
   takes `0x6b10(1)`, as the datapoint 119 handler does.

## Display

- Framebuffer 8 bytes at `0x200002c3`, shadow at `+8`; `0xc00` compares and
  copies, `0xb9c` pushes to the panel, `0xd24` lights every segment (test).
- Renderer `0x32b0(position, character)`; `0x32a8(left, right)` draws `left` at
  position 2 (the tens digit) and `right` at position 0, so child lock is
  `0x32a8('L', 'C')` and the patch's full-open marker is `0x32a8('F', 'U')`;
  `0x3398` draws a value in tenths as two digits.
- **Two 7-segment digits** (positions 0 and 2). No third digit, so 100 cannot
  be shown as a number. A half-step (`.5`) indicator exists as an icon bit.
- Segment bit map at image `0x7818`: `03 04 05 00 01 02 06` (a b c d e f g).
- Font: digits 0–9 at image `0x781f`, letters A–Z at `0x7829`, plus `-` and
  blank. Used today for `LC` (child lock), `OP` (open window), `OF` (off).
- No percent sign is driven anywhere in the firmware. Whether the glass has
  one is unknown; `0xd24`'s all-on test would show it.
- Screen direction: settings `+0xa` (0 or 2), written by dp 127 and toggled by
  a 3 s press on the configuration key (event 9), as the vendor manual
  documents. Verified under emulation in both slots: in direction 0 the
  framebuffer holds the left character's font glyph in byte 0 and the right in
  byte 2; in direction 2 each glyph is turned 180° (segments a↔d, b↔e, c↔f)
  and the two are swapped (`tests/test_emulator_stock_display.py`).

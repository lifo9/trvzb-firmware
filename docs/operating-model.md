# Operating model

How the patched valve is meant to behave inside a Home Assistant heating setup.
This describes the target once the knob patch is flashed; the stock firmware's
knob still moves the temperature setpoint.

## The setup it assumes

- **The valve's own thermostat is pinned open.** A Home Assistant automation
  holds `system_mode: heat`, the setpoint at its 35 °C maximum and
  `valve_closing_degree: 100`. The valve's internal thermostat then always
  decides "open", so `valve_opening_degree` (datapoint 119) is applied
  continuously as the live valve position (see
  [control-flow.md](control-flow.md)).
- **A Home Assistant thermostat computes the valve opening.** For example,
  Versatile Thermostat in valve mode writes its percentage to a helper. An
  automation mirrors that helper onto `valve_opening_degree`, but only when the
  helper *changes*, when the thermostat switches from off to heat, or when the
  valve rejoins the network.
- **The patched knob writes the same value.** Each detent steps
  `valve_opening_degree` by 10 % (clamped 0–100), applies it immediately, and
  reports it over Zigbee. The knob and the Home Assistant entity are two views
  of one value.

## Who controls the valve

Last writer wins. Nothing locks the value.

| Situation | Who sets the opening | Valve | Screen (when woken) | Lasts until |
|---|---|---|---|---|
| HA thermostat heating | HA thermostat, through the mirror | its computed % | that % | its next *different* computed value |
| Knob turned | knob, ±10 % per detent | new %, at once | new %, `FU` at 100 | HA thermostat's next different value, its off → heat switch, or the valve rejoining |
| Opening set in HA (slider) | user | set % | that % | same as the knob |
| HA thermostat switched off | it writes 0 % once, then stops | closed, until the knob or slider moves it | that % | the next knob or slider change, or the thermostat back on |
| HA thermostat back on (off → heat) | HA thermostat | its computed %, overriding any manual setting | that % | as the first row |
| Home Assistant, Zigbee2MQTT or the network down | knob only; it works locally | knob's % | knob's % | the system returns *and* the HA thermostat's value changes |
| Valve rejoins after an outage | mirror re-asserts the HA thermostat's value (if heating) | its computed % | that % | as the first row |
| Child lock on | nobody locally: the knob is ignored | unchanged | unchanged | lock off |
| Valve's own sensor ≥ 35 °C | valve's internal thermostat decides "closed" | closed (closing degree 100) | — | temperature drops |
| Open-window detection on and triggered | valve's internal logic | closed | `OP` | window event ends (detection is off in the reference setup) |

## Consequences

- **Overrides are not timed.** A manual setting holds only until the HA
  thermostat recomputes a different value, which can be one cycle (5 minutes)
  or hours. A fixed-length override (for example, hold for two hours, then hand
  back) is an HA-side change: detect a valve value that differs from the
  thermostat's helper, and pause the mirror for a while. Not built.
- **No feedback loop.** The knob's report updates the valve entity, never the
  thermostat's helper, so the mirror does not fire on it.
- **The pin automation stays quiet.** The patched knob no longer moves the
  setpoint, so nothing fights the pin.
- **The knob keeps working without Home Assistant.** It acts inside the valve.

## Open items for the patch

- **The child lock must be off** for the knob to work. The reference device has
  it on. A long press on button 0 (event 3) toggles it at the valve.
- **The short press must stop switching modes.** Stock, the dial press
  (button 0, event 1) toggles heat and auto. In auto the valve follows its
  weekly schedule (16–19 °C in the reference setup), so its thermostat closes
  the valve until the pin automation restores heat about 10 s later. The patch
  makes a short press only wake the screen and show the percentage.
- **Screen rotation needs no patch.** A 3 s press on the configuration key
  under the cover flips the display (event 9, settings `+0xa`).
- **The first detent only wakes the screen** (stock behaviour, kept). Turns
  after that change the value.

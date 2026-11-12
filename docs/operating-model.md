# Operating model

How the patched valve is meant to behave inside a Home Assistant heating setup.
This describes the target once the knob patch is flashed and the Home Assistant
automations are adapted; the stock firmware's knob still moves the temperature
setpoint.

## The setup it assumes

- **Heating on and off is one state, shared by the valve and HA.** The valve's
  own mode is either heat or off (`OF`), and a Home Assistant thermostat (for
  example Versatile Thermostat in valve mode) is either heating or off. Two
  automations keep them in step, in both directions: the valve switching to off
  turns the HA thermostat off, the HA thermostat switching off puts the valve in
  off, and likewise for on.
- **While heating, the valve's own thermostat is pinned open.** A Home Assistant
  automation holds the setpoint at its 35 °C maximum and `valve_closing_degree`
  at 100. The valve's internal thermostat then always decides "open", so
  `valve_opening_degree` (datapoint 119) is applied continuously as the live
  valve position (see [control-flow.md](control-flow.md)). The pin no longer
  forces heat mode; the mode follows the shared on/off state.
- **The HA thermostat computes the valve opening.** It writes its percentage to
  a helper, and an automation mirrors that helper onto `valve_opening_degree`,
  but only when the helper *changes*, when the thermostat switches from off to
  heat, or when the valve rejoins the network.
- **The patched dial writes the same value.** Each detent steps
  `valve_opening_degree` by 10 % (clamped 0–100), applies it immediately, and
  reports it over Zigbee. The dial and the Home Assistant entity are two views
  of one value. One detent below 0 % is off; one detent up from off is on.

## Controls at the valve

| Gesture | Patched behaviour |
|---|---|
| first detent or press on a sleeping screen | wake the screen, show the valve opening |
| dial clockwise | opening +10 %, up to 100 % (`FU`) |
| dial counter-clockwise | opening −10 %, down to `0` |
| dial counter-clockwise below `0` | heating off: valve mode off (`OF`), HA thermostat off |
| dial clockwise from `OF` | heating on: valve mode heat, HA thermostat on, its value re-applied |
| dial press, short | show the valve opening only (stock: switches auto ↔ manual) |
| dial press, 3 s | child lock on/off (stock) |
| dial press, 3 s while showing `OF` | pairing mode for 3 minutes (stock, kept deliberately: it is the documented way to re-pair) |
| configuration key under the cover, 3 s | rotate the display 180° (stock) |

## Who controls the valve

Last writer wins. Nothing locks the value.

| Situation | Who sets the opening | Valve | Screen (when woken) | Lasts until |
|---|---|---|---|---|
| Heating on, HA thermostat in charge | HA thermostat, through the mirror | its computed % | that % | its next *different* computed value |
| Dial turned while heating | dial, ±10 % per detent | new %, at once | new %, `FU` at 100 | HA thermostat's next different value, heating switched off and on, or the valve rejoining |
| Opening set in HA (slider) while heating | user | set % | that % | same as the dial |
| Heating switched off — at the dial (below `0`) or in HA | shared off state | closed: the valve's own off mode, closing degree 100 | `OF` | heating switched on again |
| Heating switched on — at the dial (up from `OF`) or in HA | HA thermostat | its computed %, replacing any earlier manual setting | that % | as the first row |
| Home Assistant, Zigbee2MQTT or the network down | dial only; it works locally, including off and on | dial's % or closed | dial's % or `OF` | the system returns; HA then adopts the valve's on/off state when the valve reports it |
| Valve rejoins after an outage | mirror re-asserts the HA thermostat's value (if heating) | its computed % | that % | as the first row |
| Child lock on | nobody locally: the dial is ignored | unchanged | `LC` | lock off |
| Valve's own sensor ≥ 35 °C | valve's internal thermostat decides "closed" | closed (closing degree 100) | — | temperature drops |
| Open-window detection on and triggered | valve's internal logic | closed | `OP` | window event ends (detection is off in the reference setup) |

## Consequences

- **Heating off means closed.** In off mode the valve's own thermostat holds it
  closed, so neither the dial nor the slider opens it; turning the dial up from
  `OF` switches heating back on instead.
- **Overrides are not timed.** A manual setting holds only until the HA
  thermostat recomputes a different value, which can be one cycle (5 minutes)
  or hours. A fixed-length override (for example, hold for two hours, then hand
  back) is an HA-side change: detect a valve value that differs from the
  thermostat's helper, and pause the mirror for a while. Not built.
- **No feedback loop on the opening.** The dial's report updates the valve
  entity, never the thermostat's helper, so the mirror does not fire on it.
- **The on/off sync must not ping-pong.** Each direction should act only when
  the two states disagree, so that the echo of a change does nothing.
- **The dial keeps working without Home Assistant.** It acts inside the valve.

## Open items for the patch

- **The child lock must be off** for the dial to work. The reference device has
  it on. A 3 s dial press toggles it at the valve.
- **The short press must stop switching modes.** Stock, the dial press
  (button 0, event 1) toggles heat and auto. In auto the valve follows its
  weekly schedule (16–19 °C in the reference setup), so its thermostat would
  close the valve. The patch makes a short press only wake the screen and show
  the percentage.
- **A long press in `OF` enters pairing mode.** Kept as stock: it takes a
  deliberate 3 s hold and is the documented way to re-pair. Whether pairing
  mode makes an already-joined valve leave the network is unverified.
- **Off and on from the dial already exist in stock firmware** (turning below
  the minimum calls the mode setter with off; turning up from off calls it with
  heat). The patch must keep these transitions while the dial steps the valve
  opening instead of the setpoint: below `0` → off, up from off → heat.
- **The first detent only wakes the screen** (stock behaviour, kept). Turns
  after that change the value.

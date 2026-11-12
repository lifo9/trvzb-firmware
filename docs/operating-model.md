# Operating model

How the patched valve is meant to behave inside a Home Assistant heating setup.
This describes the target once the knob patch is flashed and the Home Assistant
automations are adapted; the stock firmware's knob still moves the temperature
setpoint.

## Modes

The valve's own three modes carry the state, so it is visible on the screen and
survives Home Assistant being down. Auto is redefined from "follow the valve's
internal weekly schedule" to "the Home Assistant thermostat is in charge".

| Mode | Valve mode (`system_mode`) | Screen | Who sets the opening |
|---|---|---|---|
| **Auto** | `auto` | `Auto` icon on | the Home Assistant thermostat |
| **Manual** | `heat` | `Auto` icon off | the user, at the knob or in Home Assistant; HA stops writing |
| **Off** | `off` | `OF` | nobody; the valve is closed |

## Controls

| Action | Result |
|---|---|
| Turn or press while the screen is asleep | wake the screen and show the valve opening; nothing changes |
| Turn the knob | step the opening ±10 % (0–100, `FU` at 100) **and switch to manual** |
| Set the opening in Home Assistant (slider) | the valve moves; **switch to manual** |
| Short press, screen awake, in manual | **back to auto**: the thermostat's value is applied at once |
| Short press, screen awake, in auto | show the valve opening only |
| Turn one detent below `0` | **off**: valve closed, Home Assistant thermostat off |
| Turn clockwise from `OF` | **on, in auto**: Home Assistant thermostat on, its value applied |
| Switch the HA thermostat off / on | valve to off / to auto |
| Manual left alone | **falls back to auto at the room's next scheduled change** in Home Assistant |
| Knob press, 3 s | child lock on/off (`LC`), stock |
| Knob press, 3 s while showing `OF` | pairing mode for 3 minutes, stock (kept: the documented way to re-pair) |
| Configuration key under the cover, 3 s | rotate the display 180°, stock |

## The setup it assumes

- **Both auto and manual keep the valve's internal thermostat pinned open.** The
  setpoint is held at its 35 °C maximum in manual (heat) mode, and every weekly
  schedule slot is set to 35 °C so auto mode behaves the same.
  `valve_closing_degree` stays 100. The internal thermostat then always decides
  "open", so `valve_opening_degree` (datapoint 119) is the live valve position
  in both modes (see [control-flow.md](control-flow.md)), and the mode is purely
  a flag for who is in charge.
- **The HA thermostat writes only in auto.** It computes a percentage into a
  helper; an automation mirrors that helper onto `valve_opening_degree` while
  the valve is in auto, when the helper changes, when the valve enters auto, and
  when the valve rejoins the network in auto.
- **A slider change in auto means manual.** When `valve_opening_degree` changes
  to a value that differs from the thermostat's helper while the valve is in
  auto, an automation switches the valve to manual.
- **On/off is shared.** The valve entering off turns the HA thermostat off, and
  the HA thermostat switching off puts the valve in off; likewise for on, which
  lands in auto. Each direction acts only when the two disagree, so the echo of
  a change does nothing.
- **Manual falls back to auto at the room's next scheduled change.** All
  schedules live in Home Assistant, one per room; when a room's schedule
  changes its thermostat's target, a manual valve in that room returns to
  auto. The valve's internal weekly schedule is never used for scheduling.

## Several valves

The model is per valve. Each valve pairs with one Home Assistant thermostat
and that room's schedule; rooms may run different schedules. The automations
(mirror, manual detection, on/off sync, schedule fallback) should therefore
be written once and instantiated per valve — a blueprint or a templated
package taking the valve, its thermostat, its helper and its schedule — not
copied by hand. Each valve also needs the one-time setup: child lock off and
every weekly schedule slot at 35 °C.

## Consequences

- **Who is in charge is always visible**: the `Auto` icon. One short press hands
  control back.
- **Manual holds predictably**: until a press, the room's next scheduled
  change, or heating being switched off and on — never silently replaced by a recomputed
  thermostat value.
- **Heating off means closed.** In off mode the valve's own thermostat holds it
  closed; turning the knob up from `OF` switches heating back on in auto.
- **The knob works without Home Assistant**, including manual, off and on. When
  the system returns, Home Assistant adopts the valve's mode, since the state
  lives in the valve.

## Patch requirements

What the firmware must do differently from stock:

1. A detent on an awake screen steps `valve_opening_degree` ±10 % instead of the
   setpoint, applies and reports it, and sets the mode to heat (manual). This
   includes auto mode, where the stock knob path returns at once, so auto
   needs a new path rather than a changed one.
2. Below `0`, switch to off (stock already turns below the minimum into off).
3. Up from off, switch to auto (stock switches to heat).
4. A short press on an awake screen switches manual → auto, and does nothing in
   auto (stock toggles both ways).
5. A first detent or press on a sleeping screen only wakes it — already true in
   stock: the event producer drops inputs that wake the screen. No patch needed.
6. The woken screen shows the valve opening: `0`–`90`, `FU` at 100.

Unchanged: the child lock gesture, the pairing gesture in `OF`, screen rotation.

## Setup requirements outside the firmware

- **Child lock off.** The reference device has it on; a 3 s knob press toggles it.
- **Weekly schedule at 35 °C in every slot**, so auto mode stays pinned open.
- Home Assistant automations as described above, replacing the current pin
  (which forces heat mode) and mirror (which writes regardless of mode).

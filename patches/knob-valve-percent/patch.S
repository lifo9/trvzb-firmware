@ Knob sets the valve opening; explicit auto / manual / off modes.
@ Behaviour: docs/operating-model.md, "Patch requirements".
@
@ Valve modes (settings.mode): 0 heat = manual, 1 off, 2 auto = the Home
@ Assistant thermostat is in charge. The valve opening is datapoint 119,
@ settings.valve_opening_degree, a percentage.

    .syntax unified
    .thumb
    .include "stock_symbols.inc"

    .equ MODE_HEAT, 0
    .equ MODE_OFF, 1
    .equ MODE_AUTO, 2
    .equ KNOB_UP_EVENT, 5
    .equ STEP_PERCENT, 10
    .equ FULL_PERCENT, 100
    .equ IS_LOCAL, 1
    .equ VALVE_OPENING_DATAPOINT, 119
    @ datapoint_report_builder's second argument for a change made on the
    @ valve itself; the local temperature and open-window reports use it.
    .equ LOCAL_REPORT, 6
    @ setpoint_set waits as long before saving a local change, so a run of
    @ detents costs one flash write.
    .equ SAVE_DELAY_MILLISECONDS, 2000


@ --- Knob detent: replaces the stock setpoint step -------------------------
@ Entered from ui_main_screen's knob branch once the valve is running, with
@ r4 = event (5 up, 6 down), r5 = ui_edit, r6 = settings, and the frame of
@ push {r3-r7, lr} still on the stack.

    .section .site.knob_step, "ax"
knob_step:
    ldrb r0, [r6, #SETTINGS_MODE]
    cmp r0, #MODE_OFF
    beq knob_from_off
    ldrb r0, [r6, #SETTINGS_VALVE_OPENING_DEGREE]
    @ Datapoint 119 is stored unchecked, so treat anything above full as full.
    cmp r0, #FULL_PERCENT
    bls knob_opening_in_range
    movs r0, #FULL_PERCENT
knob_opening_in_range:
    cmp r4, #KNOB_UP_EVENT
    beq knob_up
    cmp r0, #0
    beq knob_to_off
    subs r0, r0, #STEP_PERCENT
    bge knob_store
    movs r0, #0
    b knob_store
knob_up:
    adds r0, r0, #STEP_PERCENT
    cmp r0, #FULL_PERCENT
    bls knob_store
    movs r0, #FULL_PERCENT
knob_store:
    bl set_valve_opening_from_knob
    b knob_return
knob_to_off:
    @ One detent below 0 % switches heating off.
    movs r0, #MODE_OFF
    movs r1, #IS_LOCAL
    bl mode_set
    b knob_return
knob_from_off:
    @ Down while off stays off; up switches on in auto. mode_set refuses auto
    @ until the clock has been synced over Zigbee, so fall back to heat:
    @ turning up must always bring heating on.
    cmp r4, #KNOB_UP_EVENT
    bne knob_return
    movs r0, #MODE_AUTO
    movs r1, #IS_LOCAL
    bl mode_set
    ldrb r0, [r6, #SETTINGS_MODE]
    cmp r0, #MODE_OFF
    bne knob_return
    movs r0, #MODE_HEAT
    movs r1, #IS_LOCAL
    bl mode_set
knob_return:
    pop {r3, r4, r5, r6, r7, pc}


@ --- Short press: manual -> auto only -------------------------------------
@ Stock toggles heat and auto. In auto, a press now only shows the screen;
@ heat -> auto is left as stock (it enters after this site, at the bl).

    .section .site.short_press_in_auto, "ax"
    b ui_main_screen__short_press_return
    nop


@ --- Woken screen: show the valve opening -----------------------------------

    .section .site.display_value_call, "ax"
    bl draw_valve_opening


@ --- Code cave ------------------------------------------------------------------

@ set_valve_opening_from_knob(percent): any knob change means manual.
    .section .site.cave_set_valve_opening, "ax"
    .thumb_func
set_valve_opening_from_knob:
    push {r4, lr}
    ldr r4, =SETTINGS
    strb r0, [r4, #SETTINGS_VALVE_OPENING_DEGREE]
    ldrb r0, [r4, #SETTINGS_MODE]
    cmp r0, #MODE_HEAT
    beq valve_opening_report
    movs r0, #MODE_HEAT
    movs r1, #IS_LOCAL
    bl mode_set
valve_opening_report:
    movs r0, #VALVE_OPENING_DATAPOINT
    movs r1, #LOCAL_REPORT
    bl datapoint_report_builder
    ldr r0, =SAVE_DELAY_MILLISECONDS
    bl settings_save_deferred
    @ Apply now if the valve is idle; while it is still moving from an earlier
    @ detent, retry every 3 s until it is idle, as the stock knob does.
    @ thermostat_kick would mark the thermostat loop to run regardless, and
    @ the loop records a target the moving valve drops, leaving the valve
    @ behind the shown opening after a quick spin.
    bl thermostat_apply_or_retry
    pop {r4, pc}
    .ltorg

@ draw_valve_opening(pending): the main screen's digits. Off keeps stock "OF".
@ Otherwise stock draw_setpoint runs first with the percentage rounded down to
@ an even number: an even value is a whole degree in its half-degree units, so
@ it clears the ".5" dot in either screen direction. Home Assistant may set any
@ percentage, so an odd one must not reach it. The digits are then redrawn as
@ the percentage, "FU" at 100.
    .section .site.cave_draw_valve_opening, "ax"
    .thumb_func
draw_valve_opening:
    push {r4, r5, r6, lr}
    ldr r4, =SETTINGS
    ldrb r1, [r4, #SETTINGS_MODE]
    cmp r1, #MODE_OFF
    beq draw_stock
    ldrb r5, [r4, #SETTINGS_VALVE_OPENING_DEGREE]
    cmp r5, #FULL_PERCENT
    bls draw_opening_in_range
    movs r5, #FULL_PERCENT
draw_opening_in_range:
    lsrs r0, r5, #1
    lsls r0, r0, #1
    bl draw_setpoint
    cmp r5, #FULL_PERCENT
    beq draw_full
    movs r0, r5
    movs r1, #10
    bl unsigned_divide
    adds r1, r1, #'0'
    cmp r0, #0
    beq draw_blank_tens
    adds r0, r0, #'0'
    b draw_digits
draw_blank_tens:
    movs r0, #' '
draw_digits:
    bl draw_two_characters
    pop {r4, r5, r6, pc}
draw_full:
    movs r0, #'F'
    movs r1, #'U'
    bl draw_two_characters
    pop {r4, r5, r6, pc}
draw_stock:
    bl draw_setpoint
    pop {r4, r5, r6, pc}
    .ltorg

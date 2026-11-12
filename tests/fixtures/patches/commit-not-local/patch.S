@ Test fixture: the knob commits its setpoint as a non-local change
@ (is_local 0 instead of 1). One observable difference from stock, used to
@ prove that emulator tests run the built patch set.

    .syntax unified
    .thumb
    .include "stock_symbols.inc"

    .section .site.knob_commit, "ax"
    movs r1, #0
    bl setpoint_set

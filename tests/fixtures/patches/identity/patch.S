@ Re-assembles two stock sites. Building this must reproduce the stock bytes
@ in both slots: a call (PC-relative, same in both) and a flash pointer
@ (absolute, 0x7c00 apart between the slots).

    .syntax unified
    .thumb
    .include "stock_symbols.inc"

    .section .site.knob_commit, "ax"
    movs r1, #1
    bl setpoint_set

    .section .site.digit_font_pointer, "a"
    .word digit_font

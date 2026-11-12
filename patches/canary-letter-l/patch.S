@ Acceptance canary: no code changes. The letter L is drawn with every
@ segment lit, so the child-lock screen reads "8C" instead of "LC". Seeing it
@ on the valve proves the application MCU accepted and runs this image.
@ Glyph bits: a=0 b=1 c=2 d=3 e=4 f=5 g=6; stock L is d+e+f (0x38).

    .syntax unified
    .thumb

    .equ ALL_SEGMENTS, 0x7f

    .section .site.letter_l_glyph, "a"
    .byte ALL_SEGMENTS

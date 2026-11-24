@ model_lock.s - the NAM Model lock: a press of knob 1 on a NAM block (NAM A2 Lite id 380, NAM Pedal id 768) locks / unlocks its
@ Model instead of resetting it to 1; while locked, turns of knob 1 on that block are ignored (editor and capture picks over
@ USB are not events and still change it).
@
@ A pre-filter in front of dm_event: the UI event post (0x2808b668) is a trampoline `ldr.w pc, [pc]; .word target`; this
@ module's address replaces that word and `ml_target` (set at install) is the old one. Every event comes through here, in the
@ posting task's context, with r0 the event {u8 class, u8 code, -, -, u32 value}, lr the poster's return.
@
@   class 1 (key) code 12 = knob 1 press (value 1 press, 2 release, 3 release before a hold, 5 hold)
@   class 2 (knob) code 0 = knob 1 turned
@
@ It acts only when all of these hold, otherwise the event goes on untouched:
@   - the home screen is on top (screens list node 0 flagged, 0x281737d8 bit 0): the Menu button screen moves the flag away
@   - not in the Action menu / move mode (byte 0x281852b4 is 0 there: 1 in those, where knob 1 means Copy Block ...)
@   - the selected block (SEL_BLOCK) is a NAM block (preset id 380 or 768)
@ A press (value 1) toggles the block's lock bit and every value of the press is swallowed (the stock resets on one of them);
@ a turn of a locked block is swallowed. The lock bits (by block id 0..63) are runtime only and cleared when the stored preset
@ the firmware is on changes (0x281ee64c).
@ Register use: r1, r2, r3, r12 before the first push; the stock calls run at an 8-byte aligned sp.
.syntax unified
.thumb

.equ SCREEN0, 0x281737d8            @ the screens list's first node: bit 0 = the home screen is the active one
.equ STATE60, 0x281852b4            @ the home screen object's byte +0x60: 1 in the Action menu / move mode
.equ STORED_WORD, 0x281ee64c        @ setlist * 128 + preset the firmware is on
.equ SEL_BLOCK, 0x281043e9          @ (out byte) -> the selected block's id
.equ MEMSET, 0x28155f35             @ (dst, byte, n)
.equ PRESET_OBJ, 0x28114a1d         @ () -> the current preset
.equ BLOCK_DESC, 0x28148ccd         @ (preset, block id, desc 0x34 B) -> < 0 when none
.equ PID_NAM, 380
.equ PID_PEDAL, 768
.equ KEY_KNOB1, 12

.global ml_entry
.text
ml_entry:
    ldrb r1, [r0]
    cmp r1, #1
    beq ml_key
    cmp r1, #2
    bne ml_pass
    ldrb r1, [r0, #1]               @ a knob turn: knob 1 only (index 0)
    cmp r1, #0
    bne ml_pass
    movs r3, #0                     @ r3: the kind, 0 a turn, 1 a press
    b ml_chk
ml_key:
    ldrb r1, [r0, #1]
    cmp r1, #KEY_KNOB1
    bne ml_pass
    movs r3, #1
ml_chk:
    ldr r1, =SCREEN0
    ldr r1, [r1]
    lsls r1, r1, #31
    bpl ml_pass                     @ not the home screen
    ldr r1, =STATE60
    ldrb r1, [r1]
    cmp r1, #0
    bne ml_pass                     @ the Action menu / move mode
    push {r0, r3, r4, r5, r6, lr}
    sub sp, #0x40                   @ [sp] the block descriptor (0x34 B), [sp, #0x38] SEL_BLOCK's out byte
    add r0, sp, #0x38
    ldr r3, =SEL_BLOCK
    blx r3
    mov r4, r0                      @ the block id
    cmp r4, #63
    bhi ml_out
    mov r0, sp
    movs r1, #0
    movs r2, #0x34
    ldr r3, =MEMSET
    blx r3
    ldr r3, =PRESET_OBJ
    blx r3
    mov r1, r4
    mov r2, sp
    ldr r3, =BLOCK_DESC
    blx r3
    cmp r0, #0
    blt ml_out                      @ no block
    ldr r0, [sp]
    lsrs r0, r0, #8
    ldrb r1, [sp, #4]
    orr r0, r0, r1, lsl #24         @ the preset id
    movw r1, #PID_NAM
    cmp r0, r1
    beq ml_nam
    movw r1, #PID_PEDAL
    cmp r0, r1
    bne ml_out                      @ not a NAM block
ml_nam:
    adr.w r5, ml_data
    ldr r1, =STORED_WORD
    ldr r1, [r1]
    ldr r0, [r5, #12]
    cmp r0, r1
    beq ml_same
    str r1, [r5, #12]               @ another stored preset: every lock goes
    movs r0, #0
    str r0, [r5, #4]
    str r0, [r5, #8]
ml_same:
    and r2, r4, #31
    movs r6, #1
    lsls r6, r6, r2                 @ this block's bit
    lsrs r0, r4, #5
    lsls r0, r0, #2
    add r5, r0
    adds r5, #4                     @ its lock word
    ldr r1, [r5]
    ldr r3, [sp, #0x44]             @ the kind
    cbnz r3, ml_press
    tst r1, r6                      @ a turn: swallowed when locked
    beq ml_out
    b ml_swallow
ml_press:
    ldr r0, [sp, #0x40]             @ the event
    ldr r0, [r0, #4]                @ its value
    cmp r0, #1
    bne ml_swallow                  @ a release / hold of the press: swallowed, no toggle
    eors r1, r6
    str r1, [r5]
ml_swallow:
    add sp, #0x40
    pop {r0, r3, r4, r5, r6, lr}
    movs r0, #0
    bx lr
ml_out:
    add sp, #0x40
    pop {r0, r3, r4, r5, r6, lr}
ml_pass:
    adr.w r12, ml_data
    ldr pc, [r12]                   @ the old target (dm_event)

.ltorg
.balign 4
.org 0x100
ml_data:
    .word 0                         @ +0 the target the trampoline had (thumb address), set at install
    .word 0                         @ +4 lock bits, block ids 0..31
    .word 0                         @ +8 lock bits, block ids 32..63
    .word 0xffffffff                @ +12 the stored preset the bits belong to

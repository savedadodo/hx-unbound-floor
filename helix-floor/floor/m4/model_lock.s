@ model_lock.s - the NAM Model lock: a press of knob 1 on a NAM block (NAM A2 Lite id 380, NAM Pedal id 768) locks / unlocks its
@ Model instead of resetting it to 1; while locked, turns of knob 1 on that block are ignored (editor and capture picks over
@ USB are not events and still change it), and a small padlock shows beside the "Model" label at the bottom of the screen.
@
@ Two entry points (a jump table at the start: +0 the event filter, +4 the paint wrapper):
@
@ ml_event: a pre-filter in front of dm_event. The UI event post (0x2808b668) is a trampoline `ldr.w pc, [pc]; .word target`;
@   this module's address replaces that word and ml_data+0 is the old one (set at install). Every event comes through here, in
@   the posting task's context: r0 the event {u8 class, u8 code, -, -, u32 value}, lr the poster's return.
@     class 1 (key) code 12 = knob 1 press (value 1 press, 2 release, 3 release before a hold, 5 hold)
@     class 2 (knob) code 0 = knob 1 turned
@   It acts only when all of these hold, otherwise the event goes on untouched:
@     - the home screen is on top (screens list node 0 flagged, 0x281737d8 bit 0): the Menu button screen moves the flag away
@     - not in the Action menu / move mode (byte 0x281852b4 is 0 there: 1 in those, where knob 1 means Copy Block ...)
@     - the selected block (SEL_BLOCK) is a NAM block (preset id 380 or 768)
@   A press (value 1) toggles the block's lock bit and every value of the press is swallowed (the stock resets on one of them);
@   a turn of a locked block is swallowed. The lock bits (by block id 0..63) are runtime only and cleared when the stored preset
@   the firmware is on changes (0x281ee64c).
@
@   A press also draws / wipes the padlock itself in both buffers (the stock paints only what changed, and a lock toggle changes
@   no widget).
@ ml_paint: the home view's paint (r0 the frame, r1 the view; dm_paint) wrapped: the old paint (ml_data+16) runs, then, in the
@   same guards, the padlock is drawn in this frame buffer when the selected NAM block is locked, or wiped when this buffer holds
@   one and the block is not locked any more (the stock panel only repaints what changed, and the frame has two buffers drawn in
@   turn: ml_data+20 keeps a bit for each). Left the home screen / a non-NAM block: the bit is dropped, nothing is wiped (the
@   stock repaints the panel then).
@ Register use: the stock calls run at an 8-byte aligned sp.
.syntax unified
.thumb

.equ SCREEN0, 0x281737d8            @ the screens list's first node: bit 0 = the home screen is the active one
.equ STATE60, 0x281852b4            @ the home screen object's byte +0x60: 1 in the Action menu / move mode
.equ STORED_WORD, 0x281ee64c        @ setlist * 128 + preset the firmware is on
.equ SEL_BLOCK, 0x281043e9          @ (out byte) -> the selected block's id
.equ MEMSET, 0x28155f35             @ (dst, byte, n)
.equ PRESET_OBJ, 0x28114a1d         @ () -> the current preset
.equ BLOCK_DESC, 0x28148ccd         @ (preset, block id, desc 0x34 B) -> < 0 when none
.equ FB0, 0x285a3f50                @ the first frame buffer
.equ FB1, 0x2865f750                @ the second (FB0 + 0xbb800)
.equ PID_NAM, 380
.equ PID_PEDAL, 768
.equ KEY_KNOB1, 12
.equ PAD_X, 104                     @ the padlock: 9 x 10 cells of 2 x 2 pixels, right of the "Model" label (x 31..99, y 446..470)
.equ PAD_Y, 448
.equ PAD_COLOUR, 0x7fff
.equ ROW, 1600                      @ bytes a frame row

.text
    b.w ml_event
    b.w ml_paint

@ ml_pid: -> r0 the selected block's id if it is a NAM block (380 / 768), else -1. Clobbers r1-r3, r12.
ml_pid:
    push {r4, lr}
    sub sp, #0x40                   @ [sp] the block descriptor (0x34 B), [sp, #0x38] SEL_BLOCK's out byte
    add r0, sp, #0x38
    ldr r3, =SEL_BLOCK
    blx r3
    mov r4, r0
    cmp r4, #63
    bhi ml_pid_no
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
    blt ml_pid_no                   @ no block
    ldr r0, [sp]
    lsrs r0, r0, #8
    ldrb r1, [sp, #4]
    orr r0, r0, r1, lsl #24         @ the preset id
    movw r1, #PID_NAM
    cmp r0, r1
    beq ml_pid_yes
    movw r1, #PID_PEDAL
    cmp r0, r1
    bne ml_pid_no
ml_pid_yes:
    mov r0, r4
    b ml_pid_end
ml_pid_no:
    mvn r0, #0
ml_pid_end:
    add sp, #0x40
    pop {r4, pc}

@ ml_guard: -> r0 1 when the home screen is on top and it is not in the Action menu / move mode, else 0. Clobbers r1.
ml_guard:
    ldr r1, =SCREEN0
    ldr r1, [r1]
    lsls r1, r1, #31
    bpl ml_guard_no
    ldr r1, =STATE60
    ldrb r1, [r1]
    cmp r1, #0
    bne ml_guard_no
    movs r0, #1
    bx lr
ml_guard_no:
    movs r0, #0
    bx lr

@ ml_bit: r0 a block id (0..63) -> r5 the address of its lock word, r6 its bit; clears every lock first when another stored
@ preset is on (ml_data+12). Clobbers r0-r3.
ml_bit:
    adr.w r5, ml_data
    ldr r1, =STORED_WORD
    ldr r1, [r1]
    ldr r2, [r5, #12]
    cmp r2, r1
    beq ml_bit_same
    str r1, [r5, #12]               @ another stored preset: every lock goes
    movs r2, #0
    str r2, [r5, #4]
    str r2, [r5, #8]
ml_bit_same:
    and r2, r0, #31
    movs r6, #1
    lsls r6, r6, r2
    lsrs r0, r0, #5
    lsls r0, r0, #2
    add r5, r0
    adds r5, #4
    bx lr

ml_event:
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
    push {r0, r3, r4, r5, r6, lr}
    bl ml_guard
    cbz r0, ml_out
    bl ml_pid
    cmp r0, #0
    blt ml_out                      @ not a NAM block
    bl ml_bit
    ldr r1, [r5]
    ldr r3, [sp, #4]                @ the kind
    cbnz r3, ml_press
    tst r1, r6                      @ a turn: swallowed when locked
    beq ml_out
    b ml_swallow
ml_press:
    ldr r0, [sp]                    @ the event
    ldr r0, [r0, #4]                @ its value
    cmp r0, #1
    bne ml_swallow                  @ a release / hold of the press: swallowed, no toggle
    eors r1, r6
    str r1, [r5]
    movs r2, #0                     @ the stock paints only what changed and toggling changes no widget: draw (or wipe) the
    tst r1, r6                      @ padlock now, in both frame buffers; ml_paint keeps it up after a stock repaint
    it ne
    movne r2, #1
    ldr r4, =FB0
    bl ml_blit
    ldr r4, =FB1
    bl ml_blit
    movs r0, #0
    cbz r2, ml_nodraw
    movs r0, #3
ml_nodraw:
    adr.w r12, ml_data
    str r0, [r12, #20]
ml_swallow:
    pop {r0, r3, r4, r5, r6, lr}
    movs r0, #0
    bx lr
ml_out:
    pop {r0, r3, r4, r5, r6, lr}
ml_pass:
    adr.w r12, ml_data
    ldr pc, [r12]                   @ the old target (dm_event)

ml_paint:                           @ r0 the frame, r1 the home view
    push {r4, r5, r6, r7, r8, lr}
    mov r4, r0
    adr.w r12, ml_data
    ldr r3, [r12, #16]
    blx r3                          @ the old paint
    mov r8, r0                      @ its result
    ldr r0, =FB0
    subs r0, r4, r0
    movs r7, #1                     @ this buffer's bit in ml_data+20: 1 the first, 2 the second
    cmp r0, #0
    it ne
    movne r7, #2
    bl ml_guard
    cbz r0, ml_pdrop
    bl ml_pid
    cmp r0, #0
    blt ml_pdrop
    bl ml_bit
    ldr r1, [r5]
    adr.w r12, ml_data
    ldr r0, [r12, #20]
    tst r1, r6
    beq ml_punlocked
    orr r0, r0, r7                  @ locked: the padlock, drawn every paint (the stock may have repainted the panel)
    str r0, [r12, #20]
    movs r2, #1
    bl ml_blit
    b ml_pend
ml_punlocked:
    tst r0, r7
    beq ml_pend                     @ nothing of ours in this buffer
    bic r0, r0, r7
    str r0, [r12, #20]
    movs r2, #0
    bl ml_blit                      @ wipe it
    b ml_pend
ml_pdrop:
    adr.w r12, ml_data
    ldr r0, [r12, #20]
    bic r0, r0, r7
    str r0, [r12, #20]
ml_pend:
    mov r0, r8
    pop {r4, r5, r6, r7, r8, pc}

@ ml_blit: r4 the frame, r2 1 to draw the padlock, 0 to wipe its rectangle. Clobbers r0-r3, r12.
ml_blit:
    push {r5, r6, r7, lr}
    adr.w r7, ml_pad
    movs r5, #0                     @ row
ml_br:
    ldrh r6, [r7, r5, lsl #1]
    movs r3, #0                     @ column
ml_bc:
    movs r0, #0
    cbz r2, ml_bcol                 @ wiping: black
    rsb r1, r3, #8
    lsr r1, r6, r1
    ands r1, r1, #1
    beq ml_bcol
    movw r0, #PAD_COLOUR
ml_bcol:
    lsls r1, r5, #1
    add.w r1, r1, #PAD_Y
    movw r12, #800
    mul r1, r1, r12
    lsls r12, r3, #1
    add r1, r12
    add r1, #PAD_X
    lsls r1, r1, #1
    add r1, r4
    strh r0, [r1]
    strh r0, [r1, #2]
    strh r0, [r1, #ROW]
    strh r0, [r1, #(ROW + 2)]
    adds r3, #1
    cmp r3, #9
    bne ml_bc
    adds r5, #1
    cmp r5, #10
    bne ml_br
    pop {r5, r6, r7, pc}

.ltorg
.balign 4
ml_pad:                             @ the padlock, 9 bits a row (bit 8 the left column), 10 rows
    .hword 0x07c, 0x0c6, 0x082, 0x082, 0x1ff, 0x1ff, 0x1ef, 0x1ef, 0x1ff, 0x1ff
.balign 4
.org 0x300
ml_data:
    .word 0                         @ +0 the event target the trampoline had (thumb address), set at install
    .word 0                         @ +4 lock bits, block ids 0..31
    .word 0                         @ +8 lock bits, block ids 32..63
    .word 0xffffffff                @ +12 the stored preset the bits belong to
    .word 0                         @ +16 the paint the home view had (thumb address), set at install
    .word 0                         @ +20 bit 0 / 1: the padlock is drawn in the first / second frame buffer

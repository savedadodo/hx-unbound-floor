"""Emulator test of m4/model_lock.bin: the NAM Model lock (a pre-filter in front of dm_event). Run directly:

  python test_model_lock.py

The stock calls (SEL_BLOCK, PRESET_OBJ, BLOCK_DESC, memset) are stubs; every call must come at an 8-byte aligned sp.
"""
import os
import struct

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
                               UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_SP, UC_ARM_REG_PC)

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = open(os.path.join(HERE, "m4", "model_lock.bin"), "rb").read()
BASE = 0x29F22000
TARGET = 0x1001F82F                        # the old dm_event (thumb)
RET = 0x20000101
SCREEN0, STATE60, STORED = 0x281737D8, 0x281852B4, 0x281EE64C
STUBS = {0x281043E8: "sel", 0x28155F34: "memset", 0x28114A1C: "obj", 0x28148CC8: "desc_unused", 0x28148CCC: "desc"}
DATA = BASE + 0x300
OLDPAINT = 0x20000200               # a stub of the old paint: movs r0, #0x77; bx lr
FB0, PAD_X, PAD_Y = 0x285A3F50, 104, 448
PAD = [0x07C, 0x0C6, 0x082, 0x082, 0x1FF, 0x1FF, 0x1EF, 0x1EF, 0x1FF, 0x1FF]


def machine():
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    for b, s in ((0x10000000, 0x100000), (0x28000000, 0x2000000), (0x20000000, 0x10000)):
        mu.mem_map(b, s)
    mu.mem_write(BASE, CODE)
    mu.mem_write(DATA, struct.pack("<I", TARGET | 1))
    mu.mem_write(DATA + 16, struct.pack("<I", OLDPAINT | 1))
    mu.mem_write(OLDPAINT, b"w pG")
    for a in list(STUBS) + [TARGET, RET - 1]:
        mu.mem_write(a, b"\x70\x47")
    st = {"calls": [], "sel": 5, "pids": {5: 380}, "ev": 0x20001000}

    def hook(mu, addr, size, _):
        name = STUBS.get(addr)
        if not name:
            return
        assert mu.reg_read(UC_ARM_REG_SP) % 8 == 0, f"{name}: sp not 8-byte aligned"
        st["calls"].append(name)
        if name == "sel":
            mu.reg_write(UC_ARM_REG_R0, st["sel"])
        elif name == "obj":
            mu.reg_write(UC_ARM_REG_R0, 0x20006000)
        elif name == "desc":
            bid, d = mu.reg_read(UC_ARM_REG_R1), mu.reg_read(UC_ARM_REG_R2)
            pid = st["pids"].get(bid)
            if pid is None:
                mu.reg_write(UC_ARM_REG_R0, 0xFFFFFFFF)
                return
            mu.mem_write(d, struct.pack("<I", (pid << 8) & 0xFFFFFFFF) + bytes([pid >> 24]))
            mu.reg_write(UC_ARM_REG_R0, 0)

    mu.hook_add(UC_HOOK_CODE, hook)
    mu.mem_write(SCREEN0, struct.pack("<I", 1))
    mu.mem_write(STATE60, b"\x00")
    mu.mem_write(STORED, struct.pack("<I", 9))

    def send(cls, code, value=0):
        """Post one event; returns "pass" (continued at the old target, r0 the event, lr the poster's) or "swallow"."""
        mu.mem_write(st["ev"], bytes([cls, code, 0, 0]) + struct.pack("<I", value))
        st["calls"].clear()
        regs = {UC_ARM_REG_R0: st["ev"], UC_ARM_REG_LR: RET, UC_ARM_REG_SP: 0x20008000, UC_ARM_REG_R4: 0x44, UC_ARM_REG_R5: 0x55,
                UC_ARM_REG_R6: 0x66}
        for r, v in regs.items():
            mu.reg_write(r, v)
        # run until the old target (a pass) or the poster's return address (a swallow)
        stop = {TARGET & ~1, RET & ~1}
        seen = {"hit": None}

        def stophook(mu, addr, size, _):
            if addr in stop and seen["hit"] is None:
                seen["hit"] = addr
                mu.emu_stop()
        h = mu.hook_add(UC_HOOK_CODE, stophook)
        mu.emu_start(BASE | 1, 0, count=100000)
        mu.hook_del(h)
        assert seen["hit"] is not None, "ran away"
        if seen["hit"] == TARGET & ~1:
            assert mu.reg_read(UC_ARM_REG_R0) == st["ev"], "a pass must keep r0 (the event)"
            assert mu.reg_read(UC_ARM_REG_LR) == RET, "a pass must keep the poster's lr"
            assert mu.reg_read(UC_ARM_REG_SP) == 0x20008000, "a pass must leave sp as it came"
            for r, v in ((UC_ARM_REG_R4, 0x44), (UC_ARM_REG_R5, 0x55), (UC_ARM_REG_R6, 0x66)):
                assert mu.reg_read(r) == v, "a pass must keep r4-r6"
            return "pass"
        assert mu.reg_read(UC_ARM_REG_SP) == 0x20008000
        for r, v in ((UC_ARM_REG_R4, 0x44), (UC_ARM_REG_R5, 0x55), (UC_ARM_REG_R6, 0x66)):
            assert mu.reg_read(r) == v, "a swallow must keep r4-r6"
        assert mu.reg_read(UC_ARM_REG_R0) == 0
        return "swallow"

    def bits():
        return struct.unpack("<3I", bytes(mu.mem_read(DATA + 4, 12)))

    def paint(fb):
        """The wrapped home paint: returns the old paint's result; the old paint must run first, once, with the frame and view."""
        st["calls"].clear()
        st["old"] = []
        for r, v in ((UC_ARM_REG_R0, fb), (UC_ARM_REG_R1, 0x2818C21C), (UC_ARM_REG_LR, RET), (UC_ARM_REG_SP, 0x20008000),
                     (UC_ARM_REG_R4, 0x44), (UC_ARM_REG_R5, 0x55), (UC_ARM_REG_R6, 0x66), (UC_ARM_REG_R7, 0x77), (UC_ARM_REG_R8, 0x88)):
            mu.reg_write(r, v)
        done = {"hit": False}

        def hk(mu, addr, size, _):
            if addr == OLDPAINT:
                st["old"].append((mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1)))
            if addr == RET & ~1 and not done["hit"]:
                done["hit"] = True
                mu.emu_stop()
        h = mu.hook_add(UC_HOOK_CODE, hk)
        mu.emu_start((BASE + 4) | 1, 0, count=20000)
        mu.hook_del(h)
        assert done["hit"], "ran away"
        assert st["old"] == [(fb, 0x2818C21C)], st["old"]
        assert mu.reg_read(UC_ARM_REG_SP) == 0x20008000
        for r, v in ((UC_ARM_REG_R4, 0x44), (UC_ARM_REG_R5, 0x55), (UC_ARM_REG_R6, 0x66), (UC_ARM_REG_R7, 0x77), (UC_ARM_REG_R8, 0x88)):
            assert mu.reg_read(r) == v, "the paint wrapper must keep r4-r8"
        return mu.reg_read(UC_ARM_REG_R0)

    return mu, st, send, bits, paint


def press(send, code=12):
    """One whole press of a knob: press, release before hold... as the firmware posts it (1 press, 2 release)."""
    return [send(1, code, 1), send(1, code, 2)]


mu, st, send, bits, paint = machine()
# nothing locked: a turn of knob 1 passes; unrelated events pass without stock calls
assert send(2, 0, 4) == "pass"
assert send(1, 2, 1) == "pass" and st["calls"] == []                  # Home key: untouched, no stock call
assert send(2, 3, 4) == "pass" and st["calls"] == []                  # knob 4 turn
assert send(1, 13, 1) == "pass" and st["calls"] == []                 # knob 2 press
assert send(0, 12, 1) == "pass" and send(3, 12, 1) == "pass"           # other classes

# a press of knob 1 on a NAM block locks it: both events swallowed, the bit set, no reset reaches the stock
assert press(send) == ["swallow", "swallow"]
assert bits()[0] == 1 << 5 and bits()[1:] == (0, 9), bits()
assert send(1, 12, 5) == "swallow"                                     # a hold of the press: swallowed too
assert send(1, 12, 3) == "swallow"
assert bits()[0] == 1 << 5                                            # ... and does not toggle
# locked: knob 1 turns swallowed, other knobs pass
assert send(2, 0, 4) == "swallow" and send(2, 0, -4 & 0x3F) == "swallow"
assert send(2, 1, 4) == "pass"
# the second press unlocks; turns pass again
assert press(send) == ["swallow", "swallow"] and bits()[0] == 0
assert send(2, 0, 4) == "pass"

# the NAM Pedal block (768), another block id (path B slot 2: id 22 and 40: the second bit word)
st["pids"] = {5: 380, 22: 768, 40: 380, 7: 1}
st["sel"] = 22
assert press(send) == ["swallow", "swallow"] and bits()[0] == 1 << 22
st["sel"] = 40
assert press(send) == ["swallow", "swallow"] and bits() == (1 << 22, 1 << 8, 9), bits()
assert send(2, 0, 4) == "swallow"
st["sel"] = 22
assert send(2, 0, 4) == "swallow"
# a block that is not a NAM block: everything passes, nothing set
st["sel"] = 7
assert send(1, 12, 1) == "pass" and send(2, 0, 4) == "pass" and bits() == (1 << 22, 1 << 8, 9)
# no block at the selected id: pass
st["sel"] = 30
assert send(1, 12, 1) == "pass"
# a block id past the bit range: pass
st["sel"] = 70
assert send(1, 12, 1) == "pass"

# guards: not the home screen (the Menu button screen), in the Action menu / move mode: pass, locked or not
st["sel"] = 22
mu.mem_write(SCREEN0, struct.pack("<I", 0))
assert send(1, 12, 1) == "pass" and send(2, 0, 4) == "pass"
mu.mem_write(SCREEN0, struct.pack("<I", 3))                            # bit 0 set with other bits: the home screen
assert send(2, 0, 4) == "swallow"
mu.mem_write(STATE60, b"\x01")
assert send(1, 12, 1) == "pass" and send(2, 0, 4) == "pass"
assert bits() == (1 << 22, 1 << 8, 9)                                  # the menu press did not toggle
mu.mem_write(STATE60, b"\x00")
assert send(2, 0, 4) == "swallow"

# another stored preset clears every lock
mu.mem_write(STORED, struct.pack("<I", 10))
assert send(2, 0, 4) == "pass" and bits() == (0, 0, 10), bits()
assert press(send) == ["swallow", "swallow"] and bits() == (1 << 22, 0, 10)
print("model lock: knob 1 press toggles on NAM / Pedal blocks, turns swallowed when locked, guards (screen, menu, block kind), "
      "bit words, clears on a preset change, registers kept: OK")

# ---- the padlock: drawn by the press itself in both frame buffers, kept up by the paint wrapper
FB1 = FB0 + 0xBB800
BLACK = [bytes(36)] * 20


def region(fb):
    return [bytes(mu.mem_read(fb + ((PAD_Y + y) * 800 + PAD_X) * 2, 18 * 2)) for y in range(20)]


def pad_pixels(on):
    rows = []
    for y in range(20):
        r = b""
        for x in range(18):
            c, rr = x // 2, y // 2
            bit = (PAD[rr] >> (8 - c)) & 1 if on else 0
            r += struct.pack("<H", 0x7FFF if bit else 0)
        rows.append(r)
    return rows


def plain(v):
    return [struct.pack("<H", v) * 18] * 20


def fill(fb, v):
    for y in range(-2, 22):
        mu.mem_write(fb + ((PAD_Y + y) * 800 + PAD_X - 4) * 2, struct.pack("<H", v) * 26)


def data20():
    return struct.unpack("<I", bytes(mu.mem_read(DATA + 20, 4)))[0]


mu, st, send, bits, paint = machine()
st["pids"] = {5: 380, 22: 768, 7: 1}
st["sel"] = 5
fill(FB0, 0x1234)
fill(FB1, 0x1234)
# unlocked NAM block: the old paint runs, its result is returned, nothing is drawn, nothing to wipe
assert paint(FB0) == 0x77 and region(FB0) == plain(0x1234) and data20() == 0
# a press locks it: both buffers get the padlock at once (no paint needed), both bits set; the pixels around it are untouched
press(send)
assert region(FB0) == pad_pixels(True) and region(FB1) == pad_pixels(True) and data20() == 3
assert bytes(mu.mem_read(FB0 + (PAD_Y * 800 + PAD_X - 1) * 2, 2)) == struct.pack("<H", 0x1234)       # one pixel left of it
assert bytes(mu.mem_read(FB1 + ((PAD_Y + 20) * 800 + PAD_X) * 2, 2)) == struct.pack("<H", 0x1234)    # one row below it
# the stock repaints the panel (the region wiped): the next paint of that buffer puts it back
fill(FB0, 0)
assert paint(FB0) == 0x77 and region(FB0) == pad_pixels(True) and data20() == 3
assert paint(FB1) == 0x77 and region(FB1) == pad_pixels(True)
# the second press unlocks: both buffers wiped at once, bits cleared
press(send)
assert region(FB0) == pad_pixels(False) and region(FB1) == pad_pixels(False) and data20() == 0
assert paint(FB0) == 0x77 and paint(FB1) == 0x77 and data20() == 0
# a lost bit (a stock repaint wiped one buffer's padlock while unlocked) is harmless: nothing is wiped without a bit
fill(FB0, 0x2222)
assert paint(FB0) == 0x77 and region(FB0) == plain(0x2222)
# locked again, then the guards: the Menu screen / the Action menu / a non-NAM block / the pedal block
press(send)
assert data20() == 3
fill(FB0, 0x4321)
fill(FB1, 0x4321)
mu.mem_write(SCREEN0, struct.pack("<I", 0))
assert paint(FB0) == 0x77 and data20() == 2 and region(FB0) == plain(0x4321)     # this buffer's bit dropped, nothing wiped
assert paint(FB1) == 0x77 and data20() == 0 and region(FB1) == plain(0x4321)
mu.mem_write(SCREEN0, struct.pack("<I", 1))
assert paint(FB0) == 0x77 and region(FB0) == pad_pixels(True) and data20() == 1   # back on the home screen: drawn again
fill(FB0, 0x4321)
mu.mem_write(STATE60, bytes([1]))                                               # the Action menu / move mode
assert paint(FB0) == 0x77 and data20() == 0 and region(FB0) == plain(0x4321)
mu.mem_write(STATE60, bytes([0]))
st["sel"] = 7                                                                    # a non-NAM block selected: no padlock, nothing wiped
assert paint(FB0) == 0x77 and region(FB0) == plain(0x4321) and data20() == 0
st["sel"] = 22                                                                   # the pedal block: its own bit, unlocked
assert paint(FB0) == 0x77 and region(FB0) == plain(0x4321)
press(send)
assert region(FB0) == pad_pixels(True) and data20() == 3
# the Menu screen: a press does nothing at all (no toggle, no drawing)
mu.mem_write(SCREEN0, struct.pack("<I", 0))
fill(FB0, 0x4321)
assert send(1, 12, 1) == "pass" and region(FB0) == plain(0x4321) and bits()[0] == (1 << 5) | (1 << 22)
mu.mem_write(SCREEN0, struct.pack("<I", 1))
# another stored preset clears the locks: the paint wipes the padlock
mu.mem_write(STORED, struct.pack("<I", 77))
fill(FB0, 0x4321)
mu.mem_write(FB0 + (PAD_Y * 800 + PAD_X) * 2, struct.pack("<H", 0x7FFF))
assert paint(FB0) == 0x77 and data20() == 2 and region(FB0) == pad_pixels(False)
print("model lock paint: the press draws / wipes the padlock in both buffers, the paint keeps it up, guards, results and registers kept: OK")

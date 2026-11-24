"""Emulator test of m4/model_lock.bin: the NAM Model lock (a pre-filter in front of dm_event). Run directly:

  python test_model_lock.py

The stock calls (SEL_BLOCK, PRESET_OBJ, BLOCK_DESC, memset) are stubs; every call must come at an 8-byte aligned sp.
"""
import os
import struct

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
                               UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_SP, UC_ARM_REG_PC)

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = open(os.path.join(HERE, "m4", "model_lock.bin"), "rb").read()
BASE = 0x29F22000
TARGET = 0x1001F82F                        # the old dm_event (thumb)
RET = 0x20000101
SCREEN0, STATE60, STORED = 0x281737D8, 0x281852B4, 0x281EE64C
STUBS = {0x281043E8: "sel", 0x28155F34: "memset", 0x28114A1C: "obj", 0x28148CC8: "desc_unused", 0x28148CCC: "desc"}
DATA = BASE + 0x100


def machine():
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    for b, s in ((0x10000000, 0x100000), (0x28000000, 0x2000000), (0x20000000, 0x10000)):
        mu.mem_map(b, s)
    mu.mem_write(BASE, CODE)
    mu.mem_write(DATA, struct.pack("<I", TARGET | 1))
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
        mu.emu_start(BASE | 1, 0, count=4000)
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

    return mu, st, send, bits


def press(send, code=12):
    """One whole press of a knob: press, release before hold... as the firmware posts it (1 press, 2 release)."""
    return [send(1, code, 1), send(1, code, 2)]


mu, st, send, bits = machine()
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

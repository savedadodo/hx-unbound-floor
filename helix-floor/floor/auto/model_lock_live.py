"""Dev mode, RAM only: the NAM Model lock (m4/model_lock.s) in front of dm_event, no install. A Floor restart undoes it.

  model_lock_live.py on         upload the module to free SDRAM and point the UI event post's trampoline at it
  model_lock_live.py off        put the trampoline's old target back
  model_lock_live.py state      the trampoline's target, the lock bits and the stored preset they belong to

The UI event post (0x2808b668) enters through `ldr.w pc, [pc]; .word target` (the meter module's dm_event). `on` writes the
module first (nothing runs it yet), with the old target in its ml_data, then replaces the one literal word in a single
atomic store, so an event sees either the old path or the whole new one. HX Edit must be closed.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "hx"))
sys.path.insert(0, os.path.dirname(HERE))
import asm_live  # noqa: E402
import floor_peek  # noqa: E402
import floor_poke  # noqa: E402

POST = 0x2808B668
LITERAL = POST + 4
LDR_PC = bytes.fromhex("5ff800f0")              # ldr.w pc, [pc]
VA = 0x29F22000                                  # free SDRAM (the focus module's live copy is at 0x29F20000)
DATA = 0x100                                     # ml_data in the module


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("on", "off", "state"):
        sys.exit(__doc__)
    code = None
    if mode == "on":                             # the assembler is slow: before the USB session opens
        _, code = asm_live.assemble(os.path.join(HERE, "..", "m4", "model_lock.s"))
        assert len(code) == DATA + 16, len(code)
    with floor_poke.Poke() as p:
        pk = floor_peek.Peek(p.ctl)
        try:
            head, cur = pk.read(POST, 4), struct.unpack("<I", pk.read(LITERAL, 4))[0]
            assert head == LDR_PC, f"the post's entry is not the expected trampoline: {head.hex()}"
            if mode == "state":
                print("trampoline target", hex(cur), "(the lock module)" if VA <= cur < VA + 0x200 else "")
                if VA <= cur < VA + 0x200:
                    t, lo, hi, last = struct.unpack("<4I", pk.read(VA + DATA, 16))
                    print("old target", hex(t), "lock bits", hex(lo), hex(hi), "stored preset", hex(last))
                return
            if mode == "on":
                if VA <= cur < VA + 0x200:
                    sys.exit("already on")
                assert 0x10000000 <= cur < 0x10100000 and cur & 1, f"unexpected old target {cur:#x}"
                code = bytearray(code)
                struct.pack_into("<I", code, DATA, cur)
                p.enable_bulk()
                p.write_bulk(VA, bytes(code))
                new = VA | 1
            else:
                if not (VA <= cur < VA + 0x200):
                    sys.exit("not on")
                new = struct.unpack("<I", pk.read(VA + DATA, 4))[0]
        finally:
            pk.screen()
        p.patch_atomic(LITERAL, struct.pack("<I", new))
    print("model lock", mode, f"(trampoline target {new:#x})")


if __name__ == "__main__":
    main()

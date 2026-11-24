"""Dev mode, RAM only: the NAM Model lock (m4/model_lock.s) in front of dm_event, no install. A Floor restart undoes it.

  model_lock_live.py on         upload the module to free SDRAM, point the UI event post's trampoline and the home view's paint at it
  model_lock_live.py off        put both old pointers back
  model_lock_live.py state      the pointers, the lock bits, the stored preset they belong to, the padlock's frame-buffer bits

The UI event post (0x2808b668) enters through `ldr.w pc, [pc]; .word target` (the meter module's dm_event). `on` writes the
module first (nothing runs it yet), with the old target in its ml_data, then replaces the one literal word in a single
atomic store, so an event sees either the old path or the whole new one. The padlock wrapper does the same with the paint
pointer of the home view's node in the view list (0x28175310 + 4, dm_paint). HX Edit must be closed.
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
DATA = 0x300                                     # ml_data in the module: +0 event target, +4 / +8 lock bits, +12 stored preset, +16 paint, +20 padlock bits
PAINT_PTR = 0x28175314                           # the home view node's paint pointer (view list head 0x28175908)
HOME_PAINT_NODE = 0x28175310


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("on", "off", "state"):
        sys.exit(__doc__)
    code = None
    if mode == "on":                             # the assembler is slow: before the USB session opens
        _, code = asm_live.assemble(os.path.join(HERE, "..", "m4", "model_lock.s"))
        assert len(code) == DATA + 24, len(code)
    with floor_poke.Poke() as p:
        pk = floor_peek.Peek(p.ctl)
        try:
            head, cur = pk.read(POST, 4), struct.unpack("<I", pk.read(LITERAL, 4))[0]
            paint = struct.unpack("<I", pk.read(PAINT_PTR, 4))[0]
            assert head == LDR_PC, f"the post's entry is not the expected trampoline: {head.hex()}"
            ours = lambda v: VA <= v < VA + 0x400
            if mode == "state":
                print("event target", hex(cur), "paint", hex(paint), "(the lock module)" if ours(cur) else "")
                if ours(cur):
                    t, lo, hi, last, pt, bitsd = struct.unpack("<6I", pk.read(VA + DATA, 24))
                    print("old event target", hex(t), "old paint", hex(pt), "lock bits", hex(lo), hex(hi), "stored preset", hex(last),
                          "padlock drawn in buffers", bin(bitsd))
                return
            if mode == "on":
                if ours(cur) or ours(paint):
                    sys.exit("already on")
                assert 0x10000000 <= cur < 0x10100000 and cur & 1, f"unexpected old event target {cur:#x}"
                assert 0x10000000 <= paint < 0x10100000 and paint & 1, f"unexpected old paint {paint:#x}"
                assert struct.unpack("<I", pk.read(HOME_PAINT_NODE + 0xC, 4))[0] == 0x2818C21C, "the home view's node moved"
                code = bytearray(code)
                struct.pack_into("<I", code, DATA, cur)
                struct.pack_into("<I", code, DATA + 16, paint)
                p.enable_bulk()
                p.write_bulk(VA, bytes(code))
                new_ev, new_pt = VA | 1, (VA + 4) | 1
            else:
                if not ours(cur):
                    sys.exit("not on")
                t, pt = struct.unpack("<2I", pk.read(VA + DATA, 4) + pk.read(VA + DATA + 16, 4))
                new_ev, new_pt = t, pt
        finally:
            pk.screen()
        p.patch_atomic(PAINT_PTR, struct.pack("<I", new_pt))      # the paint first: it only reads the module's data
        p.patch_atomic(LITERAL, struct.pack("<I", new_ev))
    print("model lock", mode, f"(event target {new_ev:#x}, paint {new_pt:#x})")


if __name__ == "__main__":
    main()

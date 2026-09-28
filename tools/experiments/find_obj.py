"""Generic Mono class-name -> live instance finder (streaming, 4 passes).

Usage: python tools/find_obj.py gameConfig
"""
import ctypes
import struct
import sys

import numpy as np

from read_package import find_pid, open_proc
from find_grids import regions, PAGE

k32 = ctypes.windll.kernel32


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "gameConfig"
    pid = find_pid()
    h = open_proc(pid)

    def rd(a, n):
        b = ctypes.create_string_buffer(n)
        g = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(h, ctypes.c_void_p(a), b, n, ctypes.byref(g)):
            return None
        return b.raw[: g.value]

    def rq(a):
        b = rd(a, 8)
        return struct.unpack("<Q", b)[0] if b and len(b) == 8 else None

    def rs(a, n=48):
        b = rd(a, n)
        if not b:
            return None
        i = b.find(b"\0")
        if i < 1 or i > 40:
            return None
        t = b[:i]
        return t.decode() if all(32 <= c < 127 for c in t) else None

    cache = {}
    def cname(vt, depth=0):
        if not vt or vt in cache:
            return cache.get(vt)
        if depth > 3 or not (0x1000 < vt < 0x7FFFFFFFFFFF):
            return None
        for coff in (0x00, 0x08, 0x10):
            cl = rq(vt + coff)
            if cl and 0x1000 < cl < 0x7FFFFFFFFFFF:
                np_ = rq(cl + 0x48)
                if np_:
                    s = rs(np_)
                    if s and len(s) < 40:
                        cache[vt] = s
                        return s
        v2 = rq(vt)
        r = cname(v2, depth + 1) if v2 else None
        cache[vt] = r
        return r

    def walk(fn):
        """call fn(base, bytes) for each readable committed region chunk"""
        for base, size, mtype, prot in regions(h):
            if size > 512 * 1024 * 1024:
                continue
            off = 0
            while off < size:
                n = min(PAGE, size - off)
                b = ctypes.create_string_buffer(n)
                g = ctypes.c_size_t(0)
                if not k32.ReadProcessMemory(h, ctypes.c_void_p(base + off), b, n, ctypes.byref(g)) or not g.value:
                    break
                fn(base + off, b.raw[: g.value])
                off += n

    # pass 1: find class-name string addresses
    pat = name.encode() + b"\x00"
    str_ptrs = []
    def p1(base, data):
        pos = 0
        while True:
            i = data.find(pat, pos)
            if i < 0:
                return
            if i % 4 == 0 and data[max(i-40,0):i].count(b"\x00") < 10:
                str_ptrs.append(base + i)
            pos = i + 1
    walk(p1)
    print(f"pass1: '{name}' 串 @ {[hex(x) for x in str_ptrs[:6]]} (共{len(str_ptrs)})")

    # pass 2: slots == str_ptr -> class = slot-0x48
    spset = np.array(sorted(str_ptrs), dtype=np.uint64)
    classes = set()
    def p2(base, data):
        nr = len(data) // 8
        if not nr:
            return
        u = np.frombuffer(data[: nr * 8], np.uint64)
        pos = np.clip(np.searchsorted(spset, u), 0, len(spset) - 1)
        sel = np.nonzero(spset[pos] == u)[0]
        for s in sel:
            classes.add(base + int(s) * 8 - 0x48)
    walk(p2)
    classes = {c for c in classes if 0x1000 < c < 0x7FFFFFFFFFFF}
    print(f"pass2: 候选 class* {len(classes)}: {[hex(c) for c in sorted(classes)[:8]]}")
    # keep only those whose +0x48 really points back to the name string AND looks class-y (skip wrong-ownership hits)
    good_cl = []
    for c in classes:
        np_ = rq(c + 0x48)
        if np_ in str_ptrs:
            good_cl.append(c)
    print(f"       校验后: {[hex(c) for c in good_cl]}")

    # pass 3: slots == class -> vt candidates
    clset = np.array(sorted(good_cl), dtype=np.uint64)
    vt_cands = set()
    def p3(base, data):
        nr = len(data) // 8
        if not nr:
            return
        u = np.frombuffer(data[: nr * 8], np.uint64)
        pos = np.clip(np.searchsorted(clset, u), 0, len(clset) - 1)
        sel = np.nonzero(clset[pos] == u)[0]
        for s in sel:
            loc = base + int(s) * 8
            for coff in (0x00, 0x08, 0x10):
                vt_cands.add(loc - coff)
    walk(p3)
    vt_cands = {v for v in vt_cands if 0x1000 < v < 0x7FFFFFFFFFFF}
    print(f"pass3: 候选 vtable* {len(vt_cands)}")

    # pass 4: slots == vt -> object; verify cname(obj)==name, then dump fields
    vtset = np.array(sorted(vt_cands), dtype=np.uint64)
    seen_objs = set()
    def p4(base, data):
        nr = len(data) // 8
        if not nr:
            return
        u = np.frombuffer(data[: nr * 8], np.uint64)
        pos = np.clip(np.searchsorted(vtset, u), 0, len(vtset) - 1)
        sel = np.nonzero(vtset[pos] == u)[0]
        for s in sel:
            o = base + int(s) * 8
            if o in seen_objs:
                continue
            seen_objs.add(o)
            if cname(rq(o)) != name:
                continue
            print(f"\n*** {name} instance @ {o:#x}")
            for off in range(0x08, 0x300, 8):
                v = rq(o + off)
                if v and 0x1000 < v < 0x7FFFFFFFFFFF:
                    cn = cname(rq(v))
                    extra = ""
                    if cn and cn.endswith("[]"):
                        L = rq(v + 0x18)
                        extra = f" len={L}"
                    print(f"   +{off:#04x} -> {v:#x} [{cn}]{extra}")
    walk(p4)
    print(f"\npass4: 共检查 {len(seen_objs)} 个候选对象")


if __name__ == "__main__":
    main()

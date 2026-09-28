"""Resolve itemId -> name fully automatically:
  find MonoClass 'LDB' (name string -> class at slot-0x48)
  -> static blob at class+0x30 -> pointer to object of class 'ItemProtoSet'
  -> ItemProtoSet.dataArray -> ItemProto[] -> per-proto: find index (i32 == id) + name (String)
Usage: python tools/item_names.py [ids...]   (default: dump a few + look up bag ids)
"""
import ctypes
import struct
import sys

import numpy as np

from read_package import find_pid, open_proc
from find_grids import regions, PAGE
from final_chain import mkreaders, build_class_resolver

k32 = ctypes.windll.kernel32


def main():
    h = open_proc(find_pid())
    rd, rq, r4 = mkreaders(h)
    cname = build_class_resolver(rd, rq)

    def getstr(p):
        if not p or not (0x1000 < p < 0x7FFFFFFFFFFF) or cname(rq(p)) != "String":
            return None
        n = r4(p + 0x10)
        if not 0 <= n < 100:
            return None
        b = rd(p + 0x14, n * 2)
        return b.decode("utf-16-le", "?") if b else None

    # 1) find MonoClass 'ItemProtoSet': locate its name string, then the slot at class+0x48
    pat = b"ItemProtoSet\x00"
    str_hits = []
    def find_strings(base, data):
        pos = 0
        while True:
            i = data.find(pat, pos)
            if i < 0:
                return
            if i % 4 == 0:
                str_hits.append(base + i)
            pos = i + 1
    walk_strings = lambda fn: [
        fn(base + off, buf)
        for base, size, mtype, prot in regions(h) if size <= 512 * 1024 * 1024
        for off in range(0, size, PAGE)
        for buf in [_read_chunk(h, base + off, min(PAGE, size - off))] if buf
    ]
    def _read_chunk(hh, a, n):
        b = ctypes.create_string_buffer(n)
        g = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(hh, ctypes.c_void_p(a), b, n, ctypes.byref(g)) or not g.value:
            return None
        return b.raw[: g.value]
    walk_strings(find_strings)
    print(f"'ItemProtoSet' 串: {[hex(x) for x in str_hits[:8]]}")

    # slots equal to any str hit -> class = slot-0x48
    hitset = np.array(sorted(str_hits), dtype=np.uint64) if str_hits else None
    classes = []
    if hitset is not None:
        def p2(base, data):
            nr = len(data) // 8
            if not nr:
                return
            u = np.frombuffer(data[: nr * 8], np.uint64)
            pos = np.clip(np.searchsorted(hitset, u), 0, len(hitset) - 1)
            sel = np.nonzero(hitset[pos] == u)[0]
            for s in sel:
                cl = base + int(s) * 8 - 0x48
                if rq(cl + 0x48) in str_hits:
                    classes.append(cl)
        walk_strings(p2)
    classes = sorted(set(classes))
    print("ItemProtoSet classes:", [hex(c) for c in classes])

    # 2) static blob -> ItemProtoSet instance
    proto_set = None
    for cl in classes:
        for boff in (0x28, 0x30, 0x38, 0x20, 0x40):
            blob = rq(cl + boff)
            if not blob or not (0x1000 < blob < 0x7FFFFFFFFFFF):
                continue
            for so in range(0, 0x80, 8):
                v = rq(blob + so)
                if v and 0x1000 < v < 0x7FFFFFFFFFFF and cname(rq(v)) == "ItemProtoSet":
                    print(f"*** LDB._items = {v:#x} (class+{boff:#x} blob +{so:#x})")
                    proto_set = v
                    break
            if proto_set:
                break
        if proto_set:
            break
    if not proto_set:
        print("!! 没找到 ItemProtoSet")
        return

    # 3) dataArray: field of proto_set pointing to ItemProto[] array
    arr = None
    for off in range(0x08, 0x40, 8):
        v = rq(proto_set + off)
        if v and 0x1000 < v < 0x7FFFFFFFFFFF and cname(rq(v)) == "ItemProto[]":
            L = rq(v + 0x18)
            print(f"ItemProtoSet+{off:#x} -> dataArray {v:#x} len={L}")
            arr = v
            break
    if arr is None:
        for off in range(0x08, 0x40, 8):
            v = rq(proto_set + off)
            if v and 0x1000 < v < 0x7FFFFFFFFFFF:
                print(f"  +{off:#x} -> {v:#x} [{cname(rq(v))}]")
        return

    L = rq(arr + 0x18)
    # 4) build id->name: find field offsets inside ItemProto: index i32 + name String*
    e0 = rq(arr + 0x20)
    print("proto0:", hex(e0), "class:", cname(rq(e0)))
    # scan proto for (i32 index) and strings
    pb = rd(e0, 0x80)
    print("proto raw u32/qword:")
    for off in range(0x18, 0x80, 8):
        q = struct.unpack_from("<Q", pb, off)[0]
        lo = q & 0xFFFFFFFF
        s = getstr(q) if 0x1000 < q < 0x7FFFFFFFFFFF else None
        print(f"  +{off:#x} = {q:#x}" + (f" str={s!r}" if s else (f" i32={lo}" if lo < 0x10000 else "")))


if __name__ == "__main__":
    main()

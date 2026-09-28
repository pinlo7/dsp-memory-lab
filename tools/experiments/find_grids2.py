"""v2: find GRID[] via allocation-run detection + resolve MonoClass names.

GRID objects created together at inventory init are consecutive in the Boehm
heap with a fixed stride. Detect them as: 8-aligned object O where
  O+0x10 = itemId (1..3000), O+0x14 = count (1..500000), O+0x1C = ordered (0/1),
  O+0x20 = stackSize in common set, O+0x24 = stackSizeMultiplier (1..50)
Score by vtable frequency, then group by address runs.
"""
import struct
import sys

import numpy as np

from read_package import find_pid, open_proc, qword, rd
from find_grids import regions, PAGE


def resolve_class(h, vt):
    """vtable -> class name. Mono bleeding edge: vtable+0x00 = gc_desc?, +0x08 = class.
    Try several vtable->class layouts, then class->name layouts. Returns name or None."""
    cands = []
    for coff in (0x08, 0x10, 0x00):
        cl = qword(h, vt + coff)
        if cl and 0x1000 < cl < 0x7FFFFFFFFFFF:
            cands.append(cl)
    for cl in cands:
        for noff in (0x18, 0x20, 0x28, 0x30, 0x38, 0x40):
            s = rd(h, cl + noff, 32)
            if s:
                i = s.find(b"\0")
                if 1 < i <= 30 and all(32 <= c < 127 for c in s[:i]):
                    ns = rd(h, cl + noff + 8, 32) if noff + 8 else None
                    j = ns.find(b"\0") if ns else -1
                    nss = ns[:j].decode() if 0 < j <= 30 and all(32 <= c < 127 for c in ns[:j]) else ""
                    return f"{nss}.{s[:i].decode()} (class@{cl:#x})"
    return None


def main():
    pid = find_pid()
    h = open_proc(pid)

    bufs = []
    for base, size, mtype, prot in regions(h):
        if size > 512 * 1024 * 1024 or prot & 0x104:  # skip executables
            continue
        off = 0
        chunks = []
        while off < size:
            n = min(PAGE, size - off)
            buf = ctypes_buf = None
            import ctypes
            buf = ctypes.create_string_buffer(n)
            got = ctypes.c_size_t(0)
            ok = ctypes.windll.kernel32.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, n, ctypes.byref(got))
            if not ok or got.value == 0:
                break
            chunks.append(buf.raw[:got.value])
            off += got.value
        if chunks:
            bufs.append((base, b"".join(chunks)))

    STACK = np.fromiter((1, 2, 3, 4, 5, 10, 20, 30, 50, 100, 150, 200, 300, 400, 500, 600, 750, 999, 1000, 1200, 1500, 2000, 3000, 5000), np.uint32)

    groups = {}
    for base, data in bufs:
        nr = len(data) // 8
        if nr < 12:
            continue
        rows = np.frombuffer(data[: nr * 8], np.uint64)
        lo = rows[:-8] & 0xFFFFFFFF
        hi = rows[:-8] >> 32
        r1 = rows[2:-6]
        itemId = r1 & 0xFFFFFFFF
        count = r1 >> 32
        r2 = rows[3:-5]
        inc = r2 & 0xFFFFFFFF
        ordered = r2 >> 32
        r4 = rows[4:-4]
        stack = r4 & 0xFFFFFFFF
        mult = r4 >> 32
        r5 = rows[5:-3]
        req = r5 & 0xFFFFFFFF
        recy = r5 >> 32
        vt_hi = (rows[:-8] >> 32) & 0xFFFFFFFF
        mask = (
            (itemId > 0) & (itemId < 3000)
            & (count > 0) & (count <= 500000)
            & (ordered <= 1)
            & (inc <= 3)
            & np.isin(stack, STACK)
            & (mult >= 1) & (mult <= 50)
            & (req <= 1000000) & (recy <= 1000000)
            & (vt_hi > 0x100) & (vt_hi < 0x80000)   # heap vtable high dword like 0x1c9
        )
        for r in np.nonzero(mask)[0]:
            vt = int(rows[r])
            groups.setdefault(vt, []).append((base + int(r) * 8, int(itemId[r]), int(count[r])))

    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:8]
    for vt, lst in ranked:
        ids = sorted({x[1] for x in lst})
        addrs = sorted(x[0] for x in lst)
        strides = {}
        for a, b in zip(addrs, addrs[1:]):
            strides[b - a] = strides.get(b - a, 0) + 1
        common = sorted(strides.items(), key=lambda kv: -kv[1])[:2]
        name = resolve_class(h, vt)
        print(f"vt={vt:#x} n={len(lst)} name={name}")
        print(f"   itemIds({len(ids)})={ids[:15]}")
        print(f"   addr strides={common}")
        print(f"   addr range {addrs[0]:#x}..{addrs[-1]:#x}")
    k32_close(h)


def k32_close(h):
    import ctypes
    ctypes.windll.kernel32.CloseHandle(h)


if __name__ == "__main__":
    main()

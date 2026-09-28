"""Top-down discovery: scan all committed memory of DSPGAME for GRID objects,
reconstruct GRID[] arrays, find their holders (StorageComponents), and report
the static slot holding GameMain.data. No prior offset assumptions.

python tools/find_grids.py
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys

import numpy as np

from read_package import find_pid, open_proc

k32 = ctypes.windll.kernel32


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


def regions(h):
    out = []
    addr = 0
    mbi = MBI()
    while addr < 0x7FFFFFFFFFFF:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base, size = mbi.BaseAddress, mbi.RegionSize
        if mbi.State == 0x1000 and not (mbi.Protect & (0x01 | 0x100)):  # COMMIT, not NOACCESS/GUARD
            out.append((base, size, mbi.Type, mbi.Protect))
        addr = base + size
    return out


STACK_SET = {1, 2, 3, 4, 5, 10, 20, 30, 50, 100, 150, 200, 300, 400, 500, 600, 750, 999, 1000, 1200, 1500, 2000, 3000, 5000}
PAGE = 1 << 20


def main():
    pid = find_pid()
    if not pid:
        print("游戏未运行")
        return
    h = open_proc(pid)
    print(f"PID={pid}")

    bufs = []  # (base, size, bytes)
    total = 0
    for base, size, mtype, prot in regions(h):
        if size > 512 * 1024 * 1024:
            continue
        chunks = []
        off = 0
        while off < size:
            n = min(PAGE, size - off)
            buf = ctypes.create_string_buffer(n)
            got = ctypes.c_size_t(0)
            ok = k32.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, n, ctypes.byref(got))
            if not ok or got.value == 0:
                break
            chunks.append(buf.raw[:got.value])
            off += got.value
        data = b"".join(chunks)
        if data:
            bufs.append((base, data))
            total += len(data)
    print(f"可读内存合计 {total/1e6:.0f} MB")

    # ---- step 1: GRID object candidates (u32 aligned windows) ----
    grid_by_vt = {}
    all_grids = []
    for base, data in bufs:
        n = len(data) // 4
        a = np.frombuffer(data[: n * 4], np.uint32)
        # i even (8-byte aligned object base)
        a2 = a.reshape(-1, 2)  # rows = 8-byte-aligned starts
        if a2.shape[0] < 10:
            continue
        oid = a2[:-5, 0]           # itemId   (row i words 2..: we need u32 at +0x10 = row i+2)
        # object base at row r: fields: r+2=itemId? +0x10 bytes = 4 u32 = rows... row = 8 bytes = 2 u32.
        # +0x00 vtable (r), +0x08 sync (r+1), +0x10 itemId (r+2 lo u32), +0x14 count (r+2 hi)... wait
        # +0x10 = 16 bytes = row r+2 (both u32 in that row: itemId=lo, count=hi)
        itemId = a2[2:-3, 0].astype(np.uint64)
        count = a2[2:-3, 1]
        inc = a2[3:-2, 0]
        ordered = a2[3:-2, 1]
        stack = a2[4:-1, 0]
        vt = a2[0:-5, 0].astype(np.uint64) | (a2[0:-5, 1].astype(np.uint64) << 32)
        vt_hi = a2[0:-5, 1]
        mask = (
            (itemId > 0) & (itemId < 3000)
            & (count <= 1000000)
            & (ordered <= 1)
            & (inc <= 10000)
            & np.isin(stack, np.fromiter(STACK_SET, np.uint32, len(STACK_SET)))
            & (vt_hi != 0) & (vt_hi < 0x7F)  # heap-ish vtable high dword
        )
        idx = np.nonzero(mask)[0]
        for r in idx:
            vt64 = int(vt[r])
            addr = base + int(r) * 8
            grid_by_vt.setdefault(vt64, []).append((addr, int(itemId[r]), int(count[r]), int(stack[r])))
    top = sorted(grid_by_vt.items(), key=lambda kv: -len(kv[1]))[:5]
    print("候选 vtable 分组 (top):")
    for vt64, lst in top:
        print(f"  vt={vt64:#x} count={len(lst)} sample={lst[:3]}")

    if not top:
        print("!! 没找到 GRID 特征对象")
        return

    # take the biggest group that has varied itemIds (real inventory stuff)
    for vt64, lst in top[:3]:
        ids = {x[1] for x in lst}
        if len(ids) >= 3:
            grids = lst
            gvt = vt64
            break
    else:
        print("!! 没有多 itemId 的组")
        return
    print(f"\n采用 GRID vtable={gvt:#x}, 共 {len(grids)} 个对象")

    gaddr = np.fromiter((x[0] for x in grids), np.uint64)
    ginfo = {x[0]: x for x in grids}
    gaddr_sorted = np.sort(gaddr)

    # ---- step 2: find GRID[] arrays: runs of slots (stride 8) whose values in gaddr set ----
    hits = []  # (array_addr, len, [elements])
    for base, data in bufs:
        n = len(data) // 8
        u = np.frombuffer(data[: n * 8], np.uint64)
        pos = np.clip(np.searchsorted(gaddr_sorted, u), 0, len(gaddr_sorted) - 1)
        sel = np.nonzero(gaddr_sorted[pos] == u)[0]
        if sel.size == 0:
            continue
        slots = base + sel * 8
        vals = u[sel]
        order = np.argsort(slots)
        slots, vals = slots[order], vals[order]
        # group consecutive slots (stride 8)
        runs = np.split(np.arange(len(slots)), np.nonzero(np.diff(slots) != 8)[0] + 1)
        for run in runs:
            if len(run) < 4:
                continue
            first_slot = int(slots[run[0]])
            A = first_slot - 0x18  # assume run starts at element 0
            # read length at A+0x10 (may cross region: use struct via our bufs? A should be in same region usually)
            if len(run) >= 20:
                pass
            hits.append((A, len(run), [int(v) for v in vals[run]]))
    # dedupe / sort by longest run
    seen = {}
    for A, L, els in hits:
        seen.setdefault(A, []).append((L, els))
    print(f"\n疑似 GRID[] 数组位置 (run starts):")
    reported = set()
    for A, cand in sorted(seen.items(), key=lambda kv: -max(c[0] for c in kv[1])):
        L = max(c[0] for c in cand)
        if L < 5 or A in reported:
            continue
        reported.add(A)
        print(f"  arr={A:#x} run_elems={L}")
        if len(reported) >= 8:
            break

    # ---- step 3: holders of arrays ----
    for A in list(reported)[:8]:
        for base, data in bufs:
            n = len(data) // 8
            u = np.frombuffer(data[: n * 8], np.uint64)
            sel = np.nonzero(u == A)[0]
            for s in sel:
                loc = base + int(s) * 8
                print(f"  arr {A:#x} <- holder slot at {loc:#x}")

    # ---- step 4: static slot holding known anchor (GameMain.data value if given) ----
    if len(sys.argv) > 1:
        anchor = int(sys.argv[1], 16)
        print(f"\n== 谁引用了 {anchor:#x} ==")
        for base, data in bufs:
            n = len(data) // 8
            u = np.frombuffer(data[: n * 8], np.uint64)
            sel = np.nonzero(u == anchor)[0]
            for s in sel[:10]:
                loc = base + int(s) * 8
                print(f"  {loc:#x} (+{loc-base:#x} in region {base:#x})")

    k32.CloseHandle(h)


if __name__ == "__main__":
    main()

"""Auto-locate the inventory: BFS from a known GameMain.data value, score
candidate StorageComponents by structural sanity, print the winning chain.

No assumptions about exact field offsets — we discover them.

Usage: python tools/scan_chain.py [S_hex]
"""
import ctypes
import struct
import sys

from read_package import find_pid, open_proc, qword, dword, rd, looks_ptr

MAX_ID = 3000        # item ids are small ints
MAX_COUNT = 100000   # per-grid count upper bound


def grid_entries_ok(h, arr):
    """arr = pointer to grids array object. Try inline-struct and ref layouts."""
    length = qword(h, arr + 0x10)
    if not length or length <= 0 or length > 20000:
        return None
    # ref array: elements are pointers to GRID objects
    good = 0
    checked = min(length, 12)
    for i in range(checked):
        e = qword(h, arr + 0x18 + i * 8)
        if not looks_ptr(e):
            break
        itemId = dword(h, e + 0x10)
        count = dword(h, e + 0x14)
        if itemId is None or count is None:
            break
        if itemId == 0 or (0 < itemId < MAX_ID and 0 <= count < MAX_COUNT):
            good += 1
    if good >= max(1, checked // 2):
        return ("ref", length)
    # inline struct array (stride guess = 0x20 / 0x28)
    for stride in (0x18, 0x20, 0x28):
        good = 0
        for i in range(checked):
            b = rd(h, arr + 0x18 + i * stride, 8)
            if not b:
                break
            itemId, count = struct.unpack("<II", b)
            if itemId == 0 or (0 < itemId < MAX_ID and 0 <= count < MAX_COUNT):
                good += 1
        if good >= checked:
            return ("inline%d" % stride, length)
    return None


def is_storage(h, p):
    """Check p as StorageComponent: isPlayerInventory@+0x4C bool, size@+0x50, grids@+0x58."""
    if not looks_ptr(p):
        return None
    ipi = rd(h, p + 0x4C, 1)
    if not ipi or ipi[0] != 1:
        return None
    size = dword(h, p + 0x50)
    grids = qword(h, p + 0x58)
    if not size or not (0 < size < 20000):
        return None
    g = grid_entries_ok(h, grids) if looks_ptr(grids) else None
    if g is None:
        return None
    kind, length = g
    return {"size": size, "grids": grids, "len": length, "kind": kind}


def dump_grids(h, info):
    arr = info["grids"]
    out = []
    for i in range(info["len"]):
        if info["kind"] == "ref":
            e = qword(h, arr + 0x18 + i * 8)
            if not looks_ptr(e):
                out.append((i, None))
                continue
            vals = struct.unpack("<IIII", rd(h, e + 0x10, 16) or b"\0" * 16)
            out.append((i, vals))
        else:
            stride = int(info["kind"][6:], 16)
            b = rd(h, arr + 0x18 + i * stride, 16) or b"\0" * 16
            out.append((i, struct.unpack("<IIII", b)))
    return out


def main():
    S = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x1C9770E94C0
    pid = find_pid()
    if not pid:
        print("游戏未运行")
        return
    h = open_proc(pid)
    print(f"PID={pid}  anchor S={S:#x}")

    # raw dump of S region
    print("--- raw qwords at S .. S+0x120 ---")
    for off in range(0, 0x128, 8):
        v = qword(h, S + off)
        tag = " ptr" if looks_ptr(v) else ""
        print(f"S+{off:#05x} = {v if v is None else hex(v)}{tag}")

    # BFS depth<=3 from {S} ∪ {qword(S+k)} : find (player_obj, pkg_off, pkg) triples
    seen = {S}
    frontier = [S]
    depth = 0
    hits = []
    while frontier and depth < 3:
        nxt = []
        for a in frontier:
            for off in range(0x08, 0x200, 8):
                v = qword(h, a + off)
                if not looks_ptr(v) or v in seen:
                    continue
                seen.add(v)
                info = is_storage(h, v)
                if info:
                    hits.append((a, off, v, info, depth))
                else:
                    # one more level: maybe v is Player and package is inside
                    for poff in range(0x10, 0x120, 8):
                        p2 = qword(h, v + poff)
                        i2 = is_storage(h, p2) if looks_ptr(p2) else None
                        if i2:
                            hits.append((v, poff, p2, i2, depth + 1))
                        nxt.append(p2 if looks_ptr(p2) else None)
        frontier = [x for x in nxt if x and x not in seen][:200]
        seen.update(frontier)
        depth += 1

    if not hits:
        print("\n!! 从 S 出发 3 层内没找到合法 StorageComponent —— S 已过期，重新读 GameMain.data")
        return
    for a, off, pkg, info, d in hits[:6]:
        print(f"\n=== StorageComponent {pkg:#x} (从 {a:#x}+{off:#x} 到达, depth={d}) ===")
        print(f"    size={info['size']} grids_len={info['len']} layout={info['kind']}")
        for i, vals in dump_grids(h, info):
            if vals and vals[0]:
                print(f"    grid[{i:2d}] itemId={vals[0]:5d} count={vals[1]:6d} inc={vals[2]} stackSize={vals[3]}")
            elif vals:
                print(f"    grid[{i:2d}] (empty)")


if __name__ == "__main__":
    main()

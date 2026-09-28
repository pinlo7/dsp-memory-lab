"""Pin down the final pointer chain by CLASS NAME (ground truth via vtable->MonoClass).

No guessing: for each candidate offset, resolve the pointed object's runtime class
name. package is the Player field whose target class is 'StorageComponent';
grids is the StorageComponent field whose target is an array class (name ends with '[]').

Usage: python tools/final_chain.py [GameData_addr_hex]
"""
import ctypes
import struct
import sys

from read_package import find_pid, open_proc

k32 = ctypes.windll.kernel32


def mkreaders(h):
    def rd(a, n):
        b = ctypes.create_string_buffer(n)
        g = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(h, ctypes.c_void_p(a), b, n, ctypes.byref(g)):
            return None
        return b.raw[: g.value]

    def rq(a):
        b = rd(a, 8)
        return struct.unpack("<Q", b)[0] if b and len(b) == 8 else None

    def r4(a):
        b = rd(a, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else 0

    return rd, rq, r4


def build_class_resolver(rd, rq):
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

    def class_name(vt, depth=0):
        if vt in cache:
            return cache[vt]
        if depth > 3 or vt is None or not (0x1000 < vt < 0x7FFFFFFFFFFF):
            return None
        nm = None
        for coff in (0x00, 0x08, 0x10):
            cl = rq(vt + coff)
            if cl and 0x1000 < cl < 0x7FFFFFFFFFFF:
                np_ = rq(cl + 0x48)
                if np_:
                    s = rs(np_)
                    if s and (s.isidentifier() or s.endswith("[]")):
                        nm = s
                        break
        if nm is None:
            # 可能 vtable 参数其实是对象指针: 先取对象+0
            v2 = rq(vt)
            if v2:
                cache[vt] = class_name(v2, depth + 1)
                nm = cache[vt]
        cache[vt] = nm
        return nm

    return class_name


def main():
    addr = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x1C9770E94C0
    pid = find_pid()
    if not pid:
        print("游戏未运行")
        return
    h = open_proc(pid)
    rd, rq, r4 = mkreaders(h)
    cname = build_class_resolver(rd, rq)

    G = addr
    vt = rq(G)
    print(f"GameData candidate {G:#x} class = {cname(vt)}")
    if cname(vt) != "GameData":
        print("!! 不是 GameData —— 传新的 GameMain.data 值作参数")
        return

    P = rq(G + 0xC0)
    print(f"[G+0xC0] = {P:#x} class = {cname(rq(P))}")

    # find package: Player field pointing at StorageComponent
    pkg_off = None
    for off in range(0x08, 0x120, 8):
        v = rq(P + off)
        if v and 0x1000 < v < 0x7FFFFFFFFFFF:
            cn = cname(rq(v))
            if cn == "StorageComponent":
                print(f"\n*** Player.package = +{off:#x} -> {v:#x} (StorageComponent)")
                pkg_off = off
                break
    if pkg_off is None:
        print("!! 没找到 package")
        return
    PKG = rq(P + pkg_off)

    # dump StorageComponent ints
    print(f"  ipinv-like u32s: ", {f"+{o:#x}={r4(PKG+o)}" for o in range(0x60, 0x90, 4)})

    # find grids: field of PKG pointing at an array object
    grids_off = None
    for off in range(0x08, 0x120, 8):
        v = rq(PKG + off)
        if not v or not (0x1000 < v < 0x7FFFFFFFFFFF):
            continue
        an = cname(rq(v))
        if an and an.endswith("[]"):
            L10 = rq(v + 0x10)
            L18 = rq(v + 0x18)
            L = L10 if (L10 and 1 <= L10 <= 4096) else (L18 if (L18 and 1 <= L18 <= 4096) else None)
            dat = 0x18 if L == L10 else 0x20
            print(f"\n*** StorageComponent.grids = +{off:#x} -> array {v:#x} class={an} len@+10={L10} len@+18={L18}")
            grids_off = off
            ARR = v
            DATA = dat
            break
    if grids_off is None:
        print("!! 没找到数组字段, dump PKG 指针邻居的类名:")
        for off in range(0x08, 0x120, 8):
            v = rq(PKG + off)
            if v and 0x1000 < v < 0x7FFFFFFFFFFF:
                print(f"   PKG+{off:#x} -> {v:#x} class={cname(rq(v))}")
        return

    # element layout: try ref-to-object vs inline struct
    b0 = rd(ARR + DATA, 0x40)
    first_q = struct.unpack("<Q", b0[:8])[0]
    print("\n数组头:", [hex(x) for x in struct.unpack("<6Q", rd(ARR, 0x30) or b"")])
    elem_cls = cname(rq(first_q)) if 0x1000 < first_q < 0x7FFFFFFFFFFF else None
    if elem_cls:
        print(f"元素是指针 (ref array). elem0 class = {elem_cls}")
        for i in range(6):
            e = rq(ARR + DATA + i * 8)
            if not e:
                continue
            for foff in (0x10, 0x18, 0x08):
                vals = struct.unpack("<5I", rd(e + foff, 20) or b"\0" * 20)
                if vals[0] < 30000 and vals[4] < 30000:
                    print(f"  [{i:2d}] +{foff:#x} itemId={vals[0]:5d} count={vals[1]:6d} inc={vals[2]} ordered={vals[3]} stack={vals[4]}")
                    break
    else:
        print("元素是内联结构 (value array). 试 stride:")
        raw = rd(ARR + DATA, 0x140)
        for stride in (0x14, 0x18, 0x20, 0x28):
            print(f" stride {hex(stride)}:")
            for i in range(6):
                vals = struct.unpack_from("<5I", raw, i * stride)
                print(f"   [{i:2d}] {vals}")

    k32.CloseHandle(h)


if __name__ == "__main__":
    main()

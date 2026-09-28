"""Read Dyson Sphere Program player inventory directly from process memory.

Chain (Mono x64, object header 0x10, fields from static dump of Assembly-CSharp):
  GameMain.data (static) -> S
  GameData   G = [S+0x10]            (verified by CE dissect: field "gameData" @ +0x10)
  Player     P = [G+0xC0]            (mainPlayer)
  Storage    pkg = [P+0x78]          (package, MonoBehaviour base adds m_CachedPtr @0x10)
  StorageComponent: id@0x10 entityId@0x14 prev/next/bottom/top@0x18..0x24
                    previousStorage@0x28 nextStorage@0x30 bottomStorage@0x38 topStorage@0x40
                    type@0x48 isPlayerInventory@0x4C size@0x50 bans@0x54 grids@0x58
  grids GRID[]: len@+0x10, data@+0x18
  GRID: itemId@0x10 count@0x14 inc@0x18 ordered@0x1C stackSize@0x20

Usage: python tools/read_package.py [S_hex]   # S = value seen for GameMain.data
"""
import ctypes
import ctypes.wintypes
import struct
import sys

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

k32 = ctypes.windll.kernel32


def find_pid(name="DSPGAME.exe"):
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.OpenProcess.restype = ctypes.c_void_p

    class PE32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    pe = PE32W()
    pe.dwSize = ctypes.sizeof(PE32W)
    pid = None
    if k32.Process32FirstW(snap, ctypes.byref(pe)):
        while True:
            if pe.szExeFile.lower() == name.lower():
                pid = pe.th32ProcessID
                break
            if not k32.Process32NextW(snap, ctypes.byref(pe)):
                break
    k32.CloseHandle(snap)
    return pid


def open_proc(pid):
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        raise OSError(f"OpenProcess failed err={ctypes.get_last_error()} (需要管理员权限运行?)")
    return h


def rd(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got))
    if not ok or got.value != size:
        return None
    return buf.raw


def qword(h, addr):
    b = rd(h, addr, 8)
    return struct.unpack("<Q", b)[0] if b else None


def dword(h, addr):
    b = rd(h, addr, 4)
    return struct.unpack("<I", b)[0] if b else None


def looks_ptr(v):
    return v is not None and 0x1000 < v < 0x7FFFFFFFFFFF and v % 8 == 0


def main():
    S = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x1C9770E94C0
    pid = find_pid()
    if not pid:
        print("游戏未运行")
        return
    h = open_proc(pid)
    print(f"PID={pid}  S=GameMain.data值={S:#x}")

    G = qword(h, S + 0x10)
    if not looks_ptr(G):
        print(f"[H2] [S+0x10]={G:#x} 不像指针，尝试把 S 当作 GameData 本身")
        G = S
    print(f"GameData G = {G:#x}")

    P = qword(h, G + 0xC0)
    print(f"mainPlayer P = {P:#x}")
    if not looks_ptr(P):
        print("!! P 不是合法指针，dump G+0x10..G+0xD0 供排查：")
        for off in range(0x10, 0xD8, 8):
            print(f"  G+{off:#04x} = {qword(h, G + off):#018x}")
        return

    # dump Player raw fields to verify layout
    pcol = dword(h, P + 0xA8)
    alive = rd(h, P + 0xAC, 1)[0]
    print(f"Player+0xA8 packageColCount = {pcol}  +0xAC isAlive = {alive}")
    for off in range(0x10, 0xB0, 8):
        v = qword(h, P + off)
        mark = " <== package 候选" if off == 0x78 else ("  ptr" if looks_ptr(v) else "")
        print(f"  P+{off:#04x} = {v:#018x}{mark}")

    PKG = qword(h, P + 0x78)
    print(f"package PKG = {PKG:#x}")
    if not looks_ptr(PKG):
        print("!! 0x78 不是指针，尝试 dump 里的其他候选 (逐个当 StorageComponent 验证 isPlayerInventory+size+grids)")
        cands = [qword(h, P + off) for off in range(0x20, 0xA8, 8)]
        PKG = None
        for v in cands:
            if not looks_ptr(v):
                continue
            ipi = rd(h, v + 0x4C, 1)
            size = dword(h, v + 0x50)
            grids = qword(h, v + 0x58)
            if ipi and ipi[0] == 1 and size and 0 < size < 1024 and looks_ptr(grids):
                PKG = v
                print(f"  found candidate StorageComponent: {v:#x} size={size}")
                break
        if PKG is None:
            return

    stype = dword(h, PKG + 0x48)
    ipi = rd(h, PKG + 0x4C, 1)[0]
    size = dword(h, PKG + 0x50)
    grids = qword(h, PKG + 0x58)
    print(f"StorageComponent: type={stype} isPlayerInventory={ipi} size={size} grids={grids:#x}")

    if not looks_ptr(grids):
        print("!! grids 不像指针，dump PKG+0x10..0x70:")
        for off in range(0x10, 0x78, 8):
            print(f"  PKG+{off:#04x} = {qword(h, PKG + off):#018x}")
        return

    arrlen = qword(h, grids + 0x10)
    print(f"grids 数组长度 = {arrlen}")

    # element 0: class (pointer) or inline struct?
    e0 = qword(h, grids + 0x18)
    print(f"elem0 raw qword = {e0:#x}")
    if looks_ptr(e0):
        # array of GRID references
        for i in range(min(arrlen or 0, 64)):
            item = qword(h, grids + 0x18 + i * 8)
            if not looks_ptr(item):
                continue
            itemId = dword(h, item + 0x10)
            count = dword(h, item + 0x14)
            inc = dword(h, item + 0x18)
            stack = dword(h, item + 0x20)
            if itemId:
                print(f"grid[{i:2d}] itemId={itemId:5d} count={count:6d} inc={inc} stackSize={stack}")
    else:
        # inline structs, stride = 32 (8 x i4)
        for i in range(min(arrlen or 0, 64)):
            base = grids + 0x18 + i * 32
            itemId, count, inc, ordered, stackSize = struct.unpack("<IIIII", rd(h, base, 20))
            if itemId:
                print(f"grid[{i:2d}] itemId={itemId:5d} count={count:6d} inc={inc} ordered={ordered} stackSize={stackSize}")

    k32.CloseHandle(h)


if __name__ == "__main__":
    main()

"""process.py — 外部进程内存读写 + 并发大块扫描（Windows）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
from concurrent.futures import ThreadPoolExecutor

import numpy as np

k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = ctypes.c_void_p
k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
PAGE_EXECUTE_MASK = 0xF0


class MemError(RuntimeError):
    pass


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD), ("__pad", wt.DWORD),
        ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
        ("Protect", wt.DWORD), ("Type", wt.DWORD),
    ]


def find_pid(exe_name: str) -> int:
    class PE32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    pe = PE32W()
    pe.dwSize = ctypes.sizeof(PE32W)
    pid = None
    if k32.Process32FirstW(snap, ctypes.byref(pe)):
        while True:
            if pe.szExeFile.lower() == exe_name.lower():
                pid = pe.th32ProcessID
                break
            if not k32.Process32NextW(snap, ctypes.byref(pe)):
                break
    k32.CloseHandle(snap)
    if pid is None:
        raise MemError(f"进程 {exe_name} 未运行")
    return pid


class Process:
    """一个目标进程的读/写/扫描原语。"""

    def __init__(self, exe_name: str, chunk_size: int = 4 << 20, workers: int = 8,
                 writable_only: bool = True):
        self.exe = exe_name
        self.pid = find_pid(exe_name)
        self.h = k32.OpenProcess(
            PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
            False, self.pid)
        if not self.h:
            raise MemError(f"OpenProcess 失败 err={ctypes.get_last_error()}（试试管理员运行）")
        self.chunk_size = chunk_size
        self.workers = workers
        self.writable_only = writable_only

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---- 基础读 ----
    def read(self, addr: int, n: int) -> bytes | None:
        if not addr or addr > 0x7FFFFFFFFFFF:
            return None
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got))
        return buf.raw[: got.value] if ok and got.value == n else None

    def u64(self, addr: int) -> int | None:
        b = self.read(addr, 8)
        return struct.unpack("<Q", b)[0] if b else None

    def u32(self, addr: int, default: int = 0) -> int:
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b else default

    def i32(self, addr: int, default: int = 0) -> int:
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0] if b else default

    def write_u32(self, addr: int, value: int):
        buf = struct.pack("<I", value & 0xFFFFFFFF)
        done = ctypes.c_size_t(0)
        old = wt.DWORD(0)
        if not k32.VirtualProtectEx(self.h, ctypes.c_void_p(addr), 4, 0x04, ctypes.byref(old)):
            raise MemError(f"VirtualProtectEx failed @ {addr:#x}")
        ok = k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), buf, 4, ctypes.byref(done))
        k32.VirtualProtectEx(self.h, ctypes.c_void_p(addr), 4, old, ctypes.byref(old))
        if not ok or done.value != 4:
            raise MemError(f"WriteProcessMemory failed @ {addr:#x}")

    # ---- 扫描 ----
    def chunks(self):
        """并发产出 (addr, bytes) 块（只含已提交、非可执行、私有内存）。"""
        mbi = MBI()
        addr, tasks = 0, []
        while addr < 0x7FFFFFFFFFFF:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            base, size, prot = mbi.BaseAddress, mbi.RegionSize, mbi.Protect
            if (mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE
                    and not (prot & (PAGE_NOACCESS | PAGE_GUARD | PAGE_EXECUTE_MASK))
                    and size <= 1 << 30
                    and (not self.writable_only or (prot & 0x0C))):
                for off in range(0, size, self.chunk_size):
                    tasks.append((base + off, min(self.chunk_size, size - off)))
            addr = base + size if base + size > addr else addr + 0x10000
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for (a, n), data in zip(tasks, ex.map(lambda t: self.read(t[0], t[1]), tasks)):
                if data:
                    yield a, data

    def find_bytes_multi(self, patterns: dict[str, bytes]) -> dict[str, list[int]]:
        """一次遍历找多个字节串；块间 carry 防漏；region 不连续自动放弃 carry。"""
        out: dict[str, set[int]] = {k: set() for k in patterns}
        maxlen = max(len(p) for p in patterns.values())
        carry, carry_base = b"", 0
        for base, data in self.chunks():
            if not (carry and base == carry_base + len(carry)):
                carry = b""
            buf = carry + data if carry else data
            buf_base = carry_base if carry else base
            for k, pat in patterns.items():
                pos = 0
                while True:
                    i = buf.find(pat, pos)
                    if i < 0:
                        break
                    out[k].add(buf_base + i)
                    pos = i + 1
            carry = buf[-(maxlen - 1):] if len(buf) >= maxlen - 1 else buf
            carry_base = buf_base + len(buf) - len(carry)
        return {k: sorted(v) for k, v in out.items()}

    def find_slots_multi(self, value_sets: dict[str, set[int]], limit: int = 400_000) -> dict[str, list[int]]:
        """一次遍历同时找多组 8 字节值，返回各组的槽地址。"""
        keys = [k for k, v in value_sets.items() if v]
        allvals: dict[int, str] = {}
        for k in keys:
            for v in value_sets[k]:
                allvals[v] = k
        lookup = np.array(sorted(allvals), dtype=np.uint64)
        out: dict[str, list[int]] = {k: [] for k in value_sets}
        if not len(lookup):
            return out
        for base, data in self.chunks():
            nr = len(data) // 8
            if not nr:
                continue
            u = np.frombuffer(data[: nr * 8], np.uint64)
            pos = np.clip(np.searchsorted(lookup, u), 0, len(lookup) - 1)
            sel = np.nonzero(lookup[pos] == u)[0]
            if sel.size:
                addrs = base + sel.astype(np.int64) * 8
                names = np.array([allvals[int(v)] for v in lookup[pos[sel]]])
                for k in keys:
                    m = names == k
                    out[k].extend(int(a) for a in addrs[m])
        for k in out:
            if len(out[k]) > limit:
                out[k] = out[k][:limit]
        return out

    def find_slots(self, values: set[int], limit: int = 200_000) -> list[int]:
        return self.find_slots_multi({"_": values}, limit)["_"]

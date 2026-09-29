"""dsp_core.py — Dyson Sphere Program 内存读取核心库（教学项目，逆向方法论见 README.md）

用法（库）:
    from dsp_core import DspBag
    bag = DspBag()
    bag.connect()            # 自动定位 GameData -> Player -> package -> grids（不依赖 CE）
    print(bag.summary())
    bag.set_count(slot=0, count=999)

依赖: 仅标准库 + numpy（扫描加速）。游戏必须处于「已加载存档」状态。
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import sys
import time

import numpy as np

__all__ = ["DspBag", "DspError"]

PROCESS_ALL_ACCESS_PEB = 0x1F0FFF  # 简化：按需 VM_READ/VM_WRITE/QUERY
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008  # VirtualProtectEx 需要
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
PAGE_EXECUTE_MASK = 0xF0  # 含 X 的 region 不可能是托管堆数据
PAGE_SIZE = 1 << 20

# ---------------------------------------------------------------------------
# 本版本 (DSP 1.x, Mono) 实测布局 —— 推导与验证过程见 README
# 每一项都在 connect() 时用类名/不变量复核，对不上会直接报错而不是读垃圾
# ---------------------------------------------------------------------------
OFF_GAME_MAINPLAYER = 0xC0
OFF_PLAYER_PACKAGE = 0x68
OFF_SC_SIZE = 0x70
OFF_SC_GRIDS = 0x30
OFF_ARRAY_LEN = 0x18
OFF_ARRAY_DATA = 0x20
GRID_STRIDE = 0x14
GRID_ITEMID = 0x00
GRID_COUNT = 0x08
GRID_STACK = 0x0C
OFF_PROTO_NAME = 0x20
OFF_PROTO_INDEX = 0x30
OFF_STRING_LEN = 0x10
OFF_STRING_CHARS = 0x14
MONO_CLASS_NAME_SLOT = 0x48  # class+0x48 -> char* name; 真 MonoClass 满足 class+0==class

k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = ctypes.c_void_p
k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p


class DspError(RuntimeError):
    pass


def _find_pid(name: str = "DSPGAME.exe") -> int:
    class PE32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260),
        ]

    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
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
    if pid is None:
        raise DspError(f"进程 {name} 未运行")
    return pid


find_pid = _find_pid  # 公开别名（GUI 预热探测游戏是否在跑）


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", ctypes.c_uint32), ("__pad", ctypes.c_uint32),
        ("RegionSize", ctypes.c_size_t), ("State", ctypes.c_uint32),
        ("Protect", ctypes.c_uint32), ("Type", ctypes.c_uint32),
    ]


class DspMem:
    """进程内存读写 + 向量化扫描原语。"""

    def __init__(self, exe: str = "DSPGAME.exe"):
        self.pid = _find_pid(exe)
        self.h = k32.OpenProcess(
            PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
            False, self.pid,
        )
        if not self.h:
            raise DspError(f"OpenProcess 失败 (err={ctypes.get_last_error()})，试试以管理员身份运行")

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None

    # --- 基础读 ---
    def rd(self, addr: int, n: int) -> bytes | None:
        if not addr or addr > 0x7FFFFFFFFFFF:
            return None
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got))
        return buf.raw[: got.value] if ok and got.value == n else None

    def rq(self, addr: int) -> int | None:
        b = self.rd(addr, 8)
        return struct.unpack("<Q", b)[0] if b else None

    def r4(self, addr: int, default: int = 0) -> int:
        b = self.rd(addr, 4)
        return struct.unpack("<I", b)[0] if b else default

    def write4(self, addr: int, value: int):
        buf = struct.pack("<I", value & 0xFFFFFFFF)
        done = ctypes.c_size_t(0)
        old = ctypes.c_uint32(0)
        if not k32.VirtualProtectEx(self.h, ctypes.c_void_p(addr), 4, 0x04, ctypes.byref(old)):
            raise DspError(f"VirtualProtectEx failed at {addr:#x}")
        ok = k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), buf, 4, ctypes.byref(done))
        k32.VirtualProtectEx(self.h, ctypes.c_void_p(addr), 4, old, ctypes.byref(old))
        if not ok or done.value != 4:
            raise DspError(f"WriteProcessMemory failed at {addr:#x}")

    # --- 扫描原语（只扫私有可读写内存 = 托管堆，跳过镜像/映射/可执行）---
    CHUNK = 16 << 20  # 16MB 大块: syscall 数降 4 倍

    def _region_tasks(self, lo=0, hi=0x7FFFFFFFFFFF):
        mbi = MBI()
        addr = 0
        tasks = []
        while addr < 0x7FFFFFFFFFFF:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            base, size, prot = mbi.BaseAddress, mbi.RegionSize, mbi.Protect
            if (
                mbi.State == MEM_COMMIT
                and mbi.Type == MEM_PRIVATE
                and not (prot & (PAGE_NOACCESS | PAGE_GUARD | PAGE_EXECUTE_MASK))
                and size <= 1 << 30
                and base + size > lo and base < hi          # 地址窗口剪枝
            ):
                for off in range(0, size, self.CHUNK):
                    tasks.append((base + off, min(self.CHUNK, size - off)))
            addr = base + size if base + size > addr else addr + 0x10000
        return tasks

    def chunks(self, lo=0, hi=0x7FFFFFFFFFFF, zero_copy=False):
        """并发读所有候选块。zero_copy=True 时 yield (addr, ctypes_buffer, nbytes)，
        numpy 直接 frombuffer 免一次 11GB memcpy；find 类调用方要 bytes 用 zero_copy=False。"""
        from concurrent.futures import ThreadPoolExecutor
        tasks = self._region_tasks(lo, hi)

        def _read(t):
            a, n = t
            buf = ctypes.create_string_buffer(n)
            got = ctypes.c_size_t(0)
            ok = k32.ReadProcessMemory(self.h, ctypes.c_void_p(a), buf, n, ctypes.byref(got))
            return (a, buf, got.value) if ok and got.value else None

        with ThreadPoolExecutor(max_workers=8) as ex:
            for r in ex.map(_read, tasks):
                if r:
                    if zero_copy:
                        yield r
                    else:
                        yield r[0], r[1].raw[: r[2]]

    def find_slots(self, values: set[int], limit: int = 200_000, lo=0, hi=0x7FFFFFFFFFFF) -> list[int]:
        return self.find_slots_multi({"_": values}, limit, lo, hi)["_"]

    def find_slots_multi(self, value_sets: dict[str, set[int]], limit: int = 400_000,
                         lo=0, hi=0x7FFFFFFFFFFF) -> dict[str, list[int]]:
        """一次内存遍历同时查找多个目标值集合。zero_copy: numpy 直接吃 ctypes 缓冲。"""
        keys = [k for k, v in value_sets.items() if v]
        allvals: dict[int, str] = {}
        for k in keys:
            for v in value_sets[k]:
                allvals[v] = k
        lookup = np.array(sorted(allvals), dtype=np.uint64)
        out: dict[str, list[int]] = {k: [] for k in value_sets}
        if not len(lookup):
            return out
        for base, buf, nbytes in self.chunks(lo, hi, zero_copy=True):
            nr = nbytes // 8
            if not nr:
                continue
            u = np.frombuffer(buf, np.uint64, count=nr)
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

    def find_bytes_multi(self, patterns: dict[str, bytes]) -> dict[str, list[int]]:
        """一次遍历找多个字符串。用 carry 处理块交界；结果集去重。"""
        out: dict[str, set[int]] = {k: set() for k in patterns}
        maxlen = max(len(p) for p in patterns.values())
        carry = b""
        carry_base = 0
        for base, data in self.chunks():
            if not (carry and base == carry_base + len(carry)):
                carry = b""       # region 不连续：carry 拼接会算错地址，放弃
            buf = carry + data if carry else data
            buf_base = (carry_base if carry else base)
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


class DspBag:
    """高层接口：定位 + 读背包 + 改数量 + 物品名解析。"""

    def __init__(self, verbose=True):
        self.mem = DspMem()
        self.verbose = verbose
        self._name_cache: dict[int, str | None] = {}
        self.S = self.P = self.PKG = self.ARR = None
        self.names: dict[int, str] = {}
        self.protos: dict[int, int] = {}
        self._stack_off = None

    def log(self, msg):
        if self.verbose:
            print(msg, file=sys.stderr, flush=True)

    def close(self):
        self.mem.close()

    # ------------------------------------------------------------------ cname
    def _utf8(self, addr):
        b = self.mem.rd(addr, 48)
        if not b:
            return None
        i = b.find(b"\0")
        if i < 2 or i > 42:
            return None
        t = b[:i]
        try:
            s = t.decode("ascii")
        except UnicodeDecodeError:
            return None
        return s if all(c.isalnum() or c in "_.$[]`<" for c in s) else None

    def cname(self, vt: int | None) -> str | None:
        """vtable* -> 类名（vtable+0/8/10 -> MonoClass*, class+0x48 -> char* name）"""
        if not vt or not (0x1000 < vt < 0x7FFFFFFFFFFF):
            return None
        if vt in self._name_cache:
            return self._name_cache[vt]
        nm = None
        for coff in (0x00, 0x08, 0x10):
            cl = self.mem.rq(vt + coff)
            if cl and 0x1000 < cl < 0x7FFFFFFFFFFF:
                np_ = self.mem.rq(cl + MONO_CLASS_NAME_SLOT)
                if np_:
                    s = self._utf8(np_)
                    if s and (s.isidentifier() or s.endswith("[]")):
                        nm = s
                        break
        self._name_cache[vt] = nm
        return nm

    # ------------------------------------------------------- 合并扫描（4 次遍历）
    WINDOW_PAD = 48 << 30  # mono 元数据/堆地址聚簇半径（实测跨度 ~16GB，留 3 倍余量）

    def locate(self):
        """GameData + ItemProto 一次搞定：
        pass1 字符串 -> pass2 slots==串址(得class) -> pass3 slots==class(得vtable)
        -> pass4 slots==vtable(得实例)。两条链共享每一趟遍历。
        提速: pass2-4 只扫 pass1 命中位置 ±48GB 窗口（mono 分配聚簇），失败自动回退全量。"""
        mem = self.mem
        t0 = time.time()
        self.log("扫描 #1/4: 类名串 …")
        strs = mem.find_bytes_multi({"GameData": b"GameData\x00", "ItemProto": b"ItemProto\x00"})
        strset = {k: set(v) for k, v in strs.items()}
        all_hits = strs["GameData"] + strs["ItemProto"]
        if not all_hits:
            raise DspError("找不到类名字符串（游戏未运行/IL2CPP/元数据被剥离？）")
        lo = max(0, min(all_hits) - self.WINDOW_PAD)
        hi = max(all_hits) + self.WINDOW_PAD
        self.log(f"   串 {len(all_hits)} 处; 剪枝窗口 {lo:#x}~{hi:#x}")

        def _classes(slots):
            out = {}
            for k, locs in slots.items():
                cands = {L - MONO_CLASS_NAME_SLOT for L in locs}
                out[k] = {c for c in cands if mem.rq(c) == c}  # 自引用 = 真 class
            return out

        self.log("扫描 #2/4: 谁存着串地址 -> MonoClass 候选 …")
        classes = _classes(mem.find_slots_multi(strset, lo=lo, hi=hi))
        if not classes.get("GameData"):
            self.log("   窗口内未命中, 回退全量 …")
            classes = _classes(mem.find_slots_multi(strset))
        if not classes.get("GameData"):
            raise DspError("找不到 GameData 类（游戏未运行/未加载存档/被剥离元数据？）")
        self.log(f"   class*: GameData={[hex(c) for c in classes['GameData']]}")

        self.log("扫描 #3/4: 谁存着 class -> vtable …")
        # vtable+0 == class 常见；class 本体也满足，一并收下当 header
        hdr = mem.find_slots_multi(classes, lo=lo, hi=hi)
        headers = {k: set(v) | classes[k] for k, v in hdr.items()}

        self.log("扫描 #4/4: 谁存着 header -> 实例 …")
        cl_gd = classes["GameData"]

        def _gd_insts(obj_slots):
            return [o for o in obj_slots["GameData"]
                    if mem.rq(mem.rq(o) or 0) in cl_gd and o not in cl_gd]

        obj_slots = mem.find_slots_multi(headers, limit=2_000_0, lo=lo, hi=hi)
        gd = _gd_insts(obj_slots)
        if not gd:  # 实例可能落在窗口外（Boehm 堆另开段），全量回退
            self.log("   窗口内无实例, 回退全量 …")
            obj_slots = mem.find_slots_multi(headers, limit=2_000_0)
            gd = _gd_insts(obj_slots)
        cl_ip = classes.get("ItemProto", set())
        ip_insts = [o for o in obj_slots.get("ItemProto", [])
                    if mem.rq(mem.rq(o) or 0) in cl_ip and o not in cl_ip] if cl_ip else []
        self.log(f"locate 总耗时 {time.time()-t0:.1f}s")
        return sorted(set(gd)), sorted(set(ip_insts))

    # ------------------------------------------------------------- 验证整条链
    def _verify_chain(self, S: int | None = None) -> bool:
        mem = self.mem
        S = self.S if S is None else S
        try:
            if not S or self.cname(mem.rq(S)) != "GameData":
                return False
            P = mem.rq(S + OFF_GAME_MAINPLAYER)
            if not P or self.cname(mem.rq(P)) != "Player":
                return False
            pkg = mem.rq(P + OFF_PLAYER_PACKAGE)
            if not pkg or self.cname(mem.rq(pkg)) != "StorageComponent":
                return False
            arr = mem.rq(pkg + OFF_SC_GRIDS)
            if not arr or self.cname(mem.rq(arr)) != "GRID[]":
                return False
            size = mem.r4(pkg + OFF_SC_SIZE)
            if size != mem.rq(arr + OFF_ARRAY_LEN) or not 1 <= size <= 4096:
                return False
        except Exception:
            return False
        self.S, self.P, self.PKG, self.ARR = S, P, pkg, arr
        return True

    # ------------------------------------------------------------------ chain
    def connect(self, build_names: bool = True, use_cache: bool = True):
        mem = self.mem
        if use_cache and self._load_cache():
            self.log(f"缓存命中: GameData={self.S:#x}（验证通过，0 扫描）")
            if not build_names or self.names:
                return self
            return self

        S_cands, ip_insts = self.locate()
        good = [S for S in S_cands if self._verify_chain(S)]
        if len(good) != 1:
            raise DspError(f"GameData 实例异常: {[hex(x) for x in good]}（多存档/主菜单状态？）")
        self.S = good[0]
        self.log(f"GameData={self.S:#x} package={self.PKG:#x} grids={self.ARR:#x} 容量={mem.r4(self.PKG+OFF_SC_SIZE)}")

        self.names = self._names_from_protos(ip_insts)
        if use_cache:
            self._save_cache()
        return self

    def _names_from_protos(self, insts: list[int]) -> dict[int, str]:
        mem = self.mem
        names = {}
        protos = {}
        for o in insts:
            if self.cname(mem.rq(o)) != "ItemProto":
                continue
            idx = mem.r4(o + OFF_PROTO_INDEX)
            sp = mem.rq(o + OFF_PROTO_NAME)
            if not sp or not (0 < idx < 70000) or idx in protos:
                continue
            n = mem.r4(sp + OFF_STRING_LEN)
            if not 0 < n < 64:
                continue
            b = mem.rd(sp + OFF_STRING_CHARS, n * 2)
            if b:
                names[idx] = b.decode("utf-16-le", "?")
                protos[idx] = o
        self.names = names
        self.protos = protos
        self.log(f"名表: {len(names)} 个物品")
        return names

    # ---------------------------------------- 堆叠上限：从整条 storage 链实测收集
    def _find_grid_arr(self, o: int) -> int | None:
        """对象前 0x68 字节里找指向 GRID[] 数组的字段（各 storage 子类偏移不一）。"""
        mem = self.mem
        if not o or not (0x1000 < o < 0x7FFFFFFFFFFF):
            return None
        for off in range(0x08, 0x78, 8):  # grids 偏移在子类里会变(Storage@+0x30, Delivery@+0x10)
            v = mem.rq(o + off)
            if v and 0x1000 < v < 0x7FFFFFFFFFFF and self.cname(mem.rq(v)) == "GRID[]":
                return v
        return None

    def _stack_table(self):
        """{itemId: 实测 stackSize}：候选集 = Player 字段(一跳) ∪ 字段对象的字段(二跳)，
        统一探测（本体背包/配送包/机甲五仓……），凡含 GRID[] 的容器全部采集。
        堆叠值含科技/增产倍率，直接可用。"""
        if getattr(self, "_stack_tab", None) is not None:
            return self._stack_tab
        mem = self.mem
        candidates = {self.PKG}
        l1 = [mem.rq(self.P + off) for off in range(0x10, 0xF0, 8)]
        for o in l1:
            if o and 0x1000 < o < 0x7FFFFFFFFFFF:
                candidates.add(o)
                for off in range(0x08, 0x80, 8):
                    v = mem.rq(o + off)
                    if v and 0x1000 < v < 0x7FFFFFFFFFFF:
                        candidates.add(v)
        arrays = []
        for c in candidates:
            a = self._find_grid_arr(c)
            if a and a not in arrays:
                arrays.append(a)
        if self.ARR not in arrays:
            arrays.append(self.ARR)
        tab = {}
        for garr in arrays:
            L = mem.rq(garr + 0x18) or 0
            if not 0 < L <= 4096:
                continue
            for i in range(int(L)):
                b = mem.rd(garr + 0x20 + i * GRID_STRIDE, GRID_STRIDE)
                if not b:
                    break
                itemId, stack = struct.unpack_from("<I", b, 0)[0], struct.unpack_from("<I", b, 12)[0]
                if itemId and stack and itemId in self.names:  # 过滤非 GRID 布局容器的幽灵 id
                    tab[itemId] = max(tab.get(itemId, 0), stack)
        self._stack_tab = tab
        self.log(f"堆叠实测表: {len(tab)} 个物品（实测容器数组 {len(arrays)} 组）")
        return tab

    def stack_of(self, item_id: int, default: int = 100) -> int:
        t = self._stack_table()
        return t.get(item_id, default)

    def stack_of_exact(self, item_id: int) -> int | None:
        """实测表里有没有 —— give 用它决定是否钳制。"""
        return self._stack_table().get(item_id)

    def read_grid(self, arr: int, upto: int | None = None) -> list[dict]:
        """读一个 GRID[] 数组的所有格：[{itemId,name,count,stack}]。"""
        mem = self.mem
        L = mem.rq(arr + 0x18) or 0
        L = min(int(L), upto or 4096)
        out = []
        for i in range(L):
            b = mem.rd(arr + OFF_ARRAY_DATA + i * GRID_STRIDE, GRID_STRIDE)
            if not b:
                break
            itemId, _, count, stack = struct.unpack_from("<4I", b, 0)
            known = itemId in self.names
            out.append({"slot": i, "itemId": itemId, "count": count, "stack": stack,
                        "known": known,
                        "name": self.names.get(itemId, f"#{itemId}") if itemId else ""})
        return out

    def find_containers(self) -> list[dict]:
        """Player 两跳内所有含 GRID[] 的容器：[{owner_class, grids, items}]。
        用于展示机甲仓/配送包等非背包容器。"""
        mem = self.mem
        cand = []
        seen = set()
        for o in [mem.rq(self.P + off) for off in range(0x10, 0xF0, 8)] + [self.PKG]:
            if o and 0x1000 < o < 0x7FFFFFFFFFFF and o not in seen:
                seen.add(o)
                cand.append((o, "Player.*"))
            if o and 0x1000 < o < 0x7FFFFFFFFFFF:
                for off in range(0x08, 0x80, 8):
                    v = mem.rq(o + off)
                    if v and 0x1000 < v < 0x7FFFFFFFFFFF and v not in seen:
                        seen.add(v)
                        cand.append((v, f"{self.cname(mem.rq(o))}+{off:#x}"))
        out, arrs = [], set()
        for obj, src in cand:
            g = self._find_grid_arr(obj)
            if g and g not in arrs:
                arrs.add(g)
                cn = self.cname(mem.rq(obj))
                allr = self.read_grid(g)
                filled = [r for r in allr if r["itemId"]]
                # 布局可信度: 非空格都应是注册物品。幽灵 id 多 => stride 不对(如配送包 0x20 特殊布局)
                layout_ok = all(r["known"] for r in filled)
                out.append({"owner": cn, "path": src, "grids": g,
                            "len": int(mem.rq(g + 0x18) or 0),
                            "items": [r for r in filled if r["known"]],
                            "layout_ok": layout_ok,
                            "is_main": obj == self.PKG})
        return out

    # ------------------------------------------------------------------ 缓存
    @staticmethod
    def app_dir() -> str:
        """打包成 exe 后 __file__ 在临时解压目录，缓存必须落在 exe 旁边。"""
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    @property
    def CACHE_FILE(self) -> str:
        return os.path.join(self.app_dir(), ".dsp_cache.json")

    def _proc_start(self) -> int:
        """进程启动时间（100ns），防止 PID 复用导致缓存误用。"""
        class FILETIME(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
        h = k32.OpenProcess(0x1000, False, self.mem.pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return 0
        a = FILETIME(); b = FILETIME(); c = FILETIME(); d = FILETIME()
        # 参数序: Creation, Exit, Kernel, User —— 必须用创建时间（稳定），别拿 User（一直在涨）
        ok = k32.GetProcessTimes(h, ctypes.byref(a), ctypes.byref(b), ctypes.byref(c), ctypes.byref(d))
        k32.CloseHandle(h)
        return (a.hi << 32 | a.lo) if ok else 0

    def _save_cache(self):
        data = {
            "pid": self.mem.pid, "start": self._proc_start(),
            "S": self.S, "PKG": self.PKG, "ARR": self.ARR,
            "names": {str(k): v for k, v in self.names.items()},
            "protos": {str(k): v for k, v in self.protos.items()},
            "stack_off": getattr(self, "_stack_off", None),
        }
        with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _load_cache(self) -> bool:
        try:
            with open(self.CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if "protos" not in data:  # 旧版缓存没有 protos，作废重建
            return False
        if data.get("pid") != self.mem.pid or data.get("start") != self._proc_start():
            return False  # 进程已重启，地址作废
        # 关键：缓存只当"提示"，必须逐字节验证
        if self._verify_chain(data.get("S")):
            self.names = {int(k): v for k, v in data.get("names", {}).items()}
            self.protos = {int(k): v for k, v in data.get("protos", {}).items()}
            self._stack_off = data.get("stack_off")
            return True
        return False

    # ------------------------------------------------------------------ 读取
    def slots(self):
        """返回 [{slot,itemId,name,count,stack}, ...]（40 格）"""
        mem = self.mem
        if self.ARR is None:
            raise DspError("先 connect()")
        L = int(mem.rq(self.ARR + OFF_ARRAY_LEN) or 0)
        out = []
        for i in range(L):
            b = mem.rd(self.ARR + OFF_ARRAY_DATA + i * GRID_STRIDE, GRID_STRIDE)
            if not b:
                continue
            itemId = struct.unpack_from("<I", b, GRID_ITEMID)[0]
            count = struct.unpack_from("<I", b, GRID_COUNT)[0]
            stack = struct.unpack_from("<I", b, GRID_STACK)[0]
            out.append({
                "slot": i, "itemId": itemId, "count": count, "stack": stack,
                "name": self.names.get(itemId, f"#{itemId}") if itemId else "",
            })
        return out

    def summary(self):
        rows = self.slots()
        total = sum(r["count"] for r in rows)
        lines = [f"背包（已用 {sum(1 for r in rows if r['itemId'])}/{len(rows)}，总 {total}）"]
        for r in rows:
            if r["itemId"]:
                lines.append(f"  [{r['slot']:2d}] {r['name']:<16s} x{r['count']:<6d} (上限{r['stack']})")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 模糊查找
    def search(self, query: str, limit: int = 30) -> list[tuple[int, str, int]]:
        """按 中文名 / itemId 模糊匹配，返回 [(id, name, score)] 分数高优先。
        规则：完全相等 > 前缀 > 子串 > 逐字符子序列(跳字)。数字查询按 id 命中。"""
        q = query.strip().lower()
        if not q:
            return []
        out = []
        for i, n in self.names.items():
            nl = n.lower()
            if nl == q or str(i) == q:
                score = 100
            elif nl.startswith(q):
                score = 80
            elif q in nl:
                score = 60
            else:  # 跳字子序列：q 的字符按序出现在 n 中
                it = iter(nl)
                score = 20 if all(ch in it for ch in q) else 0
            if score:
                out.append((i, n, score))
        out.sort(key=lambda x: (-x[2], x[0]))
        return out[:limit]

    # ------------------------------------------------------------------ 写入
    def grid_base(self, slot: int) -> int:
        if self.ARR is None:
            raise DspError("先 connect()")
        return self.ARR + OFF_ARRAY_DATA + slot * GRID_STRIDE

    def set_count(self, slot: int, count: int, clamp=True) -> int:
        return self.set_count_in(self.ARR, slot, count, clamp)

    def set_count_in(self, arr: int, slot: int, count: int, clamp=True) -> int:
        """通用：对任意 GRID[] 数组（主背包/机甲仓/…）的某格改数量。"""
        mem = self.mem
        base = arr + OFF_ARRAY_DATA + slot * GRID_STRIDE
        itemId = mem.r4(base + GRID_ITEMID)
        if not itemId:
            raise DspError(f"slot {slot} 是空格，改数量无意义（想加物品用 give）")
        stack = mem.r4(base + GRID_STACK) or 100
        requested = count
        if clamp and count > stack:
            count = stack
        addr = base + GRID_COUNT
        old = mem.r4(addr)
        mem.write4(addr, count)
        new = mem.r4(addr)
        if new != count:
            raise DspError(f"写入校验失败: {old} -> 期望 {count} 实得 {new}")
        extra = f"（请求 {requested} 已钳制到上限 {stack}）" if requested != count else ""
        self.log(f"arr{arr:#x} slot {slot} ({self.names.get(itemId)}): {old} -> {new} {extra}")
        return count

    def give(self, slot: int, item_id: int, count: int = None, force=False) -> int:
        """往指定格写入物品（空格或覆盖已有格）。stackSize 来自 ItemProto 自校准。返回实际 count。"""
        mem = self.mem
        base = self.grid_base(slot)
        if item_id not in self.names and not force:
            raise DspError(f"itemId {item_id} 不在名表（force=True 可强写）")
        cur = mem.r4(base + GRID_ITEMID)
        if cur and not force:
            raise DspError(f"slot {slot} 非空（{self.names.get(cur)}），覆盖需 force=True")
        tab = self._stack_table()
        stack = tab.get(item_id)
        if stack is None:  # 表里没有：可能背包刚变过，强制重测一次
            self._stack_tab = None
            stack = self._stack_table().get(item_id)
        measured = stack is not None
        stack = stack or 100
        if count is None:
            count = stack
        elif measured and count > stack:
            count = stack
        mem.write4(base + GRID_ITEMID, item_id)
        mem.write4(base + GRID_STACK, stack)
        mem.write4(base + GRID_COUNT, count)
        if mem.r4(base + GRID_ITEMID) != item_id or mem.r4(base + GRID_COUNT) != count:
            raise DspError("give 写入校验失败")
        self.log(f"give slot {slot} <- {item_id} {self.names.get(item_id)} x{count} (上限{stack})")
        return count

    def clear_slot(self, slot: int):
        base = self.grid_base(slot)
        mem = self.mem
        for off in (GRID_ITEMID, GRID_COUNT, GRID_STACK, 0x04, 0x10):
            mem.write4(base + off, 0)

    def first_empty(self) -> int | None:
        mem = self.mem
        for r in self.slots():
            if not r["itemId"]:
                return r["slot"]
        return None

    # ---------------------------------------------------------------- 沙土计数
    # Player+0x170 = 沙土(sand, itemId 1099)持有量，CE 地址验证过: Player+0x170。
    # 不是背包格子，是 Player 上的独立计数器。
    SAND_OFF = 0x170

    def get_sand(self) -> int:
        return self.mem.r4(self.P + self.SAND_OFF)

    def set_sand(self, value: int) -> int:
        v = max(0, int(value))
        addr = self.P + self.SAND_OFF
        self.mem.write4(addr, v)
        new = self.mem.r4(addr)
        if new != v:
            raise DspError(f"沙土写入校验失败: 期望 {v} 实得 {new}")
        self.log(f"sand: -> {new}")
        return new

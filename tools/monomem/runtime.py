"""runtime.py — Mono 运行时对象模型：类定位 / 类名解析 / 实例扫描 / 对象封装。

核心方法论（DSP 项目验证过，适用于任何 Unity Mono x64 游戏）：
1. 类名字符串 -> MonoClass*：全内存找 b"Name\\0"，再找"哪些 8 字节槽存着这些地址"，
   槽地址-class_name_ptr 即候选 class，用自引用 class+0==class 过滤，误杀为零
2. vtable -> 类名：对象头 vtable*，vtable 前几个 qword 之一是 class*，class+0x48 即名字
3. 实例扫描：class -> 引用它的 vtable -> [obj]==vtable 的对象
"""
from __future__ import annotations

import struct

from .layout import Layout, DSP
from .process import Process, MemError

__all__ = ["MonoGame", "MonoClass", "MonoObject", "MonoArray", "Layout", "MemError"]


def _is_ptr(v) -> bool:
    return v is not None and 0x1000 < v < 0x7FFFFFFFFFFF


class MonoObject:
    """一个托管对象的读视图。"""

    def __init__(self, game: "MonoGame", addr: int):
        self.game = game
        self.addr = addr

    def __repr__(self):
        return f"<MonoObject {self.addr:#x} {self.class_name}>"

    @property
    def vtable(self) -> int | None:
        return self.game.mem.u64(self.addr + self.game.L.obj_vtable)

    @property
    def class_name(self) -> str | None:
        return self.game.cname(self.vtable)

    # ---- 字段读 ----
    def u32(self, off: int) -> int:
        return self.game.mem.u32(self.addr + off)

    def i32(self, off: int) -> int:
        return self.game.mem.i32(self.addr + off)

    def u64(self, off: int) -> int | None:
        return self.game.mem.u64(self.addr + off)

    def f32(self, off: int) -> float | None:
        b = self.game.mem.read(self.addr + off, 4)
        return struct.unpack("<f", b)[0] if b else None

    def f64(self, off: int) -> float | None:
        b = self.game.mem.read(self.addr + off, 8)
        return struct.unpack("<d", b)[0] if b else None

    def ref(self, off: int) -> "MonoObject | None":
        """引用类型字段 -> MonoObject（不校验类名）。"""
        v = self.u64(off)
        return MonoObject(self.game, v) if _is_ptr(v) else None

    def str(self, off: int) -> str | None:
        """string 字段。"""
        o = self.ref(off)
        return self.game.read_string(o.addr) if o else None

    def array(self, off: int) -> "MonoArray | None":
        """数组字段（校验类名以 [] 结尾）。"""
        o = self.ref(off)
        if not o:
            return None
        cn = o.class_name
        return MonoArray(self.game, o.addr) if cn and cn.endswith("[]") else None

    def bytes(self, off: int, n: int) -> bytes | None:
        return self.game.mem.read(self.addr + off, n)


class MonoArray(MonoObject):
    def __init__(self, game: "MonoGame", addr: int):
        super().__init__(game, addr)

    @property
    def length(self) -> int:
        return int(self.game.mem.u64(self.addr + self.game.L.arr_len) or 0)

    def refs(self) -> list[MonoObject | None]:
        """引用数组：元素是对象指针。"""
        out = []
        for i in range(self.length):
            v = self.game.mem.u64(self.addr + self.game.L.arr_data + i * 8)
            out.append(MonoObject(self.game, v) if _is_ptr(v) else None)
        return out

    def structs(self, stride: int):
        """值类型数组：产出每个元素的原始字节。"""
        for i in range(self.length):
            b = self.game.mem.read(self.addr + self.game.L.arr_data + i * stride, stride)
            if b:
                yield i, b

    def u32s(self) -> list[int]:
        b = self.game.mem.read(self.addr + self.game.L.arr_data, self.length * 4)
        return list(struct.unpack(f"<{self.length}I", b)) if b else []


class MonoClass:
    def __init__(self, game: "MonoGame", addr: int, name: str):
        self.game = game
        self.addr = addr
        self.name = name

    def __repr__(self):
        return f"<MonoClass {self.name} @{self.addr:#x}>"

    def instances(self) -> list[MonoObject]:
        return self.game.instances_of(self)


class MonoGame:
    """入口：附着进程 + Mono 运行时查询。

    用法:
        g = MonoGame("Game.exe")
        cls = g.class_("GameData")          # 全内存定位类（结果缓存）
        obj = cls.instances()[0]            # 实例
        player = obj.ref(0xC0)              # 字段
        print(player.class_name, player.str(0x28))
    """

    def __init__(self, exe: str, layout: Layout | None = None, verbose: bool = False,
                 process: Process | None = None):
        self.L = (layout or DSP).validate()
        self.verbose = verbose
        self.mem = process or Process(exe, self.L.chunk_size, self.L.workers, self.L.writable_only)
        self._cname_cache: dict[int, str | None] = {}
        self._class_cache: dict[str, list[MonoClass]] = {}

    def log(self, msg):
        if self.verbose:
            print(f"[monomem] {msg}", flush=True)

    def close(self):
        self.mem.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------------ 类名
    def _utf8(self, addr) -> str | None:
        if not _is_ptr(addr):
            return None
        b = self.mem.read(addr, 48)
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
        return s if all(c.isalnum() or c in "_.$[]`<>" for c in s) else None

    def cname(self, vtable: int | None) -> str | None:
        """vtable* -> 类名。"""
        if not _is_ptr(vtable):
            return None
        if vtable in self._cname_cache:
            return self._cname_cache[vtable]
        nm = None
        for coff in self.L.vtable_class_offsets:
            cl = self.mem.u64(vtable + coff)
            if _is_ptr(cl):
                s = self._utf8(self.mem.u64(cl + self.L.class_name_ptr))
                if s and (s.isidentifier() or s.endswith("[]") or "`" in s):
                    nm = s
                    break
        self._cname_cache[vtable] = nm
        return nm

    # ------------------------------------------------------------------ 类定位
    def classes(self, name: str) -> list[MonoClass]:
        """按名字定位全部 MonoClass（同名可能多份，如泛型实例化）。带缓存。"""
        if name in self._class_cache:
            return self._class_cache[name]
        self.log(f"定位类 {name} …")
        strs = self.mem.find_bytes_multi({name: name.encode() + b"\0"})[name]
        if not strs:
            self._class_cache[name] = []
            return []
        slots = self.mem.find_slots(set(strs))
        cands = {s - self.L.class_name_ptr for s in slots}
        out = []
        for c in sorted(cands):
            if self.mem.u64(c + self.L.class_self_ref) == c:  # 自引用身份证
                out.append(MonoClass(self, c, name))
        self.log(f"  {name}: {len(out)} 个 class")
        self._class_cache[name] = out
        return out

    def class_(self, name: str) -> MonoClass:
        cs = self.classes(name)
        if not cs:
            raise MemError(f"找不到类 {name}")
        return cs[0]

    # ------------------------------------------------------------------ 实例
    def instances_of(self, cls: MonoClass) -> list[MonoObject]:
        """class -> vtable(被引用处) -> [obj]==vtable 的对象。"""
        self.log(f"扫描 {cls.name} 实例 …")
        locs = self.mem.find_slots({cls.addr})          # 谁存着 class*（vtable+0 等）
        headers = set(locs)
        headers.add(cls.addr)                            # 自引用槽也算
        objs = self.mem.find_slots(headers)              # 谁存着这些 header
        out = []
        for o in objs:
            if o == cls.addr:
                continue
            vt = self.mem.u64(o)
            if vt in headers and self.mem.u64(vt) == cls.addr:
                out.append(MonoObject(self, o))
        out = list({o.addr: o for o in out}.values())
        self.log(f"  {cls.name}: {len(out)} 个实例")
        return out

    def instances(self, class_name: str) -> list[MonoObject]:
        return self.class_(class_name).instances()

    # ------------------------------------------------------------------ 字符串
    def read_string(self, addr: int) -> str | None:
        """MonoString* -> python str。"""
        if not _is_ptr(addr):
            return None
        n = self.mem.u32(addr + self.L.str_len)
        if not 0 <= n < 4096:
            return None
        b = self.mem.read(addr + self.L.str_chars, n * 2)
        return b.decode("utf-16-le", "?") if b else None

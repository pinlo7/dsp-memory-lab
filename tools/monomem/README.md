# monomem — 通用 Unity Mono 游戏外部内存读取库

从「戴森球计划背包工具」沉淀的逆向基础设施。**不注入、不改游戏文件、不需要
Cheat Engine**，纯 `ReadProcessMemory` + numpy 扫描，适用于任何 Unity **Mono**
后端（非 IL2CPP）的 x64 游戏。

## 30 秒上手

```python
from monomem import MonoGame

g = MonoGame("Game.exe", verbose=True)      # 附着进程

cls = g.class_("GameData")                  # 按类名定位 MonoClass（全内存扫描，~1分钟）
objs = cls.instances()                      # 该类的所有实例
gd = objs[0]

player = gd.ref(0xC0)                       # 引用字段 -> MonoObject
print(player.class_name)                    # "Player"（vtable 反查，用来验证偏移）
print(player.str(0x28))                     # string 字段
arr = some_obj.array(0x30)                  # 数组字段（校验类名以 [] 结尾）
print(arr.length, arr.refs()[0].class_name) # 引用数组
for i, b in arr.structs(0x14):              # 值类型数组（stride 自定义）
    ...
g.mem.write_u32(addr, 999)                  # 写（带 VirtualProtect 恢复）
```

## 它替你解决的三件脏活

1. **类定位**：`class_("Name")` = 全内存找 `b"Name\0"` → 找存着串地址的槽 →
   `槽-0x48` 候选 → **自引用 `class+0==class` 过滤**（MonoClass 唯一身份证，误杀为零）
2. **类名反查**：`obj.class_name` = vtable → class* → `class+0x48` 名字串。
   逆向时"猜偏移"变成"验布局"：字段指着的对象类名不对，立刻知道猜错了
3. **并发扫描**：4MB 大块 + 8 线程 + 只扫可写私有内存 + 多目标合并遍历
   （11GB 进程实测单趟 ~20s）

## 版本相关偏移都在 `layout.py`

对象头大小、String/Array 布局、类名字段位置——不同 Unity/Mono 版本可能不同。
对不上时**先校准 `Layout`，别改逻辑**。默认值 = 戴森球计划（Unity 2019.4, Mono
bleeding edge x64）实测：对象头 0x18、String len@0x10 chars@0x14(UTF-16)、
Array len@0x18 data@0x20、class name@0x48。

## 实战参考

`tools/examples/dsp_via_monomem.py`：用本库 60 行重写 DSP 背包读取
（GameData→Player→StorageComponent→GRID[] + 175 物品名表），与专用实现输出一致。

## 边界与已知限制

- 只支持 **Mono**（IL2CPP 游戏没有运行时元数据，这套方法不适用）
- 定位类/实例是全内存扫描，**慢**（分钟级）；库不做缓存——上层自己存地址
  （参考 `dsp_core.py` 的 `.dsp_cache.json`：pid+进程创建时间+逐字节验链）
- `instances_of` 靠"槽值==vtable"反查，会漏掉 vtable 被内联优化的极端情况；
  找到候选后务必用 `class_name`/字段不变量复核
- 写内存前想清楚：游戏 GC 不搬移对象（Boehm），地址在进程生命周期内稳定，
  但**重启就变**，任何硬编码地址都要有验证-重找机制

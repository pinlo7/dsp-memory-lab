# experiments/ — 逆向过程的实验脚本（教学参考，非产品代码）

这些是打通指针链**过程中**写的一次性探索脚本，保留了当时的思路（包括走弯路的），
按时间顺序阅读约等于重走一遍逆向现场。正式工具请用上层 `dsp_core.py`。

> ⚠️ 脚本里的十六进制地址（如 `0x1C9770E94C0`）是**当次游戏会话的运行时地址**，
> 重启游戏即失效——这正是后来 `dsp_core` 做"类名解析 + 自动定位 + 缓存验证"的原因。
> 运行方式：`cd tools/experiments && python <脚本>.py`（相互有 import，需在本目录跑）。

| 脚本 | 阶段 | 干了什么 |
|---|---|---|
| `read_package.py` | 1 | 进程附着/PID 查找/基础读内存 helper（被后续脚本 import） |
| `scan_chain.py` | 1 | 从 CE 给的锚点 BFS 找"结构合理"的 StorageComponent（启发式，后被类名法取代） |
| `find_grids.py` | 2 | 全局扫 GRID 特征找背包数组（第一版，误报多） |
| `find_grids2.py` | 2 | 按 vtable 分组改进版（发现 GRID 是 struct 内联数组的关键一步） |
| `final_chain.py` | 3 | **vtable→类名解析器**首次跑通：自动定位 package/grids 并 dump 数组 |
| `find_obj.py` | 4 | 通用"类名→MonoClass→vtable→实例"四遍扫描（monomem 的前身） |
| `item_names.py` | 4 | 175 物品名表提取（ItemProto 实例 + String 解码） |

静态元数据三件套（`../dump_types.py` / `../field_types.py` / `../search_roots.py`）
用 dnfile 解析 `Assembly-CSharp.dll`，不依赖游戏运行，可随时用。

方法论总结见 [../../docs/TUTORIAL.md](../../docs/TUTORIAL.md)。

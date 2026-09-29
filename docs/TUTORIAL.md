# Unity Mono 游戏内存逆向完整教程

> 以《戴森球计划》背包读取为实例，从零到可用工具的完整方法论。
> 本项目是**学习项目**：所有技术仅用于单机游戏的自我娱乐与逆向工程学习。
> 文中方法适用于任何 **Unity Mono**（非 IL2CPP）后端的 x64 Windows 游戏。

---

## 目录

1. [判断引擎类型：一切的前提](#1-判断引擎类型)
2. [静态分析：dnfile 拆元数据](#2-静态分析)
3. [Cheat Engine：手动验证的正确打开方式](#3-cheat-engine)
4. [脱离 CE：纯 Python 读进程内存](#4-纯-python-读内存)
5. [Mono 对象模型速成](#5-mono-对象模型速成)
6. [三大核心技术](#6-三大核心技术)
7. [实战全记录：背包链是怎么挖出来的](#7-实战全记录)
8. [写入与安全](#8-写入与安全)
9. [性能：11GB 内存怎么扫](#9-性能)
10. [踩坑总清单](#10-踩坑总清单)
11. [移植到你自己的游戏：checklist](#11-移植-checklist)

---

## 1. 判断引擎类型

逆向 Unity 游戏第一步永远是分清算术：

| 特征 | Mono | IL2CPP |
|---|---|---|
| 游戏目录 | `*_Data/Managed/Assembly-CSharp.dll`（.NET 程序集） | `GameAssembly.dll`（原生机器码） |
| 元数据 | 完整保留（类名/字段名/类型） | 剥离到 `global-metadata.dat`（加密/裁剪） |
| 逆向难度 | ★★（本文范围） | ★★★★★（需要 Il2CppDumper 等） |

```bash
ls "游戏目录/GameName_Data/Managed/" | head    # 有 Assembly-CSharp.dll => Mono
ls "游戏目录/" | grep -i gameassembly          # 有 GameAssembly.dll => IL2CPP
```

戴森球计划：`DSPGAME_Data/Managed/Assembly-CSharp.dll` 存在，无 `GameAssembly.dll`
→ **Mono**，且类名字段名全部明文（无混淆），是最理想的学习靶子。

## 2. 静态分析

工具：Python + [dnfile](https://github.com/malwarefrank/dnfile)（本项目 `tools/dump_types.py` 等三件套）。

能拿到什么：
- 全部类（typedef）、字段名、字段类型、static 标记
- 引用关系（谁的字段类型是 GameData → 反查持有者）

**拿不到什么（关键认知）**：
- ❌ 运行时内存偏移——字段布局由 Mono JIT 决定，声明顺序只是强烈暗示
- ❌ 实例地址——每次运行都不同

> 实测教训：本项目静态 dump 的 Player 字段声明顺序与运行时布局**不完全一致**
> （基类字段插入、对齐填充），凡按声明顺序硬算偏移的尝试全部翻车。
> 静态分析的正确用途：**知道该找什么**（字段名/类型），偏移一律运行时验证。

常用命令：

```bash
python tools/dump_types.py <Assembly-CSharp.dll路径> =Player        # 精确类名 dump 字段
python tools/field_types.py <dll> StorageComponent                  # 字段+类型
python tools/search_roots.py <dll> GameData                         # 谁引用了 GameData
```

## 3. Cheat Engine

CE 的价值不是"改数值"，而是它的 **Mono 面板**能直接把运行时元数据翻译给你看：

1. 附加进程 → 菜单 `Mono → Activate Mono features`
2. `Mono → Browse this process's domains` → 找到 `Assembly-CSharp`
3. 浏览类 → 静态字段（如 `GameMain.data`）可以直接 "Add to address list"，
   CE 会注册一个**符号**，地址表达式里能直接写 `[GameMain.data]`
4. 对着对象地址 `Dissect data/structures` → CE 自动解析 vtable → 显示真实类名+字段表

本项目用 CE 只做了一件事：**确认 `GameMain.data` 静态字段的值**（第一次人肉锚点）。
之后的所有工作都脱离了 CE——因为我们的目标是可编程的工具，而 CE 符号无法被 Python 直接消费。

> CE 的 Dissect 结果也要交叉验证：本项目中 CE 嵌套结构展示曾误导过一次层级关系
> （把"静态字段块"当成了对象本身）。任何单一信源都可能骗你，**不变量互证**才是王道（见 §6.3）。

## 4. 纯 Python 读内存

只需要 `ctypes`，零第三方依赖（numpy 仅用于加速扫描）：

```python
import ctypes
k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = ctypes.c_void_p

h = k32.OpenProcess(0x0010 | 0x0020 | 0x0008 | 0x0400, False, pid)
#                   VM_READ VM_WRITE VM_OPERATION QUERY_INFORMATION
# 注意：想写内存必须带 VM_OPERATION，否则 VirtualProtectEx 失败（实测踩坑）

buf = ctypes.create_string_buffer(8)
got = ctypes.c_size_t(0)
k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, 8, ctypes.byref(got))
value = struct.unpack("<Q", buf.raw)[0]
```

要点：
- 游戏与本工具**同权限级别**即可读（都不需要管理员时）；游戏以管理员运行则工具也要
- 枚举内存区域用 `VirtualQueryEx`：只关心 `MEM_COMMIT + MEM_PRIVATE + 可写`（托管堆特征）
- `ReadProcessMemory` 大块读会因 hole/guard 页失败 → 分块读（4MB 实测是甜点）
- **Boehm GC（Unity 默认）不搬移对象** → 进程生命周期内地址稳定 → 缓存合法

## 5. Mono 对象模型速成

x64 Unity Mono（bleeding edge）实测布局，**这是全部后续工作的地基**：

```
普通对象:      [ +0x00 vtable* ][ +0x08 sync ][ +0x10 ??? ][ +0x18 起: 实例字段 ]
                ⚠️ 本项目实测对象头是 0x18 而不是教科书的 0x8/0x10！必须实测！

MonoArray:     [ +0x00 vtable* ][ +0x08 ][ +0x10 ][ +0x18 len(int64) ][ +0x20 元素区 ]
                引用数组: 元素是 8 字节指针
                值类型数组(struct): 元素内联, stride = sizeof(struct)

MonoString:    [ +0x00 vtable* ][...][ +0x10 len(int32, 字符数) ][ +0x14 UTF-16LE 字符 ]

MonoVTable:    [ +0x00 class* ](常见) —— class 也可能在 +0x08/+0x10, 逐个试
MonoClass:     [ +0x00 == 自身指针!! ][...][ +0x48 char* name(UTF8) ]
                自引用是 MonoClass 的身份证（§6.1）

MonoClassField: stride 0x20: [ +0x00 u32 offset? ][ +0x08 type* ][ +0x10 name* ][ +0x18 class* ]
                （offset 字段语义存疑, name/class 可靠 —— 布局配对可能错行, 慎用）

静态字段:      不在对象堆里, 在 class 关联的静态数据块; 无 CE 时可全内存反查（§6.1 变体）
```

字段排列规则：基类字段在前，然后按声明顺序，自然对齐（引用 8 字节对齐、int 4 字节）。
但**别信推算，信验证**（§6.3）。

## 6. 三大核心技术

### 6.1 类定位：自引用身份证

**问题**：不调用 mono API、不注入，怎么在 11GB 内存里找到 `GameData` 类？

**解法**：利用两个不变量——
1. 类名字符串 `b"GameData\0"` 一定在内存里（Mono 元数据堆）
2. 真正的 `MonoClass` 结构满足 `*(void**)class == class`（自引用），且 `class+0x48` 指向类名串

```python
str_hits = find_bytes(b"GameData\0")           # 第一遍: 找字符串
slots    = find_slots(set(str_hits))           # 第二遍: 谁存着这些串地址
classes  = [s - 0x48 for s in slots            # 槽地址-0x48 = 候选 class
            if read_u64(s - 0x48) == s - 0x48] # 自引用过滤 => 零误杀
```

细节坑：**元数据字符串地址可以是奇数**（按字节紧密排列），任何对齐过滤都会把它误杀。

### 6.2 类名反查：让"猜偏移"变成"验布局"

```python
def cname(vtable):
    for coff in (0x00, 0x08, 0x10):
        cls = read_u64(vtable + coff)
        name_ptr = read_u64(cls + 0x48)
        name = read_cstring(name_ptr)      # 可打印 ASCII 且像标识符
        if name: return name               # 数组类名形如 "GRID[]" / "ItemProto[]"
```

有了它，逆向工作流彻底改变：

- ❌ 旧：`Player+0x60 应该是 package 吧？读出来验证一下数值像不像`
- ✅ 新：`Player+0x60 指着的对象，类名是什么？` → `"MechaArmorModel"` → 不是，下一个
  → `Player+0x68` → `"StorageComponent"` → **实锤**

本项目靠它当场纠正了三处"已验证"的错误偏移（package/grids/stride），
并在 23 个 GRID[] 数组中一眼认出配送包（`DeliveryPackage`）和机甲仓。

### 6.3 不变量交叉验证

单一证据都可能是巧合，**两个独立来源互证**才可信：

| 不变量 | 验证了什么 |
|---|---|
| `grids数组.len == StorageComponent.size` | grids 偏移 + 数组头布局同时正确 |
| `isPlayerInventory == 1` 且 `size == 40/50` | 找对了玩家背包而非世界容器 |
| GRID 元素 `count <= stackSize` 且 stackSize ∈ {50,100,200,300,500…} | stride 和字段序正确 |
| `Player.uPosition` 是三个 double（星球坐标量级） | 对象中段布局与声明序吻合 |
| 写入后**回读校验** | WriteProcessMemory 真的生效 |

## 7. 实战全记录

完整链路（含两次大翻车和纠正过程）：

```
第一步  CE 读 GameMain.data 静态字段值 → S
第二步  Dissect S → 发现它就是 GameData（patch=23 整数、坐标 double 群吻合字段表）
第三步  [S+0xC0] → 对象类名验证 = "Player" ✓
第四步  Player 字段逐个类名反查:
          +0x60 → MechaArmorModel ✗（一度误认为是 package!）
          +0x68 → StorageComponent ✓ = package
第五步  package+0x30 → 类名 "GRID[]" ✓, len@+0x18 = 50 = size@+0x70 ✓（不变量互证）
第六步  元素 stride: 用背包里"同物品多格"数据试 0x14/0x18/0x20/0x28 → 0x14 全一致
第七步  物品名: ItemProto 实例 → +0x20 String(名字) / +0x30 u32(itemId)
          交叉验证: itemProtoById 稀疏数组(12000长, 下标==id) 非空槽 = 175 = 实例扫描数
第八步  沙土: 不在背包! 按 (1099,1010) GRID 模式全内存搜索 0 命中 → 醒悟它是标量字段
          → Player 0x150~0x190 区间扫"当前已知值 1010" → +0x170, 与用户 CE 地址互认
```

**两次大翻车**（都写进了 README 踩坑节）：

1. **层级误判**：CE 嵌套结构让我以为 `GameMain.data` 值是个"壳"，真 GameData 在 `+0x10` 处
   → 按错误层级读出一堆小整数垃圾。纠正方式：拿字段表逐字节对照原始 dump
   （`patch=23`、double 坐标群），确认 S 本身就是 GameData。
2. **顺序依赖的收集器**：堆叠表两跳扫描时，Mecha 先作为别人的"二跳子节点"进了 seen 集合，
   轮到一跳处理时被跳过 → 机甲五仓全部漏收。纠正方式：改为**候选集统一探测**
   （一跳∪二跳先去重、再逐个检查），消灭顺序依赖。

**方法论升华**：翻车都源于"相信单一信源/隐式顺序"，纠正都靠"不变量互证/无状态化"。

## 8. 写入与安全

写入流程（`dsp_core.DspBag.set_count_in`）：

```python
VirtualProtectEx(h, addr, 4, PAGE_READWRITE, &old)   # 需要 VM_OPERATION 权限
WriteProcessMemory(h, addr, pack("<I", value), 4, &done)
VirtualProtectEx(h, addr, 4, old, &old)              # 恢复保护属性
assert read_u32(addr) == value                        # 回读校验, 失败即抛错
```

安全设计（本项目全部实现）：
- **钳制**：count 不超过该格 stackSize（且 stackSize 来自实测而非猜测）
- **拒写**：空格拒绝 set_count；非空格 give 需要显式 force
- **信任分级**：堆叠上限只有"实测过"才用于钳制，猜测值绝不限制用户显式输入
  （曾因此把用户 178 个传送带错钳成 100 —— 教训写进 README）
- **测试纪律**：破坏性测试一律 mock；活游戏只做"写入→验证→复原"闭环

## 9. 性能

11GB 进程的扫描优化实录（冷启动 **254s → 66s → 55s**）：

1. **合并遍历**：GameData 链和 ItemProto 链共享同一次全内存 pass
   （`find_slots_multi` 一次 numpy searchsorted 查多组目标值）——8 遍变 4 遍
2. **大块读**：ReadProcessMemory 单次 16MB（syscall 开销主导，块越大越快；4MB→16MB 再省一截）
3. **并发读**：ThreadPoolExecutor 8 线程（GIL 在 syscall 期间释放，真并行）
4. **只扫可写私有内存**：托管堆必然是 `MEM_PRIVATE + PAGE_READWRITE`，
   一刀砍掉镜像/映射/只读区（注意：实测该游戏私有内存几乎全可写，此过滤收益有限，
   别想当然——**先 profile 再优化**：纯读 11.4GB 仅 9.2s，大头是遍数×numpy）
5. **零拷贝**：numpy `frombuffer` 直接吃 ctypes buffer，省掉每遍 11GB 的 `buf.raw[:]` memcpy
6. **地址窗口剪枝**：mono 的元数据/类/vtable/堆在**同一会话内地址聚簇**（实测跨度 ~16GB）。
   pass1 找到类名串后，pass2-4 只扫命中位置 ±48GB 窗口；**窗口内落空自动回退全量**
   （剪枝必须配回退，否则换个内存布局就静默失败）
7. **磁盘缓存**：`{pid, 进程创建时间, 全部关键地址}` → 下次连接先**逐字节验链**，
   通过则 0 扫描（0.15s），不通过自动重扫。进程创建时间防 PID 复用
8. **GUI 后台预热**：窗口一打开就在 daemon 线程里开扫，用户点"连接"时 join 预热线程
   ——把"等 55 秒"变成"感知不到"。这是产品层面对算法极限的兜底
9. numpy 技巧：`searchsorted + 回代比较` 做百万级集合成员测试；
   `find_bytes` 跨块边界用 carry 拼接（region 不连续时放弃 carry 防地址错位）

**还没做但可行**：pass1 的字符串搜索理论上可跳过——类名串在 Assembly-CSharp.dll 的
#Strings 堆里偏移固定（dnfile 可算），若能廉价定位 mono 元数据 blob 的基址（搜 4 字节
`BSJB` 签名），所有串地址 = blob基址 + 文件偏移，pass1 从 13s 变毫秒级。

## 10. 踩坑总清单

按杀伤力排序：

1. **对象头不是 8 字节**：本游戏实测 0x18（vtable+sync+bounds）。数组 len 在 +0x18、
   数据在 +0x20。教科书值会全链错位
2. **静态元数据顺序 ≠ 运行时布局**：dnfile 的字段序只能当线索
3. **CE 的嵌套 Dissect 会误导层级**：结构名对但基准地址错，读出"两个小整数拼的假指针"
4. **元数据字符串奇地址**：对齐过滤 = 自断线索
5. **tkinter 线程模型**：后台线程连 `root.after()` 都不能调（内部 createcommand 同样要求
   主线程）。唯一正确姿势：worker 写普通属性，主线程 after 轮询
6. **WriteProcessMemory 前必须 VirtualProtectEx**，而后者需要 OpenProcess 带 VM_OPERATION
7. **GBK 控制台**：游戏字符串含 `\xa0`（不间断空格）→ print 直接 UnicodeEncodeError；
   打包脚本里连 emoji 都不能 print
8. **GetProcessTimes 参数序**：Creation/Exit/Kernel/User，拿错字段缓存永不命中
9. **ttk.Treeview 的 iid 只能在 insert() 时传**，item() 不能改
10. **trace_add 回调签名是 (*args)**：tkinter 传 name/index/mode 三个参数
11. **对活游戏做破坏性测试**：玩家在玩时库存是移动靶，写入验证全部失真
12. **无人值守测试触发 messagebox**：模态框永久阻塞测试进程

## 11. 移植 checklist

想把这套方法用到别的 Unity Mono 游戏？按顺序：

- [ ] 确认 Mono（Managed/Assembly-CSharp.dll 存在，无 GameAssembly.dll）
- [ ] dnfile dump 目标类字段（知道要找什么名字/类型）
- [ ] `monomem` 附着进程，`g.class_("目标类名")` 定位类
- [ ] `cls.instances()` 拿实例（多候选时用"字段指向的对象类名"筛）
- [ ] 从单例根（`GameMain.data` 式静态字段）或特征实例出发，**逐字段类名反查**走引用链
- [ ] 每层用不变量互证（len==size、count<=stack 之类）
- [ ] 对不上先怀疑 `monomem/layout.py` 的版本偏移（对象头/String/Array），**先校准再改逻辑**
- [ ] 写入必须：ProtectEx → Write → 恢复 → 回读校验 → 上层钳制

`tools/monomem/` 就是按这个 checklist 抽的库，`tools/examples/dsp_via_monomem.py`
是 60 行的完整示范。

---

*本文档与代码同步演进；发现文档与实测不符时，以实测为准并回来改文档。*

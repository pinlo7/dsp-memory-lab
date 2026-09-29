# 戴森球计划 · 内存逆向学习项目（DSP Memory Lab）

> ## ⚠️ 这是一个学习项目 / THIS IS A LEARNING PROJECT
>
> **目的**：学习 Unity **Mono** 引擎游戏的通用内存逆向方法论（类定位 / vtable 反查 /
> 布局不变量验证 / 外部进程读写），以《戴森球计划》单机背包为练习靶子。
>
> - 仅用于**单机、自娱、学习**——不注入、不改游戏文件、不碰任何在线/多人功能
> - 使用即表示你自行承担风险；作者与 Youthcat Studio / Gamera Games 无任何关联
> - 游戏更新后偏移会失效——**这正是学习点**：按 [教程](docs/TUTORIAL.md) 的方法重新验证，而不是抄旧偏移
> - 如果你是想学逆向的初学者：直接读 **[docs/TUTORIAL.md](docs/TUTORIAL.md)**，
>   它比工具本身更有价值（含完整踩坑记录和移植 checklist）

不装任何注入、不改任何游戏文件，从**外部进程**读戴森球计划的玩家背包（含物品中文名），
并可修改格子数量/沙土计数。全程只用 Windows 原生 API（`ReadProcessMemory`/`WriteProcessMemory`）
+ Python。方法论已抽成通用库 **[monomem](tools/monomem/)**，适用于几乎所有 Mono 后端游戏。

## 文档索引

| 文档 | 内容 |
|---|---|
| **[docs/TUTORIAL.md](docs/TUTORIAL.md)** | ⭐ 核心教程：Mono 逆向完整方法论 + 实战全记录 + 12 条踩坑清单 + 移植 checklist |
| [tools/monomem/README.md](tools/monomem/README.md) | 通用 Mono 内存库 API 与原理 |
| [tools/experiments/README.md](tools/experiments/README.md) | 逆向过程的实验脚本导读（含弯路） |
| 本文件 | 工具使用手册 |

## 功能一览

```
┌─ dsp_gui.py  tkinter 图形界面（表格 / 双击改数量 / 右键菜单 / 堆满 / 一键全堆满(不再提示) /
│              自动堆满(定时循环,默认1s,后台线程) / 添加物品·模糊搜索·历史chips /
│              其他容器树形窗(机甲仓可读可改) / 沙土读写）
├─ dsp_bag.py  命令行（--set / --give / --clear / --search / --sand / --list-items / --json）
├─ dsp_core.py 核心库（自动定位 + 验证 + 读写 + 缓存 + 175 物品名表）   ← 依赖 numpy
└─ monomem/    通用 Unity Mono 内存库（本项目方法论的产品化，可移植到其他游戏）
```

## 快速开始

```bash
pip install numpy            # 唯一第三方依赖（扫描加速）
python tools/dsp_bag.py                  # 自动定位并打印背包
python tools/dsp_bag.py --set 0 300     # 第 0 格数量改 300（自动钳到堆叠上限）
python tools/dsp_bag.py --give 2001     # 往第一个空格放满堆传送带
python tools/dsp_bag.py --give 宇宙矩阵 --count 5 --slot 3   # 按名字给，指定格/数量
python tools/dsp_bag.py --search 矩阵   # 模糊查 id（名称片段/拼音无、id 数字均可）
python tools/dsp_bag.py --clear 4       # 清空第 4 格
python tools/dsp_bag.py --sand          # 查看沙土量 / --sand 10000 设置
python tools/dsp_bag.py --json           # 机器可读输出
python tools/dsp_bag.py --list-items     # itemId -> 中文名 全表（175 个）
python tools/dsp_gui.py                  # 图形界面

# 配方/生产链分析（首次扫内存 ~3 分钟生成 recipes.json，之后全离线秒查）
python tools/recipes.py                  # 23 种初级原料 → 直接产出总览
python tools/recipes.py --tree 铁矿 3    # 铁矿的 3 级产出树
python tools/recipes.py --item 磁铁      # 磁铁能做什么 / 从哪来（含配方明细）
python tools/recipes.py --rescan         # 游戏更新后强制重扫

# 递归成本计算：合成某物品的全部基础原料 + 设备需求
python tools/calc.py 宇宙矩阵 --rate 6   # 6个/min 宇宙矩阵: 12种原料 + 153台设备
python tools/calc.py 引力透镜 --via 氢:1107 --mk 3   # 多配方物品手动选路线

# 科技树分析：研究中 / 可立即研究(前置满足) / 已研究 / 锁定(缺什么前置)
python tools/techs.py                  # 全报告（首次扫 TechProto ~2分钟, 之后读缓存秒出）
python tools/techs.py 量子             # 关键词过滤
```

### 打包成 exe（免装 Python，双击即用）

```bash
pip install pyinstaller
python tools/build_exe.py            # 打包 GUI -> dist/DSP背包工具.exe
python tools/build_exe.py --cli      # 打包命令行 -> dist/DSP背包CLI.exe
python tools/build_exe.py --both     # 两个都打
python tools/build_exe.py --onedir   # 目录模式（启动更快，产物是文件夹）
```

- 默认 `--onefile` 单文件；缓存/设置文件生成在 **exe 同目录**（已处理打包后 `__file__` 指向临时解压目录的坑）。
- 首次运行仍需全内存扫描约 1 分钟，之后秒开（实测缓存命中 1.4s 出全表）。
- 杀软可能对"读写游戏内存 + 无签名"误报，加白名单即可。
- CLI exe 在 cmd.exe 里中文乱码的话，先 `chcp 65001`（游戏物品名含 `\xa0` 等字符，
  已强制 stdout utf-8 防 GBK 崩溃）。
- 打包时踩过的坑：物品名含 `\xa0` 会让 GBK 控制台直接 UnicodeEncodeError；
  PyInstaller 的 print 里别放 emoji（同样 GBK 崩）。

前提：游戏运行中且**已加载进星球**（主菜单时 GameData 还没构造）。
改完数量回游戏**拿放一次该物品**，UI 才会刷新（内存数据本身即时生效，存档时带走）。

> 首次连接要全内存扫描（优化后 11GB 实测 **~55s**，演进史: 254s → 66s → 55s，见 TUTORIAL §9）。
> 成功后地址写入 `tools/.dsp_cache.json`；同一游戏会话内再次连接**毫秒级**（缓存只用几十字节读来验证）。
> 游戏重启后缓存自动作废并重新扫描。**GUI 打开即后台预热扫描**——你填个搜索框的功夫它就绪了。

## 指针链（本版本实测，游戏更新后偏移可能变化）

```
GameMain.data 静态字段 → GameData (S)          [Mono 对象头 0x18]
  └ [S + 0xC0]  mainPlayer : Player
      └ [P + 0x68]  package : StorageComponent
          ├ [PKG + 0x6C] isPlayerInventory : bool == 1
          ├ [PKG + 0x70] size : int32     （背包容量，动态：实测 40→50 自动适配）
          └ [PKG + 0x30] grids : GRID[]            （数组对象）
              ├ [ARR + 0x18] len : int64
              └ [ARR + 0x20] 元素区, stride 0x14（20 字节 = 5×int32）
                  +0x00 itemId   +0x08 count   +0x0C stackSize
物品名: ItemProto 实例  → [O+0x20] name:String   [O+0x30] index:itemId
String 对象: [len:int32 @ +0x10][UTF-16 字符 @ +0x14]

Player 其他已知字段（第二阶段实测）:
  [P + 0x70] deliveryPackage : DeliveryPackage   grids 在 +0x10(!)，元素 stride 0x20(特殊)
  [P + 0xD8] mecha : Mecha
      ├ +0x30 StorageComponent 机甲仓A(size4)  ├ +0x38 仓B(1)  ├ +0x58 仓C(3)
      └ +0x60 仓D(1)                          └ +0x68 仓E(5)   （grids 都在 +0x30）
  [P + 0x170] 沙土数量 : int32（机甲采沙总计数，不是背包格！）
  [P + 0x17C] = 103 恒定，疑似星球 id（待换星球验证）
```

背包 + 机甲五仓 + 配送包统一由「两跳容器探测」自动发现（凡字段挂着 `GRID[]` 数组的对象），
GUI「其他容器」按钮可浏览；非注册物品 id 自动过滤（防配送包特殊 stride 注入幽灵数据）。

## 物品 id 全量枚举 & 堆叠上限来源

游戏有 **175 个注册物品**（本版实测，含 mod 但都是原版 id），id 段按功能分块：
1000 原料 / 1100 精炼 / 12xx-15xx 组件 / 16xx-18xx 燃料弹药 / 20xx-23xx 设施 / 29xx 研究 / 30xx 战斗 / 5xxx 载具黑雾 / 60xx 矩阵。

枚举方法（两种互验，结论一致即"全"）：
- **稀疏注册表** `itemProtoById`：一个长 12000 的 `ItemProto[]` 数组，**下标 == itemId**，非空即注册物品。
- **类实例扫描**：按 `ItemProto` 类找全部实例，读 `+0x30` 的 index 字段。两者都得到 175、id 范围 1000~6006。

`give` 时该物品的**堆叠上限来源**：proto 里没找到干净的 StackSize 字段（该版本 proto 布局把 453/454 这类指针碎片混在候选偏移里，硬校准失败）。改用更可靠的办法——**从 Player 出发两跳**，凡是字段里挂着 `GRID[]` 数组的对象（本体背包/配送包/机甲仓…）全部实测其 stackSize（自带科技/增产倍率），`--give 传送带` 因此得到真实的 300 而非猜测值。表里没有的物品先强制重测一次（背包可能刚变），仍没有则按默认 100 写上限、且**不再拿猜测值钳制你显式指定的数量**。

## 核心方法论（比结论更值钱）

**1. 类名字符串 → MonoClass*：自引用是唯一身份证。**
Mono 运行时的每个类（MonoClass）结构里，`class+0x00 == class 自身`，`class+0x48` 存类名指针。
全内存扫 `b"GameData\0"` 得到字符串地址，再找"哪些 8 字节槽存着这些地址"，`槽地址 - 0x48`
即候选 class*，用自引用过滤，误杀为零。（本项目的 12779 个类注册表就是这么建的。）

**2. vtable → 类名反查，让"猜偏移"变成"验布局"。**
对象头 8 字节是 vtable*，vtable 的前三个 qword 之一必是 class*，class+0x48 即名字。
有了 `cname(vt)`，验证一条链只需问"这个字段指着的对象到底叫什么类"：
`Player → package` 指着的对象类名必须是 `StorageComponent`，`grids` 必须是 `GRID[]`。
**任何对不上的偏移都当场报错，绝不让垃圾数据混进结果。**

**3. 用不变量交叉定位字段，而非死记偏移。**
`grids` 数组的 `len` 字段 == `StorageComponent.size` 字段 == 40，两条独立来源互相印证；
GRID 元素的 `count ≤ stackSize` 且 stackSize ∈ {50,100,200,300} 等特征也吻合。
这类结构不变量在版本更新后仍然是重新定位字段的锚点。

**4. 静态字段不硬编码，每次按特征重找。**
`GameMain.data` 的运行时地址随进程、随重启变化（ASLR + Mono 静态堆）。工具不依赖 CE
符号，而是每次扫 `GameData` 类实例并验证整条链——找不到就明确报错。

**5. 踩过的坑（都写在 git 历史里）：**
- Mono x64 对象头是 **0x18**（vtable + sync + bounds），不是教科书上的 0x8/0x10；
  数组 len 在 +0x18 不在 +0x10。
- dnfile 静态元数据的**声明顺序 ≠ 运行时布局顺序**，只可靠用于查类型。
- MonoClassField 表（stride 0x20：`[u32 offset][type*][name*][class*]`）可用，
  但 name↔offset 配对容易错行，务必用类名解析复核。
- 元数据字符串地址**可以是奇数**（按字节紧密排列），对齐过滤会把它滤掉。
- `GRID` 有 class 和 struct 两份 typedef，背包里是 **struct 内联数组**，stride=20；
  不是指针数组。
- **tkinter 非线程安全到骨子里**：后台线程不仅不能碰控件，连 `root.after()` 都不能调
  （它内部 `createcommand` 同样要求主线程）。正确姿势：worker 只写普通属性
  （`self._af_changed += n`），主线程用 `after` 轮询这些属性再更新 UI。
- **别对正在玩的活游戏做破坏性写入测试**：库存是移动靶，测试期间玩家捡走物品会让
  "写入后读回"验证完全失真（还差点误清用户一格）。自动化测试用 mock bag。
- **modal 对话框（messagebox）会阻塞无人值守的自动化测试**：测试路径别触发它。

## 安全说明

- **只读默认**。`--set` 会写目标进程 4 字节，写后立即回读校验。
- 改数量是游戏存档层面合法的数值（≤ stackSize 时游戏逻辑能自然消化）；
  改成超过堆叠上限或往空格硬塞 id 可能让 UI 异常，工具默认钳制、空格拒写。
- 建议实验前手动存档（改坏了读档即可）。仅用于自己单机存档。

## 已知限制

- 主背包动态适配（实测 40→50 格自动跟随）；机甲五仓可读写；**配送包只读展示**
  （元素 stride 0x20 特殊布局，未完整解码——见 TUTORIAL 待办）。
- `count` 语义 = 该格物品数；`inc`（+0x04，增产剂方向标记）未做处理。
- 冷启动全内存扫描约 1 分钟（11GB 实测 66s）；同游戏会话内缓存命中 <1s。
- 游戏大版本更新后偏移可能变：按 [TUTORIAL.md](docs/TUTORIAL.md) §11 checklist 重验，
  而不是改用猜的。

## 文件清单

```
├── README.md                  本文件（工具使用手册）
├── LICENSE                    MIT
├── docs/
│   └── TUTORIAL.md            ⭐ 核心教程：完整方法论 + 实战记录 + 踩坑清单
└── tools/
    ├── dsp_core.py            库：DspMem(进程读写扫描) + DspBag(定位/读/写/缓存)
    ├── dsp_bag.py             CLI 入口
    ├── dsp_gui.py             tkinter GUI
    ├── build_exe.py           PyInstaller 一键打包（GUI/CLI/both）
    ├── recipes.py             配方解析：原料→产物 生产链分析（离线/重扫两模式）
    ├── calc.py                递归成本计算：基础原料总量 + 分设备需求量
    ├── techs.py               科技树分析：研究中/可研究(前置满足)/锁定(缺前置)
    ├── calibrate_recipes.py   RecipeProto 字段偏移标定过程（教学：数据特征反推布局）
    ├── items.json             175 物品 id→中文名（运行时可重新生成）
    ├── recipes.json           162 配方全数据（含 type/耗时/手搓标记，运行时可重新生成）
    ├── dump_types.py          ┐
    ├── field_types.py         ├ dnfile 静态元数据分析三件套（不依赖游戏运行）
    ├── search_roots.py        ┘
    ├── monomem/               通用 Unity Mono 内存库（独立文档在其 README.md）
    ├── examples/
    │   └── dsp_via_monomem.py 用 monomem 60 行重写背包读取（抽象验证）
    └── experiments/           逆向过程实验脚本（教学参考，含弯路，见其 README.md）
```

运行时生成、不入库：`.dsp_cache.json`（会话缓存）、`.dsp_gui_settings.json`（GUI 偏好）、
`class_registry.json`（类注册表快照）、`build/` `dist/`（打包产物）。

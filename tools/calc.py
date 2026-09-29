"""calc.py — 递归成本计算：合成 1 个目标物品需要多少基础原料 + 多少设备。

    python tools/calc.py 宇宙矩阵                 # 原料成本 + 设备需求(默认按 1/min)
    python tools/calc.py 宇宙矩阵 --rate 6        # 目标产能 6/min 的设备数
    python tools/calc.py 处理器 --mk 3            # 制造台按 Mk.III(4x速) 算
    python tools/calc.py 引力透镜 --via 氢:1107   # 氢改走原油路线(多配方物品手动选路)

数据源: recipes.json（recipes.py --rescan 生成，游戏大更新后重扫）。
设备速度模型: 制造台 Mk.I=1x Mk.II=2x Mk.III=4x；其他设施按 1x（位面熔炉/量子化工厂等
加速建筑未计入）。分馏塔的 TimeSpend 语义特殊(每 tick 一次通过)，其设备数仅供参考。
副产品（如炼油同时出氢）会在链内自动抵扣。多配方物品默认取第一条，输出末尾会提示可用
--via 改道；结果是"忠实于所选配方的精确解"，不是全局最优解（那是个线性规划问题）。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# ---- RecipeProto 字段（内存标定, 标定过程见 calibrate_recipes.py）----
# u32@0x98: 低字节=Type, 第2字节=Handcraft;  u32@0x9C: TimeSpend(1/60秒);  u32@0xA0: index
TYPE_NAMES = {1: "电弧熔炉", 2: "化工厂", 3: "原油精炼厂", 4: "制造台",
              5: "微型粒子对撞机", 6: "能量枢纽", 7: "射线接收站",
              8: "分馏塔", 9: "科研站", 15: "矩阵研究站", 0: "手动/其他"}


def load():
    d = json.load(open(Path(__file__).parent / "recipes.json", encoding="utf-8"))
    names = {int(k): v for k, v in d["names"].items()}
    recipes = []
    for r in d["recipes"]:
        raw = r.get("raw", {})
        recipes.append({
            "name": r["name"], "index": r.get("index", raw.get("a0", 0)),
            "items": [(int(i), int(c)) for i, c in r["items"]],
            "results": [(int(i), int(c)) for i, c in r["results"]],
            # 新 schema 有顶层字段就用，旧 json 退回 raw
            "type": r["type"] if "type" in r else (raw.get("98", 0) & 0xFF),
            "handcraft": r["handcraft"] if "handcraft" in r else ((raw.get("98", 0) >> 8) & 1),
            "time": (r["time"] if "time" in r else raw.get("9c", 60)) / 60.0,  # ticks->秒
        })
    return names, recipes


def parse_args(argv):
    """支持 --rate 6 / --rate=6 两种写法，返回 (位置参数, 选项dict)。"""
    pos, opts, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                opts[k] = v
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                opts[key] = argv[i + 1]
                i += 1
            else:
                opts[key] = True
        else:
            pos.append(a)
        i += 1
    return pos, opts


def main():
    args, opts = parse_args(sys.argv[1:])
    if not args:
        print(__doc__)
        return
    query = args[0]
    rate = float(opts.get("rate", 60))     # 目标产能: 个/分钟
    mk = int(opts.get("mk", 1))            # 制造台等级 1/2/3
    mk_speed = {1: 1.0, 2: 2.0, 3: 4.0}[mk]

    names, recipes = load()
    # 目标物品 id（精确名 > id > 子串）
    exact = [i for i, n in names.items() if n == query or str(i) == query]
    hits = exact or [i for i, n in names.items() if query in n]
    if not hits:
        print(f"找不到物品 {query!r}")
        return
    target = hits[0]
    if not exact and len(hits) > 1:
        print(f"(模糊匹配到多个: {[names[i] for i in hits[:6]]}，取第一个)", file=sys.stderr)

    produced_by = defaultdict(list)
    for r in recipes:
        for iid, _c in r["results"]:
            produced_by[iid].append(r)

    # --via 物品名:配方id,物品名:配方id —— 多配方物品手动指定路线
    via = {}
    for pair in str(opts.get("via", "")).split(","):
        if ":" in pair:
            k, v = pair.split(":")
            k = k.strip()
            exact = [i for i, n in names.items() if n == k]
            hit = exact or [i for i, n in names.items() if k in n]
            if hit:
                via[hit[0]] = int(v)
            else:
                print(f"(--via 未识别物品: {k})", file=sys.stderr)

    # 递归展开（带副产品抵扣）
    need = defaultdict(float)
    need[target] = 1.0
    machine_sec = defaultdict(float)   # type -> 秒 / 每目标物品
    used = {}                          # item -> recipe (选中路径)
    multi = {}                         # item -> [recipes] (多配方, 提示用户可 --via)
    handcraft_all = True
    queue = [target]
    guard = 0
    while queue and guard < 10000:
        guard += 1
        item = queue.pop(0)
        amount = need.get(item, 0.0)
        if amount <= 1e-9:
            continue
        rs = produced_by.get(item)
        if not rs:
            continue                   # 初级原料，留在 need 里
        if item in via:
            r = next((x for x in rs if x["index"] == via[item]), rs[0])
        elif len(rs) > 1:
            r = rs[0]                  # 默认取第一个（警告在最后统一提示）
            multi[item] = rs
        else:
            r = rs[0]
        used[item] = r
        out_c = next(c for i, c in r["results"] if i == item)
        runs = amount / out_c          # 需要执行配方次数（分数）
        need[item] = 0.0
        t = r["time"]
        if r["type"] == 4:
            t = t / mk_speed
        machine_sec[r["type"]] += t * runs
        if not r["handcraft"]:
            handcraft_all = False
        for iid, c in r["items"]:
            need[iid] += c * runs
            queue.append(iid)
        for iid, c in r["results"]:    # 副产品抵扣
            if iid != item:
                need[iid] -= c * runs
                if need[iid] < -1e-9:
                    queue.append(iid)  # 盈余（不继续展开，仅记账）

    print(f"\n=== {names[target]} ×1 成本 ===")
    print("基础原料（递归到底，含副产品抵扣）:")
    for iid in sorted(i for i, v in need.items() if v > 1e-9 and not produced_by.get(i)):
        print(f"  {names.get(iid, iid):<10s} x{need[iid]:.3f}")
    surplus = {i: -v for i, v in need.items() if v < -1e-9 and not produced_by.get(i)}
    if surplus:
        print("  (盈余副产品: " + ", ".join(f"{names.get(i,i)}x{v:.2f}" for i, v in surplus.items()) + ")")

    print(f"\n设备需求（目标 {rate:g} 个/分钟，制造台按 Mk.{mk} {mk_speed:g}x）:")
    total = 0.0
    for t, sec in sorted(machine_sec.items()):
        machines = sec * rate / 60.0
        total += machines
        print(f"  {TYPE_NAMES.get(t, f'type{t}'):<10s} x{machines:.2f}")
    print(f"  {'合计':<10s} x{total:.2f}")
    print(f"\n全程可手搓: {'是' if handcraft_all else '否'}")

    # 路径明细
    print("\n合成路径:")
    for item, r in used.items():
        ins = ", ".join(f"{names.get(i,i)}x{c}" for i, c in r["items"])
        outs = ", ".join(f"{names.get(o,o)}x{c}" for o, c in r["results"])
        hc = " [手搓]" if r["handcraft"] else ""
        print(f"  {names.get(item,item)}: [{r['name']}]{hc} {ins} -> {outs}  ({r['time']:g}s, {TYPE_NAMES.get(r['type'],r['type'])})")

    if multi:
        print("\n注意: 以下物品有多条配方路线, 当前取的是第一条(未必最省)。")
        print("      用 --via 物品名:配方id 指定, 例如 --via 氢:1307")
        for item, rs in multi.items():
            alts = ", ".join(f"[{r['index']}]" for r in rs)
            print(f"  {names.get(item,item)}: {alts} (当前用 [{used[item]['index']}])")


if __name__ == "__main__":
    main()

"""recipes.py — 从内存解析全部配方（RecipeProto），构建 原料→产物 生产图。

    python tools/recipes.py                # 输出 矿物→可做产物 分析 + recipes.json
    python tools/recipes.py --item 铁矿    # 查单个物品的全部用途/来源

原理: monomem 定位 RecipeProto 实例 -> 字段偏移自动校准（4 个 Int32[] 数组
按内容特征分类: itemId 范围 vs 小计数）-> 反向索引。
"""
from __future__ import annotations

import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monomem import MonoGame  # noqa: E402


def read_i32_array(g: MonoGame, arr_addr: int) -> list[int] | None:
    """Int32[] 数组 -> python list（len@+0x18, data@+0x20，DSP 布局）。"""
    L = g.mem.u64(arr_addr + 0x18)
    if not L or L > 64:
        return None
    b = g.mem.read(arr_addr + 0x20, int(L) * 4)
    return list(struct.unpack(f"<{L}i", b)) if b else None


def extract_recipes(g: MonoGame) -> list[dict]:
    protos = g.instances("RecipeProto")
    print(f"RecipeProto 实例: {len(protos)} 个", file=sys.stderr)
    recipes, seen_idx = [], set()
    for p in protos:
        idx = p.u32(0xA0)  # RecipeProto: index@0xA0（0x30 是 GridIndex，别用错——踩过坑）
        # 收集对象里的 Int32[] 数组字段和 String 字段
        arrays, strings = {}, {}
        for off in range(0x18, 0x90, 8):
            v = p.u64(off)
            if not v or not (0x1000 < v < 0x7FFFFFFFFFFF):
                continue
            cn = g.cname(g.mem.u64(v))
            if cn == "Int32[]":
                arr = read_i32_array(g, v)
                if arr is not None and arr:
                    arrays[off] = arr
            elif cn == "String" and off not in strings:
                s = g.read_string(v)
                if s and len(s) < 30:
                    strings[off] = s
        # 校准: 4 数组 = items(itemId大), itemCounts(小), results(itemId大), resultCounts(小)
        id_arrs = {o: a for o, a in arrays.items() if max(a) >= 1000}
        cnt_arrs = {o: a for o, a in arrays.items() if max(a) < 1000}
        if len(id_arrs) < 2 or len(cnt_arrs) < 2 or not strings:
            continue
        (io, items), (ro, results) = sorted(id_arrs.items())[:2]
        (co, icounts), (rco, rcounts) = sorted(cnt_arrs.items())[:2]
        name = strings[min(strings)]  # 最靠前的 String 一般是 Proto.name
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        # 已标定字段（数据特征反推 + 已知配方交叉验证，见 calibrate_recipes.py）:
        #   u32@0x98: 低字节=Type(ERecipeType), 第2字节=Handcraft
        #   u32@0x9C: TimeSpend (1/60 秒)
        packed = p.u32(0x98)
        raw = {f"{off:x}": p.u32(off) for off in range(0x18, 0xC8, 4)}  # 留档备查
        recipes.append({
            "index": idx, "name": name, "addr": p.addr,
            "items": list(zip(items, icounts)),
            "results": list(zip(results, rcounts)),
            "type": packed & 0xFF,
            "handcraft": (packed >> 8) & 1,
            "time": p.u32(0x9C),
            "raw": raw,
        })
    return recipes


def load_or_scan(rescan: bool = False):
    """recipes.json 存在且非 --rescan 时离线加载（0 秒），否则扫内存（~3分钟）。"""
    path = Path(__file__).parent / "recipes.json"
    if path.exists() and not rescan:
        d = json.load(open(path, encoding="utf-8"))
        names = {int(k): v for k, v in d["names"].items()}
        recipes = [{**r, "items": [tuple(x) for x in r["items"]],
                    "results": [tuple(x) for x in r["results"]]} for r in d["recipes"]]
        print(f"(离线加载 recipes.json: {len(recipes)} 配方)", file=sys.stderr)
        return names, recipes
    g = MonoGame("DSPGAME.exe", verbose=True)
    recipes = extract_recipes(g)
    names = {}
    for p in g.instances("ItemProto"):
        i = p.u32(0x30)
        s = p.str(0x20)
        if 0 < i < 70000 and s:
            names.setdefault(i, s)
    g.close()
    json.dump({"names": {str(k): v for k, v in names.items()},
               "recipes": [{**r, "items": [list(x) for x in r["items"]],
                            "results": [list(x) for x in r["results"]]} for r in recipes]},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return names, recipes


def print_tree(iid, names, prod, depth, seen, prefix=""):
    outs = sorted(prod.get(iid, []))
    for j, o in enumerate(outs):
        last = j == len(outs) - 1
        print(f"{prefix}{'└─ ' if last else '├─ '}{names.get(o, o)}")
        if o not in seen and depth > 1:
            print_tree(o, names, prod, depth - 1, seen | {o}, prefix + ("   " if last else "│  "))


def main():
    args = sys.argv[1:]
    rescan = "--rescan" in args
    args = [a for a in args if a != "--rescan"]
    names, recipes = load_or_scan(rescan)

    used_in = defaultdict(list)
    produced_by = defaultdict(list)
    prod = defaultdict(set)
    for r in recipes:
        for iid, _c in r["items"]:
            used_in[iid].append(r)
        for iid, _c in r["results"]:
            produced_by[iid].append(r)
        for iid, _c in r["items"]:
            for o, _oc in r["results"]:
                prod[iid].add(o)

    if args and args[0] == "--tree":
        q = args[1] if len(args) > 1 else "铁矿"
        depth = int(args[2]) if len(args) > 2 else 3
        hits = [i for i, n in names.items() if q in n or q == str(i)]
        for iid in hits[:1]:
            print(f"===== {names.get(iid, iid)} 产出树（{depth} 级）=====")
            print_tree(iid, names, prod, depth, {iid})
        return

    if args and args[0] == "--item":
        q = args[1]
        for iid in [i for i, n in names.items() if q in n][:3]:
            print(f"\n=== {names[iid]} ({iid}) ===")
            print("  能做出:")
            for r in used_in.get(iid, []):
                outs = ", ".join(f"{names.get(o, o)}x{c}" for o, c in r["results"])
                ins = ", ".join(f"{names.get(i, i)}x{c}" for i, c in r["items"])
                print(f"    [{r['name']}] {ins}  ->  {outs}")
            print("  来源:")
            for r in produced_by.get(iid, []):
                print(f"    [{r['name']}]")
        return

    # 总览: 只列"矿物/原料"（是某些配方的输入、且本身不是任何配方的输出 = 初级原料）
    primaries = sorted(i for i in used_in if i not in produced_by and i in names)
    print(f"\n== 初级原料 {len(primaries)} 种（可直接开采/采集，无合成来源）==")
    for iid in primaries:
        outs = set()
        for r in used_in[iid]:
            for o, _c in r["results"]:
                outs.add(o)
        out_s = "、".join(names.get(o, str(o)) for o in sorted(outs))
        print(f"  {names[iid]:<8s} → 直接产出: {out_s}")


if __name__ == "__main__":
    main()

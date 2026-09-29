"""calibrate_recipes.py — 从 recipes.json 的 raw 字段反推 Type/TimeSpend/Handcraft 偏移。

方法（不猜，用数据特征）：
- Type:       distinct 值 2~12 个，且"冶炼组共享一值、化工组另一值、组装组又一值"
- TimeSpend:  每个配方基本不同，量级合理（1~3600），铁块/磁线圈应是短耗时
- Handcraft:  值域 {0,1}（或打包在 u32 低字节），且 磁线圈=1、钢材=0（手搓不了冶炼? 以游戏为准仅参考）

    python tools/calibrate_recipes.py
"""
import json
from collections import defaultdict
from pathlib import Path

d = json.load(open(Path(__file__).parent / "recipes.json", encoding="utf-8"))
rs = d["recipes"]
names = {int(k): v for k, v in d["names"].items()}


def rec_of(product_name):
    pid = [i for i, n in names.items() if n == product_name]
    if not pid:
        return None
    pid = pid[0]
    return next((r for r in rs if any(i == pid for i, _ in r["results"])), None)


GROUPS = {
    "冶炼": ["铁块", "铜块", "钢材", "玻璃", "石材", "高能石墨", "钛块"],
    "化工": ["硫酸", "塑料", "有机晶体", "精炼油"],
    "组装": ["磁线圈", "齿轮", "电路板", "电动机", "电磁涡轮"],
    "研究": ["电磁矩阵", "宇宙矩阵"],
    "对撞": ["反物质"],
}

offs = sorted(rs[0]["raw"].keys(), key=lambda x: int(x, 16))
print(f"共 {len(rs)} 配方, raw 偏移 {len(offs)} 个\n")

print("== Type 候选（组内一致、组间不同、distinct<=12）==")
for off in offs:
    vals = {}
    ok = True
    for g, items in GROUPS.items():
        vs = set()
        for it in items:
            r = rec_of(it)
            if r is None:
                continue
            vs.add(r["raw"].get(off))
        if len(vs) != 1:
            ok = False
            break
        vals[g] = vs.pop()
    if ok and 3 <= len(set(vals.values())) <= 12:
        all_vals = {r["raw"].get(off) for r in rs}
        if len(all_vals) <= 12:
            print(f"  +0x{off}: 组值={vals}  全局distinct={sorted(v for v in all_vals if v is not None)}")

print("\n== TimeSpend 候选（distinct 多、量级 1~3600、样本合理）==")
for off in offs:
    vs = [r["raw"].get(off) for r in rs]
    vs = [v for v in vs if v is not None and 0 < v <= 3600]
    if len(vs) < len(rs) * 0.8 or len(set(vs)) < 10:
        continue
    sample = {n: (rec_of(n) or {"raw": {}})["raw"].get(off) for n in ("铁块", "磁线圈", "电路板", "钢材", "宇宙矩阵", "石墨烯")}
    print(f"  +0x{off}: 样本={sample}")

print("\n== Handcraft 候选（值域小 {0,1} 类）==")
for off in offs:
    vs = {r["raw"].get(off) for r in rs}
    if vs and all(v in (0, 1) for v in vs if v is not None) and len(vs) >= 2:
        ones = sum(1 for r in rs if r["raw"].get(off) == 1)
        sample = {n: (rec_of(n) or {"raw": {}})["raw"].get(off) for n in ("磁线圈", "齿轮", "钢材", "硫酸")}
        print(f"  +0x{off}: 1的个数={ones}/{len(rs)} 样本={sample}")

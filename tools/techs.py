"""techs.py — 科技树分析：已研究 / 正在研究 / 可研究(前置满足) / 锁定(缺前置)。

    python tools/techs.py            # 全报告（首次扫 TechProto ~1-2 分钟, 之后读 techs.json 秒出）
    python tools/techs.py --rescan   # 强制重扫
    python tools/techs.py 量子       # 过滤显示含关键词的科技

数据链路（全部类名/结构验证，无猜测偏移）:
    GameData+0x40 -> GameHistoryData
      +0x40 Dictionary<int,uint> techStates: buckets@+0x10 entries@+0x18 count@+0x40lo
             Entry(stride16): hash,next,key,val; int键 hash==key
      +0x58 Int32[] techQueue（当前研究队列）
    TechProto 实例(monomem 扫类): index@0x30? name@0x20 string, PreTechs/Items int[]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monomem import MonoGame, MemError  # noqa: E402
from dsp_core import DspBag  # noqa: E402  (复用缓存链拿 GameHistoryData)


def read_int_array(g, arr_addr):
    L = g.mem.u64(arr_addr + 0x18)
    if not L or L > 256:
        return []
    b = g.mem.read(arr_addr + 0x20, int(L) * 4)
    return list(struct.unpack(f"<{L}i", b)) if b else []


def scan_tech_protos(g: MonoGame) -> dict:
    """TechProto 实例 -> {id: {...}}。字段偏移用运行时探测而非硬编码:
    index/name 与 ItemProto 同基类(index@0x30,name@0x20 已验证), 数组字段按类名找。"""
    out = {}
    for p in g.instances("TechProto"):
        idx = p.u32(0x30)
        if not (0 < idx < 100000000) or idx in out:
            continue
        name = p.str(0x20)
        if not name:
            continue
        ints, arrays = {}, {}
        for off in range(0x34, 0xC0, 4):
            ints[off] = p.u32(off)
        for off in range(0x38, 0xC0, 8):
            v = p.u64(off)
            if v and 0x1000 < v < 0x7FFFFFFFFFFF:
                cn = g.cname(g.mem.u64(v))
                if cn == "Int32[]":
                    arrays[off] = read_int_array(g, v)
        out[idx] = {"name": name, "ints": {hex(k): v for k, v in ints.items()},
                    "arrays": {hex(k): v for k, v in arrays.items()}, "addr": p.addr}
    return out


def parse_tech_states(bag: DspBag) -> tuple[dict, list, int]:
    """返回 (techStates, techQueue, currentTech)。
    techStates = dict@H+0x30: Dictionary<int,TechState(20B)>，Entry stride 32:
      [hash][next][key][+0x0C: a, unlocked, curLevel, maxLevel, e]
      实测语义: unlocked(+0x10)>=1 = 已研究; curLevel/maxLevel = 升级科技等级
    currentTech = u32@H+0x7C（实时变化，与队列 techQueue@H+0x58 Int32[] 配合）"""
    mem = bag.mem
    H = mem.rq(bag.S + 0x40)
    assert bag.cname(mem.rq(H)) == "GameHistoryData"
    D = mem.rq(H + 0x30)
    E = mem.rq(D + 0x18)
    cnt = mem.r4(D + 0x40)
    L = int(mem.rq(E + 0x18) or 0)
    states = {}
    for i in range(min(L, cnt + 64)):
        b = mem.rd(E + 0x20 + i * 32, 32)
        if not b:
            break
        _h, _nxt, k = struct.unpack_from("<3I", b, 0)
        if k and k not in states:
            a, unlocked, cur, maxlv, e = struct.unpack_from("<5I", b, 12)
            states[k] = {"unlocked": unlocked, "cur": cur, "max": maxlv}
    Q = mem.rq(H + 0x58)
    queue = []
    if Q and bag.cname(mem.rq(Q)) == "Int32[]":
        ql = int(mem.rq(Q + 0x18) or 0)
        b = mem.rd(Q + 0x20, ql * 4)
        queue = [x for x in struct.unpack(f"<{ql}i", b) if x] if b and ql else []
    current = mem.r4(H + 0x7C)
    return states, queue, current


def classify_arrays(protos: dict, recipe_ids: set) -> dict:
    """给每个 TechProto 的 Int32[] 字段分类: PreTechs / UnlockRecipes / Items(矩阵消耗)。
    科技 id 与配方 id 数值域重叠(1001 既是科技又是配方 id)，用整数组投票判别:
      - 值全在 6001..6006 (矩阵) => Items(研究消耗)
      - 多数值是"配方独有 id"(不在科技表) => UnlockRecipes
      - 多数值是"科技表 id" 且非矩阵 => PreTechs
    """
    tech_ids = set(protos)
    out = {}
    for tid, p in protos.items():
        pre, unlock, items = set(), set(), []
        for off, arr in p["arrays"].items():
            if not arr:
                continue
            vals = [x for x in arr if x > 0]
            if not vals:
                continue
            if all(6001 <= x <= 6006 for x in vals):
                items = vals
                continue
            tech_hit = sum(1 for x in vals if x in tech_ids)
            recipe_hit = sum(1 for x in vals if x in recipe_ids)
            if recipe_hit > tech_hit:
                unlock |= set(vals)
            elif tech_hit >= max(1, len(vals) // 2):
                pre |= set(vals)
        out[tid] = {"pre": pre, "unlock": unlock, "items": items}
    return out


def main():
    rescan = "--rescan" in sys.argv
    kw = [a for a in sys.argv[1:] if not a.startswith("--")]
    cache = Path(__file__).parent / "techs.json"

    bag = DspBag(verbose=False)
    bag.connect()
    states, queue, current = parse_tech_states(bag)
    print(f"techStates: {len(states)} 条, 当前研究: {current}, 队列: {queue}", file=sys.stderr)

    if cache.exists() and not rescan:
        protos = {int(k): v for k, v in json.load(open(cache, encoding="utf-8")).items()}
        print(f"(离线 TechProto: {len(protos)})", file=sys.stderr)
    else:
        g = MonoGame("DSPGAME.exe", verbose=True)
        protos = scan_tech_protos(g)
        g.close()
        json.dump({str(k): v for k, v in protos.items()},
                  open(cache, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"TechProto: {len(protos)} 个 -> techs.json", file=sys.stderr)

    rj = Path(__file__).parent / "recipes.json"
    recipe_ids = set()
    if rj.exists():
        recipe_ids = {r["index"] for r in json.load(open(rj, encoding="utf-8"))["recipes"]}
    cls = classify_arrays(protos, recipe_ids)

    researched = {t for t, s in states.items() if s["unlocked"] >= 1}
    doing = {current} | set(queue)

    def fmt(tid):
        p = protos.get(tid)
        nm = p["name"] if p else f"#{tid}"
        s = states.get(tid)
        lv = f" Lv{s['cur']}/{s['max']}" if s and s["max"] > 1 else ""
        return f"{nm}{lv}({tid})"

    lines_res, lines_doing, lines_avail, lines_locked = [], [], [], []
    for tid in sorted(set(protos) | set(states)):
        if kw and not any(k in (protos.get(tid, {}).get("name", "") + str(tid)) for k in kw):
            continue
        if tid in doing and tid not in researched:
            lines_doing.append(fmt(tid))
        elif tid in researched:
            lines_res.append(fmt(tid))
        else:
            missing = sorted(m for m in cls.get(tid, {}).get("pre", ()) if m not in researched)
            if not missing:
                lines_avail.append(f"✓ {fmt(tid)}")
            else:
                lines_locked.append(f"✗ {fmt(tid)}  缺: {', '.join(fmt(m) for m in missing[:3])}")

    if lines_doing:
        print(f"\n===== ▶ 研究中 ({len(lines_doing)}) =====")
        for l in lines_doing:
            print(" ", l)
    print(f"\n===== ✓ 可立即研究·前置已满足 ({len(lines_avail)}) =====")
    for l in lines_avail:
        print(" ", l)
    print(f"\n===== 已研究 ({len(lines_res)}) =====")
    for l in lines_res:
        print(" ", l)
    if not kw:
        print(f"\n===== 锁定 ({len(lines_locked)}, 显示前30) =====")
        for l in lines_locked[:30]:
            print(" ", l)


if __name__ == "__main__":
    main()

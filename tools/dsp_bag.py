"""dsp_bag.py — 戴森球计划 背包查看/修改 CLI（教学项目，文档见 README.md）

用法:
    python tools/dsp_bag.py                      # 自动定位并打印背包
    python tools/dsp_bag.py --json               # JSON 输出
    python tools/dsp_bag.py --set 0 999          # 第 0 格数量改 999（自动钳到上限）
    python tools/dsp_bag.py --give 2001          # 往第一个空格放传送带(满堆)
    python tools/dsp_bag.py --give 2001 --count 5 --slot 3   # 第3格放5个
    python tools/dsp_bag.py --give 宇宙矩阵       # 按名字给（模糊匹配唯一时）
    python tools/dsp_bag.py --search 矩阵         # 模糊查物品 id
    python tools/dsp_bag.py --clear 4            # 清空第 4 格
    python tools/dsp_bag.py --list-items         # 打印全部 itemId -> 名字

前置: 游戏运行中且已加载存档。改数量后回游戏拿放一次即可刷新显示。
"""
import argparse
import json
import sys

# 游戏物品名可能含 \xa0 等字符，Windows GBK 控制台会崩；统一 utf-8 输出
# （cmd.exe 里若乱码，先执行 chcp 65001）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dsp_core import DspBag, DspError


def main():
    ap = argparse.ArgumentParser(description="DSP 背包内存工具")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--set", nargs=2, type=int, metavar=("SLOT", "COUNT"), help="修改指定格数量")
    ap.add_argument("--give", help="给物品: id 或 名字(模糊唯一)")
    ap.add_argument("--count", type=int, help="配合 --give，默认满堆")
    ap.add_argument("--slot", type=int, help="配合 --give，目标格(默认第一个空格)")
    ap.add_argument("--clear", type=int, metavar="SLOT", help="清空指定格")
    ap.add_argument("--sand", nargs="?", type=int, const=-1, metavar="N",
                    help="查看沙土数量(不带值)或设为 N")
    ap.add_argument("--search", metavar="KW", help="模糊查物品名/id")
    ap.add_argument("--list-items", action="store_true", help="打印物品 id->名 表")
    ap.add_argument("--quiet", action="store_true", help="不打印定位过程")
    args = ap.parse_args()

    try:
        bag = DspBag(verbose=not args.quiet)
    except DspError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        bag.connect()

        if args.search:
            res = bag.search(args.search)
            if not res:
                print("无匹配")
            for i, n, s in res:
                print(f"{i:6d}  {n}  (match={s})")
            return

        if args.list_items:
            for i, n in sorted(bag.names.items()):
                print(f"{i:6d}  {n}")
            return

        if args.give is not None:
            item_id = resolve_item(bag, args.give)
            slot = args.slot if args.slot is not None else bag.first_empty()
            if slot is None:
                raise DspError("背包无空格，用 --slot 指定或先清一格")
            applied = bag.give(slot, item_id, args.count, force=args.slot is not None)
            print(f"已放入 第{slot}格: {item_id} {bag.names.get(item_id)} x{applied}")

        if args.clear is not None:
            bag.clear_slot(args.clear)
            print(f"已清空第 {args.clear} 格")

        if args.sand is not None:
            if args.sand == -1:
                print(f"沙土: {bag.get_sand()}")
            else:
                bag.set_sand(args.sand)
                print(f"沙土 -> {bag.get_sand()}")

        if args.set:
            slot, cnt = args.set
            bag.set_count(slot, cnt)

        rows = bag.slots()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            print(bag.summary())
    except DspError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        bag.close()


def resolve_item(bag, s: str) -> int:
    if s.strip().isdigit():
        i = int(s)
        if i in bag.names:
            return i
    res = bag.search(s)
    if not res:
        raise DspError(f"找不到物品 {s!r}")
    if len(res) > 1 and not s.strip().isdigit():
        top = res[0]
        exact = [r for r in res if r[1] == s]
        if exact:
            return exact[0][0]
        raise DspError(f"{s!r} 匹配多个: {[f'{i}{n}' for i, n, _ in res[:6]]}（改用 id 或更精确名）")
    return res[0][0]


if __name__ == "__main__":
    main()

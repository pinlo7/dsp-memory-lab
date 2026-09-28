"""dsp_via_monomem.py — 用通用 monomem 库重写 DSP 背包读取，验证抽象成立。

对比 dsp_core.py（专用实现）：这里没有任何 DSP 专属的扫描/解析代码，
只有"游戏知识"（偏移常量）——这正是抽象的目标。

    python tools/examples/dsp_via_monomem.py
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monomem import MonoGame, MemError  # noqa: E402

# ---- 游戏知识（版本相关偏移，逆向得来的"地图"）----
OFF_MAIN_PLAYER = 0xC0      # GameData.mainPlayer
OFF_PACKAGE = 0x68          # Player.package (StorageComponent)
OFF_GRIDS = 0x30            # StorageComponent.grids (GRID[])
OFF_SAND = 0x170            # Player.sandCount
GRID_STRIDE = 0x14          # GRID 结构体大小
OFF_PROTO_NAME = 0x20       # ItemProto.<name> (string)
OFF_PROTO_INDEX = 0x30      # ItemProto.<index> (itemId)


def main():
    g = MonoGame("DSPGAME.exe", verbose=True)

    # 1) GameData 单例：类名 -> 实例 -> 验证 mainPlayer 是 Player
    gd = None
    for cand in g.instances("GameData"):
        p = cand.ref(OFF_MAIN_PLAYER)
        if p and p.class_name == "Player":
            gd = cand
            break
    if gd is None:
        raise MemError("没找到已加载存档的 GameData")
    player = gd.ref(OFF_MAIN_PLAYER)
    print(f"GameData={gd.addr:#x}  Player={player.addr:#x}")

    # 2) 背包
    pkg = player.ref(OFF_PACKAGE)
    assert pkg.class_name == "StorageComponent", pkg.class_name
    grids = pkg.array(OFF_GRIDS)
    assert grids is not None and grids.class_name == "GRID[]"
    size = pkg.u32(0x70)
    print(f"背包容量={size} 数组长度={grids.length}（不变量互证: {size == grids.length}）")

    # 3) 物品名表：ItemProto 实例
    names = {}
    for proto in g.instances("ItemProto"):
        idx = proto.u32(OFF_PROTO_INDEX)
        nm = proto.str(OFF_PROTO_NAME)
        if 0 < idx < 70000 and nm:
            names.setdefault(idx, nm)
    print(f"物品名表: {len(names)} 个")

    # 4) 读格子
    total = 0
    for i, b in grids.structs(GRID_STRIDE):
        item_id, _, count, stack = struct.unpack_from("<4I", b, 0)
        if item_id:
            total += count
            print(f"  [{i:2d}] {names.get(item_id, f'#{item_id}'):<14s} x{count}/{stack}")
    print(f"共 {total} 件物品；沙土 = {player.u32(OFF_SAND)}")
    g.close()


if __name__ == "__main__":
    main()

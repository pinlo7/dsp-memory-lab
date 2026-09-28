"""monomem — 通用 Unity Mono 游戏外部内存读取库（从 DSP 背包工具沉淀）。

    from monomem import MonoGame
    g = MonoGame("Game.exe")
    obj = g.instances("GameData")[0]
    player = obj.ref(0xC0)
    print(player.class_name)

只读默认；写入用 obj.game.mem.write_u32。不注入、不改游戏文件。
"""
from .layout import Layout, DSP
from .process import Process, MemError, find_pid
from .runtime import MonoGame, MonoClass, MonoObject, MonoArray

__all__ = ["MonoGame", "MonoClass", "MonoObject", "MonoArray",
           "Layout", "DSP", "Process", "MemError", "find_pid"]
__version__ = "0.1.0"

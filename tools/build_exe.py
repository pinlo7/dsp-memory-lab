"""build_exe.py — 一键打包 DSP 背包工具为 exe（PyInstaller）

用法:
    python tools/build_exe.py              # 打包 GUI（默认，无控制台窗口）
    python tools/build_exe.py --cli        # 打包命令行版
    python tools/build_exe.py --both       # 两个都打
    python tools/build_exe.py --onedir     # 目录模式（启动快，产物是文件夹）

产物: dist/DSP背包工具.exe （或 dist/DSP背包CLI.exe）
注意:
  - 缓存(.dsp_cache.json)和设置(.dsp_gui_settings.json)会生成在 exe 同目录
  - 首次运行仍要全内存扫描（~1分钟），之后秒开
  - 杀软可能误报（读写游戏内存 + 无签名），加白名单即可
"""
import argparse
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("未装 PyInstaller，正在安装…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])


def build(entry: str, name: str, windowed: bool, onedir: bool) -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if onedir else "--onefile",
        "--windowed" if windowed else "--console",
        "--name", name,
        "--paths", str(HERE),          # 让 dsp_core 可被 import
        "--hidden-import", "numpy",
        "--collect-submodules", "numpy",  # numpy 有些子模块动态 import，保险
        str(HERE / entry),
    ]
    print("$", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    ext = ".exe"
    out = ROOT / "dist" / (name if onedir else name + ext)
    print(f"✅ 产物: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true", help="打包命令行版")
    ap.add_argument("--both", action="store_true", help="GUI+CLI 都打")
    ap.add_argument("--onedir", action="store_true", help="目录模式(启动快)")
    args = ap.parse_args()

    ensure_pyinstaller()
    if args.cli:
        build("dsp_bag.py", "DSP背包CLI", windowed=False, onedir=args.onedir)
    elif args.both:
        build("dsp_gui.py", "DSP背包工具", windowed=True, onedir=args.onedir)
        build("dsp_bag.py", "DSP背包CLI", windowed=False, onedir=args.onedir)
    else:
        build("dsp_gui.py", "DSP背包工具", windowed=True, onedir=args.onedir)


if __name__ == "__main__":
    main()

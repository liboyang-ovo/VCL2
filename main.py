#!/usr/bin/env python3
"""VCL2 - Voxel Craft Launcher 2 入口。

不带参数        -> 图形界面（tkinter，纯标准库）
带子命令        -> 命令行模式（见 --help）

示例：
    python main.py
    python main.py list --filter release
    python main.py launch 1.21.1 --name Steve --ram 4096
"""

import os
import sys

CLI_COMMANDS = ("list", "install", "launch", "fabric", "mod", "java",
                "config", "remote")
VERSION = "2.0.6"


def main(argv=None):
    # Windows 控制台编码兜底，避免中文输出崩在 GBK 上
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass

    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-v", "--version"):
        print("VCL2 %s  (Voxel Craft Launcher 2)" % VERSION)
        return 0

    # 显式要求命令行
    if argv and argv[0] == "cli":
        from minecraft_launcher.cli import main as cli_main
        return cli_main(argv[1:])

    # 帮助统一走 CLI 的解析器
    if argv and argv[0] in ("-h", "--help"):
        from minecraft_launcher.cli import build_parser
        build_parser().print_help()
        return 0

    # 有子命令就走命令行；否则（无参数或 --gui）开图形界面
    if argv and argv[0] in CLI_COMMANDS:
        from minecraft_launcher.cli import main as cli_main
        return cli_main(argv)

    if argv and argv[0] != "--gui":
        print("未知参数：%s\n" % argv[0], file=sys.stderr)
        from minecraft_launcher.cli import build_parser
        build_parser().print_help()
        return 2

    from minecraft_launcher.gui_tk import launch_gui
    launch_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""系统信息探测：位数、平台标签。

32 位系统只能装 32 位 Java（1.17+ 需要 64 位），进程内存上限也低。
这些判断集中在这里，避免散落在各模块里漏掉。
"""

import os
import platform
import sys


def app_dir():
    """程序所在目录：打包后是 exe 所在目录，源码运行时是项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_windows():
    return os.name == "nt"


def is_64bit_os():
    """操作系统是否 64 位。

    注意不能用 platform.machine() 判断：32 位 Python 跑在 64 位 Windows 上
    也常常报 AMD64。要看 WOW64 环境变量。
    """
    if is_windows():
        # 32 位进程在 64 位系统上会设置 PROCESSOR_ARCHITEW6432
        if os.environ.get("PROCESSOR_ARCHITEW6432"):
            return True
        return (os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() in ("AMD64", "ARM64"))
    return platform.machine().lower() in ("x86_64", "amd64", "aarch64", "arm64")


def is_64bit_python():
    return platform.architecture()[0] == "64bit"


def arch_label():
    """"Win7 32 位" 这种可读标签，用于提示。"""
    bits = "64 位" if is_64bit_os() else "32 位"
    return "%s %s" % (platform.system(), bits)


def java_runtime_platform_key():
    """Mojang Java 运行时清单里的平台键。"""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        if not is_64bit_os():
            return "windows-x86"
        if "arm" in machine:
            return "windows-arm64"
        return "windows-x64"
    if system == "Darwin":
        return "mac-os-arm64" if "arm" in machine else "mac-os"
    return "linux"


def supports_new_minecraft():
    """1.17 起官方只提供 64 位 Java，32 位系统玩不了。"""
    return is_64bit_os()

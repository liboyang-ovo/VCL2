"""Java 运行时管理：检测本机 Java、自动下载官方版 JRE。"""

import os
import re
import shutil
import subprocess
import json
import sys

from .network.downloader import Downloader
from . import sysinfo

JAVA_RUNTIME_MANIFEST_URL = (
    "https://piston-meta.mojang.com/v1/products/java-runtime/"
    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
)


class JavaManager:
    def __init__(self, minecraft_dir: str, downloader: Downloader):
        self.minecraft_dir = minecraft_dir
        self.dl = downloader
        self.runtimes_root = os.path.join(minecraft_dir, "runtime")
        self.manifest = None

    # ---------------- 检测本机 Java ----------------
    @staticmethod
    def _java_version(java_exe):
        """运行 java -version 获取主版本号。"""
        try:
            out = subprocess.run(
                [java_exe, "-version"],
                capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = (out.stdout + "\n" + out.stderr)
            m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
            if m:
                major = int(m.group(1))
                if major == 1:
                    major = int(m.group(2) or 8)
                return major
        except Exception:
            return None
        return None

    def find_system_java(self, min_major=None):
        """在 PATH 与常见位置查找 java。"""
        candidates = []
        # PATH
        which = shutil.which("java")
        if which:
            candidates.append(which)
        # JAVA_HOME
        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            candidates.append(os.path.join(java_home, "bin", "java.exe"))
        # 常见安装路径
        for prog in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if not prog:
                continue
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft"):
                base = os.path.join(prog, vendor)
                if os.path.isdir(base):
                    for sub in os.listdir(base):
                        if sub.lower().startswith("jdk") or sub.lower().startswith("jre"):
                            exe = os.path.join(base, sub, "bin", "java.exe")
                            if os.path.exists(exe):
                                candidates.append(exe)

        for cand in candidates:
            major = self._java_version(cand)
            if major is not None:
                if min_major and major < min_major:
                    continue
                return cand
        return None

    # ---------------- 官方 JRE ----------------
    def _get_manifest(self):
        if self.manifest is None:
            self.manifest = self.dl.get_json(JAVA_RUNTIME_MANIFEST_URL)
        return self.manifest

    def _platform_key(self):
        return sysinfo.java_runtime_platform_key()

    def _java_dir_for(self, component):
        return os.path.join(self.runtimes_root, component)

    def java_exe_for(self, component):
        java_dir = self._java_dir_for(component)
        exe = os.path.join(java_dir, "bin", "java.exe" if os.name == "nt" else "java")
        return exe if os.path.exists(exe) else None

    def install_java(self, component, progress_cb=None):
        """下载并安装官方 JRE 组件。返回 java.exe 路径。"""
        platform_key = self._platform_key()
        manifest = self._get_manifest()
        platform_data = manifest.get(platform_key) or {}
        entry = platform_data.get(component)
        if not entry:
            if platform_key == "windows-x86":
                raise RuntimeError(
                    "Mojang 已不再为 32 位 Windows 提供 Java 运行时。\n"
                    "请手动安装 32 位 Java（1.16.5 及更早版本用 Java 8），\n"
                    "然后在「设置 → Java 路径」里指定 java.exe。")
            raise RuntimeError("运行时组件 %s 在当前平台（%s）不可用" % (component, platform_key))

        java_dir = self._java_dir_for(component)
        done_flag = os.path.join(java_dir, ".installed")

        # 已安装标记存在 或 组件已存在且 java 可运行 -> 直接复用，避免重复下载
        exe = self.java_exe_for(component)
        if os.path.exists(done_flag) and exe:
            return exe
        if exe and self._java_version(exe) is not None:
            return exe

        # 先删除旧目录，避免残留
        if os.path.isdir(java_dir):
            shutil.rmtree(java_dir, ignore_errors=True)
        os.makedirs(java_dir, exist_ok=True)

        # 组件可能有多条（不同版本），取第一条
        entry_data = entry[0] if isinstance(entry, list) else entry
        manifest_url = entry_data["manifest"]["url"]
        runtime_manifest = self.dl.get_json(manifest_url)

        files = runtime_manifest.get("files", {})
        tasks = []
        for rel_path, info in files.items():
            if info.get("type") == "directory":
                continue
            downloads = info.get("downloads", {})
            if not downloads:
                continue
            # lzma 压缩格式（官方 JRE 用 lzma 压缩的 exe 等）
            lzma = downloads.get("lzma")
            raw = downloads.get("raw")
            entry_for_dl = lzma or raw
            if not entry_for_dl:
                continue
            url = entry_for_dl["url"]
            sha1 = entry_for_dl["sha1"]
            dest = os.path.join(java_dir, rel_path)
            # lzma 下载到临时文件，解压后写入真实路径
            dl_dest = dest + (".lzma" if lzma else "")
            tasks.append((url, dl_dest, sha1, entry_for_dl.get("size", 0),
                          lzma is not None, dest, info.get("executable", False)))

        if progress_cb:
            progress_cb(f"正在下载 Java 运行时（{component}，{len(tasks)} 个文件）...")

        # 分批下载，然后处理 lzma 解压
        plain_tasks = [(u, d, s, sz) for u, d, s, sz, _, _, _ in tasks]
        self.dl.download_many(plain_tasks, total=len(plain_tasks))

        # lzma 解压
        failed = []
        for url, dl_dest, sha1, size, is_lzma, dest, is_exec in tasks:
            if not os.path.exists(dl_dest):
                failed.append(dest)
                continue
            if is_lzma:
                try:
                    self._decompress_lzma(dl_dest, dest)
                    os.remove(dl_dest)
                except Exception:
                    failed.append(dest)
        if failed:
            raise RuntimeError(f"Java 运行时安装失败：{len(failed)} 个文件处理出错")

        # 写完成标记
        with open(done_flag, "w", encoding="utf-8") as f:
            json.dump({"component": component, "platform": platform_key}, f)

        exe = self.java_exe_for(component)
        if not exe:
            raise RuntimeError("Java 运行时安装后未找到 java.exe")
        if progress_cb:
            progress_cb(f"Java 运行时 {component} 安装完成")
        return exe

    @staticmethod
    def _decompress_lzma(lzma_path, dest_path):
        import lzma
        with open(lzma_path, "rb") as f_in:
            # 官方 JRE 的文件通常带 256 字节头 + lzma 数据，尝试多种方式
            data = f_in.read()
        # 尝试直接作为 xz/lzma
        for use_xz in (True, False):
            try:
                if use_xz:
                    decoded = lzma.decompress(data)
                else:
                    decoded = lzma.decompress(data, format=lzma.FORMAT_ALONE)
                with open(dest_path, "wb") as f_out:
                    f_out.write(decoded)
                return
            except Exception:
                continue
        # 带自定义头：跳过前 256 字节后按 FORMAT_ALONE 解压
        try:
            decoded = lzma.decompress(data[256:], format=lzma.FORMAT_ALONE)
            with open(dest_path, "wb") as f_out:
                f_out.write(decoded)
            return
        except Exception:
            pass
        raise RuntimeError("无法解压 LZMA 文件")

    def resolve(self, version_j, config, progress_cb=None):
        """根据配置解析出可用的 java.exe 路径。"""
        # 32 位系统跑不了需要 Java 17+ 的版本（1.17 起官方只发 64 位）
        need_major = getattr(version_j, "java_major", 0) or 0
        if not sysinfo.is_64bit_os() and need_major >= 17:
            raise RuntimeError(
                "这个版本需要 Java %d（64 位），而当前系统是 %s。\n"
                "32 位 Windows 最高只能玩到 1.16.5。" % (need_major, sysinfo.arch_label()))

        # 用户自定义 Java
        user_java = config["java_path"]
        if user_java:
            if os.path.isfile(user_java):
                return user_java
            raise RuntimeError(f"指定的 Java 路径不存在：{user_java}")

        component = version_j.java_component
        if component:
            exe = self.java_exe_for(component)
            if exe:
                return exe

        if config["auto_java"] and component:
            return self.install_java(component, progress_cb)

        # 兜底：系统 Java
        min_major = getattr(version_j, "java_major", 0) or 8
        sys_java = self.find_system_java(min_major)
        if sys_java:
            return sys_java
        raise RuntimeError("未找到可用的 Java 运行时，请在设置中指定 Java 路径或开启自动下载")
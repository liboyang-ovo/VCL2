"""库文件管理：解析版本 JSON 中的库文件与原生库(natives)、下载并解压 natives。"""

import os
import zipfile
import shutil
import hashlib
import platform

from .network.downloader import Downloader


class OSType:
    WINDOWS = "windows"
    OSX = "osx"
    LINUX = "linux"

    @staticmethod
    def detect():
        sys = platform.system()
        if sys == "Windows":
            return OSType.WINDOWS
        if sys == "Darwin":
            return OSType.OSX
        return OSType.LINUX

    @staticmethod
    def arch64():
        return platform.machine().lower() in ("amd64", "x86_64")


OS = OSType.detect()


def _matches_os(os_info):
    """判断库的 os 规则是否匹配当前系统。os_info 是规则里的 "os" 字典。"""
    if not os_info:
        return True
    machine = platform.machine().lower()
    is_arm = machine in ("aarch64", "arm64")
    is_x64 = machine in ("amd64", "x86_64")
    is_x86 = machine in ("x86", "i386", "i686")

    name = os_info.get("name")
    if name:
        base = name.split("-")[0]
        if base != OS:
            return False
        # 名字里的架构后缀：windows-arm64 / windows-x86
        if name.endswith("-arm64") and not is_arm:
            return False
        if name.endswith("-x86") and not (is_x86 or (is_x64 and OS != OSType.WINDOWS)):
            return False

    arch = os_info.get("arch")
    if arch:
        if arch == "x86" and not is_x86:
            return False
        if arch in ("arm", "arm64") and not is_arm:
            return False
        if arch in ("x86_64", "amd64") and not (is_x64 or is_arm):
            return False
    return True


class Rule:
    """处理 rules 列表。返回是否应包含该项。"""

    @staticmethod
    def passes(rules, active_features=None):
        if not rules:
            return True
        active_features = active_features or {}
        allowed = False
        for rule in rules:
            action = rule.get("action", "allow")
            os_info = rule.get("os")
            if os_info and not (_matches_os(os_info) and matches_os_version(os_info)):
                continue
            if not _matches_features(rule.get("features"), active_features):
                continue
            allowed = (action == "allow")
        return allowed


def matches_os_version(os_info):
    """简化版 os.version 处理：Windows 一般满足正则判断。"""
    return True


def _matches_features(features, active_features):
    """判断 features 是否匹配。feature 值为 true 需启用，false 需禁用。"""
    if not features:
        return True
    for key, required in features.items():
        actual = key in active_features
        if required != actual:
            return False
    return True


class LibraryManager:
    """解析版本库、下载库与 natives。"""

    def __init__(self, minecraft_dir: str, downloader: Downloader):
        self.minecraft_dir = minecraft_dir
        self.dl = downloader
        self.libraries_root = os.path.join(minecraft_dir, "libraries")

    # ---------------- Maven 坐标解析 ----------------
    @staticmethod
    def parse_name(name):
        parts = name.split("@")
        ext = parts[1] if len(parts) > 1 else "jar"
        coords = parts[0].split(":")
        if len(coords) < 3:
            raise ValueError("无法解析库坐标: %s" % name)
        group, artifact, version = coords[0], coords[1], coords[2]
        classifier = coords[3] if len(coords) >= 4 else None
        return group, artifact, version, classifier, ext

    def maven_path(self, group, artifact, version, classifier=None, ext="jar"):
        """根据 Maven 坐标得到库相对路径（libraries 下）。"""
        path_parts = group.split(".") + [artifact, version]
        fname = f"{artifact}-{version}"
        if classifier:
            fname += f"-{classifier}"
        fname += f".{ext}"
        return os.path.join(*path_parts, fname)

    # ---------------- 原生库 classifier ----------------
    def _natives_classifier(self, library):
        """若 library 定义了 natives 映射，返回当前系统的 classifier 名，否则 None。"""
        natives = library.get("natives")
        if not natives:
            return None
        clazz = natives.get(OS)
        if not clazz:
            return None
        return clazz.replace("{arch}", "64" if OSType.arch64() else "32")

    def _resolve_library_artifact(self, library):
        """返回 (artifact_info, is_native) 。
        artifact_info: dict(url, path, sha1, size) 或 None（被跳过）。
        is_native: 是否为需要解压的 native 库。
        """
        if not Rule.passes(library.get("rules")):
            return None, False
        downloads = library.get("downloads") or {}
        name = library.get("name", "")

        # 情况一：明确的 native classifier（老格式 natives 字段）
        natives_clazz = self._natives_classifier(library)
        if natives_clazz:
            classifiers = downloads.get("classifiers") or {}
            info = classifiers.get(natives_clazz)
            if info:
                return info, True
            return None, False

        # 情况二：新格式，library 名称中带 ":natives-xxx"（如 lwjgl *:natives-windows）
        if ":natives-" in name:
            # 判断是否匹配本系统（规则里带 os）
            if not Rule.passes(library.get("rules")):
                return None, False
            # classifier 后缀决定架构：natives-windows / -arm64 / -x86
            classifier = name.split(":natives-")[-1]
            machine = platform.machine().lower()
            is_arm = machine in ("aarch64", "arm64")
            is_x86 = machine in ("x86", "i386", "i686")
            suffix = classifier.split("-", 1)[1] if "-" in classifier else ""
            if suffix == "arm64" and not is_arm:
                return None, False
            if suffix == "x86" and not is_x86:
                return None, False
            if suffix == "" and (is_arm or is_x86):
                # 无后缀的默认库仅在 x64 使用
                return None, False
            artifact = downloads.get("artifact")
            return (artifact, True) if artifact else (None, False)

        # 普通库
        artifact = downloads.get("artifact")
        if artifact:
            return artifact, False

        # 老式格式（Fabric/Forge 库常见）：只有 name + 仓库基址 url + sha1/size
        if name and library.get("url"):
            return self._legacy_artifact(name, library), False
        return None, False

    @staticmethod
    def _legacy_artifact(name, library):
        """把老式 {name, url, sha1, size} 库转换为 artifact 结构。路径用正斜杠。"""
        group, artifact, version, classifier, ext = LibraryManager.parse_name(name)
        path_parts = group.split(".") + [artifact, version]
        fname = f"{artifact}-{version}"
        if classifier:
            fname += f"-{classifier}"
        fname += f".{ext}"
        path = "/".join(path_parts + [fname])
        return {
            "url": library["url"].rstrip("/") + "/" + path,
            "path": path,
            "sha1": library.get("sha1"),
            "size": library.get("size"),
        }

    # ---------------- 下载 ----------------
    def _download_tasks(self, version_j):
        """返回 (普通库tasks, 原生库tasks)。task=(url, dest, sha1, size)。"""
        normal, natives = [], []
        seen = set()
        for lib in version_j.libraries:
            artifact, is_native = self._resolve_library_artifact(lib)
            if not artifact:
                continue
            path = artifact.get("path")
            if not path:
                continue
            url = artifact.get("url", "")
            sha1 = artifact.get("sha1")
            size = artifact.get("size", 0)
            dest = os.path.join(self.libraries_root, path)
            if dest in seen:
                continue
            seen.add(dest)
            task = (url, dest, sha1, size)
            (natives if is_native else normal).append(task)
        return normal, natives

    def ensure_libraries(self, version_j, progress_cb=None):
        """下载缺失的库（含 natives）。返回普通库的 classpath 列表。"""
        normal, natives = self._download_tasks(version_j)

        need_normal = [t for t in normal if not self._check(t)]
        need_natives = [t for t in natives if not self._check(t)]

        if need_normal and progress_cb:
            progress_cb(f"正在下载库文件（{len(need_normal)} 个）...")
        self.dl.download_many(need_normal, total=len(need_normal))

        if need_natives and progress_cb:
            progress_cb(f"正在下载原生库（{len(need_natives)} 个）...")
        self.dl.download_many(need_natives, total=len(need_natives))

        failed = [t for t in need_normal + need_natives if not self._check(t)]
        if failed:
            raise RuntimeError(f"有 {len(failed)} 个库文件下载失败")

        return [t[1] for t in normal]

    def extract_natives(self, version_id, version_j, progress_cb=None):
        """下载并解压原生库到版本目录。"""
        _, natives = self._download_tasks(version_j)
        want = [t for t in natives if not self._check(t)]
        if want:
            if progress_cb:
                progress_cb(f"正在下载原生库（{len(want)} 个）...")
            self.dl.download_many(want, total=len(want))

        natives_dir = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}-natives")
        if os.path.isdir(natives_dir):
            shutil.rmtree(natives_dir, ignore_errors=True)
        os.makedirs(natives_dir, exist_ok=True)

        for task in natives:
            dest = task[1]
            if not os.path.exists(dest):
                continue
            try:
                with zipfile.ZipFile(dest) as zf:
                    for member in zf.namelist():
                        if member.endswith("/"):
                            continue
                        self._safe_extract(zf, member, natives_dir)
            except zipfile.BadZipFile:
                continue
        if progress_cb:
            progress_cb("原生库解压完成")

    @staticmethod
    def _safe_extract(zf, member, dest_dir):
        target = os.path.realpath(os.path.join(dest_dir, member))
        dest = os.path.realpath(dest_dir)
        if not target.startswith(dest + os.sep):
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)

    def _check(self, task):
        url, dest, sha1, size = task
        if not sha1:
            return os.path.exists(dest) and (size is None or os.path.getsize(dest) == size)
        return _sha1_file(dest) == sha1.lower()


def _sha1_file(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()
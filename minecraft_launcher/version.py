"""版本管理：拉取官方版本清单、解析版本 JSON、下载客户端与资源文件。"""

import os
import json
import time

from .constants import VERSION_MANIFEST_URL, ASSET_BASE, USER_AGENT
from .network.downloader import Downloader


class VersionInfo:
    """版本清单中的一条。"""

    def __init__(self, data):
        self.id = data["id"]
        self.type = data.get("type", "release")
        self.url = data["url"]
        self.release_time = data.get("releaseTime", "")
        self.compliance_level = data.get("complianceLevel", 0)

    def __repr__(self):
        return f"<VersionInfo {self.id} ({self.type})>"


class VersionJSON:
    """单个版本的 version.json 解析结果。"""

    def __init__(self, data):
        self.data = data
        self.id = data.get("id", "")
        self.main_class = data.get("mainClass", "")
        self.min_launcher_version = data.get("minimumLauncherVersion", 0)

        self.assets_id = data.get("assets", data.get("assetIndex", {}).get("id", ""))
        asset_index = data.get("assetIndex") or {}
        self.asset_index = {
            "id": self.assets_id,
            "url": asset_index.get("url", ""),
            "sha1": asset_index.get("sha1", ""),
            "size": asset_index.get("size", 0),
        }

        downloads = data.get("downloads", {})
        client = downloads.get("client") or {}
        self.client = {
            "url": client.get("url", ""),
            "sha1": client.get("sha1", ""),
            "size": client.get("size", 0),
        }

        self.java_component = None
        java_version = data.get("javaVersion")
        if java_version:
            self.java_component = java_version.get("component")
            self.java_major = java_version.get("majorVersion", 0)

        # 日志配置（log4j 等）
        logging_cfg = data.get("logging", {}).get("client")
        self.logging_file = None
        self.logging_argument = ""
        if logging_cfg:
            file_info = logging_cfg.get("file") or {}
            self.logging_argument = logging_cfg.get("argument", "")
            self.logging_file = {
                "id": file_info.get("id", ""),
                "url": file_info.get("url", ""),
                "sha1": file_info.get("sha1", ""),
                "size": file_info.get("size", 0),
            }

        # 兼容旧版参数（1.7 之前使用 minecraftArguments）
        self.minecraft_arguments = data.get("minecraftArguments", "")
        arguments = data.get("arguments") or {}
        self.game_arguments = arguments.get("game") or []
        self.jvm_arguments = arguments.get("jvm") or []

        self.libraries = data.get("libraries", [])

    @property
    def is_modern(self) -> bool:
        """新版（1.13+）使用 arguments 结构，旧版用 minecraftArguments。"""
        return bool(self.game_arguments)


class VersionManager:
    """负责版本清单拉取、版本安装。"""

    def __init__(self, minecraft_dir: str, downloader: Downloader):
        self.minecraft_dir = minecraft_dir
        self.dl = downloader
        self.manifest = None
        self.manifest_cache_path = os.path.join(minecraft_dir, "version_manifest_v2.json")

    # ---------------- 清单 ----------------
    def fetch_manifest(self, force: bool = False):
        """拉取版本清单（带本地缓存）。"""
        if self.manifest is not None and not force:
            return self.manifest
        data = None
        # 缓存存在且 30 分钟内则直接用
        if not force and os.path.exists(self.manifest_cache_path):
            try:
                age = time.time() - os.path.getmtime(self.manifest_cache_path)
                if age < 30 * 60:
                    with open(self.manifest_cache_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
            except Exception:
                data = None
        if data is None:
            data = self.dl.get_json(VERSION_MANIFEST_URL)
            os.makedirs(self.minecraft_dir, exist_ok=True)
            with open(self.manifest_cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        self.manifest = data
        return self.manifest

    def list_versions(self, include_snapshot=True):
        """返回版本信息列表（按 releaseTime 倒序）。"""
        manifest = self.fetch_manifest()
        versions = [VersionInfo(v) for v in manifest.get("versions", [])]
        if not include_snapshot:
            versions = [v for v in versions if v.type == "release"]
        versions.sort(key=lambda v: v.release_time, reverse=True)
        return versions

    def get_version_info(self, version_id):
        manifest = self.fetch_manifest()
        for v in manifest.get("versions", []):
            if v["id"] == version_id:
                return VersionInfo(v)
        return None

    # ---------------- 版本 JSON ----------------
    def version_json_path(self, version_id):
        return os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.json")

    def get_version_json(self, version_id, force=False):
        """获取并缓存某版本的 version.json，返回 VersionJSON。"""
        path = self.version_json_path(version_id)
        if not force and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return VersionJSON(data)
        info = self.get_version_info(version_id)
        if not info:
            raise RuntimeError(f"未在版本清单中找到版本 {version_id}")
        data = None
        # BMCLAPI 版本 JSON 走 /version/<id>/json 专用路径
        if self.dl.mirror == "bmclapi" and self.dl:
            try:
                data = self.dl.get_json(
                    f"https://bmclapi2.bangbang93.com/version/{version_id}/json")
            except Exception:
                data = None
        if data is None:
            data = self.dl.get_json(info.url)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return VersionJSON(data)

    # ---------------- 下载安装 ----------------
    def install_client(self, version_id, progress_cb=None):
        """下载 client.jar。返回路径。"""
        vj = self.get_version_json(version_id)
        client_jar = os.path.join(self.minecraft_dir, "versions", version_id, f"{version_id}.jar")
        if os.path.exists(client_jar) and self._file_ok(client_jar, vj.client.get("sha1")):
            return client_jar
        if progress_cb:
            progress_cb(f"正在下载客户端 {version_id}.jar ...")
        url = vj.client["url"]
        # BMCLAPI 客户端走 /version/<id>/client
        if self.dl.mirror == "bmclapi" and ("piston-data.mojang.com" in url or "launcher.mojang.com" in url):
            url = f"https://bmclapi2.bangbang93.com/version/{version_id}/client"
        result = self.dl.download(url, client_jar, vj.client.get("sha1"))
        if not result.success:
            raise RuntimeError(f"下载客户端失败: {result.error}")
        return client_jar

    def download_assets_index(self, version_id):
        """下载资源索引文件，返回索引 JSON 字典。"""
        vj = self.get_version_json(version_id)
        idx_dir = os.path.join(self.minecraft_dir, "assets", "indexes")
        os.makedirs(idx_dir, exist_ok=True)
        idx_path = os.path.join(idx_dir, f"{vj.asset_index['id']}.json")
        if os.path.exists(idx_path) and self._file_ok(idx_path, vj.asset_index.get("sha1")):
            with open(idx_path, "r", encoding="utf-8") as f:
                return json.load(f)
        result = self.dl.download(vj.asset_index["url"], idx_path, vj.asset_index.get("sha1"))
        if not result.success:
            raise RuntimeError(f"下载资源索引失败: {result.error}")
        with open(idx_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def download_assets(self, version_id, progress_cb=None):
        """下载全部资源对象（objects）。返回 (下载数, 总数)。"""
        vj = self.get_version_json(version_id)
        index = self.download_assets_index(version_id)
        objects = index.get("objects", {})
        obj_dir = os.path.join(self.minecraft_dir, "assets", "objects")

        tasks = []
        for name, obj in objects.items():
            sha1 = obj.get("hash", "")
            size = obj.get("size", 0)
            dest = os.path.join(obj_dir, sha1[:2], sha1)
            if os.path.exists(dest) and self._file_ok(dest, sha1):
                continue
            url = f"{ASSET_BASE}{sha1[:2]}/{sha1}"
            tasks.append((url, dest, sha1, size))

        if not tasks:
            return 0, len(objects)
        if progress_cb:
            progress_cb(f"正在下载资源文件 {len(objects)} 个 ...")
        self.dl.download_many(tasks, total=len(objects))
        # 汇总失败项
        failed = [t for t in tasks if not os.path.exists(t[1]) or not self._file_ok(t[1], t[2])]
        if failed:
            raise RuntimeError(f"有 {len(failed)} 个资源文件下载失败")
        return len(tasks), len(objects)

    @staticmethod
    def _file_ok(path, sha1):
        if not sha1:
            return os.path.exists(path) and os.path.getsize(path) > 0
        if not os.path.exists(path):
            return False
        try:
            import hashlib
            h = hashlib.sha1()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest().lower() == sha1.lower()
        except Exception:
            return False

    def install_all(self, version_id, progress_cb=None):
        """完整安装：客户端 jar + 资源。库文件由 LibraryManager 处理。"""
        if progress_cb:
            progress_cb(f"开始安装版本 {version_id}")
        self.install_client(version_id, progress_cb)
        self.download_assets(version_id, progress_cb)
        if progress_cb:
            progress_cb(f"版本 {version_id} 安装完成")
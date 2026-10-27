"""Mod 加载器（Fabric）安装：查询版本、合成 version.json。"""

import os
import json

from .network.downloader import Downloader

# Fabric meta
FABRIC_LOADER_LIST_URL = "https://meta.fabricmc.net/v2/versions/loader/{mc}"
FABRIC_PROFILE_URL = "https://meta.fabricmc.net/v2/versions/loader/{mc}/{loader}/profile/json"


class ModLoaderError(Exception):
    pass


class FabricManager:
    """负责 Fabric 加载器安装。"""

    def __init__(self, minecraft_dir: str, downloader: Downloader):
        self.minecraft_dir = minecraft_dir
        self.dl = downloader
        self.versions_dir = os.path.join(minecraft_dir, "versions")

    def version_exists(self, version_id):
        return os.path.exists(os.path.join(self.versions_dir, version_id, f"{version_id}.json"))

    def scan_local_versions(self):
        """扫描 versions/ 目录中的版本 json，返回 [(id, type)]，用于合并进列表。"""
        result = []
        if not os.path.isdir(self.versions_dir):
            return result
        for folder in os.listdir(self.versions_dir):
            json_path = os.path.join(self.versions_dir, folder, f"{folder}.json")
            if not os.path.isfile(json_path):
                continue
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            vid = data.get("id") or folder
            vtype = data.get("type", "release")
            result.append((vid, vtype))
        return result

    # ---------------- Fabric ----------------
    def list_fabric(self, mc_version):
        """返回该原版版本可用的 Fabric loader 版本列表（稳定优先）。"""
        data = self.dl.get_json(FABRIC_LOADER_LIST_URL.format(mc=mc_version))
        result = []
        for item in data:
            loader = item.get("loader", {})
            v = loader.get("version")
            if v:
                result.append({"name": v, "stable": bool(loader.get("stable"))})
        return result

    def install_fabric(self, mc_version, loader_version, progress_cb=None):
        """安装 Fabric。合成自包含 version.json（继承原版 client/assets/javaVersion）。"""
        version_id = f"fabric-loader-{loader_version}-{mc_version}"
        dest_dir = os.path.join(self.versions_dir, version_id)
        json_path = os.path.join(dest_dir, f"{version_id}.json")
        if os.path.exists(json_path):
            return version_id

        if progress_cb:
            progress_cb(f"正在获取 Fabric profile ({loader_version}) ...")
        profile = self.dl.get_json(
            FABRIC_PROFILE_URL.format(mc=mc_version, loader=loader_version))
        if not profile:
            raise ModLoaderError("Fabric 官方未返回该版本配置")

        # 合并原版父版本：补齐 client / 资源索引 / Java 版本 / 父库
        parent_id = profile.get("inheritsFrom", mc_version)
        parent = self._fetch_parent_json(parent_id)
        if parent:
            for key in ("downloads", "assetIndex", "assets", "javaVersion",
                        "minimumLauncherVersion", "complianceLevel"):
                if key not in profile and key in parent:
                    profile[key] = parent[key]
            have = {lib.get("name") for lib in profile.get("libraries", [])}
            for lib in parent.get("libraries", []):
                if lib.get("name") not in have:
                    profile.setdefault("libraries", []).append(lib)

        profile["id"] = version_id
        os.makedirs(dest_dir, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        return version_id

    def _fetch_parent_json(self, parent_id):
        """获取原版父版本 json。返回 dict 或 None。"""
        from .version import VersionManager
        vm = VersionManager(self.minecraft_dir, self.dl)
        try:
            info = vm.get_version_info(parent_id)
            if not info:
                return None
            return self.dl.get_json(info.url)
        except Exception:
            return None

    # 兼容旧命名
    def list_forge(self, *args, **kwargs):
        return []

    def install_forge(self, *args, **kwargs):
        return ""
"""游戏启动核心：构建 JVM/游戏参数、启动进程、日志处理。"""

import os
import subprocess
import threading

from .libraries import LibraryManager, Rule
from .java import JavaManager
from .auth import offline_uuid
from .version import VersionManager
from . import sysinfo


class LaunchError(Exception):
    pass


class GameLauncher:
    """负责把版本、账户、配置组合为启动命令并启动游戏进程。"""

    def __init__(self, minecraft_dir, config, downloader):
        self.minecraft_dir = minecraft_dir
        self.config = config
        self.dl = downloader
        self.vm = VersionManager(minecraft_dir, downloader)
        self.lib_mgr = LibraryManager(minecraft_dir, downloader)
        self.java_mgr = JavaManager(minecraft_dir, downloader)

    # ---------------- 参数构建 ----------------
    @staticmethod
    def _substitute(text, mapping):
        for key, val in mapping.items():
            text = text.replace("${" + key + "}", str(val))
        # 剩余的未定义占位符置空
        import re
        text = re.sub(r"\$\{[^}]+\}", "", text)
        return text

    def build_command(self, version_j, profile, java_exe, progress_cb=None):
        """构建完整启动命令列表。
        profile: {'name','uuid','access_token'} 或离线模式生成的 profile
        """
        config = self.config
        mc_dir = self.minecraft_dir
        version_id = version_j.id

        # ---------- 库 & 客户端 jar ----------
        if progress_cb:
            progress_cb("正在检查库文件 ...")
        lib_classpath = self.lib_mgr.ensure_libraries(version_j, progress_cb)
        client_jar = os.path.join(mc_dir, "versions", version_id, f"{version_id}.jar")
        if not os.path.exists(client_jar):
            raise LaunchError(f"缺少客户端文件 {client_jar}")

        # ---------- natives 目录 ----------
        natives_dir = os.path.join(mc_dir, "versions", version_id, f"{version_id}-natives")
        if progress_cb:
            progress_cb("正在解压原生库 ...")
        self.lib_mgr.extract_natives(version_id, version_j, progress_cb)
        os.makedirs(natives_dir, exist_ok=True)

        # ---------- 资源 ----------
        assets_root = os.path.join(mc_dir, "assets")

        # ---------- 日志配置 ----------
        log_config_path = None
        if version_j.logging_file:
            log_config_path = os.path.join(
                mc_dir, "assets", "log_configs", version_j.logging_file["sha1"]
            )
            if not os.path.exists(log_config_path):
                if progress_cb:
                    progress_cb("正在下载日志配置文件 ...")
                r = self.dl.download(version_j.logging_file["url"], log_config_path,
                                     version_j.logging_file.get("sha1"))
                if not r.success:
                    log_config_path = None

        # ---------- 通用变量 ----------
        launcher_name = config["launcher_name"]
        launcher_version = config["launcher_version"]
        uuid_str = profile["uuid"] or offline_uuid(profile["name"])
        access_token = profile.get("access_token", "0")
        user_type = "msa" if profile.get("msa", False) else "legacy"

        mapping = {
            "natives_directory": natives_dir,
            "launcher_name": launcher_name,
            "launcher_version": launcher_version,
            "classpath": os.pathsep.join([client_jar] + lib_classpath),
            "classpath_separator": os.pathsep,
            "library_directory": self.lib_mgr.libraries_root,
            "assets_root": assets_root,
            "assets_index_name": version_j.assets_id,
            "auth_player_name": profile["name"],
            "auth_uuid": uuid_str,
            "auth_access_token": access_token,
            "auth_xuid": profile.get("xuid", ""),
            "clientid": CLIENT_ID_LITERAL,
            "user_type": user_type,
            "version_name": version_id,
            "version_type": version_j.data.get("type", "release"),
            "game_directory": mc_dir,
            "resolution_width": str(config.get("res_width", 854)),
            "resolution_height": str(config.get("res_height", 480)),
            # 老版本（1.6 及以前）用的占位符
            "game_assets": assets_root,
            "auth_session": "token:%s:%s" % (access_token, uuid_str),
            "user_properties": "{}",
            "profile_name": launcher_name,
        }
        # ---------- JVM 参数 ----------
        jvm_args = self._build_jvm_args(version_j, mapping, log_config_path, config)
        # ---------- 游戏参数 ----------
        game_args = self._build_game_args(version_j, mapping, config)

        cmd = [java_exe]
        cmd += jvm_args
        cmd.append(version_j.main_class)
        cmd += game_args
        return cmd

    def _active_features(self, config):
        """根据配置返回启用的 features（用于 rules 判断）。"""
        features = {}
        # 只有用户显式打开“自定义窗口大小”时才启用，否则让游戏用默认分辨率
        if config.get("custom_resolution"):
            features["has_custom_resolution"] = True
        return features

    def _build_jvm_args(self, version_j, mapping, log_config_path, config):
        args = []
        # 内存：32 位系统单进程上限约 2 GB，堆内存要压住
        max_ram = int(config["max_ram"])
        if not sysinfo.is_64bit_os():
            max_ram = min(max_ram, 1024)
        args.append("-Xmx%dM" % max_ram)
        if config.get("min_ram"):
            min_ram = min(int(config["min_ram"]), max_ram)
            args.append("-Xms%dM" % min_ram)
        # 用户自定义 JVM 参数
        if config.get("jvm_args"):
            args += config["jvm_args"].split()
        features = self._active_features(config)

        if version_j.is_modern:
            for item in version_j.jvm_arguments:
                if isinstance(item, str):
                    args.append(self._substitute(item, mapping))
                elif isinstance(item, dict):
                    if Rule.passes(item.get("rules"), features):
                        values = item.get("value")
                        if isinstance(values, str):
                            values = [values]
                        for v in values:
                            args.append(self._substitute(v, mapping))
        else:
            # 旧版默认 JVM 参数
            args += ["-Djava.library.path=" + mapping["natives_directory"],
                     "-cp", mapping["classpath"]]

        # 日志参数
        if version_j.logging_argument and log_config_path:
            args.append(self._substitute(version_j.logging_argument,
                                         {"path": log_config_path}))

        return args

    @staticmethod
    def _legacy_args(template, mapping):
        """把旧版 minecraftArguments 模板转成参数列表。

        先按空格切分模板再逐项替换，这样替换值里带空格（比如游戏目录在
        "Program Files" 下）也不会被拆成两个参数。
        """
        args = []
        for token in template.split():
            value = GameLauncher._substitute(token, mapping)
            # 空值只在它不是某个选项的值时才丢（--assetsDir 后面必须留位置）
            if value == "" and (not args or not args[-1].startswith("--")):
                continue
            args.append(value)
        return args

    def _build_game_args(self, version_j, mapping, config):
        args = []
        features = self._active_features(config)
        if version_j.is_modern:
            for item in version_j.game_arguments:
                if isinstance(item, str):
                    args.append(self._substitute(item, mapping))
                elif isinstance(item, dict):
                    if Rule.passes(item.get("rules"), features):
                        values = item.get("value")
                        if isinstance(values, str):
                            values = [values]
                        for v in values:
                            args.append(self._substitute(v, mapping))
        else:
            # 旧版用 minecraftArguments 模板
            template = version_j.minecraft_arguments or \
                "--username ${auth_player_name} --version ${version_name} " \
                "--gameDir ${game_directory} --assetsDir ${assets_root} " \
                "--assetIndex ${assets_index_name} --uuid ${auth_uuid} " \
                "--accessToken ${auth_access_token} --userType ${user_type} " \
                "--versionType ${version_type}"
            args += self._legacy_args(template, mapping)

        # 用户自定义游戏参数
        if config.get("game_args"):
            args += config["game_args"].split()
        if config.get("fullscreen"):
            args.append("--fullscreen")
        return args

    # ---------------- 进程管理 ----------------
    def install_and_launch(self, version_id, profile, log=lambda m: None):
        """安装依赖并启动游戏，供 GUI 调用。"""
        # 1. 完整安装（客户端 jar + 资源）
        self.vm.install_all(version_id, progress_cb=log)
        vj = self.vm.get_version_json(version_id)

        # 2. Java
        java_exe = self.java_mgr.resolve(vj, self.config, progress_cb=log)
        log(f"使用 Java: {java_exe}")

        # 3. 构建命令（内部会校验并下载库、解压 natives）
        cmd = self.build_command(vj, profile, java_exe, progress_cb=log)

        # 4. 启动
        return self.launch(cmd, log_cb=log)

    def launch(self, command, log_cb=None, exit_cb=None):
        """以 .minecraft 为工作目录启动游戏。返回 Popen。"""
        os.makedirs(self.minecraft_dir, exist_ok=True)
        flags = 0
        if os.name == "nt":
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._proc = subprocess.Popen(
            command,
            cwd=self.minecraft_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # 日志读取线程
        def pump():
            try:
                for line in self._proc.stdout:
                    if log_cb:
                        log_cb(line.rstrip("\r\n"))
            except Exception:
                pass
            finally:
                if exit_cb:
                    exit_cb(self._proc.poll())

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        return self._proc


# 微软启动器 client id 常量（用于 --clientId 参数）
CLIENT_ID_LITERAL = "00000000402b5328"
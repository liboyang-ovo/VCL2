"""配置管理：负责启动器的本地配置读写。"""

import json
import os
import shutil

# 默认离线玩家名
DEFAULT_USERNAME = "玩家1001"


class LauncherConfig:
    """管理启动器配置文件 .launcher.json。"""

    DEFAULTS = {
        "game_dir": "",        # .minecraft 目录（空则用默认路径）
        "auth": "offline",     # "offline" | "microsoft"
        "username": DEFAULT_USERNAME,  # 离线模式显示名
        "java_path": "",       # 自定义 Java 路径（空则自动管理/检测）
        "auto_java": True,     # 自动下载 Java
        "max_ram": 2048,       # 最大内存 (MB)
        "min_ram": 512,        # 最小内存 (MB)
        "version": "",         # 上次选择的版本 ID
        "fullscreen": False,
        "custom_resolution": False,  # 是否使用下面的自定义窗口大小
        "res_width": 854,      # 窗口宽（配合 has_custom_resolution 特性）
        "res_height": 480,     # 窗口高
        "jvm_args": "",        # 额外的 JVM 参数
        "game_args": "",       # 额外的游戏参数
        "launcher_name": "vcl2",
        "launcher_version": "1.0",
        "mirror": "official",    # "official" | "bmclapi"
        "insecure_ssl": False,   # 关闭 HTTPS 证书校验（仅用于证书异常的网络）
        "theme": "dark",         # "dark" | "light"
        "remote_config_url": "https://vcl2.lbyovo.cn/config.json",  # 远程配置地址
        "remote_config_enabled": True,  # 是否启动时拉取远程配置
    }

    def __init__(self, path):
        self.path = path
        self.data = dict(self.DEFAULTS)
        self.load()

    @property
    def minecraft_dir(self) -> str:
        if self.data["game_dir"]:
            return self.data["game_dir"]
        return self._default_game_dir()

    def get(self, key, default=None):
        return self.data.get(key, default)

    @staticmethod
    def _default_game_dir() -> str:
        """默认游戏目录：启动器同级的 .minecraft（便携模式）。

        目录不可写时（比如装在 Program Files）退回用户 APPDATA 下的 .minecraft。
        """
        from . import sysinfo
        portable = os.path.join(sysinfo.app_dir(), ".minecraft")
        try:
            os.makedirs(portable, exist_ok=True)
            return portable
        except OSError:
            return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                                ".minecraft")

    def load(self):
        try:
            # utf-8-sig：兼容被记事本/编辑器加了 BOM 的配置文件
            with open(self.path, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            self.data.update({k: v for k, v in loaded.items() if k in self.DEFAULTS})
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # 旧版本的远程地址（http / www 域名）升级成现在的 https 地址
        if migrate_legacy_remote_url(self.data):
            try:
                self.save()
            except OSError:
                pass

    def save(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def __getitem__(self, key):
        return self.data[key]
    def __setitem__(self, key, value):
        if key in self.DEFAULTS:
            self.data[key] = value


def _env_home():
    """显式指定的配置根目录：VCL2_HOME，兼容旧的 QCL_HOME / TIANFUN_HOME。"""
    for key in ("VCL2_HOME", "QCL_HOME", "TIANFUN_HOME"):
        value = os.environ.get(key)
        if value:
            return value
    return ""


CURRENT_REMOTE_URL = "https://vcl2.lbyovo.cn/config.json"

LEGACY_REMOTE_URLS = (
    "http://www.lbyovo.cn/config.json",
    "http://lbyovo.cn/config.json",
    "http://vcl2.lbyovo.cn/config.json",
    "https://www.lbyovo.cn/config.json",
)


def migrate_legacy_remote_url(data):
    """把老的远程配置地址换成现在的 https 地址，返回是否改动过。"""
    url = (data.get("remote_config_url") or "").strip()
    if not url:
        return False
    if url == CURRENT_REMOTE_URL:
        return False
    if url in LEGACY_REMOTE_URLS or ("lbyovo.cn" in url and not url.startswith(CURRENT_REMOTE_URL)):
        data["remote_config_url"] = CURRENT_REMOTE_URL
        return True
    return False


def home_dir():
    """配置根目录：优先 VCL2_HOME（兼容旧的 QCL_HOME / TIANFUN_HOME）。"""
    return (_env_home() or os.path.expanduser("~"))


def _try_config_dir(base):
    """确保目录可写，返回配置文件路径；不可写返回 None。"""
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, ".write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return os.path.join(base, "launcher.json")
    except OSError:
        return None


def default_config_path():
    """返回配置文件路径：**便携优先**。

    查找顺序：
      1. 环境变量 VCL2_HOME（显式指定，测试/多实例用）
      2. 程序目录下的 .vcl2（绿色版，配置跟程序走）
      3. 用户主目录下的 .vcl2（程序目录不可写时，比如装在 Program Files）
      4. 当前工作目录下的 .vcl2（最后兜底）

    从主目录迁移到程序目录时，会**自动把旧的 launcher.json 拷过来**，不用重新配置。
    """
    env_home = _env_home()
    if env_home:
        path = _try_config_dir(os.path.join(env_home, ".vcl2"))
        if path:
            return path

    from . import sysinfo
    portable = os.path.join(sysinfo.app_dir(), ".vcl2")
    home_cfg = os.path.join(os.path.expanduser("~"), ".vcl2")

    path = _try_config_dir(portable)
    if path:
        old = os.path.join(home_cfg, "launcher.json")
        if not os.path.exists(path) and os.path.exists(old):
            try:
                shutil.copy2(old, path)
            except OSError:
                pass
        return path

    path = _try_config_dir(home_cfg)
    if path:
        return path

    path = _try_config_dir(os.path.join(os.getcwd(), ".vcl2"))
    if path:
        return path

    raise RuntimeError("找不到可写的配置目录（程序目录、用户目录、当前目录都不可写）")

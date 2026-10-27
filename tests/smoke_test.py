#!/usr/bin/env python3
"""离线冒烟测试：不联网，验证参数构建、规则判断、配置读写等核心逻辑。

运行：
    python tests/smoke_test.py
"""

import json
import os
import shutil
import sys
import tempfile

# 允许从仓库根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from minecraft_launcher.config import LauncherConfig                 # noqa: E402
from minecraft_launcher.libraries import Rule, _matches_os           # noqa: E402
from minecraft_launcher.version import VersionJSON                   # noqa: E402
from minecraft_launcher.game_launcher import GameLauncher            # noqa: E402
from minecraft_launcher.auth import offline_uuid                     # noqa: E402
from minecraft_launcher.remote_config import normalize, RemoteConfig, _version_key  # noqa: E402
from minecraft_launcher.network.downloader import Downloader  # noqa: E402
import minecraft_launcher.config as cfgmod                                # noqa: E402
import minecraft_launcher.sysinfo as sysinfo_mod                         # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  ok   %s" % name)
    else:
        FAIL.append("%s %s" % (name, detail))
        print("  FAIL %s %s" % (name, detail))


class FakeDownloader:
    mirror = "official"

    def download(self, *a, **k):
        raise AssertionError("离线测试不应该发起下载")


class FakeJava:
    def resolve(self, *a, **k):
        return "java"


class FakeLibManager:
    def __init__(self, mc_dir):
        self.minecraft_dir = mc_dir
        self.libraries_root = os.path.join(mc_dir, "libraries")

    def ensure_libraries(self, version_j, progress_cb=None):
        return []

    def extract_natives(self, version_id, version_j, progress_cb=None):
        return None


VERSION_JSON = {
    "id": "1.21.1",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "assets": "17",
    "assetIndex": {"id": "17", "url": "http://x/17.json", "sha1": "a", "size": 1},
    "downloads": {"client": {"url": "http://x/c.jar", "sha1": "b", "size": 1}},
    "javaVersion": {"component": "java-runtime-delta", "majorVersion": 21},
    "arguments": {
        "game": [
            "--username", "${auth_player_name}",
            "--version", "${version_name}",
            "--gameDir", "${game_directory}",
            "--assetsDir", "${assets_root}",
            "--assetIndex", "${assets_index_name}",
            "--uuid", "${auth_uuid}",
            "--accessToken", "${auth_access_token}",
            {"rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
             "value": ["--width", "${resolution_width}", "--height", "${resolution_height}"]},
        ],
        "jvm": [
            "-Djava.library.path=${natives_directory}",
            "-Djna.tmpdir=${natives_directory}",
            "-cp", "${classpath}",
            "-DlibraryDirectory=${library_directory}",
        ],
    },
    "libraries": [],
}


def test_rules():
    print("[规则判断]")
    check("windows allow", Rule.passes([{"action": "allow", "os": {"name": "windows"}}]) ==
          (_matches_os({"name": "windows"})))
    check("空规则 = 允许", Rule.passes(None) is True)
    check("osx 规则在 windows 上不匹配",
          Rule.passes([{"action": "allow", "os": {"name": "osx"}}]) is False)
    check("features 未启用时排除",
          Rule.passes([{"action": "allow", "features": {"has_custom_resolution": True}}],
                      {}) is False)
    check("features 启用时包含",
          Rule.passes([{"action": "allow", "features": {"has_custom_resolution": True}}],
                      {"has_custom_resolution": True}) is True)


def test_offline_uuid():
    print("[离线 UUID]")
    u1 = offline_uuid("Steve")
    u2 = offline_uuid("Steve")
    check("稳定", u1 == u2, u1)
    check("格式", len(u1) == 36 and u1.count("-") == 4, u1)
    check("不同名字不同 UUID", offline_uuid("Alex") != u1)


def test_config(tmp):
    print("[配置读写]")
    path = os.path.join(tmp, "launcher.json")
    cfg = LauncherConfig(path)
    check("默认内存", cfg["max_ram"] == 2048, cfg["max_ram"])
    cfg["max_ram"] = 4096
    cfg.save()
    cfg2 = LauncherConfig(path)
    check("持久化", cfg2["max_ram"] == 4096, cfg2["max_ram"])
    cfg2["unknown_key"] = 1
    check("未知键被忽略", "unknown_key" not in cfg2.data)


def test_build_command(tmp):
    print("[启动参数构建]")
    mc_dir = os.path.join(tmp, "mc")
    os.makedirs(os.path.join(mc_dir, "versions", "1.21.1"), exist_ok=True)
    with open(os.path.join(mc_dir, "versions", "1.21.1", "1.21.1.jar"), "wb") as f:
        f.write(b"fake")

    cfg = LauncherConfig(os.path.join(tmp, "c.json"))
    cfg["custom_resolution"] = False
    launcher = GameLauncher(mc_dir, cfg, FakeDownloader())
    launcher.lib_mgr = FakeLibManager(mc_dir)
    launcher.java_mgr = FakeJava()
    vj = VersionJSON(VERSION_JSON)
    profile = {"name": "Steve", "uuid": offline_uuid("Steve"),
               "access_token": "0", "msa": False}

    cmd = launcher.build_command(vj, profile, "java.exe")
    joined = " ".join(cmd)
    check("主类存在", "net.minecraft.client.main.Main" in cmd, joined)
    check("classpath 含客户端 jar", "1.21.1.jar" in joined)
    check("玩家名替换", "Steve" in joined)
    check("library_directory 替换", mc_dir.replace("\\", "\\") in joined or "libraries" in joined)
    check("未定义占位符被清空", "${" not in joined, joined)
    check("未开自定义分辨率时不加 --width", "--width" not in cmd, joined)

    cfg["custom_resolution"] = True
    cmd2 = launcher.build_command(vj, profile, "java.exe")
    check("开启后加 --width", "--width" in cmd2, " ".join(cmd2))
    check("分辨率值正确", "854" in cmd2 and "480" in cmd2, " ".join(cmd2))


def test_remote_config():
    print("[远程配置]")
    data = normalize({
        "schema": 1,
        "announcement": "  公告内容  ",
        "announcement_level": "bogus",
        "recommended_version": "1.21.1",
        "latest": {"version": "1.2", "url": "https://example.com/dl"},
        "mirrors": ["official", "bmclapi", "", 123],
        "evil_field": "should be dropped",
    })
    check("公告去空白", data["announcement"] == "公告内容", data["announcement"])
    check("非法等级回落 info", data["announcement_level"] == "info")
    check("推荐版本", data["recommended_version"] == "1.21.1")
    check("latest 保留", data.get("latest", {}).get("version") == "1.2")
    check("未知字段丢弃", "evil_field" not in data)
    check("mirrors 过滤非字符串", data["mirrors"] == ["official", "bmclapi"], data["mirrors"])

    bad = normalize({"latest": {"version": "9", "url": "javascript:alert(1)"}})
    check("拒绝非 http 的更新地址", "latest" not in bad)

    check("版本比较 1.2 > 1.1", _version_key("1.2") > _version_key("1.1"))
    check("版本比较 1.10 > 1.9", _version_key("1.10") > _version_key("1.9"))

    rc = RemoteConfig("", None)
    check("空地址不报错但不发请求", rc.fetch() == {} and rc.error)


def test_portable_config(tmp):
    print("[便携配置]")
    fake_app = os.path.join(tmp, "app")
    fake_home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(fake_home, ".vcl2"), exist_ok=True)
    os.makedirs(fake_app, exist_ok=True)
    # 老配置放在“用户目录”
    old = os.path.join(fake_home, ".vcl2", "launcher.json")
    with open(old, "w", encoding="utf-8") as f:
        json.dump({"username": "OldUser", "max_ram": 1234}, f)

    saved = (sysinfo_mod.app_dir, os.path.expanduser, os.environ.get("VCL2_HOME"))
    try:
        sysinfo_mod.app_dir = lambda: fake_app
        os.path.expanduser = lambda p: fake_home if p == "~" else p
        os.environ.pop("VCL2_HOME", None)
        os.environ.pop("TIANFUN_HOME", None)
        path = cfgmod.default_config_path()
        check("配置落在程序目录", path.startswith(fake_app), path)
        check("老配置被迁移", os.path.exists(path))
        migrated = json.load(open(path, encoding="utf-8"))
        check("迁移内容正确", migrated.get("username") == "OldUser", migrated)
    finally:
        sysinfo_mod.app_dir, os.path.expanduser, old_env = saved
        if old_env is not None:
            os.environ["VCL2_HOME"] = old_env


def test_remote_url_migration():
    print("[远程地址迁移]")
    from minecraft_launcher.config import migrate_legacy_remote_url, CURRENT_REMOTE_URL
    data = {"remote_config_url": "http://www.lbyovo.cn/config.json"}
    changed = migrate_legacy_remote_url(data)
    check("旧 http 地址升级为 https", changed and data["remote_config_url"] == CURRENT_REMOTE_URL,
          data)
    data2 = {"remote_config_url": "https://example.com/config.json"}
    check("自定义地址不受影响", not migrate_legacy_remote_url(data2), data2)
    data3 = {"remote_config_url": CURRENT_REMOTE_URL}
    check("当前地址不重复处理", not migrate_legacy_remote_url(data3))


def test_download_dedupe():
    print("[下载去重]")
    dl = Downloader(threads=4, mirror="official")
    tasks = [
        ("http://a/x.jar", "libs/x.jar", "sha", 1),
        ("http://a/x.jar", "libs/x.jar", "sha", 1),   # 老版本 JSON 里的重复 natives
        ("http://a/y.jar", "libs/y.jar", "sha", 2),
    ]
    deduped = dl._dedupe(tasks)
    check("重复目标只保留一个", len(deduped) == 2, len(deduped))
    check("保留顺序不变", [t[1] for t in deduped] == ["libs/x.jar", "libs/y.jar"])


def test_legacy_args():
    print("[老版本启动参数]")
    mapping = {
        "auth_player_name": "Steve",
        "auth_session": "token:0:uuid",
        "game_directory": "C:/mc dir",
        "game_assets": "C:/mc dir/assets",
    }
    template = "${auth_player_name} ${auth_session} --gameDir ${game_directory} --assetsDir ${game_assets}"
    args = GameLauncher._legacy_args(template, mapping)
    check("会话参数正确", args[1] == "token:0:uuid", args)
    check("assetsDir 带值", args[args.index("--assetsDir") + 1] == "C:/mc dir/assets", args)
    check("带空格的路径没被拆开", "C:/mc dir" in args, args)
    check("没有空参数", "" not in args, args)


def main():
    # 放在仓库根目录下；用 makedirs 而不是 mkdtemp（后者会设成 0700，
    # 在某些受限沙箱里会导致后续写入被拒绝）
    tmp = os.path.join(os.getcwd(), ".tianfun-test-tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        test_rules()
        test_offline_uuid()
        test_config(tmp)
        test_build_command(tmp)
        test_remote_config()
        test_download_dedupe()
        test_remote_url_migration()
        test_legacy_args()
        test_portable_config(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())

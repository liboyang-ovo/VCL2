"""纯命令行模式（无 GUI 依赖，可在服务器 / SSH / Win7 上跑）。

用法示例：
    python main.py list
    python main.py list --filter release --limit 10
    python main.py install 1.21.1
    python main.py launch 1.21.1 --name Steve --ram 4096
    python main.py fabric list 1.21.1
    python main.py fabric install 1.21.1
    python main.py mod search 小地图
    python main.py mod install 1.21.1 小地图
    python main.py java check
    python main.py config show
    python main.py config set max_ram 4096
"""

import argparse
import json
import os
import sys

from . import constants
from .config import LauncherConfig, default_config_path
from .version import VersionManager
from .java import JavaManager
from .libraries import LibraryManager
from .auth import AuthManager, offline_uuid
from .game_launcher import GameLauncher
from .network.downloader import Downloader
from .modloader import FabricManager
from . import sysinfo
from .remote_config import RemoteConfig


def build_context(args):
    """根据全局参数构造配置与各管理器。"""
    cfg = LauncherConfig(args.config or default_config_path())
    if getattr(args, "dir", None):
        cfg["game_dir"] = os.path.abspath(args.dir)
    if getattr(args, "mirror", None):
        cfg["mirror"] = args.mirror
    dl = Downloader(constants.DEFAULT_THREADS, mirror=cfg["mirror"])
    if cfg.get("insecure_ssl"):
        dl.set_ssl_verification(False)
    mc_dir = cfg.minecraft_dir
    ctx = {
        "cfg": cfg,
        "dl": dl,
        "dir": mc_dir,
        "vm": VersionManager(mc_dir, dl),
        "jm": JavaManager(mc_dir, dl),
        "lm": LibraryManager(mc_dir, dl),
        "fabric": FabricManager(mc_dir, dl),
        "launcher": GameLauncher(mc_dir, cfg, dl),
    }
    return ctx


def out(args, text):
    if not getattr(args, "quiet", False):
        print(text)


# ---------------------------------------------------------------- 子命令

def cmd_list(args, ctx):
    versions = ctx["vm"].list_versions(True)
    if args.filter != "all":
        if args.filter == "release":
            versions = [v for v in versions if v.type == "release"]
        elif args.filter == "snapshot":
            versions = [v for v in versions if v.type == "snapshot"]
        elif args.filter == "old":
            versions = [v for v in versions if v.type in ("old_beta", "old_alpha")]

    # 本地已安装
    installed = set()
    vdir = os.path.join(ctx["dir"], "versions")
    if os.path.isdir(vdir):
        installed = {d for d in os.listdir(vdir)
                     if os.path.isfile(os.path.join(vdir, d, d + ".json"))}

    if args.limit:
        versions = versions[: args.limit]

    if args.json:
        print(json.dumps([
            {"id": v.id, "type": v.type, "release_time": v.release_time,
             "installed": v.id in installed}
            for v in versions], ensure_ascii=False, indent=2))
        return 0

    for v in versions:
        mark = "*" if v.id in installed else " "
        print("%s %-16s %-10s %s" % (mark, v.id, v.type, v.release_time))
    print("\n共 %d 个版本（* = 已安装）" % len(versions))
    return 0


def cmd_install(args, ctx):
    version = args.version
    cfg = ctx["cfg"]
    log = (lambda m: None) if args.quiet else (lambda m: print(m, flush=True))

    vj = ctx["vm"].get_version_json(version)
    log("版本: %s  主类: %s" % (vj.id, vj.main_class or "?"))

    ctx["vm"].install_all(version, progress_cb=log)
    ctx["lm"].ensure_libraries(vj, progress_cb=log)
    ctx["lm"].extract_natives(version, vj, progress_cb=log)

    java_exe = ctx["jm"].resolve(vj, cfg, progress_cb=log)
    log("Java: %s" % java_exe)
    print("安装完成: %s" % version)
    return 0


def cmd_launch(args, ctx):
    version = args.version
    cfg = ctx["cfg"]
    if args.ram:
        cfg["max_ram"] = args.ram
    if args.min_ram is not None:
        cfg["min_ram"] = args.min_ram
    if args.name:
        cfg["username"] = args.name
    if args.jvm:
        cfg["jvm_args"] = args.jvm
    if args.game_args:
        cfg["game_args"] = args.game_args
    cfg["fullscreen"] = bool(args.fullscreen)
    if args.width and args.height:
        cfg["custom_resolution"] = True
        cfg["res_width"] = args.width
        cfg["res_height"] = args.height

    name = cfg["username"] or "玩家"
    profile = {"name": name, "uuid": offline_uuid(name),
               "access_token": "0", "msa": False}

    # 微软账户：使用已保存的登录
    if args.auth == "microsoft":
        token_path = os.path.join(os.path.dirname(ctx["cfg"].path), "microsoft_tokens.json")
        auth = AuthManager(token_path)
        if not auth.has_stored_login:
            print("错误：没有已保存的微软登录，请先用图形界面登录一次", file=sys.stderr)
            return 2
        profile = auth.login_with_stored()

    if not sysinfo.is_64bit_os():
        print("提示：32 位系统只能运行 1.16.5 及更早版本，最大内存限制 1024 MB",
              file=sys.stderr)

    log = (lambda m: None) if args.quiet else (lambda m: print(m, flush=True))
    proc = ctx["launcher"].install_and_launch(version, profile, log=log)
    print("游戏已启动 (PID %s)" % proc.pid)
    if args.detach:
        return 0
    # 阻塞直到游戏退出，并转发日志
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
        return proc.wait()
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


def cmd_fabric(args, ctx):
    fabric = ctx["fabric"]
    if args.fabric_cmd == "list":
        loaders = fabric.list_fabric(args.mc_version)
        if args.json:
            print(json.dumps(loaders, ensure_ascii=False, indent=2))
            return 0
        for item in loaders:
            print("%-24s %s" % (item["name"], "stable" if item["stable"] else ""))
        return 0
    vid = fabric.install_fabric(args.mc_version, args.loader,
                                progress_cb=lambda m: None if args.quiet else print(m))
    print("Fabric 安装完成: %s" % vid)
    return 0


def cmd_mod(args, ctx):
    from .mcmod import McModSearcher, ModrinthClient, ModInstaller

    searcher = McModSearcher()
    if args.mod_cmd == "search":
        items = searcher.search(args.keyword)
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        for i, it in enumerate(items, 1):
            print("%2d. %-50s %s" % (i, it["title"][:50], it["url"]))
        print("\n共 %d 条" % len(items))
        return 0

    # install
    version_id = args.version
    items = searcher.search(args.keyword)
    if not items:
        print("没有搜索到结果", file=sys.stderr)
        return 1
    item = items[0]
    print("使用结果: %s" % item["title"])
    slugs = searcher.get_modrinth_slugs(item["url"])
    if not slugs:
        print("该 Mod 在 Modrinth 上没有下载页", file=sys.stderr)
        return 1
    client = ModrinthClient()
    mc_ver = _mc_version_of(version_id)
    for slug in slugs:
        pick = client.pick(slug, mc_ver, "fabric")
        if pick:
            filename, url, _size, _vn = pick
            installer = ModInstaller(ctx["dir"])
            path = installer.install(version_id, filename, url)
            print("已安装: %s" % path)
            return 0
    print("未找到匹配 %s + Fabric 的下载文件" % mc_ver, file=sys.stderr)
    return 1


def cmd_java(args, ctx):
    jm = ctx["jm"]
    exe = jm.find_system_java()
    if not exe:
        print("未在 PATH / JAVA_HOME / 常见安装目录中找到 Java")
        return 1
    major = jm._java_version(exe)
    print("系统: %s" % sysinfo.arch_label())
    print("Java: %s" % exe)
    print("主版本: %s" % major)
    return 0


def cmd_remote(args, ctx):
    """拉取远程配置并打印（用于调试自己的配置服务器）。"""
    cfg = ctx["cfg"]
    url = args.url or (cfg.get("remote_config_url") or "").strip()
    if not url:
        print("未配置远程地址：用 --url 指定，或 config set remote_config_url <地址>",
              file=sys.stderr)
        return 2
    cache = os.path.join(os.path.dirname(cfg.path), "remote_config.json")
    rc = RemoteConfig(url, cache)
    data = rc.fetch()
    if rc.error and not data:
        print("获取失败：%s" % rc.error, file=sys.stderr)
        return 1
    if rc.error:
        print("提示：本次使用缓存（%s）" % rc.error, file=sys.stderr)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_config(args, ctx):
    cfg = ctx["cfg"]
    if args.config_cmd == "show":
        print(json.dumps(cfg.data, ensure_ascii=False, indent=2))
        print("# 配置文件: %s" % cfg.path)
        return 0
    if args.config_cmd == "get":
        print(json.dumps(cfg.get(args.key), ensure_ascii=False))
        return 0
    # set
    key, value = args.key, args.value
    if key not in cfg.DEFAULTS:
        print("未知配置项: %s（可用项: %s）" % (key, ", ".join(sorted(cfg.DEFAULTS))),
              file=sys.stderr)
        return 2
    default = cfg.DEFAULTS[key]
    try:
        if isinstance(default, bool):
            parsed = value.lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            parsed = int(value)
        else:
            parsed = value
    except ValueError:
        print("值不合法: %s" % value, file=sys.stderr)
        return 2
    cfg[key] = parsed
    cfg.save()
    print("%s = %s" % (key, json.dumps(parsed, ensure_ascii=False)))
    return 0


def _mc_version_of(version_id):
    if version_id.startswith("fabric-loader-"):
        rest = version_id[len("fabric-loader-"):]
        if "-" in rest:
            return rest.split("-", 1)[1]
        return rest
    return version_id


# ---------------------------------------------------------------- 入口

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", metavar="FILE", help="配置文件路径")
    common.add_argument("--dir", metavar="PATH", help=".minecraft 目录")
    common.add_argument("--mirror", choices=["official", "bmclapi"], help="下载源")
    common.add_argument("--quiet", "-q", action="store_true", help="减少输出")

    ap = argparse.ArgumentParser(
        prog="vcl2",
        description="VCL2 - Voxel Craft Launcher 2, command line mode",
        parents=[common])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", parents=[common], help="列出可用版本")
    p.add_argument("--filter", choices=["all", "release", "snapshot", "old"], default="all")
    p.add_argument("--limit", type=int, default=0, help="只显示前 N 个")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("install", parents=[common], help="下载安装指定版本")
    p.add_argument("version")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("launch", parents=[common], help="启动指定版本")
    p.add_argument("version")
    p.add_argument("--name", help="离线玩家名")
    p.add_argument("--ram", type=int, help="最大内存 MB")
    p.add_argument("--min-ram", type=int, dest="min_ram")
    p.add_argument("--jvm", help="额外 JVM 参数")
    p.add_argument("--game-args", dest="game_args")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--auth", choices=["offline", "microsoft"], default="offline")
    p.add_argument("--detach", action="store_true", help="启动后立即返回，不等游戏退出")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("fabric", parents=[common], help="Fabric 加载器")
    fsub = p.add_subparsers(dest="fabric_cmd", required=True)
    q = fsub.add_parser("list", parents=[common])
    q.add_argument("mc_version")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_fabric)
    q = fsub.add_parser("install", parents=[common])
    q.add_argument("mc_version")
    q.add_argument("--loader", required=True, help="Fabric loader 版本号")
    q.set_defaults(func=cmd_fabric)

    p = sub.add_parser("mod", parents=[common], help="Mod 搜索与安装")
    msub = p.add_subparsers(dest="mod_cmd", required=True)
    q = msub.add_parser("search", parents=[common])
    q.add_argument("keyword")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_mod)
    q = msub.add_parser("install", parents=[common])
    q.add_argument("version")
    q.add_argument("keyword")
    q.set_defaults(func=cmd_mod)

    p = sub.add_parser("remote", parents=[common], help="拉取远程配置")
    p.add_argument("--url", help="临时指定远程配置地址")
    p.set_defaults(func=cmd_remote)

    p = sub.add_parser("java", parents=[common], help="Java 检测")
    jsub = p.add_subparsers(dest="java_cmd", required=False)
    jsub.add_parser("check", parents=[common]).set_defaults(func=cmd_java)
    p.set_defaults(func=cmd_java)

    p = sub.add_parser("config", parents=[common], help="读写配置")
    csub = p.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("show", parents=[common]).set_defaults(func=cmd_config)
    q = csub.add_parser("get", parents=[common])
    q.add_argument("key")
    q.set_defaults(func=cmd_config)
    q = csub.add_parser("set", parents=[common])
    q.add_argument("key")
    q.add_argument("value")
    q.set_defaults(func=cmd_config)

    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ctx = build_context(args)
        return args.func(args, ctx) or 0
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print("错误: %s" % exc, file=sys.stderr)
        if os.environ.get("VCL2_DEBUG"):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""MC 百科（mcmod.cn）搜索 + Modrinth 下载安装 mod。"""

import os
import re
import ssl
import threading
import urllib.parse
import urllib.request

from .network.downloader import Downloader

# MC 百科搜索
MCMOD_SEARCH_URL = "https://search.mcmod.cn/s?key={key}"
# Modrinth API
MODRINTH_API = "https://api.modrinth.com/v2/"
USER_AGENT = "VCL2/1.0 (Minecraft Launcher; mod-search)"


class McModError(Exception):
    pass


class McModSearcher:
    """通过 MC 百科搜索 mod，并解析可用的 Modrinth 下载入口。"""

    def __init__(self):
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        self._lock = threading.Lock()

    def _get(self, url, timeout=30):
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.mcmod.cn/"})
        with self._lock:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as resp:
                return resp.read()

    def _get_text(self, url, timeout=30):
        return self._get(url, timeout).decode("utf-8", "replace")

    def _get_json(self, url, timeout=30):
        return json_dumps_loads(self._get(url, timeout))

    def search(self, keyword, limit=15):
        """按关键词搜 MC 百科，返回条目列表。"""
        url = MCMOD_SEARCH_URL.format(key=urllib.parse.quote(keyword))
        try:
            html = self._get_text(url, timeout=30)
        except Exception as e:
            raise McModError(f"搜索失败：{e}")

        items = []
        # 每个 result-item 一个条目
        p = re.compile(r'<div class="result-item">(.*?)(?=<div class="result-item"|<div class="result-page"|$)', re.S)
        for m in p.finditer(html):
            seg = m.group(1)
            link = re.search(
                r'<a[^>]+href="(https://www\.mcmod\.cn/class/(\d+)\.html)"[^>]*>(.*?)</a>', seg, re.S)
            if not link:
                continue
            mod_url = link.group(1)
            mod_id = link.group(2)
            title = re.sub(r"<[^>]+>", "", link.group(3))
            title = re.sub(r"\s+", " ", title).strip()
            body = re.search(r'class="body">(.*?)</div>', seg, re.S)
            desc = re.sub(r"<[^>]+>", " ", body.group(1)) if body else ""
            desc = re.sub(r"\s+", " ", desc).strip()
            items.append({
                "title": title,
                "url": mod_url,
                "class_id": mod_id,
                "desc": desc[:160],
            })
            if len(items) >= limit:
                break
        return items

    def get_modrinth_slugs(self, class_url):
        """打开 mod 详情页，提取其 Modrinth 下载入口。"""
        try:
            html = self._get_text(class_url, timeout=30)
        except Exception:
            return []
        # 站外链接里会写出真实 modrinth 地址
        slugs = re.findall(r'https://modrinth\.com/mod/([\w-]+)', html)
        return list(dict.fromkeys(slugs))


def json_dumps_loads(data):
    import json
    return json.loads(data.decode("utf-8"))


class ModrinthClient:
    """Modrinth API 客户端：项目查询、版本匹配。"""

    def __init__(self):
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _get(self, url, timeout=30):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as resp:
            return resp.read()

    def _get_json(self, url, timeout=30):
        return json_dumps_loads(self._get(url, timeout))

    def project(self, slug):
        return self._get_json(MODRINTH_API + f"project/{slug}")

    def project_versions(self, project_id):
        return self._get_json(MODRINTH_API + f"project/{project_id}/version")

    def pick(self, slug, mc_version, loader="fabric"):
        """返回该项目匹配 mc_version+loader 的主文件 (filename, url, size) 或 None。"""
        proj = self.project(slug)
        versions = self.project_versions(proj["id"])
        for v in versions:
            if mc_version not in (v.get("game_versions") or []):
                continue
            if loader not in (v.get("loaders") or []):
                continue
            for f in v.get("files", []):
                if f.get("primary") and f.get("url"):
                    return f.get("filename"), f.get("url"), f.get("size", 0), v.get("version_number")
            for f in v.get("files", []):
                fn = f.get("filename", "")
                if (fn.endswith(".jar") or fn.endswith(".zip")) and f.get("url"):
                    return fn, f.get("url"), f.get("size", 0), v.get("version_number")
        return None


class ModInstaller:
    """下载 mod 文件到指定版本的 mods 目录。"""

    def __init__(self, minecraft_dir):
        self.minecraft_dir = minecraft_dir
        self._dl = Downloader(1)
        self._dl.set_ssl_verification(False)

    def mods_dir(self, version_id):
        return os.path.join(self.minecraft_dir, "versions", version_id, "mods")

    def install(self, version_id, filename, url, progress_cb=None):
        dest_dir = self.mods_dir(version_id)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        if progress_cb:
            progress_cb(f"正在下载 mod：{filename} ...")
        r = self._dl.download(url, dest)
        if not r.success:
            raise McModError(f"下载 mod 失败：{r.error}")
        return dest
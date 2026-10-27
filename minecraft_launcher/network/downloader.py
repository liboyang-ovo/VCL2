"""下载器：支持单文件下载、批量并发下载、断点续传、进度回调、镜像源重写。"""

import os
import json
import ssl
import hashlib
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..constants import DEFAULT_THREADS

# 用户代理，部分 CDN 会校验
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MinecraftLauncher/1.0"

# 镜像源重写规则：按顺序匹配，命中即返回
MIRROR_RULES = {
    "bmclapi": [
        # 资源文件：http://resources.download.minecraft.net/xx/yyy -> /assets/xx/yyy
        ("resources.download.minecraft.net", "bmclapi2.bangbang93.com/assets"),
        # 库文件：https://libraries.minecraft.net/xxx -> /maven/xxx
        ("libraries.minecraft.net", "bmclapi2.bangbang93.com/maven"),
        # Forge/Fabric/NeoForge Maven
        ("files.minecraftforge.net/maven", "bmclapi2.bangbang93.com/maven"),
        ("maven.fabricmc.net", "bmclapi2.bangbang93.com/maven"),
        ("maven.neoforged.net/releases", "bmclapi2.bangbang93.com/maven"),
        # 版本清单 / 版本 JSON / 客户端 / Java 运行时
        ("launchermeta.mojang.com", "bmclapi2.bangbang93.com"),
        ("launcher.mojang.com", "bmclapi2.bangbang93.com"),
        ("piston-meta.mojang.com", "bmclapi2.bangbang93.com"),
        ("piston-data.mojang.com", "bmclapi2.bangbang93.com"),
    ],
}

def rewrite_url(url: str, mirror: str) -> str:
    """按镜像源重写下载 URL。mirror 为 "official" 时返回原 URL。"""
    if mirror == "official" or not url:
        return url
    for old, new in MIRROR_RULES.get(mirror, []):
        if old in url:
            return url.replace(old, new, 1)
    return url


class DownloadResult:
    def __init__(self, path, success, error=None):
        self.path = path
        self.success = success
        self.error = error


class ProgressReporter:
    """简单的进度回调，可在任一线程安全调用。"""

    def __init__(self, on_progress=None):
        self._on_progress = on_progress  # callable(current, total)
        self.total = 0
        self.done = 0
        self._lock = threading.Lock()

    def reset(self, total=0):
        with self._lock:
            self.total = total
            self.done = 0

    def add(self, n=1):
        with self._lock:
            self.done += n
            cb = self._on_progress
        if cb:
            cb(self.done, self.total)


class Downloader:
    def __init__(self, threads: int = DEFAULT_THREADS, mirror: str = "official"):
        self.threads = threads
        self.mirror = mirror
        self.reporter = None
        self._ssl_ctx = None

    def set_ssl_verification(self, enabled: bool):
        """某些网络/代理环境证书验证会失败，可关闭校验。默认开启。"""
        if not enabled:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_ctx = ctx
        else:
            self._ssl_ctx = None

    def set_progress_callback(self, cb):
        if cb:
            if self.reporter is None:
                self.reporter = ProgressReporter(cb)
            else:
                self.reporter._on_progress = cb

    def _rewrite(self, url: str) -> str:
        return rewrite_url(url, self.mirror)

    def _build_request(self, url, resume_from=0):
        headers = {"User-Agent": USER_AGENT}
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"
        req = urllib.request.Request(url, headers=headers)
        return req, self._ssl_ctx

    def _open(self, url, timeout):
        req, ctx = self._build_request(url)
        if ctx is not None:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return urllib.request.urlopen(req, timeout=timeout)

    def _download_one(self, url, dest, sha1=None, size=None, rewrite=True):
        """下载单个文件，返回 DownloadResult。dest 为完整目标路径。失败自动重试。"""
        if rewrite:
            url = self._rewrite(url)
        tmp = dest + ".download"
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        def do_download():
            resume = 0
            if os.path.exists(tmp):
                resume = os.path.getsize(tmp)
            try:
                req, ctx = self._build_request(url, resume)
                if ctx is not None:
                    resp = urllib.request.urlopen(req, timeout=60, context=ctx)
                else:
                    resp = urllib.request.urlopen(req, timeout=60)
                with resp:
                    # 请求了 Range 但服务器忽略返回 200 时，续传会损坏文件，改为从头写
                    if resume and getattr(resp, "status", 206) == 200:
                        resume = 0
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                    mode = "ab" if resume else "wb"
                    with open(tmp, mode) as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            if size is not None and os.path.getsize(tmp) > size:
                                raise urllib.error.URLError("下载大小超出预期")
            except urllib.error.HTTPError as e:
                if e.code == 416 and resume:  # 已下载完整
                    pass
                else:
                    if 400 <= e.code < 500:
                        # 4xx 错误续传无意义，清掉残留
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                    return False, f"HTTP {e.code}"
            except Exception as e:
                # 网络中断时保留 .download 以便续传
                return False, str(e)
            # 校验 sha1（临时文件可能已被并发任务移走，按失败处理）
            if sha1:
                try:
                    ok_sha1 = self._check_sha1(tmp, sha1)
                except OSError:
                    ok_sha1 = False
                if not ok_sha1:
                    # 校验失败可能因为续传损坏，删掉重下
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    return False, "SHA1 校验失败"
            try:
                os.replace(tmp, dest)
            except Exception:
                return False, "文件写入失败"
            return True, None

        last_err = None
        for attempt in range(3):
            ok, err = do_download()
            if ok:
                if self.reporter:
                    self.reporter.add(1)
                return DownloadResult(dest, True, None)
            last_err = err
        return DownloadResult(dest, False, last_err)

    def _check_sha1(self, path, sha1):
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest().lower() == sha1.lower()

    def download(self, url, dest, sha1=None, size=None):
        """下载单个文件。使用镜像源时优先镜像，失败自动回退官方源。"""
        result = self._download_one(url, dest, sha1, size)
        # 镜像失败且当前不是官方源时，回退官方源再试一次（跳过镜像重写）
        if not result.success and self.mirror != "official":
            result = self._download_one(url, dest, sha1, size, rewrite=False)
        return result

    @staticmethod
    def _dedupe(tasks):
        """同一个目标文件只下载一次（老版本 JSON 里会有重复的 natives 条目）。"""
        seen, out = set(), []
        for task in tasks:
            dest = task[1]
            if dest in seen:
                continue
            seen.add(dest)
            out.append(task)
        return out

    def download_many(self, tasks, total=None):
        """批量下载。
        tasks: 每个元素为 (url, dest, sha1, size)
        """
        tasks = self._dedupe(tasks)
        if total is None:
            total = len(tasks)
        if self.reporter:
            self.reporter.reset(total)
        results = []
        if self.threads <= 1 or len(tasks) == 1:
            for item in tasks:
                results.append(self.download(*item))
        else:
            with ThreadPoolExecutor(max_workers=self.threads) as ex:
                futures = {ex.submit(self.download, *item): item for item in tasks}
                for fut in as_completed(futures):
                    r = fut.result()
                    # 失败的文件在结果里由调用方决定是否重试
                    results.append(r)
        return results

    def get_json(self, url, timeout=60):
        """下载并解析 JSON。镜像源失败时回退官方源。"""
        try:
            return self._get_json(url, timeout)
        except Exception:
            if self.mirror != "official":
                return self._get_json(url, timeout, rewrite=False)
            raise

    def _get_json(self, url, timeout=60, rewrite=True):
        if rewrite:
            url = self._rewrite(url)
        req, ctx = self._build_request(url)
        if ctx is not None:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
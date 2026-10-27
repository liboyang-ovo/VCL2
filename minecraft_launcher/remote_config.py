"""远程配置：拉取公告、推荐版本和更新提示。

字段白名单校验，拉取失败回退到本地缓存，离线也能正常启动。
配置格式见 examples/remote_config.json。
"""

import json
import os
import time
import urllib.request

DEFAULT_TIMEOUT = 10
MAX_BYTES = 256 * 1024
USER_AGENT = "VCL2-RemoteConfig/1.0"

ALLOWED_LEVELS = ("info", "warning", "critical")


def _as_text(value, limit=2000):
    if isinstance(value, str):
        return value.strip()[:limit]
    return ""


def _as_list_of_str(value, limit=20):
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip()[:200])
            if len(out) >= limit:
                break
        return out
    return []


def normalize(raw):
    """把任意 JSON 收敛成安全的配置字典（未知字段丢弃）。"""
    if not isinstance(raw, dict):
        return {}
    data = {}
    data["schema"] = int(raw.get("schema", 1)) if isinstance(raw.get("schema"), int) else 1
    data["announcement"] = _as_text(raw.get("announcement"))
    level = raw.get("announcement_level")
    data["announcement_level"] = level if level in ALLOWED_LEVELS else "info"
    data["recommended_version"] = _as_text(raw.get("recommended_version"), 64)

    latest = raw.get("latest")
    if isinstance(latest, dict):
        version = _as_text(latest.get("version"), 32)
        url = _as_text(latest.get("url"), 500)
        if version and url.startswith(("http://", "https://")):
            data["latest"] = {"version": version, "url": url}

    data["mirrors"] = _as_list_of_str(raw.get("mirrors"))
    data["source_url"] = _as_text(raw.get("source_url"), 500) or ""
    data["fetched_at"] = int(time.time())
    return data


class RemoteConfigError(Exception):
    pass


def describe_error(exc):
    """把底层异常翻译成用户能看懂的一句话。"""
    text = str(exc)
    code = getattr(exc, "code", None)
    if code == 503:
        return "服务端暂时不可用（HTTP 503），隧道或后台可能没启动"
    if code == 502:
        return "网关错误（HTTP 502）"
    if code == 504:
        return "网关超时（HTTP 504）"
    if code in (401, 403):
        return "服务端拒绝访问（HTTP %s）" % code
    if code == 404:
        return "地址不存在（HTTP 404），检查 config.json 路径"
    if code:
        return "服务端返回 HTTP %s" % code
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "连接超时，服务端响应太慢"
    if "CERTIFICATE" in text.upper():
        return "证书校验失败，检查域名证书是否有效"
    if "Name or service not known" in text or "getaddrinfo" in text:
        return "域名解析失败，检查 DNS"
    if "Connection refused" in text:
        return "连接被拒绝，服务端没在监听这个端口"
    if "Connection reset" in text:
        return "连接被重置，网络或服务端异常"
    return text


class RemoteConfig:
    """远程配置的拉取与缓存。"""

    def __init__(self, url="", cache_path=None):
        self.url = (url or "").strip()
        self.cache_path = cache_path
        self.data = {}
        self.error = None
        self.from_cache = False
        if cache_path:
            self._load_cache()

    # ---------------- 缓存 ----------------
    def _load_cache(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict):
                self.data = cached
        except Exception:  # noqa: BLE001
            pass

    def _save_cache(self):
        if not self.cache_path:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 拉取 ----------------
    def fetch(self, timeout=DEFAULT_TIMEOUT, attempts=3):
        """拉取并解析；失败会重试，最终失败时保留缓存并设置 self.error。"""
        self.error = None
        self.from_cache = False
        if not self.url:
            self.error = "未配置远程地址"
            return self.data
        if not self.url.startswith(("http://", "https://")):
            self.error = "远程地址必须以 http:// 或 https:// 开头"
            return self.data

        last_error = None
        for attempt in range(max(1, attempts)):
            try:
                req = urllib.request.Request(self.url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                })
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise RemoteConfigError("配置超过 %d KB 上限" % (MAX_BYTES // 1024))
                parsed = json.loads(raw.decode("utf-8"))
                data = normalize(parsed)
                if not data:
                    raise RemoteConfigError("配置内容不是合法对象")
                self.data = data
                self._save_cache()
                return self.data
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < attempts - 1:
                    # 5xx 通常是后台还没起来，等久一点再试
                    code = getattr(exc, "code", None)
                    time.sleep(2.0 if code and 500 <= code < 600 else 1.0)

        self.error = describe_error(last_error) if last_error else "未知错误"
        self.from_cache = bool(self.data)
        return self.data

    # ---------------- 便捷读取 ----------------
    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def announcement(self):
        return self.data.get("announcement", "")

    def newer_version_than(self, current):
        """远端 latest.version 是否比当前版本新（简单的数字段比较）。"""
        latest = (self.data.get("latest") or {}).get("version")
        if not latest or not current:
            return False
        return _version_key(latest) > _version_key(current)


def _version_key(text):
    parts = []
    for chunk in str(text).replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])

"""微软浏览器登录（PCL2 式：自动打开浏览器 + 监听窗口标题抓取授权码）。

登录完成后，微软会把浏览器跳转到
    https://login.live.com/oauth20_desktop.srf?code=XXXX&lc=1033
此时浏览器窗口标题会包含该 URL。本模块通过 Win32 API 轮询窗口标题
来抓取 code，无需用户手动复制粘贴。
"""

import time
import urllib.parse
import webbrowser
import re

try:
    import ctypes
    from ctypes import wintypes

    _WINDOWS = True
except Exception:
    _WINDOWS = False


def _window_titles():
    """返回所有可见窗口标题（Win32）。非 Windows 返回空。"""
    titles = []
    if not _WINDOWS:
        return titles

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, lparam):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            t = buf.value or ""
            if t:
                titles.append(t)
        return True

    try:
        ctypes.windll.user32.EnumWindows(EnumWindowsProc(_cb), 0)
    except Exception:
        pass
    return titles


def _extract_code(title: str):
    """从窗口标题形如 '...oauth20_desktop.srf?code=XXX&lc=1033' 中提取 code。"""
    if "oauth20_desktop.srf" not in title:
        return None
    m = re.search(r"[?&]code=([^&\s\"']+)", title)
    if not m:
        return None
    return urllib.parse.unquote(m.group(1))


def get_code_from_browser(
    auth_url: str,
    cancel_event=None,
    timeout: float = 300.0,
    poll_interval: float = 0.5,
    on_status=None,
    open_browser: bool = True,
) -> str:
    """打开系统浏览器登录，自动等待并返回授权码。

    返回授权码；超时或用户取消返回 None。
    cancel_event: threading.Event，调用 cancel_login() 时被设置。
    on_status: 可选回调(文本) 用于更新进度提示。
    open_browser: 是否自动打开浏览器（调用方已打开时可传 False）。
    """
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
        if on_status:
            on_status("已在浏览器中打开登录页，请完成登录...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancel_event and cancel_event.is_set():
            return None
        for title in _window_titles():
            if "oauth20_desktop.srf" in title and "code=" in title:
                code = _extract_code(title)
                if code:
                    if on_status:
                        on_status("已检测到授权码，正在登录...")
                    time.sleep(0.3)  # 确保浏览器先保存
                    return code
        time.sleep(poll_interval)
    return None
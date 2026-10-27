"""tkinter 图形界面（纯标准库，零第三方依赖）。

特性：
  - 深色 / 浅色双主题，运行中可切换（重建界面，不重启程序）
  - 两列网格版本卡片（圆角、悬停、选中描边、类型徽章、安装状态）
  - 启动动画：无边框渐变启动页，淡入淡出
  - 右侧封面：程序化生成的渐变 + 像素方块装饰
  - 全部 Canvas 自绘，Windows 7 + Python 3.8 可用
"""

import hashlib
import os
import queue
import sys
import threading
import time
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

from . import constants
from . import sysinfo
from .config import LauncherConfig, default_config_path
from .version import VersionManager
from .java import JavaManager
from .auth import AuthManager, offline_uuid
from .game_launcher import GameLauncher
from .network.downloader import Downloader
from .ms_login import get_code_from_browser
from .modloader import FabricManager
from .remote_config import RemoteConfig

WIN = "Microsoft YaHei UI" if os.name == "nt" else "Noto Sans CJK SC"
MONO = "Consolas" if os.name == "nt" else "DejaVu Sans Mono"

VERSION_OF_APP = "2.0.6"


def pick_font(size=10, weight="normal", family=WIN):
    return (family, size, weight)


# ---------------------------------------------------------------- 主题

THEMES = {
    "dark": {
        "bg": "#0d1117", "surface": "#161b22", "surface2": "#1f2630",
        "hover": "#222c3a", "border": "#2b3442",
        "text": "#e6edf3", "muted": "#8b949e", "dim": "#6e7681",
        "accent": "#3b82f6", "accent_hover": "#60a5fa",
        "green": "#238636", "green_hover": "#2ea043",
        "red": "#f85149", "yellow": "#d29922", "purple": "#8957e5",
        "header": ((13, 17, 23), (26, 46, 84), (13, 17, 23)),
        "hero": ((22, 33, 58), (37, 63, 110), (15, 22, 38)),
        "logbg": "#0a0d12", "logfg": "#9aa7b8", "bar": "#0a0d12",
        "badge_bg": "#1f2630",
    },
    "light": {
        "bg": "#f6f8fa", "surface": "#ffffff", "surface2": "#eef1f5",
        "hover": "#e6ebf1", "border": "#d0d7de",
        "text": "#1f2328", "muted": "#656d76", "dim": "#8c959f",
        "accent": "#0969da", "accent_hover": "#2b7fd9",
        "green": "#1a7f37", "green_hover": "#2da44e",
        "red": "#cf222e", "yellow": "#9a6700", "purple": "#8250df",
        "header": ((246, 248, 250), (205, 222, 240), (246, 248, 250)),
        "hero": ((222, 235, 247), (186, 212, 240), (233, 241, 250)),
        "logbg": "#ffffff", "logfg": "#57606a", "bar": "#f6f8fa",
        "badge_bg": "#eef1f5",
    },
}

BG = SURFACE = SURFACE2 = HOVER = BORDER = TEXT = MUTED = DIM = ""
ACCENT = ACCENT_HOVER = GREEN = GREEN_HOVER = RED = YELLOW = PURPLE = ""
HEADER_GRAD = HERO_GRAD = ()
LOG_BG = LOG_FG = BAR_BG = BADGE_BG = ""
CURRENT_THEME = "dark"


def set_theme(name):
    global BG, SURFACE, SURFACE2, HOVER, BORDER, TEXT, MUTED, DIM
    global ACCENT, ACCENT_HOVER, GREEN, GREEN_HOVER, RED, YELLOW, PURPLE
    global HEADER_GRAD, HERO_GRAD, LOG_BG, LOG_FG, BAR_BG, BADGE_BG
    global CURRENT_THEME
    t = THEMES.get(name) or THEMES["dark"]
    CURRENT_THEME = name if name in THEMES else "dark"
    BG, SURFACE, SURFACE2 = t["bg"], t["surface"], t["surface2"]
    HOVER, BORDER = t["hover"], t["border"]
    TEXT, MUTED, DIM = t["text"], t["muted"], t["dim"]
    ACCENT, ACCENT_HOVER = t["accent"], t["accent_hover"]
    GREEN, GREEN_HOVER = t["green"], t["green_hover"]
    RED, YELLOW, PURPLE = t["red"], t["yellow"], t["purple"]
    HEADER_GRAD, HERO_GRAD = t["header"], t["hero"]
    LOG_BG, LOG_FG, BAR_BG = t["logbg"], t["logfg"], t["bar"]
    BADGE_BG = t["badge_bg"]


set_theme("dark")


# ---------------------------------------------------------------- 绘制工具

def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=12, **kw)


def vertical_gradient(canvas, w, h, colors, horizontal=False):
    """铺一条多段线性渐变。"""
    colors = list(colors)
    if len(colors) < 2:
        canvas.create_rectangle(0, 0, w, h,
                                fill="#%02x%02x%02x" % tuple(colors[0]), outline="")
        return
    n = len(colors) - 1
    span = w if horizontal else h
    seg = max(1, span // n)
    for i in range(span):
        k = min(n - 1, i // seg)
        t = (i - k * seg) / float(seg)
        c0, c1 = colors[k], colors[k + 1]
        rgb = (int(c0[0] + (c1[0] - c0[0]) * t),
               int(c0[1] + (c1[1] - c0[1]) * t),
               int(c0[2] + (c1[2] - c0[2]) * t))
        line = "#%02x%02x%02x" % rgb
        if horizontal:
            canvas.create_line(i, 0, i, h, fill=line)
        else:
            canvas.create_line(0, i, w, i, fill=line)


# ---------------------------------------------------------------- 矢量图标

def draw_cube(cv, cx, cy, size, color=None, tag=None):
    """画一个等距立体方块（启动器标志）。"""
    c = color or ACCENT
    top = _shade(c, 1.25)
    left = _shade(c, 0.75)
    right = _shade(c, 0.95)
    w = size
    h = size * 0.5
    d = size * 0.55
    x1, y1 = cx - w / 2.0, cy
    # 顶面
    cv.create_polygon(x1, y1 - h / 2, x1 + w / 2, y1 - h,
                      x1 + w, y1 - h / 2, x1 + w / 2, y1,
                      fill=top, outline="", tags=tag)
    # 左面
    cv.create_polygon(x1, y1 - h / 2, x1 + w / 2, y1,
                      x1 + w / 2, y1 + d, x1, y1 - h / 2 + d,
                      fill=left, outline="", tags=tag)
    # 右面
    cv.create_polygon(x1 + w, y1 - h / 2, x1 + w / 2, y1,
                      x1 + w / 2, y1 + d, x1 + w, y1 - h / 2 + d,
                      fill=right, outline="", tags=tag)


def _shade(hex_color, factor):
    hex_color = hex_color.lstrip("#")
    r = min(255, int(int(hex_color[0:2], 16) * factor))
    g = min(255, int(int(hex_color[2:4], 16) * factor))
    b = min(255, int(int(hex_color[4:6], 16) * factor))
    return "#%02x%02x%02x" % (r, g, b)


def draw_sun(cv, cx, cy, r, color=None):
    c = color or YELLOW
    cv.create_oval(cx - r * 0.45, cy - r * 0.45, cx + r * 0.45, cy + r * 0.45,
                   fill=c, outline="")
    import math
    for k in range(8):
        a = math.pi * 2 * k / 8
        x1 = cx + math.cos(a) * r * 0.62
        y1 = cy + math.sin(a) * r * 0.62
        x2 = cx + math.cos(a) * r
        y2 = cy + math.sin(a) * r
        cv.create_line(x1, y1, x2, y2, fill=c, width=2,
                       capstyle="round")


def draw_moon(cv, cx, cy, r, color=None, bg=None):
    """月牙：画一个圆，再用背景色圆遮掉一块。"""
    c = color or ACCENT
    mask = bg or SURFACE
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=c, outline="")
    dx, dy = r * 0.45, -r * 0.18
    cv.create_oval(cx - r + dx, cy - r + dy, cx + r + dx, cy + r + dy,
                   fill=mask, outline="")


def draw_gear(cv, cx, cy, r, color=None, bg=None):
    c = color or MUTED
    import math
    pts = []
    for k in range(8):
        a = math.pi * 2 * k / 8
        rr = r if k % 2 == 0 else r * 0.72
        pts.extend([cx + math.cos(a) * rr, cy + math.sin(a) * rr])
    cv.create_polygon(pts, fill=c, outline="")
    cv.create_oval(cx - r * 0.3, cy - r * 0.3, cx + r * 0.3, cy + r * 0.3,
                   fill=bg or SURFACE, outline="")


class IconButton(tk.Canvas):
    """纯图形按钮（无文字），用于主题切换等。"""

    def __init__(self, master, kind="sun", command=None, size=32):
        bg = master["bg"] if "bg" in master.keys() else BG
        tk.Canvas.__init__(self, master, width=size, height=size, bg=bg,
                           highlightthickness=0, bd=0, cursor="hand2")
        self._kind = kind
        self._command = command
        self._hover = False
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._command() if self._command else None)
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def set_kind(self, kind):
        self._kind = kind
        self._draw()

    def _set_hover(self, flag):
        self._hover = flag
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 32
        h = self.winfo_height() or 32
        if self._hover:
            rounded_rect(self, 2, 2, w - 2, h - 2, 7, fill=HOVER, outline="")
        cx, cy, r = w / 2.0, h / 2.0, min(w, h) * 0.22
        if self._kind == "sun":
            draw_sun(self, cx, cy, r, YELLOW)
        elif self._kind == "moon":
            draw_moon(self, cx, cy, r * 1.05, ACCENT_HOVER, bg=self["bg"] if not self._hover else HOVER)
        elif self._kind == "gear":
            draw_gear(self, cx, cy, r * 1.25, MUTED,
                      bg=self["bg"] if not self._hover else HOVER)


class RButton(tk.Canvas):
    """圆角按钮：hover / press / disabled 三态。"""

    STYLES = {
        "primary": ("accent", "accent_hover", "#ffffff", False),
        "success": ("green", "green_hover", "#ffffff", False),
        "normal": ("surface2", "hover", "text", True),
        "ghost": ("bg", "hover", "muted", False),
    }

    def __init__(self, master, text, command=None, kind="normal",
                 width=120, height=36, radius=9, font=None):
        bg = master["bg"] if "bg" in master.keys() else BG
        tk.Canvas.__init__(self, master, width=width, height=height, bg=bg,
                           highlightthickness=0, bd=0, cursor="hand2")
        self._command = command
        self._kind = kind
        self._radius = radius
        self._text = text
        self._font = font or pick_font(10, "bold")
        self._enabled = True
        self._hover = False
        self._pressed = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def set_enabled(self, flag):
        self._enabled = bool(flag)
        self.configure(cursor="hand2" if flag else "arrow")
        self._draw()

    def set_text(self, text):
        self._text = text
        self._draw()

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _e):
        self._pressed = True
        self._draw()

    def _on_release(self, e):
        was, self._pressed = self._pressed, False
        self._draw()
        if was and self._enabled and self._command:
            if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
                self._command()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or int(float(self["width"]))
        h = self.winfo_height() or int(float(self["height"]))
        k_fill, k_hover, k_fg, outline = self.STYLES[self._kind]
        fill = {"accent": ACCENT, "accent_hover": ACCENT_HOVER,
                "surface2": SURFACE2, "hover": HOVER, "bg": BG,
                "green": GREEN, "green_hover": GREEN_HOVER,
                "text": TEXT, "muted": MUTED}[k_fill]
        hover = {"accent": ACCENT, "accent_hover": ACCENT_HOVER,
                 "surface2": HOVER, "hover": HOVER, "bg": HOVER,
                 "green": GREEN, "green_hover": GREEN_HOVER,
                 "text": TEXT, "muted": MUTED}[k_hover]
        fg = {"#ffffff": "#ffffff", "text": TEXT, "muted": MUTED}[k_fg]
        if not self._enabled:
            fill, fg = SURFACE2, DIM
        elif self._hover or self._pressed:
            fill = hover
        rounded_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill,
                     outline=BORDER if outline else fill)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class VersionGrid(tk.Canvas):
    """两列网格版本卡片。"""

    CARD_H = 92
    GAP = 10
    PAD = 12

    TYPE_STYLE = {
        "release": ("正式版", ACCENT),
        "snapshot": ("快照", PURPLE),
        "old_beta": ("旧版", DIM),
        "old_alpha": ("旧版", DIM),
        "mod": ("Mod", GREEN),
    }

    def __init__(self, master, on_select=None, on_activate=None):
        tk.Canvas.__init__(self, master, bg=SURFACE, highlightthickness=0, bd=0)
        self._items = []
        self._rects = []
        self._selected = -1
        self._hover = -1
        self._on_select = on_select
        self._on_activate = on_activate
        self._f_big = tkfont.Font(font=pick_font(11, "bold"))
        self._f_small = tkfont.Font(font=pick_font(8))
        self._pending_scroll = -1
        self.bind("<Configure>", self._on_configure)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Double-1>", self._on_double)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", lambda e: self.yview_scroll(-1, "units"))
        self.bind("<Button-5>", lambda e: self.yview_scroll(1, "units"))

    def attach_scrollbar(self, sb):
        self.configure(yscrollcommand=sb.set)
        sb.configure(command=self.yview)

    def set_items(self, items, keep_selection=None):
        self._items = list(items)
        self._selected = -1
        if keep_selection:
            ids = [v.id for v, _ in self._items]
            if keep_selection in ids:
                self._selected = ids.index(keep_selection)
        self._render()
        if self._selected >= 0:
            if self.winfo_height() > 20:
                self._ensure_visible(self._selected)
                self._pending_scroll = -1
            else:
                self._pending_scroll = self._selected

    def _on_configure(self, _e=None):
        self._render()
        if self._pending_scroll >= 0 and self.winfo_height() > 20:
            self._ensure_visible(self._pending_scroll)
            self._pending_scroll = -1

    def select(self, version_id):
        ids = [v.id for v, _ in self._items]
        if version_id in ids:
            self._selected = ids.index(version_id)
            self._render()
            self._ensure_visible(self._selected)
            if self._on_select:
                self._on_select(self._items[self._selected][0])

    def selected_version(self):
        if 0 <= self._selected < len(self._items):
            return self._items[self._selected][0]
        return None

    def _hit(self, x, y):
        cy = self.canvasy(y)
        for x1, y1, x2, y2, idx in self._rects:
            if x1 <= x <= x2 and y1 <= cy <= y2:
                return idx
        return -1

    def _on_motion(self, e):
        idx = self._hit(e.x, e.y)
        if idx != self._hover:
            self._hover = idx
            self._render()

    def _set_hover(self, idx):
        if idx != self._hover:
            self._hover = idx
            self._render()

    def _on_click(self, e):
        idx = self._hit(e.x, e.y)
        if idx >= 0:
            self._selected = idx
            self._render()
            if self._on_select:
                self._on_select(self._items[idx][0])

    def _on_double(self, e):
        idx = self._hit(e.x, e.y)
        if idx >= 0 and self._on_activate:
            self._on_activate(self._items[idx][0])

    def _on_wheel(self, e):
        self.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _card_w(self, w):
        return (w - 2 * self.PAD - self.GAP) // 2

    def _card_y(self, idx):
        return self.PAD + (idx // 2) * (self.CARD_H + self.GAP)

    def _total_height(self):
        rows = (len(self._items) + 1) // 2
        return self.PAD * 2 + rows * (self.CARD_H + self.GAP)

    def _ensure_visible(self, idx):
        if self.winfo_height() < 20:
            return
        total = max(1, self._total_height())
        y1 = self._card_y(idx)
        y2 = y1 + self.CARD_H
        top = self.canvasy(0)
        h = self.winfo_height()
        if y1 < top:
            self.yview_moveto(y1 / float(total))
        elif y2 > top + h:
            self.yview_moveto(max(0.0, (y2 - h) / float(total)))

    def _render(self):
        self.delete("all")
        self._rects = []
        w = self.winfo_width()
        if w <= 1:
            return
        self.configure(scrollregion=(0, 0, w, max(self._total_height(),
                                                  self.winfo_height())))
        if not self._items:
            self.create_text(w / 2, max(80, self.winfo_height() / 2),
                             text="没有可显示的版本", fill=DIM, font=pick_font(10))
            return
        cw = self._card_w(w)
        # 不做虚拟化：卡片数量（几百个）对 Canvas 来说完全能承受，
        # 全量绘制后滚动交给 Canvas 自己做，不会出现空白区域
        for i, (ver, installed) in enumerate(self._items):
            y1 = self._card_y(i)
            y2 = y1 + self.CARD_H
            x1 = self.PAD + (i % 2) * (cw + self.GAP)
            x2 = x1 + cw
            self._rects.append((x1, y1, x2, y2, i))
            self._draw_card(i, ver, installed, x1, y1, x2, y2)

    def _draw_card(self, i, ver, installed, x1, y1, x2, y2):
        active = i == self._selected
        hovered = i == self._hover
        if active:
            rounded_rect(self, x1, y1, x2, y2, 10, fill=SURFACE2, outline=ACCENT,
                         width=2)
            self.create_line(x1 + 6, y1 + 14, x1 + 6, y2 - 14, fill=ACCENT, width=3)
        elif hovered:
            rounded_rect(self, x1, y1, x2, y2, 10, fill=HOVER, outline=BORDER)
        else:
            rounded_rect(self, x1, y1, x2, y2, 10, fill=SURFACE, outline=BORDER)

        self.create_text(x1 + 18, y1 + 24, text=ver.id, anchor="w", fill=TEXT,
                         font=self._f_big)
        date = (getattr(ver, "release_time", "") or "")[:10]
        if date:
            self.create_text(x1 + 18, y1 + 46, text=date, anchor="w", fill=DIM,
                             font=self._f_small)

        dot = GREEN if installed else BORDER
        self.create_oval(x1 + 18, y1 + 62, x1 + 25, y1 + 69, fill=dot, outline="")
        self.create_text(x1 + 31, y1 + 66, text="已安装" if installed else "未安装",
                         anchor="w", fill=MUTED if installed else DIM,
                         font=self._f_small)

        label, color = self.TYPE_STYLE.get(getattr(ver, "type", "release"),
                                           ("未知", DIM))
        tw = self._f_small.measure(label) + 16
        bx2, bx1 = x2 - 12, x2 - 12 - tw
        rounded_rect(self, bx1, y1 + 12, bx2, y1 + 30, 9, fill=BADGE_BG,
                     outline=BORDER)
        self.create_text((bx1 + bx2) / 2, y1 + 21, text=label, fill=color,
                         font=self._f_small)


class Hero(tk.Canvas):
    """右侧封面：渐变 + 装饰像素块 + 版本标题。"""

    def __init__(self, master, height=132):
        tk.Canvas.__init__(self, master, height=height, bg=SURFACE,
                           highlightthickness=0, bd=0)
        self._title = "未选择版本"
        self._sub = "从左侧选择一个版本"
        self._installed = False
        self.bind("<Configure>", lambda e: self._render())

    def update_info(self, title, sub, installed=False):
        self._title, self._sub, self._installed = title, sub, installed
        self._render()

    def _render(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        vertical_gradient(self, w, h, HERO_GRAD)
        seed = hashlib.md5(self._title.encode("utf-8")).hexdigest()
        base = HERO_GRAD[1]
        for k in range(12):
            v = int(seed[k * 2:k * 2 + 2], 16)
            bx = w - 24 - (v % 8) * 16
            by = 12 + ((v // 8) % 5) * 16
            size = 8 + (v % 6)
            shade = "#%02x%02x%02x" % (min(255, base[0] + 30),
                                       min(255, base[1] + 30),
                                       min(255, base[2] + 30))
            self.create_rectangle(bx, by, bx + size, by + size, fill=shade, outline="")
        self.create_text(18, h / 2 - 18, text=self._title, anchor="w", fill=TEXT,
                         font=(WIN, 18, "bold"))
        self.create_text(18, h / 2 + 12, text=self._sub, anchor="w", fill=MUTED,
                         font=pick_font(9))


class Splash(tk.Toplevel):
    """无边框启动页：渐变 + 进度条 + 淡入淡出。"""

    def __init__(self, master, title=None, subtitle="正在准备 ..."):
        tk.Toplevel.__init__(self, master)
        self.overrideredirect(True)
        self.configure(bg=BG)
        self._cw, self._ch = 560, 300
        title = title or constants.APP_NAME
        x = (self.winfo_screenwidth() - self._cw) // 2
        y = (self.winfo_screenheight() - self._ch) // 2 - 40
        self.geometry("%dx%d+%d+%d" % (self._cw, self._ch, x, y))
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:  # noqa: BLE001
            pass

        self.canvas = tk.Canvas(self, width=self._cw, height=self._ch, bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._progress = 0.0
        self._closing = False
        self._start = time.time()

        vertical_gradient(self.canvas, self._cw, self._ch, HEADER_GRAD)
        self.canvas.create_text(self._cw / 2, self._ch / 2 - 54, text="◆",
                                fill=ACCENT, font=(WIN, 26))
        self.canvas.create_text(self._cw / 2, self._ch / 2 - 8, text=title,
                                fill=TEXT, font=(WIN, 24, "bold"))
        self.sub_id = self.canvas.create_text(self._cw / 2, self._ch / 2 + 30,
                                              text=subtitle, fill=MUTED,
                                              font=pick_font(9))
        self.canvas.create_rectangle(self._cw / 2 - 110, self._ch - 56,
                                     self._cw / 2 + 110, self._ch - 50,
                                     fill=SURFACE2, outline="")
        self._bar = self.canvas.create_rectangle(self._cw / 2 - 110, self._ch - 56,
                                                 self._cw / 2 - 110, self._ch - 50,
                                                 fill=ACCENT, outline="")
        self._fade(0.0, 0.98, 12)
        self.after(80, self._tick)

    def set_status(self, text):
        try:
            self.canvas.itemconfigure(self.sub_id, text=text)
        except Exception:  # noqa: BLE001
            pass

    def _tick(self):
        if self._closing:
            return
        if self._progress < 0.92:
            self._progress += 0.035
            self._draw_bar()
            self.after(70, self._tick)

    def _draw_bar(self):
        x1 = self._cw / 2 - 110
        x2 = x1 + 220 * min(1.0, self._progress)
        try:
            self.canvas.coords(self._bar, x1, self._ch - 56, max(x1 + 2, x2),
                               self._ch - 50)
        except Exception:  # noqa: BLE001
            pass

    def _fade(self, start, end, steps, done=None, i=0):
        try:
            val = start + (end - start) * (i / float(steps))
            self.attributes("-alpha", max(0.0, min(1.0, val)))
        except Exception:  # noqa: BLE001
            if done:
                done()
            return
        if i < steps:
            self.after(18, lambda: self._fade(start, end, steps, done, i + 1))
        elif done:
            done()

    MIN_VISIBLE = 0.9

    def close(self):
        if self._closing:
            return
        remain = self.MIN_VISIBLE - (time.time() - self._start)
        if remain > 0:
            self.after(int(remain * 1000), self.close)
            return
        self._closing = True
        self._progress = 1.0
        self._draw_bar()
        self._fade(0.98, 0.0, 8, done=self._destroy)

    def _destroy(self):
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 主窗口

class App(tk.Tk):
    def __init__(self, config_path):
        tk.Tk.__init__(self)
        self.config_file = config_path
        self.cfg = LauncherConfig(config_path)
        set_theme(self.cfg.get("theme") or "dark")

        self.q = queue.Queue()
        self._versions = []
        self._installed = set()
        self._launching = False
        self._log_lines = []
        self._splash = None
        self._banner_info = None

        self.title(constants.WINDOW_TITLE)
        self.geometry("1140x740")
        self.minsize(1000, 620)
        self.configure(bg=BG)

        self._set_window_icon()
        self._build_managers()
        self.remote = RemoteConfig(self.cfg.get("remote_config_url", ""),
                                   self._remote_cache_path())
        self._style_ttk()
        self._build_ui()
        self._load_config()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._pump)
        self._splash = Splash(self)
        self.after(250, self._boot)

        if not sysinfo.is_64bit_os():
            self._log("提示：当前是 32 位系统，只能运行 1.16.5 及更早版本（需要 64 位 Java）。")
            self._log("最大内存已限制在 1024 MB。")

    # ---------------- 启动 ----------------
    def _boot(self):
        if self._splash:
            self._splash.set_status("正在获取版本列表 ...")
        self.refresh_versions()

    def _close_splash(self):
        if self._splash is not None:
            self._splash.close()
            self._splash = None

    # ---------------- 管理器 ----------------
    def _set_window_icon(self):
        """设置标题栏 / 任务栏图标（打包后从 PyInstaller 解包目录读）。"""
        try:
            base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base, "assets", "icon.png")
            if os.path.exists(path):
                self._icon_img = tk.PhotoImage(file=path)
                self.iconphoto(True, self._icon_img)
        except Exception:  # noqa: BLE001
            pass

    def _remote_cache_path(self):
        return os.path.join(os.path.dirname(self.cfg.path), "remote_config.json")

    def _build_managers(self):
        mc_dir = self.cfg.minecraft_dir
        self.dl = Downloader(constants.DEFAULT_THREADS, mirror=self.cfg["mirror"])
        if self.cfg.get("insecure_ssl"):
            self.dl.set_ssl_verification(False)
        self.dl.set_progress_callback(
            lambda done, total: self.q.put(("progress", done, total)))
        self.vm = VersionManager(mc_dir, self.dl)
        self.jm = JavaManager(mc_dir, self.dl)
        self.auth = AuthManager(self._token_path())
        self.launcher = GameLauncher(mc_dir, self.cfg, self.dl)
        self.fabric = FabricManager(mc_dir, self.dl)

    def _token_path(self):
        return os.path.join(os.path.dirname(self.cfg.path), "microsoft_tokens.json")

    def _style_ttk(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=SURFACE2, background=SURFACE2,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE2)],
                  foreground=[("readonly", TEXT)])
        style.configure("TSpinbox", fieldbackground=SURFACE2, background=SURFACE2,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=4)
        style.configure("TScale", background=SURFACE, troughcolor=SURFACE2,
                        bordercolor=SURFACE2, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Vertical.TScrollbar", background=SURFACE2, troughcolor=SURFACE,
                        bordercolor=SURFACE, arrowcolor=MUTED, gripcount=0)
        style.configure("TProgressbar", background=ACCENT, troughcolor=SURFACE2,
                        bordercolor=SURFACE2, lightcolor=ACCENT, darkcolor=ACCENT,
                        thickness=4)

    # ---------------- 界面搭建 ----------------
    def _build_ui(self):
        self._build_header()
        self._build_statusbar()
        self._body = tk.Frame(self, bg=BG)
        self._body.pack(fill="both", expand=True, padx=18, pady=(14, 12))
        body = self._body

        # 公告条（默认隐藏，拿到远程配置后再显示）
        self.banner = tk.Frame(self, bg=SURFACE, highlightbackground=BORDER,
                               highlightthickness=1)
        self._banner_label = tk.Label(self.banner, text="", bg=SURFACE, fg=TEXT,
                                      font=pick_font(9), anchor="w", justify="left")
        self._banner_label.pack(fill="x", padx=12, pady=8)
        self._render_banner()
        self._build_left(body)
        self._build_right(body)

    def _build_header(self):
        bar = tk.Canvas(self, height=64, bg=BG, highlightthickness=0, bd=0)
        bar.pack(fill="x")

        def redraw(_e=None):
            bar.delete("all")
            w = max(2, bar.winfo_width())
            vertical_gradient(bar, w, 64, HEADER_GRAD, horizontal=True)
            bar.create_line(0, 63, w, 63, fill=BORDER)
            # 标志：和程序图标一致的「圆角底 + 方块」
            rounded_rect(bar, 16, 16, 44, 44, 8, fill=ACCENT, outline="")
            draw_cube(bar, 30, 31, 15, color="#ffffff")
            bar.create_text(52, 24, text=constants.APP_NAME, fill=TEXT,
                            font=(WIN, 15, "bold"), anchor="w")
            bar.create_text(52, 44, text=constants.APP_NAME_FULL, fill=MUTED,
                            font=(WIN, 8), anchor="w")
            bar.create_text(w - 250, 32, text="v" + VERSION_OF_APP, fill=DIM, font=(WIN, 8), anchor="e")

        bar.bind("<Configure>", redraw)
        holder = tk.Frame(self, bg=BG)
        holder.place(in_=bar, relx=1.0, rely=0.5, anchor="e", x=-16)
        self.theme_btn = IconButton(
            holder, "sun" if CURRENT_THEME == "dark" else "moon",
            self._toggle_theme, size=30)
        self.theme_btn.pack(side="right", padx=(6, 0))
        RButton(holder, "打开目录", self.open_mc_dir, kind="ghost", width=84,
                height=30, font=pick_font(9)).pack(side="right", padx=(0, 6))
        RButton(holder, "设置", self.open_settings, kind="ghost", width=64,
                height=30, font=pick_font(9)).pack(side="right")

    def _build_left(self, parent):
        left = tk.Frame(parent, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 14))

        top_row = tk.Frame(left, bg=BG)
        top_row.pack(fill="x")
        search_wrap = tk.Frame(top_row, bg=SURFACE2, highlightbackground=BORDER,
                               highlightthickness=1)
        search_wrap.pack(side="left", fill="x", expand=True)
        tk.Label(search_wrap, text="搜索", bg=SURFACE2, fg=DIM,
                 font=pick_font(9)).pack(side="left", padx=(12, 6), pady=8)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._apply_filter())
        tk.Entry(search_wrap, textvariable=self.search_var, bg=SURFACE2, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=0,
                 font=pick_font(10)).pack(side="left", fill="x", expand=True,
                                          pady=8, padx=(0, 10))
        self.filter_var = tk.StringVar(value="全部")
        self.filter_box = ttk.Combobox(top_row, textvariable=self.filter_var,
                                       state="readonly", width=8,
                                       values=("全部", "正式版", "快照", "旧版", "Mod"))
        self.filter_box.pack(side="left", padx=(8, 0))
        self.filter_box.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())

        grid_card = tk.Frame(left, bg=SURFACE, highlightbackground=BORDER,
                             highlightthickness=1)
        grid_card.pack(fill="both", expand=True, pady=(10, 0))
        self.grid = VersionGrid(grid_card, on_select=self._on_select,
                                on_activate=lambda v: self.start_launch())
        sb = ttk.Scrollbar(grid_card, orient="vertical")
        self.grid.attach_scrollbar(sb)
        self.grid.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        sb.pack(side="right", fill="y", padx=(0, 1), pady=1)

        ops = tk.Frame(left, bg=BG)
        ops.pack(fill="x", pady=(10, 0))
        RButton(ops, "刷新版本", self.refresh_versions, width=110, height=34
                ).pack(side="left", fill="x", expand=True, padx=(0, 5))
        RButton(ops, "安装 Fabric", self.open_fabric, width=110, height=34
                ).pack(side="left", fill="x", expand=True, padx=5)
        RButton(ops, "搜索 Mod", self.open_mods, width=110, height=34
                ).pack(side="left", fill="x", expand=True, padx=(5, 0))

    def _build_right(self, parent):
        right = tk.Frame(parent, bg=BG, width=404)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        hero_card = tk.Frame(right, bg=SURFACE, highlightbackground=BORDER,
                             highlightthickness=1)
        hero_card.pack(fill="x")
        self.hero = Hero(hero_card, height=132)
        self.hero.pack(fill="x", padx=1, pady=1)

        acc = tk.Frame(right, bg=SURFACE, highlightbackground=BORDER,
                       highlightthickness=1)
        acc.pack(fill="x", pady=(12, 0))
        acc_in = tk.Frame(acc, bg=SURFACE)
        acc_in.pack(fill="x", padx=16, pady=14)
        row = tk.Frame(acc_in, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="登录方式", bg=SURFACE, fg=MUTED,
                 font=pick_font(9)).pack(side="left")
        self.auth_var = tk.StringVar(value="离线模式")
        self.auth_box = ttk.Combobox(row, textvariable=self.auth_var,
                                     state="readonly", width=10,
                                     values=("离线模式", "微软账户"))
        self.auth_box.pack(side="right")
        self.auth_box.bind("<<ComboboxSelected>>", lambda e: self._on_auth_changed())

        row = tk.Frame(acc_in, bg=SURFACE)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="玩家名", bg=SURFACE, fg=MUTED,
                 font=pick_font(9)).pack(side="left")
        name_wrap = tk.Frame(row, bg=SURFACE2, highlightbackground=BORDER,
                             highlightthickness=1)
        name_wrap.pack(side="right", fill="x", expand=True, padx=(12, 0))
        self.name_var = tk.StringVar()
        tk.Entry(name_wrap, textvariable=self.name_var, bg=SURFACE2, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=0,
                 font=pick_font(10)).pack(fill="x", padx=8, pady=5)

        self.account_label = tk.Label(acc_in, text="", bg=SURFACE, fg=ACCENT,
                                      font=pick_font(9), anchor="w")
        self.account_label.pack(fill="x", pady=(8, 0))
        self.ms_row = tk.Frame(acc_in, bg=SURFACE)
        self.ms_row.pack(fill="x", pady=(6, 0))
        self.login_btn = RButton(self.ms_row, "登录微软账户", self.do_ms_login,
                                 kind="primary", width=130, height=30,
                                 font=pick_font(9))
        self.logout_btn = RButton(self.ms_row, "退出登录", self.do_ms_logout,
                                  width=90, height=30, font=pick_font(9))

        mem = tk.Frame(right, bg=SURFACE, highlightbackground=BORDER,
                       highlightthickness=1)
        mem.pack(fill="x", pady=(12, 0))
        mem_in = tk.Frame(mem, bg=SURFACE)
        mem_in.pack(fill="x", padx=16, pady=14)
        row = tk.Frame(mem_in, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="最大内存", bg=SURFACE, fg=MUTED,
                 font=pick_font(9)).pack(side="left")
        self.ram_text = tk.Label(row, text="2048 MB", bg=SURFACE, fg=ACCENT,
                                 font=pick_font(10, "bold"))
        self.ram_text.pack(side="right")
        self.ram_var = tk.IntVar(value=2048)
        ttk.Scale(mem_in, from_=512, to=16384, orient="horizontal",
                  variable=self.ram_var, command=self._on_ram_change).pack(
            fill="x", pady=(10, 0))
        row = tk.Frame(mem_in, bg=SURFACE)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="初始内存", bg=SURFACE, fg=MUTED,
                 font=pick_font(9)).pack(side="left")
        self.minram_var = tk.IntVar(value=512)
        ttk.Spinbox(row, from_=256, to=8192, increment=256, width=8,
                    textvariable=self.minram_var).pack(side="right")

        self.launch_btn = RButton(right, "启动游戏", self.start_launch,
                                  kind="success", width=380, height=52,
                                  radius=10, font=(WIN, 13, "bold"))
        self.launch_btn.pack(fill="x", pady=(14, 0))

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = tk.Label(right, textvariable=self.status_var, bg=BG,
                                     fg=GREEN, font=pick_font(9), anchor="w")
        self.status_label.pack(fill="x", pady=(10, 4))
        self.progress = ttk.Progressbar(right, mode="determinate", value=0)
        self.progress.pack(fill="x")

        head = tk.Frame(right, bg=BG)
        head.pack(fill="x", pady=(12, 4))
        tk.Label(head, text="日志", bg=BG, fg=MUTED,
                 font=pick_font(9, "bold")).pack(side="left")
        clear = tk.Label(head, text="清空", bg=BG, fg=DIM, font=pick_font(8),
                         cursor="hand2")
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda e: self._clear_log())

        log_card = tk.Frame(right, bg=LOG_BG, highlightbackground=BORDER,
                            highlightthickness=1)
        log_card.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_card, bg=LOG_BG, fg=LOG_FG, relief="flat",
                                bd=0, wrap="word", font=(MONO, 8), state="disabled",
                                insertbackground=TEXT, padx=10, pady=8)
        lsb = ttk.Scrollbar(log_card, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=BAR_BG, height=28, highlightbackground=BORDER,
                       highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.path_label = tk.Label(bar, text="", bg=BAR_BG, fg=DIM,
                                   font=pick_font(8))
        self.path_label.pack(side="left", padx=14)
        tk.Label(bar, text=sysinfo.arch_label() + "   ·   BMCLAPI " +
                 ("开" if self.cfg.get("mirror") == "bmclapi" else "关"),
                 bg=BAR_BG, fg=DIM, font=pick_font(8)).pack(side="right", padx=14)

    # ---------------- 主题切换 ----------------
    def _toggle_theme(self):
        self.apply_theme("light" if CURRENT_THEME == "dark" else "dark")

    def apply_theme(self, name):
        if name == CURRENT_THEME:
            return
        set_theme(name)
        self.cfg["theme"] = name
        self.cfg.save()
        self._rebuild_ui()
        self._log("已切换主题：" + ("浅色" if name == "light" else "深色"))

    def _rebuild_ui(self):
        for child in list(self.winfo_children()):
            child.destroy()
        self.configure(bg=BG)
        self._style_ttk()
        self._build_ui()
        self._load_config()
        if self._versions:
            self._apply_filter()
        if self._log_lines:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(self._log_lines) + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    # ---------------- 配置 ----------------
    def _load_config(self):
        c = self.cfg
        self.name_var.set(c["username"])
        self.ram_var.set(int(c["max_ram"]))
        self.minram_var.set(int(c["min_ram"]))
        self._on_ram_change(int(c["max_ram"]))
        if c["auth"] == "microsoft":
            self.auth_var.set("微软账户")
        self._on_auth_changed(save=False)
        self.path_label.configure(text=c.minecraft_dir)

    def _on_ram_change(self, value):
        try:
            mb = int(float(value))
        except (TypeError, ValueError):
            return
        self.ram_text.configure(text="%d MB" % mb)

    # ---------------- 后台 ----------------
    def _run_bg(self, fn, on_ok=None, on_err=None):
        def worker():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self.q.put(("error", on_err, "%s" % exc))
            else:
                self.q.put(("ok", on_ok, result))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                tag = msg[0]
                if tag == "log":
                    self._append_log(msg[1], remember=True)
                elif tag == "progress":
                    self._on_progress(msg[1], msg[2])
                elif tag == "ok":
                    if msg[1]:
                        msg[1](msg[2])
                elif tag == "error":
                    self._append_log("[错误] " + msg[2], remember=True)
                    if msg[1]:
                        msg[1](msg[2])
                    else:
                        self._set_status("出错：" + msg[2], RED)
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _log(self, msg):
        self.q.put(("log", str(msg)))

    def _append_log(self, msg, remember=False):
        text = str(msg)
        if remember:
            self._log_lines.append(text)
            if len(self._log_lines) > 500:
                self._log_lines = self._log_lines[-500:]
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    def _clear_log(self):
        self._log_lines = []
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _on_progress(self, done, total):
        self.status_var.set("下载中 %d/%d" % (done, total) if total else "下载中 %d" % done)

    def _set_status(self, text, color=GREEN):
        self.status_var.set(text)
        try:
            self.status_label.configure(fg=color)
        except Exception:  # noqa: BLE001
            pass

    def _progress_busy(self):
        try:
            self.progress.configure(mode="indeterminate")
            self.progress.start(14)
        except Exception:  # noqa: BLE001
            pass

    def _progress_idle(self):
        try:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 版本 ----------------
    def refresh_versions(self):
        self._set_status("正在获取版本列表 ...", YELLOW)
        self._progress_busy()
        self._run_bg(lambda: self.vm.list_versions(True),
                     self._versions_ok, self._versions_err)

    def _versions_ok(self, versions):
        self._close_splash()
        self._fetch_remote()
        self._progress_idle()
        self._set_status("就绪", GREEN)
        self._versions = list(versions)
        seen = {v.id for v in self._versions}
        for vid, vtype in self.fabric.scan_local_versions():
            if vid not in seen:
                self._versions.append(_LocalVersion(vid, vtype))
        self._installed = self._scan_installed()
        self._apply_filter()

    def _fetch_remote(self, retry=0):
        if not self.cfg.get("remote_config_enabled", True):
            return
        url = (self.cfg.get("remote_config_url") or "").strip()
        if not url:
            return
        self.remote.url = url

        def work():
            return self.remote.fetch()

        def ok(_data):
            if self.remote.error:
                self._log("远程配置获取失败：" + self.remote.error)
                # 后台没起来时过一分钟再试，最多 3 次
                if retry < 3:
                    self.after(60000, lambda: self._fetch_remote(retry + 1))
            else:
                self._log("远程配置已更新（%s）" % url)
            text = self.remote.announcement
            if text:
                self._banner_info = (text, self.remote.get("announcement_level", "info"))
                self._render_banner()
            rec = self.remote.get("recommended_version")
            if rec:
                self._log("推荐版本：" + rec)
            if self.remote.newer_version_than(VERSION_OF_APP):
                latest = self.remote.get("latest") or {}
                if latest.get("url"):
                    if messagebox.askyesno(
                            "发现新版本",
                            "VCL2 %s 已发布，当前版本 %s。\n要打开下载页面吗？"
                            % (latest.get("version"), VERSION_OF_APP)):
                        webbrowser.open(latest["url"])

        self._run_bg(work, ok)

    def _render_banner(self):
        if not self._banner_info:
            try:
                self.banner.pack_forget()
            except Exception:  # noqa: BLE001
                pass
            return
        text, level = self._banner_info
        color = {"info": ACCENT, "warning": YELLOW, "critical": RED}.get(level, ACCENT)
        try:
            self._banner_label.configure(text=text, fg=color)
            self.banner.pack(fill="x", padx=18, pady=(8, 0), before=self._body)
            # 公告条会占掉一行高度，把窗口撑高一点，避免底部被裁
            if not getattr(self, "_banner_expanded", False):
                self._banner_expanded = True
                self.update_idletasks()
                h = self.banner.winfo_height() + 8
                self.geometry("%dx%d" % (self.winfo_width(), self.winfo_height() + h))
        except Exception:  # noqa: BLE001
            pass

    def _scan_installed(self):
        result = set()
        vdir = os.path.join(self.cfg.minecraft_dir, "versions")
        if os.path.isdir(vdir):
            for d in os.listdir(vdir):
                if os.path.isfile(os.path.join(vdir, d, d + ".json")):
                    result.add(d)
        return result

    def _versions_err(self, msg):
        self._close_splash()
        self._progress_idle()
        self._set_status("版本获取失败", RED)
        try:
            self.hero.update_info("没有版本数据", "检查网络后点「刷新版本」重试")
        except Exception:  # noqa: BLE001
            pass
        messagebox.showwarning("刷新失败", msg)

    def _apply_filter(self):
        if not self._versions:
            return
        keyword = (self.search_var.get() or "").strip().lower()
        mode = self.filter_var.get()
        items = []
        for v in self._versions:
            vtype = getattr(v, "type", "release")
            if mode == "正式版" and vtype != "release":
                continue
            if mode == "快照" and vtype != "snapshot":
                continue
            if mode == "旧版" and vtype not in ("old_beta", "old_alpha"):
                continue
            if mode == "Mod" and vtype != "mod":
                continue
            if keyword and keyword not in v.id.lower():
                continue
            items.append((v, v.id in self._installed or vtype == "mod"))
        self.grid.set_items(items, keep_selection=self.cfg["version"])
        current = self.grid.selected_version()
        if current is None and items:
            self.grid.select(items[0][0].id)
        elif current is not None:
            self._update_selected(current)

    def _on_select(self, ver):
        self.cfg["version"] = ver.id
        self.cfg.save()
        self._update_selected(ver)

    def _update_selected(self, ver):
        installed = ver.id in self._installed or getattr(ver, "type", "") == "mod"
        date = (getattr(ver, "release_time", "") or "")[:10]
        sub = "   ·   ".join([x for x in (date, "已安装" if installed else "未安装") if x])
        self.hero.update_info(ver.id, sub, installed)

    # ---------------- 账户 ----------------
    def _on_auth_changed(self, save=True):
        ms = self.auth_var.get() == "微软账户"
        if ms:
            if self.auth.has_stored_login:
                self.account_label.configure(text="已登录：" + self.auth.stored_display_name)
                self.login_btn.pack_forget()
                self.logout_btn.pack(side="right")
            else:
                self.account_label.configure(text="")
                self.logout_btn.pack_forget()
                self.login_btn.pack(side="right")
        else:
            self.account_label.configure(text="")
            self.login_btn.pack_forget()
            self.logout_btn.pack_forget()
        if save:
            self.cfg["auth"] = "microsoft" if ms else "offline"
            self.cfg.save()

    def do_ms_login(self):
        dlg = MsLoginDialog(self, self.auth.build_authorize_url())
        self.wait_window(dlg)
        code = dlg.result_code
        if not code:
            return
        self._log("正在用授权码登录微软账户 ...")
        self._progress_busy()

        def ok(profile):
            self._progress_idle()
            self.cfg["auth"] = "microsoft"
            self.cfg["username"] = profile["name"]
            self.cfg.save()
            self.name_var.set(profile["name"])
            self.account_label.configure(text="已登录：" + profile["name"])
            self._log("微软账户登录成功：" + profile["name"])
            messagebox.showinfo("成功", "登录成功：" + profile["name"])

        def err(msg):
            self._progress_idle()
            messagebox.showerror("登录失败", msg)

        self._run_bg(lambda: self.auth.login_with_code(code), ok, err)

    def do_ms_logout(self):
        if not messagebox.askyesno("确认", "退出并清除已保存的微软登录？"):
            return
        self.auth.logout()
        self.cfg["auth"] = "offline"
        self.cfg.save()
        self.auth_var.set("离线模式")
        self._on_auth_changed()
        self._log("已退出微软登录")

    # ---------------- 启动游戏 ----------------
    def _current_profile(self):
        if self.auth_var.get() == "微软账户":
            if not self.auth.has_stored_login:
                raise RuntimeError("请先登录微软账户")
            return self.auth.login_with_stored()
        name = (self.name_var.get() or "").strip() or "玩家"
        self.name_var.set(name)
        return {"name": name, "uuid": offline_uuid(name),
                "access_token": "0", "msa": False}

    def start_launch(self):
        if self._launching:
            return
        version = self.cfg["version"]
        if not version:
            messagebox.showwarning("提示", "请先选择一个版本")
            return
        self.cfg["max_ram"] = int(float(self.ram_var.get()))
        self.cfg["min_ram"] = int(self.minram_var.get())
        self.cfg["username"] = (self.name_var.get() or "").strip() or self.cfg["username"]
        self.cfg.save()
        try:
            profile = self._current_profile()
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("账户错误", str(exc))
            return

        self._launching = True
        self.launch_btn.set_enabled(False)
        self.launch_btn.set_text("正在启动 ...")
        self._progress_busy()
        self._set_status("正在安装 / 启动 ...", YELLOW)

        def ok(_r):
            self._launching = False
            self._progress_idle()
            self.launch_btn.set_enabled(True)
            self.launch_btn.set_text("启动游戏")
            self._set_status("游戏已启动", GREEN)
            self._log("Minecraft 进程已启动。")

        def err(msg):
            self._launching = False
            self._progress_idle()
            self.launch_btn.set_enabled(True)
            self.launch_btn.set_text("启动游戏")
            self._set_status("启动失败", RED)
            messagebox.showerror("启动失败", msg)

        self._run_bg(lambda: self.launcher.install_and_launch(
            version, profile, log=self._log), ok, err)

    # ---------------- 入口按钮 ----------------
    def open_settings(self):
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.saved:
            if dlg.theme_changed:
                self.apply_theme(dlg.theme_changed)
                return
            self._build_managers()
            self.path_label.configure(text=self.cfg.minecraft_dir)
            self._log("设置已保存")

    def open_mc_dir(self):
        d = self.cfg.minecraft_dir
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)  # noqa: S606
        except Exception:  # noqa: BLE001
            webbrowser.open("file://" + d)

    def open_fabric(self):
        base = self._base_version()
        if not base:
            messagebox.showwarning("提示", "请先选择一个原版版本（如 1.21.1）")
            return
        dlg = FabricDialog(self, self.fabric, base)
        self.wait_window(dlg)
        if dlg.installed_id:
            self.cfg["version"] = dlg.installed_id
            self.cfg.save()
            self._log("Fabric 已安装：" + dlg.installed_id)
            self.refresh_versions()

    def open_mods(self):
        version = self.cfg["version"]
        if not version:
            messagebox.showwarning("提示", "请先选择一个版本")
            return
        dlg = ModDialog(self, version)
        self.wait_window(dlg)

    def _base_version(self):
        ver = self.cfg["version"]
        if not ver:
            return None
        if ver.startswith("fabric-loader-"):
            parts = ver[len("fabric-loader-"):].split("-", 1)
            return parts[1] if len(parts) > 1 else ver
        return ver

    def _on_close(self):
        try:
            self.cfg.save()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


class _LocalVersion:
    def __init__(self, vid, vtype):
        self.id = vid
        self.type = vtype
        self.release_time = ""


# ---------------------------------------------------------------- 对话框

class _Dialog(tk.Toplevel):
    def __init__(self, master, title, size="560x460"):
        tk.Toplevel.__init__(self, master)
        self.title(title)
        self.geometry(size)
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()
        self.resizable(True, True)
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=16)


def _field(parent, label_text, var, width=12):
    row = tk.Frame(parent, bg=BG)
    row.pack(fill="x", pady=5)
    tk.Label(row, text=label_text, bg=BG, fg=MUTED, font=pick_font(9),
             width=width, anchor="w").pack(side="left")
    wrap = tk.Frame(row, bg=SURFACE2, highlightbackground=BORDER, highlightthickness=1)
    wrap.pack(side="left", fill="x", expand=True)
    tk.Entry(wrap, textvariable=var, bg=SURFACE2, fg=TEXT, relief="flat", bd=0,
             insertbackground=TEXT, font=pick_font(10)).pack(fill="x", padx=8, pady=5)
    return row


class SettingsDialog(_Dialog):
    def __init__(self, master, cfg):
        _Dialog.__init__(self, master, "设置", "640x620")
        self.cfg = cfg
        self.saved = False
        self.theme_changed = None

        tk.Label(self.body, text="设置", bg=BG, fg=TEXT,
                 font=(WIN, 14, "bold")).pack(anchor="w", pady=(0, 10))

        self.java_var = tk.StringVar(value=cfg["java_path"])
        row = _field(self.body, "Java 路径", self.java_var)
        RButton(row, "浏览", self._browse_java, width=64, height=28,
                font=pick_font(9)).pack(side="left", padx=(8, 0))

        self.auto_java_var = tk.BooleanVar(value=bool(cfg["auto_java"]))
        self.fullscreen_var = tk.BooleanVar(value=bool(cfg.get("fullscreen")))
        self.insecure_var = tk.BooleanVar(value=bool(cfg.get("insecure_ssl")))
        for text, var in (("自动下载官方 Java 运行时", self.auto_java_var),
                          ("全屏启动", self.fullscreen_var),
                          ("关闭 HTTPS 证书校验（仅证书异常的网络）", self.insecure_var)):
            tk.Checkbutton(self.body, text=text, variable=var, bg=BG, fg=TEXT,
                           selectcolor=SURFACE2, activebackground=BG,
                           activeforeground=TEXT, font=pick_font(9)).pack(anchor="w")

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=5)
        tk.Label(row, text="主题", bg=BG, fg=MUTED, font=pick_font(9), width=12,
                 anchor="w").pack(side="left")
        self.theme_var = tk.StringVar(value="浅色" if CURRENT_THEME == "light" else "深色")
        ttk.Combobox(row, textvariable=self.theme_var, state="readonly", width=10,
                     values=("深色", "浅色")).pack(side="left")

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=5)
        tk.Label(row, text="下载源", bg=BG, fg=MUTED, font=pick_font(9), width=12,
                 anchor="w").pack(side="left")
        self.mirror_var = tk.StringVar(value=cfg.get("mirror", "official"))
        ttk.Combobox(row, textvariable=self.mirror_var, state="readonly", width=18,
                     values=("official", "bmclapi")).pack(side="left")

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=5)
        tk.Label(row, text="内存", bg=BG, fg=MUTED, font=pick_font(9), width=12,
                 anchor="w").pack(side="left")
        self.max_ram_var = tk.IntVar(value=int(cfg["max_ram"]))
        ram_max = 1024 if not sysinfo.is_64bit_os() else 32768
        ttk.Spinbox(row, from_=256, to=ram_max, increment=256, width=8,
                    textvariable=self.max_ram_var).pack(side="left")
        tk.Label(row, text="最大(MB)", bg=BG, fg=DIM,
                 font=pick_font(8)).pack(side="left", padx=(4, 14))
        self.min_ram_var = tk.IntVar(value=int(cfg["min_ram"]))
        ttk.Spinbox(row, from_=256, to=8192, increment=256, width=8,
                    textvariable=self.min_ram_var).pack(side="left")
        tk.Label(row, text="初始(MB)", bg=BG, fg=DIM,
                 font=pick_font(8)).pack(side="left", padx=4)

        self.remote_url_var = tk.StringVar(value=cfg.get("remote_config_url", ""))
        _field(self.body, "远程配置", self.remote_url_var)
        self.remote_enabled_var = tk.BooleanVar(
            value=bool(cfg.get("remote_config_enabled", True)))
        tk.Checkbutton(self.body, text="启动时拉取远程配置（公告 / 推荐版本 / 更新提示）",
                       variable=self.remote_enabled_var, bg=BG, fg=TEXT,
                       selectcolor=SURFACE2, activebackground=BG,
                       activeforeground=TEXT, font=pick_font(9)).pack(anchor="w")

        self.dir_var = tk.StringVar(value=cfg.minecraft_dir)
        row = _field(self.body, "游戏目录", self.dir_var)
        RButton(row, "浏览", self._browse_dir, width=64, height=28,
                font=pick_font(9)).pack(side="left", padx=(8, 0))

        self.jvm_var = tk.StringVar(value=cfg.get("jvm_args", ""))
        _field(self.body, "额外 JVM", self.jvm_var)
        self.game_args_var = tk.StringVar(value=cfg.get("game_args", ""))
        _field(self.body, "额外游戏参数", self.game_args_var)

        btns = tk.Frame(self.body, bg=BG)
        btns.pack(fill="x", side="bottom", pady=(14, 0))
        RButton(btns, "保存", self._save, kind="primary", width=96,
                height=34).pack(side="right")
        RButton(btns, "取消", self.destroy, width=96,
                height=34).pack(side="right", padx=(0, 8))

    def _browse_java(self):
        path = filedialog.askopenfilename(
            title="选择 java.exe", filetypes=[("Java", "java.exe"), ("所有文件", "*.*")])
        if path:
            self.java_var.set(path)

    def _browse_dir(self):
        path = filedialog.askdirectory(title="选择 .minecraft 目录")
        if path:
            self.dir_var.set(path)

    def _save(self):
        c = self.cfg
        c["java_path"] = self.java_var.get().strip()
        c["auto_java"] = bool(self.auto_java_var.get())
        c["mirror"] = self.mirror_var.get()
        c["max_ram"] = int(self.max_ram_var.get())
        c["min_ram"] = int(self.min_ram_var.get())
        c["jvm_args"] = self.jvm_var.get().strip()
        c["game_args"] = self.game_args_var.get().strip()
        c["fullscreen"] = bool(self.fullscreen_var.get())
        c["insecure_ssl"] = bool(self.insecure_var.get())
        c["remote_config_url"] = self.remote_url_var.get().strip()
        c["remote_config_enabled"] = bool(self.remote_enabled_var.get())
        new_dir = self.dir_var.get().strip()
        if new_dir:
            c["game_dir"] = new_dir
        want = "light" if self.theme_var.get() == "浅色" else "dark"
        if want != CURRENT_THEME:
            self.theme_changed = want
            c["theme"] = want
        c.save()
        self.saved = True
        self.destroy()


class FabricDialog(_Dialog):
    def __init__(self, master, fabric_mgr, mc_version):
        _Dialog.__init__(self, master, "安装 Fabric", "520x480")
        self.fabric = fabric_mgr
        self.mc_version = mc_version
        self.installed_id = None
        self.loaders = []
        self._app = master if isinstance(master, App) else None

        tk.Label(self.body, text="安装 Fabric", bg=BG, fg=TEXT,
                 font=(WIN, 14, "bold")).pack(anchor="w")
        tk.Label(self.body, text="基础版本：%s" % mc_version, bg=BG, fg=MUTED,
                 font=pick_font(9)).pack(anchor="w", pady=(4, 10))

        self.status_var = tk.StringVar(value="加载中 ...")
        tk.Label(self.body, textvariable=self.status_var, bg=BG, fg=GREEN,
                 font=pick_font(9), anchor="w").pack(fill="x", pady=(0, 8))

        wrap = tk.Frame(self.body, bg=SURFACE, highlightbackground=BORDER,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(wrap, bg=SURFACE, fg=TEXT, selectbackground=ACCENT,
                                  selectforeground="#ffffff", relief="flat", bd=0,
                                  highlightthickness=0, activestyle="none",
                                  font=pick_font(10))
        self.listbox.pack(fill="both", expand=True, padx=2, pady=2)

        btns = tk.Frame(self.body, bg=BG)
        btns.pack(fill="x", pady=(12, 0))
        self.ok_btn = RButton(btns, "安装", self._install, kind="primary",
                              width=96, height=34)
        self.ok_btn.set_enabled(False)
        self.ok_btn.pack(side="right")
        RButton(btns, "取消", self.destroy, width=96,
                height=34).pack(side="right", padx=(0, 8))

        if self._app:
            self._app._run_bg(lambda: self.fabric.list_fabric(mc_version),
                              self._loaded, lambda m: self.status_var.set("获取失败：" + m))

    def _loaded(self, loaders):
        self.loaders = loaders
        if not loaders:
            self.status_var.set("%s 没有可用的 Fabric 版本" % self.mc_version)
            return
        self.status_var.set("选择加载器版本")
        for item in loaders:
            self.listbox.insert("end", item["name"] + ("   稳定" if item["stable"] else ""))
        self.listbox.selection_set(0)
        self.ok_btn.set_enabled(True)

    def _install(self):
        sel = self.listbox.curselection()
        if not sel or not self._app:
            return
        loader = self.loaders[sel[0]]["name"]
        self.status_var.set("正在安装 ...")
        self.ok_btn.set_enabled(False)

        def ok(vid):
            self.installed_id = vid
            self.destroy()

        def err(msg):
            self.status_var.set("安装失败：" + msg)
            self.ok_btn.set_enabled(True)

        self._app._run_bg(lambda: self.fabric.install_fabric(self.mc_version, loader),
                          ok, err)


class ModDialog(_Dialog):
    def __init__(self, master, version_id):
        _Dialog.__init__(self, master, "搜索 Mod", "780x580")
        from .mcmod import McModSearcher, ModrinthClient, ModInstaller

        self.version_id = version_id
        self.searcher = McModSearcher()
        self.modrinth = ModrinthClient()
        self.installer = ModInstaller(master.launcher.minecraft_dir)
        self.items = []
        self._app = master if isinstance(master, App) else None

        tk.Label(self.body, text="搜索 Mod", bg=BG, fg=TEXT,
                 font=(WIN, 14, "bold")).pack(anchor="w")
        tk.Label(self.body, text="安装到：%s" % version_id, bg=BG, fg=MUTED,
                 font=pick_font(9)).pack(anchor="w", pady=(4, 10))

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x")
        box = tk.Frame(row, bg=SURFACE2, highlightbackground=BORDER,
                       highlightthickness=1)
        box.pack(side="left", fill="x", expand=True)
        self.kw_var = tk.StringVar()
        entry = tk.Entry(box, textvariable=self.kw_var, bg=SURFACE2, fg=TEXT,
                         relief="flat", bd=0, insertbackground=TEXT,
                         font=pick_font(10))
        entry.pack(fill="x", padx=10, pady=7)
        entry.bind("<Return>", lambda e: self._search())
        RButton(row, "搜索", self._search, kind="primary", width=88,
                height=34).pack(side="left", padx=(8, 0))

        wrap = tk.Frame(self.body, bg=SURFACE, highlightbackground=BORDER,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True, pady=12)
        self.listbox = tk.Listbox(wrap, bg=SURFACE, fg=TEXT, selectbackground=ACCENT,
                                  selectforeground="#ffffff", relief="flat", bd=0,
                                  highlightthickness=0, activestyle="none",
                                  font=pick_font(10))
        self.listbox.pack(fill="both", expand=True, padx=2, pady=2)

        self.status_var = tk.StringVar(value="")
        tk.Label(self.body, textvariable=self.status_var, bg=BG, fg=GREEN,
                 font=pick_font(9), anchor="w").pack(fill="x")

        btns = tk.Frame(self.body, bg=BG)
        btns.pack(fill="x", pady=(10, 0))
        RButton(btns, "下载到 mods", self._install_sel, kind="primary",
                width=120, height=34).pack(side="left")
        RButton(btns, "打开页面", self._open_page, width=100,
                height=34).pack(side="left", padx=8)
        RButton(btns, "关闭", self.destroy, width=90,
                height=34).pack(side="right")

    def _search(self):
        kw = self.kw_var.get().strip()
        if not kw or not self._app:
            return
        self.status_var.set("搜索中 ...")
        self.listbox.delete(0, "end")
        self.items = []

        def ok(items):
            self.items = items
            self.status_var.set("找到 %d 条结果" % len(items))
            for it in items:
                self.listbox.insert("end", it["title"])

        self._app._run_bg(lambda: self.searcher.search(kw), ok,
                          lambda m: self.status_var.set("搜索失败：" + m))

    def _open_page(self):
        sel = self.listbox.curselection()
        if sel and sel[0] < len(self.items):
            webbrowser.open(self.items[sel[0]]["url"])

    def _install_sel(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.items) or not self._app:
            return
        item = self.items[sel[0]]
        self.status_var.set("正在解析下载地址 ...")

        def work():
            slugs = self.searcher.get_modrinth_slugs(item["url"])
            if not slugs:
                raise RuntimeError("该 Mod 在 Modrinth 上未找到下载页")
            mc_ver = _mc_version_of(self.version_id)
            for slug in slugs:
                pick = self.modrinth.pick(slug, mc_ver, "fabric")
                if pick:
                    filename, url, _size, _vn = pick
                    return self.installer.install(self.version_id, filename, url)
            raise RuntimeError("未找到匹配当前版本 + Fabric 的文件")

        self._app._run_bg(work,
                          lambda p: self.status_var.set("已安装：" + os.path.basename(p)),
                          lambda m: self.status_var.set("下载失败：" + m))


class MsLoginDialog(_Dialog):
    def __init__(self, master, url):
        _Dialog.__init__(self, master, "微软账户登录", "640x430")
        self.url = url
        self.result_code = None
        self._cancel = threading.Event()

        tk.Label(self.body, text="登录微软账户", bg=BG, fg=TEXT,
                 font=(WIN, 14, "bold")).pack(anchor="w")
        tip = ("浏览器会自动打开微软登录页。完成登录后，本窗口会自动抓取授权码。\n"
               "若长时间没反应，可以把浏览器地址栏里 code= 后面的内容粘贴到下面。")
        tk.Label(self.body, text=tip, bg=BG, fg=MUTED, font=pick_font(9),
                 justify="left", wraplength=580).pack(anchor="w", pady=10)

        self.status_var = tk.StringVar(value="等待登录 ...")
        tk.Label(self.body, textvariable=self.status_var, bg=BG, fg=GREEN,
                 font=pick_font(9), anchor="w").pack(fill="x", pady=8)

        RButton(self.body, "重新打开浏览器", self._open_browser, width=150,
                height=32).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="授权码", bg=BG, fg=MUTED, font=pick_font(9),
                 width=8, anchor="w").pack(side="left")
        wrap = tk.Frame(row, bg=SURFACE2, highlightbackground=BORDER,
                        highlightthickness=1)
        wrap.pack(side="left", fill="x", expand=True)
        self.code_var = tk.StringVar()
        tk.Entry(wrap, textvariable=self.code_var, bg=SURFACE2, fg=TEXT,
                 relief="flat", bd=0, insertbackground=TEXT,
                 font=pick_font(10)).pack(fill="x", padx=8, pady=6)

        btns = tk.Frame(self.body, bg=BG)
        btns.pack(fill="x", side="bottom", pady=(14, 0))
        RButton(btns, "完成", self._finish, kind="primary", width=96,
                height=34).pack(side="right")
        RButton(btns, "取消", self._cancel_and_close, width=96,
                height=34).pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self._cancel_and_close)
        self._open_browser()
        threading.Thread(target=self._monitor, daemon=True).start()

    def _open_browser(self):
        try:
            webbrowser.open(self.url)
        except Exception:  # noqa: BLE001
            pass

    def _monitor(self):
        code = get_code_from_browser(
            self.url, cancel_event=self._cancel,
            on_status=lambda s: self.status_var.set(s) if self._alive() else None,
            open_browser=False)
        if code and self._alive():
            self.code_var.set(code)
            self._finish()

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except Exception:  # noqa: BLE001
            return False

    def _finish(self):
        code = (self.code_var.get() or "").strip()
        if not code:
            self.status_var.set("还没有授权码，请先完成浏览器登录")
            return
        self.result_code = code
        self._cancel.set()
        self.destroy()

    def _cancel_and_close(self):
        self._cancel.set()
        self.destroy()


def _mc_version_of(version_id):
    if version_id.startswith("fabric-loader-"):
        rest = version_id[len("fabric-loader-"):]
        if "-" in rest:
            return rest.split("-", 1)[1]
        return rest
    return version_id


def launch_gui():
    app = App(default_config_path())
    app.mainloop()


#!/usr/bin/env python3
"""生成启动器图标：渐变圆角底 + 等距立体方块。

用超采样（4x）再缩小的方式做抗锯齿，输出：
  assets/icon.png      256x256（窗口/任务栏）
  assets/icon_64.png   64x64（README 等）
  assets/icon.ico      16/24/32/48/64/128/256 多尺寸

运行（需要 Pillow）：
  python assets/make_icon.py
"""

import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 4  # 超采样倍数


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def build(size=1024):
    """画一张 size x size 的 RGBA 图。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ---- 圆角底：竖向渐变 ----
    top_color = (59, 130, 246)     # #3b82f6
    bottom_color = (23, 58, 138)   # #173a8a
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        gd.line([(0, y), (size, y)], fill=lerp(top_color, bottom_color, y / (size - 1.0)))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # ---- 等距方块 ----
    cx, cy = size / 2.0, size / 2.0 + size * 0.02
    w = size * 0.52          # 方块宽度
    h = w * 0.5              # 顶面高度的一半
    d = w * 0.55             # 侧面高度

    x1 = cx - w / 2.0
    top_face = [
        (x1, cy - h / 2), (cx, cy - h), (x1 + w, cy - h / 2), (cx, cy)
    ]
    left_face = [
        (x1, cy - h / 2), (cx, cy), (cx, cy + d), (x1, cy + d - h / 2)
    ]
    right_face = [
        (x1 + w, cy - h / 2), (cx, cy), (cx, cy + d), (x1 + w, cy + d - h / 2)
    ]

    # 投影：让方块从背景里"浮"起来
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        [x1 + w * 0.10, cy + d - h * 0.34, x1 + w * 0.90, cy + d + h * 0.18],
        fill=(8, 20, 45, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=size * 0.03))
    img.alpha_composite(shadow)

    draw.polygon(top_face, fill=(191, 219, 254))    # 顶面：最亮
    draw.polygon(left_face, fill=(59, 130, 246))    # 左面：中亮
    draw.polygon(right_face, fill=(29, 78, 216))    # 右面：暗面

    # 棱线：一点点浅色描边，小尺寸下轮廓更清楚
    edge = (255, 255, 255, 60)
    draw.line([top_face[0], top_face[1], top_face[2], top_face[3], top_face[0]],
              fill=edge, width=max(1, size // 220))

    return img


def main():
    base = build(1024)
    icon_png = os.path.join(HERE, "icon.png")
    base.resize((256, 256), Image.LANCZOS).save(icon_png)
    base.resize((64, 64), Image.LANCZOS).save(os.path.join(HERE, "icon_64.png"))

    ico = os.path.join(HERE, "icon.ico")
    base.resize((256, 256), Image.LANCZOS).save(
        ico, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                  (64, 64), (128, 128), (256, 256)])
    print("生成完成：")
    for p in (icon_png, os.path.join(HERE, "icon_64.png"), ico):
        print("  %-28s %6.1f KB" % (os.path.basename(p), os.path.getsize(p) / 1024.0))


if __name__ == "__main__":
    main()

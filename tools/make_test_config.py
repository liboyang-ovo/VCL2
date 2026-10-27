#!/usr/bin/env python3
"""生成截图用的测试配置（开发辅助，不参与启动器运行）。

环境变量：
  VCL2_HOME        配置根目录（默认 .testhome）
  VCL2_THEME       dark / light
  VCL2_REMOTE      远程配置地址（可空）
  VCL2_VERSION     预设选中版本（默认 1.21.1）
"""

import json
import os

home = os.environ.get("VCL2_HOME") or os.path.join(os.getcwd(), ".testhome")
theme = os.environ.get("VCL2_THEME") or "dark"
remote = os.environ.get("VCL2_REMOTE") or ""
version = os.environ.get("VCL2_VERSION") or "1.21.1"

base = os.path.join(home, ".vcl2")
os.makedirs(base, exist_ok=True)
path = os.path.join(base, "launcher.json")

cfg = {}
if os.path.exists(path):
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cfg = {}

if remote:
    cfg["remote_config_url"] = remote
cfg.update({
    "username": "Steve",
    "max_ram": 4096,
    "min_ram": 512,
    "version": version,
    "mirror": "bmclapi",
    "theme": theme,
    "remote_config_enabled": True,
})
json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("config ->", path, "| theme:", theme, "| remote:", remote or "(none)")

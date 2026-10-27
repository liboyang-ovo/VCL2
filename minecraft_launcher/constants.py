"""全局常量定义。"""

# 用户代理
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 MinecraftLauncher/1.0"

# 版本清单
VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

# 下载基地址
ASSET_BASE = "https://resources.download.minecraft.net/"

# 微软认证相关
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

# 下载并发数
DEFAULT_THREADS = 32

# 默认窗口标题
APP_NAME = "VCL2"
APP_NAME_FULL = "Voxel Craft Launcher 2"
WINDOW_TITLE = APP_NAME_FULL

TMP_PREFIX = "_mclauncher_"
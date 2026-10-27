"""微软账户(Microsoft) OAuth2 登录 / 离线模式身份。"""

import hashlib
import json
import os
import uuid
import urllib.parse
import urllib.request

from . import constants

# 官方 Minecraft 启动器公开的 client id（社区通用，无需注册 Azure App）
CLIENT_ID = "00000000402b5328"
REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
AUTH_URL = "https://login.live.com/oauth20_authorize.srf"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"


class AuthError(Exception):
    pass


def offline_uuid(name: str) -> str:
    """根据玩家名生成稳定的 v3 UUID（离线登录惯例）。"""
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest))


class AuthManager:
    """管理 Microsoft 登录、token 刷新、离线身份。"""

    def __init__(self, token_file=None):
        self.token_file = token_file
        self.stored = self._load_stored()

    # ---------------- 存储 ----------------
    def _load_stored(self):
        if not self.token_file:
            return {}
        try:
            with open(self.token_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_stored(self):
        if not self.token_file:
            return
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        with open(self.token_file, "w", encoding="utf-8") as f:
            json.dump(self.stored, f, ensure_ascii=False, indent=2)

    @property
    def has_stored_login(self) -> bool:
        return bool(self.stored.get("refresh_token"))

    @property
    def stored_display_name(self) -> str:
        return self.stored.get("name", "")

    # ---------------- 登录流程 ----------------
    def build_authorize_url(self) -> str:
        """构造微软授权页 URL（用户需在浏览器中打开）。"""
        params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": "XboxLive.signin offline_access",
            "prompt": "select_account",
        }
        return AUTH_URL + "?" + urllib.parse.urlencode(params)

    def login_with_code(self, code: str) -> dict:
        """用授权码完成整个登录流程，返回 {'name','uuid','access_token','refresh_token'}。"""
        ms_token = self._exchange_code(code)
        profile = self._complete_ms(ms_token["access_token"])
        self.stored = {
            "name": profile["name"],
            "uuid": profile["uuid"],
            "refresh_token": ms_token.get("refresh_token", ""),
            "access_token": profile["access_token"],
            "msa": True,
        }
        self._save_stored()
        return dict(self.stored)

    def login_with_stored(self) -> dict:
        """使用保存的 refresh_token 静默登录。失败时抛 AuthError。"""
        refresh_token = self.stored.get("refresh_token")
        if not refresh_token:
            raise AuthError("没有已保存的登录信息")
        ms_token = self._refresh_ms(refresh_token)
        profile = self._complete_ms(ms_token["access_token"])
        self.stored = {
            "name": profile["name"],
            "uuid": profile["uuid"],
            "refresh_token": ms_token.get("refresh_token") or refresh_token,
            "access_token": profile["access_token"],
            "msa": True,
        }
        self._save_stored()
        return dict(self.stored)

    def logout(self):
        self.stored = {}
        self._save_stored()

    # ---------------- 内部 ----------------
    def _exchange_code(self, code: str) -> dict:
        data = {
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
            "scope": "XboxLive.signin offline_access",
        }
        resp = self._post_form(TOKEN_URL, data)
        if "error" in resp:
            raise AuthError(f"微软授权失败：{resp.get('error_description', resp['error'])}")
        return resp

    def _refresh_ms(self, refresh_token: str) -> dict:
        data = {
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "XboxLive.signin offline_access",
        }
        resp = self._post_form(TOKEN_URL, data)
        if "error" in resp:
            raise AuthError("登录已过期，请重新登录微软账号")
        return resp

    def _complete_ms(self, ms_access_token: str) -> dict:
        """XBL -> XSTS -> Minecraft，返回 {'name','uuid','access_token'}。"""
        # 1. XBL
        xbl = self._post_json(
            constants.XBL_AUTH_URL,
            {
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": "d=" + ms_access_token,
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            },
        )
        if "Token" not in xbl:
            raise AuthError("Xbox Live 认证失败")
        xbl_token = xbl["Token"]
        user_hash = xbl["DisplayClaims"]["xui"][0]["uhs"]

        # 2. XSTS
        xsts = self._post_json(
            constants.XSTS_AUTH_URL,
            {
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT",
            },
        )
        if "Token" not in xsts:
            xerr = xsts.get("XErr", "?")
            msg = {
                "2148916233": "该账户没有 Xbox 账户",
                "2148916235": "该账户所在地区不支持 Xbox Live",
                "2148916236": "该账户需要成年验证",
                "2148916238": "该账户为未成年账号，需要加入家庭组",
            }.get(str(xerr), f"Xbox Live 授权失败 (XErr={xerr})")
            raise AuthError(msg)
        xsts_token = xsts["Token"]

        # 3. Minecraft 登录
        mc = self._post_json(
            constants.MC_LOGIN_URL,
            {"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
        )
        if "access_token" not in mc:
            raise AuthError("Minecraft 登录失败")
        mc_token = mc["access_token"]

        # 4. 获取玩家资料
        profile = self._get_json(constants.MC_PROFILE_URL, {"Authorization": f"Bearer {mc_token}"})
        if "id" not in profile:
            raise AuthError("该账户未拥有 Minecraft Java 版")
        return {
            "access_token": mc_token,
            "uuid": profile.get("id", ""),
            "name": profile.get("name", ""),
        }

    # ---------------- HTTP 工具 ----------------
    def _post_form(self, url, form_data):
        body = urllib.parse.urlencode(form_data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("User-Agent", "Mozilla/5.0")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_json(self, url, payload):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get_json(self, url, headers=None):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        req.add_header("Accept", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
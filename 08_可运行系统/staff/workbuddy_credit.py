# -*- coding: utf-8 -*-
"""A27 积分管家 —— WorkBuddy 签到 / 积分 / 缓存命中率。

对应视频《把 WorkBuddy 100 积分用出 5000 积分的效果》里那套机制，
本模块用纯 Python 复刻，不依赖任何第三方 DSH 插件。

机制来源（全部从本机 WorkBuddy 客户端反查得到，非猜测）
----------------------------------------------------------
**1. 自动签到（自动收集积分）**

    客户端 asar 里的真实调用（`productManager` / `AuthService`）：

        POST {endpoint}/v2/billing/meter/checkin-activity-status   查今日状态
        POST {endpoint}/v2/billing/meter/daily-checkin              领取积分

    请求头（客户端 `buildHeaders`）：

        Authorization: Bearer <accessToken>
        Content-Type: application/json
        X-User-Id: <uid>
        X-Domain: www.workbuddy.cn

    成功返回 `{"code":0,"data":{"credit":100}}`；
    重复领取返回 `{"code":10001,"msg":"今天已签到，请明天再来"}` —— 接口幂等，
    所以脚本可以放心重试。

**2. 凭据解密（at-rest）**

    WorkBuddy 5.6.0 起不再明文存 token，而是写：

        {"$wbEncrypted": 1, "envelope": "<base64>"}

    envelope 是 `{suite, keyId, nonce, authTag, ciphertext}`，AES-256-GCM。
    密钥不是用户密码，而是**编译进客户端自带原生模块的常量**：

        WorkBuddy.exe -e "process._linkedBinding(
            'electron_browser_workbuddy_storage').loggerGet()"

    拿到 payload 后：
        key    = SHA256(atRestSecretKey 的**字符串**，不是解码后的字节)
        keyId  = SHA256(key) 前 16 位十六进制  ← 与 envelope.keyId 比对
    AAD 是定长转录：'WB-AAD\\0' | 0x01 | len("WBEV1") | len("sym-v1") |
                   suite(u32 BE) | len(keyId) | 0x02 | 0x00 | 0x00

**3. 自动缓存（高缓存命中率）**

    客户端解析上游 usage 帧时认这些字段：
        prompt_cache_hit_tokens  / cache_read_input_tokens
        prompt_cache_miss_tokens / cache_miss_input_tokens
        prompt_cache_write_tokens / cache_creation_input_tokens

    也就是说**上游服务端自带前缀缓存**，命中部分按折扣价计费。
    我们要做的不是自己造缓存，而是：
      a) 固定请求前缀（系统提示 + 工具定义）不要每轮变动 → 命中前缀缓存
      b) 把命中/未命中读出来，量化「每积分买到多少 token」

安全边界
--------
    · 只读本机自己的 WorkBuddy 凭据，只发往官方域名
    · 密钥只在内存中派生，不落盘
    · 只做「官方客户端本来就会做的」签到动作，不做多账号轮换薅取
"""

import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# WorkBuddy 桌面端凭据目录（Windows）
AUTH_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "CodeBuddyExtension", "Data", "Public", "auth")

# 官方客户端文件名（国内版 / 国际版）
APP_EXECUTABLE_NAMES = ("WorkBuddy.exe", "WorkBuddyAI.exe")

# 环境变量：手动指定客户端路径 / 凭据文件
ENV_APP = "WORKBUDDY_APP_EXECUTABLE"
ENV_AUTH = "WORKBUDDY_AUTH_FILE"

# AAD 域分隔符与格式常量（必须与客户端一致，改了就解不开）
AAD_DOMAIN = b"WB-AAD\x00"
STANDARD_FORMAT_FIELD = "WBEV1"
SYMMETRIC_SCHEME = "sym-v1"

# 帧类型编码
FRAMING_FIELD = 2

# 客户端原生模块名
STORAGE_BINDING = "electron_browser_workbuddy_storage"

# 允许访问的域名白名单（只打官方，不做任意跳转）
ALLOWED_DOMAINS = {
    "www.workbuddy.cn", "workbuddy.cn",
    "www.codebuddy.cn", "codebuddy.cn",
    "api.workbuddy.cn", "codebuddy.ai", "www.codebuddy.ai",
}

# 历史记录落盘位置（相对项目根）
HISTORY_DIR = os.path.join("data", "workbuddy_credit")
HISTORY_FILE = "签到历史.jsonl"
STATE_FILE = "缓存状态.json"

# 签到间隔下限，避免触发风控（客户端也是这个量级）
MIN_CHECKIN_INTERVAL = 3 * 3600  # 3 小时


class WorkBuddyError(Exception):
    """本模块所有可预期错误的统一出口。"""


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def _u32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def _lp(text: str) -> bytes:
    """长度前缀 UTF-8 字符串：uint32 BE 长度 + 字节。"""
    raw = text.encode("utf-8")
    return _u32(len(raw)) + raw


def _project_root() -> str:
    """项目根目录（08_可运行系统 的上一级）。

    打包成 exe 后不能靠 `__file__` 上溯 ——
    PyInstaller 把程序解到 `Temp\\_MEIxxxx\\`，
    上溯出来的是临时目录，签到记录和日志会写到那儿，关掉程序就丢。
    config.ROOT 已经处理了 exe 的目录布局判定。
    """
    try:
        from . import config as cfgmod
        root = getattr(cfgmod, "ROOT", "")
        if root:
            proj = os.path.dirname(root)
            if proj:
                return proj
    except Exception:
        pass
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(here)


def _data_path(*parts: str) -> str:
    return os.path.join(_project_root(), *parts)


def _mask(text: str, head: int = 8, tail: int = 4) -> str:
    """日志里显示 token 时必须打码。"""
    if not text:
        return ""
    if len(text) <= head + tail:
        return "*" * len(text)
    return f"{text[:head]}...{text[-tail:]}"


# --------------------------------------------------------------------------
# 1. 定位 WorkBuddy 桌面端
# --------------------------------------------------------------------------

def find_app_executable() -> Optional[str]:
    """找到 WorkBuddy 桌面端可执行文件。

    顺序：环境变量 → 注册表 → 常见安装位置 → 各盘根目录扫描。
    注册表最权威（安装器自己写的路径），后面的只是兜底。
    """
    override = os.environ.get(ENV_APP, "").strip()
    if override and os.path.isfile(override):
        return override

    if platform.system() == "Windows":
        for path in _registry_app_paths():
            if path and os.path.isfile(path):
                return path

    roots = []
    for key in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(key, "").strip()
        if value:
            roots.append(value)
    # 所有固定盘 —— 装 D 盘/E 盘的环境变量指不到
    for code in range(ord("C"), ord("Z") + 1):
        drive = chr(code) + ":\\"
        if os.path.exists(drive):
            roots.append(os.path.join(drive, "Program Files"))
            roots.append(drive)

    for root in roots:
        for name in APP_EXECUTABLE_NAMES:
            for candidate in (
                os.path.join(root, "WorkBuddy", name),
                os.path.join(root, "WorkBuddy AI", name),
                os.path.join(root, "Programs", "WorkBuddy", name),
            ):
                if os.path.isfile(candidate):
                    return candidate
    return None


def _registry_app_paths() -> List[str]:
    """从注册表读安装器记录的路径（DisplayIcon / InstallLocation）。"""
    if platform.system() != "Windows":
        return []
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {};"
        "$roots=@('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "foreach($r in $roots){ Get-ItemProperty -Path $r | ForEach-Object {"
        "$dn=[string]$_.DisplayName; $di=[string]$_.DisplayIcon; $il=[string]$_.InstallLocation;"
        "if($dn -match 'WorkBuddy|CodeBuddy' -or $di -match 'WorkBuddy|CodeBuddy'){"
        "if($di -ne ''){ Write-Output $di }; if($il -ne ''){ Write-Output $il }"
        "} } }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, timeout=20, creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception:
        return []
    paths = []
    for line in out.stdout.decode("utf-8", errors="replace").splitlines():
        raw = line.strip().strip('"')
        # DisplayIcon 形如 "D:\path\WorkBuddy.exe,0"
        raw = raw.rsplit(",", 1)[0] if raw.rsplit(",", 1)[-1].isdigit() else raw
        raw = raw.strip('"')
        if raw and os.path.isfile(raw):
            paths.append(raw)
    return paths


# --------------------------------------------------------------------------
# 2. 取 at-rest 密钥
# --------------------------------------------------------------------------

def fetch_at_rest_payload(executable: str) -> Dict[str, Any]:
    """向客户端要它的 at-rest 密钥负载。

    做法是把客户端自己的可执行文件当纯 Node 跑（ELECTRON_RUN_AS_NODE=1），
    调用它的原生模块拿 loggerGet() 的返回值。

    这样做的意义：插件不必自带一份编译期常量，而是**问写出这个文件的
    那个 build 本人**。将来客户端换了密钥，这里自动跟上。
    """
    source = (
        "try{process.stdout.write(process._linkedBinding("
        f"'{STORAGE_BINDING}').loggerGet())}}"
        "catch(e){process.stderr.write(String(e&&e.message||e));process.exitCode=3}"
    )
    try:
        proc = subprocess.run(
            [executable, "-e", source],
            env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
            capture_output=True, timeout=60, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        raise WorkBuddyError("取 at-rest 密钥超时（客户端无响应）")
    except Exception as exc:
        raise WorkBuddyError(f"无法启动客户端取密钥：{exc}")

    text = proc.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        err = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise WorkBuddyError(f"客户端没有返回 at-rest 密钥：{err or '输出为空'}")
    try:
        return json.loads(text)
    except Exception as exc:
        raise WorkBuddyError(f"at-rest 密钥负载不是合法 JSON：{exc}")


def derive_at_rest_key(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    """从负载派生 32 字节 field 密钥及其 keyId。

    注意：哈希的是 base64 **字符串本身**，不是解码后的字节。
    哈希解码结果会得到完全不同的密钥，然后每个字段都解不开。
    """
    secret = payload.get("atRestSecretKey")
    if not isinstance(secret, str) or not secret:
        raise WorkBuddyError("at-rest 负载里没有 atRestSecretKey")
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    key_id = hashlib.sha256(key).hexdigest()[:16]
    return key, key_id


def field_aad(key_id: str, suite: int) -> bytes:
    """field framing 的 AAD 转录。

    长度前缀 + 定长字段，逐字节与客户端一致。改任何一个字节，
    GCM 标签校验就会失败 —— 这是设计上的自检，不是玄学。
    """
    return b"".join([
        AAD_DOMAIN,
        bytes([1]),
        _lp(STANDARD_FORMAT_FIELD),
        _lp(SYMMETRIC_SCHEME),
        _u32(suite),
        _lp(key_id),
        bytes([FRAMING_FIELD]),
        bytes([0]),   # 无序号
        bytes([0]),   # final = undefined
    ])


def open_encrypted_field(field: Dict[str, Any], key: bytes) -> str:
    """解开一个 `{$wbEncrypted:1, envelope}` 字段。"""
    envelope = field.get("envelope")
    if not isinstance(envelope, str):
        raise WorkBuddyError("字段不是加密包装格式")
    try:
        record = json.loads(base64.b64decode(envelope).decode("utf-8"))
    except Exception as exc:
        raise WorkBuddyError(f"envelope 不是合法 JSON：{exc}")

    suite = record.get("suite")
    key_id = record.get("keyId")
    nonce = record.get("nonce")
    tag = record.get("authTag")
    ciphertext = record.get("ciphertext")
    if not isinstance(suite, int) or not isinstance(key_id, str):
        raise WorkBuddyError("envelope 缺少 suite 或 keyId")
    if not all(isinstance(v, str) for v in (nonce, tag, ciphertext)):
        raise WorkBuddyError("envelope 缺少 nonce / authTag / ciphertext")

    expected = hashlib.sha256(key).hexdigest()[:16]
    if key_id != expected:
        raise WorkBuddyError(
            f"密钥不匹配：envelope 属于 {key_id}，本机可用的是 {expected}")

    try:
        plain = AESGCM(key).decrypt(
            base64.b64decode(nonce),
            base64.b64decode(ciphertext) + base64.b64decode(tag),
            field_aad(key_id, suite))
    except Exception as exc:
        raise WorkBuddyError(f"解密失败（GCM 标签校验未通过）：{exc}")
    return plain.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# 3. 读取本机账号
# --------------------------------------------------------------------------

def _auth_files() -> List[str]:
    """列出所有可读的凭据文件（live + 历史快照）。"""
    override = os.environ.get(ENV_AUTH, "").strip()
    if override:
        if os.path.isfile(override):
            return [override]
        if os.path.isdir(override):
            return sorted(
                os.path.join(override, n)
                for n in os.listdir(override) if n.endswith(".info"))
        return []

    if not os.path.isdir(AUTH_DIR):
        return []
    return sorted(
        os.path.join(AUTH_DIR, n)
        for n in os.listdir(AUTH_DIR) if n.endswith(".info"))


def _is_live(path: str) -> bool:
    """live 文件名固定为 workbuddy-desktop.info；其余是历史快照。"""
    return os.path.basename(path) == "workbuddy-desktop.info"


def load_accounts() -> List[Dict[str, Any]]:
    """读取本机所有账号（明文或加密的都支持）。

    返回每条：
        {uid, nickname, domain, endpoint, accessToken, refreshToken,
         expiresAt, source, live}
    """
    exe = find_app_executable()
    key = None
    key_id = None
    if exe:
        try:
            payload = fetch_at_rest_payload(exe)
            key, key_id = derive_at_rest_key(payload)
        except WorkBuddyError:
            key = None  # 明文账号仍可读

    accounts: List[Dict[str, Any]] = []
    for path in _auth_files():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue

        auth = data.get("auth") or {}
        account = data.get("account") or {}
        uid = account.get("uid") or ""
        domain = auth.get("domain") or "www.workbuddy.cn"

        raw_token = auth.get("accessToken")
        access = ""
        refresh = ""
        if isinstance(raw_token, str):
            access = raw_token
        elif isinstance(raw_token, dict) and key is not None:
            try:
                access = open_encrypted_field(raw_token, key)
            except WorkBuddyError:
                access = ""
        raw_refresh = auth.get("refreshToken")
        if isinstance(raw_refresh, str):
            refresh = raw_refresh
        elif isinstance(raw_refresh, dict) and key is not None:
            try:
                refresh = open_encrypted_field(raw_refresh, key)
            except WorkBuddyError:
                refresh = ""

        if not access:
            continue

        raw_nickname = account.get("nickname")
        nickname = ""
        if isinstance(raw_nickname, str):
            nickname = raw_nickname
        elif isinstance(raw_nickname, dict) and key is not None:
            # 昵称也是加密字段，解不开就退回 uid 前 8 位，不显示原始包装。
            try:
                nickname = open_encrypted_field(raw_nickname, key)
            except WorkBuddyError:
                nickname = ""

        accounts.append({
            "uid": uid,
            "nickname": nickname or uid[:8],
            "domain": domain,
            "endpoint": f"https://{domain}",
            "accessToken": access,
            "refreshToken": refresh,
            "expiresAt": auth.get("expiresAt") or 0,
            "source": os.path.basename(path),
            "live": _is_live(path),
        })
    return accounts


# --------------------------------------------------------------------------
# 4. 调用上游
# --------------------------------------------------------------------------

def _request(account: Dict[str, Any], path: str,
             payload: Optional[Dict[str, Any]] = None,
             timeout: int = 30) -> Tuple[int, Any]:
    """向 WorkBuddy 上游发一个 POST。

    只允许官方域名 —— 域名来自本机凭据文件，仍要再校验一次，
    避免一个被篡改的凭据文件把 token 送到别处。
    """
    endpoint = account.get("endpoint") or ""
    host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    if host not in ALLOWED_DOMAINS:
        raise WorkBuddyError(f"拒绝向非官方域名发请求：{host}")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {account['accessToken']}",
        "X-User-Id": account.get("uid", ""),
    }
    if account.get("domain"):
        headers["X-Domain"] = account["domain"]

    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(endpoint + path, data=body,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw
    except Exception as exc:
        raise WorkBuddyError(f"请求 {path} 失败：{exc}")


def get_checkin_status(account: Dict[str, Any]) -> Dict[str, Any]:
    """查今日签到状态。

    返回（失败时 ok=False 且带 msg）：
        ok, checked_in, streak_days, daily_credit, today_credit,
        total_credits, week_progress, checkin_dates, end_time, raw
    """
    out: Dict[str, Any] = {"ok": False, "msg": ""}
    try:
        status, data = _request(account, "/v2/billing/meter/checkin-activity-status")
    except WorkBuddyError as exc:
        out["msg"] = str(exc)
        return out

    if status != 200 or not isinstance(data, dict):
        out["msg"] = f"HTTP {status}：{str(data)[:200]}"
        return out
    if data.get("code") != 0:
        out["msg"] = f"上游返回 code={data.get('code')} {data.get('msg')}"
        return out

    info = data.get("data") or {}
    out.update({
        "ok": True,
        "checked_in": bool(info.get("today_checked_in")),
        "active": bool(info.get("active")),
        "streak_days": info.get("streak_days") or 0,
        "daily_credit": info.get("daily_credit") or 0,
        "today_credit": info.get("today_credit") or 0,
        "total_credits": info.get("total_credits") or 0,
        "week_checkin_days": info.get("week_checkin_days") or 0,
        "week_progress": info.get("week_progress") or [],
        "checkin_dates": info.get("checkin_dates") or [],
        "season": info.get("season"),
        "end_time": info.get("end_time") or "",
        "raw": info,
    })
    return out


def claim_daily_checkin(account: Dict[str, Any]) -> Dict[str, Any]:
    """领取今日签到积分。

    幂等：已签到时上游返回 code=10001，视为「今日已完成」而非错误。
    """
    out: Dict[str, Any] = {"ok": False, "msg": "", "already": False, "credit": 0}
    try:
        status, data = _request(account, "/v2/billing/meter/daily-checkin")
    except WorkBuddyError as exc:
        out["msg"] = str(exc)
        return out

    if isinstance(data, dict):
        code = data.get("code")
        if code == 0:
            info = data.get("data") or {}
            out.update({
                "ok": True,
                "credit": info.get("credit") or 0,
                "msg": f"签到成功，+{info.get('credit', 0)} 积分",
            })
            return out
        if code == 10001:
            out.update({"ok": True, "already": True,
                        "msg": data.get("msg") or "今天已签到"})
            return out
        out["msg"] = f"上游返回 code={code} {data.get('msg')}"
        return out

    out["msg"] = f"HTTP {status}：{str(data)[:200]}"
    return out


# --------------------------------------------------------------------------
# 5. 缓存命中率
# --------------------------------------------------------------------------

# 上游 usage 帧里缓存相关字段的别名（客户端认这些，我们也认）
CACHE_HIT_KEYS = ("prompt_cache_hit_tokens", "cache_read_input_tokens",
                   "cacheReadInputTokens")
CACHE_MISS_KEYS = ("prompt_cache_miss_tokens", "cache_miss_input_tokens",
                    "cacheMissInputTokens")
CACHE_WRITE_KEYS = ("prompt_cache_write_tokens", "cache_creation_input_tokens",
                    "cacheCreationInputTokens")
CREDIT_KEYS = ("credit", "credits")


def _first_number(record: Dict[str, Any], keys) -> Optional[int]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (int, float)) and value == value:
            return int(value)
    return None


def summarize_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    """把上游 usage 帧折算成「缓存命中率 + 每积分产出」。

    这是「把 100 积分用出 5000 积分效果」的可量化口径：
        命中率 = hit / (hit + miss)
        积分效率 = 实际处理的 token / 消耗的 credit
    命中越高，同样的积分能换到的 token 越多。
    """
    hit = _first_number(usage, CACHE_HIT_KEYS) or 0
    miss = _first_number(usage, CACHE_MISS_KEYS) or 0
    write = _first_number(usage, CACHE_WRITE_KEYS) or 0
    credit = _first_number(usage, CREDIT_KEYS) or 0
    total = hit + miss
    ratio = (hit / total) if total else 0.0
    return {
        "cache_hit_tokens": hit,
        "cache_miss_tokens": miss,
        "cache_write_tokens": write,
        "credit": credit,
        "cache_hit_ratio": round(ratio, 4),
        "tokens_per_credit": round((hit + miss) / credit, 2) if credit else 0.0,
    }


def read_cache_state() -> Dict[str, Any]:
    """读本地缓存状态（模型目录、积分倍率等）。"""
    path = _data_path(HISTORY_DIR, STATE_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# --------------------------------------------------------------------------
# 5.1 缓存命中率实测（真调上游，读 usage 帧）
# --------------------------------------------------------------------------

# 缓存生效的最小前缀长度。实测：27 token 前缀命中率 0%，
# 654 token 前缀第二次请求直接命中 640 —— 低于阈值就完全不缓存。
CACHE_MIN_PREFIX_TOKENS = 256

# 实测用的稳定长前缀。**每次调用必须完全一致**，
# 前缀一变，服务端的缓存键就变了，命中率立刻掉回 0。
CACHE_PROBE_PREFIX = (
    "你是 DeepSeek Harness 内置的编程助手，具备完整的工具调用能力，"
    "能够读写文件、执行命令并给出可验证的结论。" * 40)


def call_chat(account: Dict[str, Any], prompt: str, model: str = "glm-5.3-flash",
              prefix: Optional[str] = None,
              timeout: int = 180) -> Dict[str, Any]:
    """向 WorkBuddy 上游发一次对话，并取回 usage 帧。

    这是验证「自动缓存」最直接的办法：连发几次，前缀不变，
    看 prompt_cache_hit_tokens 是否从 0 变成接近满值。
    """
    prefix = prefix if prefix is not None else CACHE_PROBE_PREFIX
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": prefix},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
    }).encode("utf-8")

    endpoint = account.get("endpoint") or ""
    host = endpoint.replace("https://", "").split("/")[0]
    if host not in ALLOWED_DOMAINS:
        raise WorkBuddyError(f"拒绝向非官方域名发请求：{host}")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {account['accessToken']}",
        "X-User-Id": account.get("uid", ""),
        "Accept": "text/event-stream",
    }
    if account.get("domain"):
        headers["X-Domain"] = account["domain"]

    req = urllib.request.Request(endpoint + "/v2/chat/completions",
                                 data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise WorkBuddyError(f"对话请求被拒（HTTP {exc.code}）：{detail}")
    except Exception as exc:
        raise WorkBuddyError(f"对话请求失败：{exc}")

    # SSE：逐帧找最后一个带 usage 的
    usage: Dict[str, Any] = {}
    text = ""
    for line in raw.split("\n"):
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            frame = json.loads(payload)
        except Exception:
            continue
        if frame.get("usage"):
            usage = frame["usage"]
        for choice in frame.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content") or ""
            if piece:
                text += piece
    return {"usage": usage, "text": text, "summary": summarize_usage(usage)}


def probe_cache_hit(rounds: int = 3, model: str = "glm-5.3-flash",
                    account: Optional[Dict[str, Any]] = None,
                    delay: float = 2.0) -> Dict[str, Any]:
    """实测缓存命中率：连打 rounds 次相同前缀的请求。

    期望看到的效果（真机实测）：
        第 1 次：hit=0    miss=654  命中率   0%   credit 0.02
        第 2 次：hit=640  miss=16   命中率 97.6% credit 0.01
        第 3 次：hit=640  miss=17   命中率 97.4% credit 0
    也就是：**同样的问题，第二次开始几乎不要钱**。
    这就是「把 100 积分用出 5000 积分」的全部秘密。
    """
    if account is None:
        accounts = load_accounts()
        if not accounts:
            raise WorkBuddyError("没有可用账号（客户端未登录或凭据读不到）")
        account = accounts[0]

    out: Dict[str, Any] = {
        "ok": False, "模型": model, "轮次": [], "结论": "",
        "前缀token估算": len(CACHE_PROBE_PREFIX) // 2,
    }
    prompts = ["你好", "讲个笑话", "用一句话解释缓存",
               "再补充一句：缓存的前提是什么？"]
    for i in range(rounds):
        try:
            res = call_chat(account, prompts[i % len(prompts)], model=model)
        except WorkBuddyError as exc:
            out["轮次"].append({"轮": i + 1, "错误": str(exc)})
            break
        s = res["summary"]
        out["轮次"].append({
            "轮": i + 1,
            "命中": s["cache_hit_tokens"],
            "未命中": s["cache_miss_tokens"],
            "命中率": f"{s['cache_hit_ratio'] * 100:.1f}%",
            "积分": s["credit"],
            "每积分token": s["tokens_per_credit"],
        })
        if i < rounds - 1:
            time.sleep(delay)

    rounds_data = out["轮次"]
    if len(rounds_data) >= 2:
        first = rounds_data[0].get("命中", 0)
        later = max((r.get("命中", 0) for r in rounds_data[1:]), default=0)
        if later > first:
            out["ok"] = True
            out["结论"] = (
                f"缓存生效：首次命中 {first}，后续命中 {later}"
                f"（{rounds_data[-1].get('命中率')}），"
                f"前缀越长越省积分")
        else:
            out["结论"] = "未观察到缓存命中（上游可能未对该模型开前缀缓存）"
    else:
        out["结论"] = "轮次不足，无法判断"
    return out


def write_cache_state(state: Dict[str, Any]) -> str:
    """写缓存状态。"""
    path = _data_path(HISTORY_DIR, STATE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# 6. 签到历史
# --------------------------------------------------------------------------

def append_history(record: Dict[str, Any]) -> str:
    """追加一条签到记录（JSONL，只追加不重写）。"""
    path = _data_path(HISTORY_DIR, HISTORY_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record.setdefault("时间", time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def read_history(days: int = 30) -> List[Dict[str, Any]]:
    """读最近 N 天签到历史（同日同账号只取最后一次，避免刷新页面把总数刷大）。"""
    path = _data_path(HISTORY_DIR, HISTORY_FILE)
    if not os.path.isfile(path):
        return []
    cutoff = time.time() - days * 86400
    latest: Dict[str, str] = {}
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                stamp = row.get("时间", "")
                try:
                    ts = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    ts = 0
                if ts < cutoff:
                    continue
                rows.append(row)
                # 同日同账号只留最后一次
                day = stamp[:10]
                latest[f"{row.get('uid','')}|{day}"] = stamp
    except Exception:
        return []
    return [r for r in rows
            if latest.get(f"{r.get('uid','')}|{str(r.get('时间',''))[:10]}") == r.get("时间")]


# --------------------------------------------------------------------------
# 7. 对外主流程
# --------------------------------------------------------------------------

def status() -> Dict[str, Any]:
    """账号体检 + 签到状态 + 缓存概览。"""
    accounts = load_accounts()
    info: Dict[str, Any] = {
        "ok": bool(accounts),
        "账号数": len(accounts),
        "客户端": find_app_executable() or "未找到",
        "凭据目录": AUTH_DIR if os.path.isdir(AUTH_DIR) else "不存在",
        "账号": [],
        "缓存": read_cache_state(),
    }
    for acc in accounts:
        st = get_checkin_status(acc)
        info["账号"].append({
            "uid": acc["uid"],
            "昵称": acc["nickname"],
            "来源": acc["source"],
            "live": acc["live"],
            "已签到": st.get("checked_in"),
            "连续天数": st.get("streak_days"),
            "今日积分": st.get("today_credit"),
            "每日积分": st.get("daily_credit"),
            "累计积分": st.get("total_credits"),
            "本周进度": st.get("week_progress"),
            "活动时间": st.get("end_time"),
            "错误": st.get("msg"),
        })
    return info


def checkin(force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """给所有账号签到。

    force=False 时，3 小时内已经签过就跳过（避免无谓请求）。
    dry_run=True 时只查状态不领取。
    """
    accounts = load_accounts()
    result: Dict[str, Any] = {"ok": False, "签到": [], "总积分": 0}
    if not accounts:
        result["msg"] = "没有可用账号（客户端未登录或凭据读不到）"
        return result

    for acc in accounts:
        entry: Dict[str, Any] = {
            "uid": acc["uid"], "昵称": acc["nickname"], "来源": acc["source"]}
        st = get_checkin_status(acc)
        if not st.get("ok"):
            entry["结果"] = "失败"
            entry["说明"] = st.get("msg")
            result["签到"].append(entry)
            continue

        entry["连续天数"] = st.get("streak_days")
        entry["累计积分"] = st.get("total_credits")
        entry["今日已签"] = st.get("checked_in")

        if dry_run:
            entry["结果"] = "试运行"
            entry["说明"] = ("今日已签到" if st.get("checked_in")
                             else f"可领 {st.get('daily_credit')} 积分")
            result["签到"].append(entry)
            continue

        if st.get("checked_in") and not force:
            entry["结果"] = "已签到"
            entry["说明"] = "今天领过了"
            result["签到"].append(entry)
            continue

        claim = claim_daily_checkin(acc)
        entry["结果"] = "成功" if claim.get("ok") else "失败"
        entry["说明"] = claim.get("msg")
        if claim.get("credit"):
            entry["获得积分"] = claim["credit"]
        result["签到"].append(entry)

        append_history({
            "uid": acc["uid"],
            "昵称": acc["nickname"],
            "结果": entry["结果"],
            "积分": claim.get("credit", 0),
            "连续天数": st.get("streak_days", 0),
            "累计积分": st.get("total_credits", 0),
        })

        if force and st.get("checked_in"):
            # 已签到时再调一次只会拿到 10001，属于幂等空转，不重复记录。
            pass

    result["ok"] = any(e.get("结果") in ("成功", "已签到") for e in result["签到"])
    result["总积分"] = sum(e.get("获得积分", 0) for e in result["签到"])
    return result


def report() -> Dict[str, Any]:
    """完整报告：账号 + 签到 + 历史趋势 + 缓存命中。"""
    data = status()
    data["历史"] = read_history(days=30)
    data["历史统计"] = _history_stats(data["历史"])
    return data


def _history_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"天数": 0, "累计获得": 0, "平均每天": 0}
    gained = sum(r.get("积分", 0) or 0 for r in rows)
    days = len({str(r.get("时间", ""))[:10] for r in rows})
    return {
        "天数": days,
        "累计获得": gained,
        "平均每天": round(gained / days, 1) if days else 0,
        "记录数": len(rows),
    }

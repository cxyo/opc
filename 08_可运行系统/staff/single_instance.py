"""单实例闸门 —— 同一套系统只允许一个控制台在跑。

为什么必须有这个
----------------
2026-10-31 现场事故：控制台同时起了 4 个实例。

    8899  主控制台（自己的那套）        ← 该留
    8900  客户模拟副本（交付包/_客户模拟）
    8901  自己那套，但 --host 0.0.0.0 对外暴露
    8903  验证脚本跑完没关干净

结果就是用户说的那句：**"一下子很多网站都分不清哪个是主页了"**。
更危险的是 8901 —— 它监听 0.0.0.0，等于把这套系统的控制台暴露在局域网里，
而控制台里有派活台、知识库问答、技能商店文档读取这些能力。

三个问题，一个根因：**启动入口没有唯一性约束**。
  · 端口可自由指定 → 换一个端口就是"新一个网站"
  · 副本目录可独立启动 → 交付包里的副本也能拉起控制台
  · 验证脚本不回收进程 → 测完留在后台

所以本模块做三件事：

  ① **按项目根目录上锁**（不是按端口）
     锁文件写在项目自己的 data/state 下。副本目录有各自的锁，互不干扰 ——
     但同一目录再启动就会被拦下。

  ② **锁里记着端口和 PID**，第二次启动时直接告诉用户"已经有了一个，在哪个端口"，
     并提示直接打开，而不是默默开第二个。

  ③ **进程退出自动释放锁**，不留死锁。
     兜底：锁里的 PID 不在了就当锁失效（上次是强杀留下的），自动接管。

安全说明
--------
  · 锁只锁**控制台进程**，不碰业务数据
  · 锁写在项目 data/state 下，不写系统目录，不需要管理员权限
  · 识别"死锁"靠 PID 存活检查，强杀进程后能自动恢复

对外行为
--------
    python run.py console
    → 已有实例在 8899：提示 + 不启动新实例（退出码 0，不报错）
    → 无实例：正常启动，端口 8899
"""

import json
import os
import socket
import sys
import time
from typing import Any, Dict, Optional

# 锁文件名（放在项目 data/state 目录下）
LOCK_NAME = "console.lock.json"

# 测试实例用独立锁文件。
#
# 为什么需要：验证脚本要起自己的控制台实例跑测试，
# 而主控制台（8899）正占着生产锁 —— 如果共用一个锁文件，
# 验证脚本会被闸门拦下，表现为「服务起不来 / 502 / 页面空白」。
#
# 关键：测试实例必须**用不同的 data/state 目录**（run_console 会设环境变量），
# 否则它会跟主实例共享产出目录，测试互相污染。
# 这里只隔离锁文件，其余行为一致。
TEST_LOCK_NAME = "console.test.lock.json"


def _lock_name() -> str:
    """按实例身份选锁文件。

    STAFF_INSTANCE_TEST=1  → 独立锁（测试实例之间互不干扰）
    STAFF_LOCK_STRICT=1    → 强制用生产锁（验证单实例闸门本身时用）
    """
    if (os.environ.get("STAFF_INSTANCE_TEST") == "1"
            and os.environ.get("STAFF_LOCK_STRICT") != "1"):
        return TEST_LOCK_NAME
    return LOCK_NAME


def _state_dir() -> str:
    """锁文件所在目录 —— 固定为项目自己的 data/state。

    为什么不用 config["paths"]["state"]：
      测试实例会设 STAFF_INSTANCE_DIR 把 state 重定向到隔离目录，
      那样每个测试实例会写自己的锁文件 → 两个实例互不干扰 → 闸门失效。
    锁必须**固定位置 + 固定文件名**，才能真正做到"同一套系统只允许一个"。

    从 config.ROOT 推导（08_可运行系统），不受任何环境影响。
    config.ROOT 已经处理了 exe 的目录布局判定。
    """
    try:
        from . import config as cfgmod
        root = getattr(cfgmod, "ROOT", "")
        if root:
            d = os.path.join(root, "data", "state")
            os.makedirs(d, exist_ok=True)
            return d
    except Exception:
        pass
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(here, "data", "state")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        import tempfile
        return tempfile.gettempdir()
    return d


def _lock_path() -> str:
    return os.path.join(_state_dir(), _lock_name())


def _project_key() -> str:
    """项目根目录的绝对路径（用来识别"是不是同一套系统"）。"""
    try:
        from . import config as cfgmod
        cfg = cfgmod.load()
        # state 的上一级就是项目根（data/state → data → 项目根）
        root = os.path.dirname(os.path.dirname(cfg["paths"]["state"]))
        return os.path.normcase(os.path.abspath(root))
    except Exception:
        return os.path.normcase(os.path.abspath(os.getcwd()))


def _pid_alive(pid: int) -> bool:
    """进程是否还活着。Windows 用 tasklist，其他平台用 os.kill(0)。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            # 用二进制读 + errors=ignore：tasklist 输出含中文，GBK 编码，
            # 直接用 text=True 会抛 UnicodeDecodeError（不影响判断但会刷错误栈）
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            raw = (out.stdout or b"").decode("utf-8", errors="ignore")
            raw += (out.stdout or b"").decode("gbk", errors="ignore")
            return str(pid) in raw
        except Exception:
            return True  # 查不了就当活着，宁可误拦也不要误开第二个
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_alive(port: int) -> bool:
    """端口上是否有东西在听。"""
    if not port:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def read_lock() -> Optional[Dict[str, Any]]:
    """读锁。返回 None 表示没有有效的锁。"""
    p = _lock_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    pid = int(data.get("pid") or 0)
    port = int(data.get("port") or 0)
    # 死锁判定：进程没了，或端口没人听 → 视为无锁
    if not _pid_alive(pid) or (port and not _port_alive(port)):
        return None
    return data


def acquire(port: int, host: str = "127.0.0.1") -> Dict[str, Any]:
    """尝试拿锁。

    返回：
      {"ok": True,  "lock": {...}}        拿到锁，可以启动
      {"ok": False, "lock": {...}}         已有实例，返回它的信息

    自动接管死锁：如果锁里的进程已经不在了，直接覆盖。
    """
    p = _lock_path()
    existing = read_lock()
    if existing:
        return {"ok": False, "lock": existing}

    lock = {
        "pid": os.getpid(),
        "port": int(port),
        "host": host,
        "project": _project_key(),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": f"http://127.0.0.1:{int(port)}/",
    }
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(lock, f, ensure_ascii=False, indent=2)
    except Exception:
        # 写不了锁不算致命错误：降级为"允许启动"，只是失去唯一性保护
        return {"ok": True, "lock": lock, "degraded": True}
    return {"ok": True, "lock": lock}


def release() -> None:
    """释放锁。只删自己那份（PID 匹配才删）。"""
    p = _lock_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if int(data.get("pid") or 0) != os.getpid():
            return  # 不是自己的锁，别动
    except Exception:
        pass
    try:
        os.remove(p)
    except Exception:
        pass


def describe_running() -> str:
    """给用户看的一句话：现在跑着的是哪个。"""
    lock = read_lock()
    if not lock:
        return ""
    return ("控制台已经在运行了 —— 端口 %s，PID %s，启动于 %s。"
            "直接打开 %s 就行，不要再起第二个。"
            % (lock.get("port"), lock.get("pid"),
               lock.get("started_at", "?"), lock.get("url", "")))

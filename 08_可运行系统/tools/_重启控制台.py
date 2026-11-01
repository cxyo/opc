# -*- coding: utf-8 -*-
"""重启控制台（清理旧进程树 → 起新实例）。"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# 加载中文名模块
spec = importlib.util.spec_from_file_location(
    "清理工具", os.path.join(HERE, "tools", "清理工具.py"))
clean = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899


def port_busy(p):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    lock = os.path.join(HERE, "data", "state", "console.lock.json")
    pid = None
    if os.path.isfile(lock):
        try:
            with open(lock, encoding="utf-8") as f:
                pid = json.load(f).get("pid")
        except Exception:
            pass
    if pid:
        print("停止旧实例 PID %s ..." % pid)
        try:
            clean.kill_tree(int(pid))
        except Exception as e:
            print("  停止异常：%s" % e)
    # 兜底：按端口杀
    if port_busy(PORT):
        print("端口 %d 仍被占用，按端口清理" % PORT)
        try:
            clean.free_port(PORT)
        except Exception as e:
            print("  端口清理异常：%s" % e)
    time.sleep(1.5)
    if port_busy(PORT):
        print("❌ 端口仍占用")
        return 1
    print("启动新实例 …")
    # **关键**：用独立进程组启动，避免随本脚本退出而被连带杀掉
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                   | getattr(subprocess, "DETACHED_PROCESS", 0))
        kwargs["close_fds"] = True
    env = os.environ.copy()
    # 去掉代理环境变量，避免服务内部访问本机时被代理劫持
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)
    p = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "run.py"), "console",
         "--port", str(PORT)],
        cwd=HERE, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    time.sleep(7)
    if not port_busy(PORT):
        print("❌ 启动失败")
        return 1
    print("✅ 控制台已启动：http://127.0.0.1:%d/  (PID %d)" % (PORT, p.pid))
    return 0


if __name__ == "__main__":
    sys.exit(main())

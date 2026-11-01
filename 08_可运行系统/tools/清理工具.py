"""验证脚本的统一清理 —— 跑完必须把进程收干净。

为什么要有这个
----------------
2026-10-31 现场发现 4 个控制台同时在跑，其中 8903 是验证脚本留下的。

根因：`subprocess.terminate()` 只终止**直接子进程**。
`python run.py console` 会再起一个 python 进程跑真正的服务，
外层被杀了，内层还活着 → 端口一直被占。

而且更隐蔽的是：验证脚本用固定端口（8903），
如果上一个没死透，下一个脚本连端口都抢不到，测试结果不可信。

所以本模块提供：
  · `kill_tree(pid)`      杀掉进程及其所有子孙（Windows 用 taskkill /T）
  · `free_port(port)`     端口被占就杀掉占用者
  · `run_console(...)`    起一个控制台，返回带自动清理的句柄

用法：
    with run_console(PORT) as proc:
        ... 测试 ...
    # 退出 with 自动收干净
"""

import os
import socket
import subprocess
import sys
import time
from typing import List, Optional

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _port_pids(port: int) -> List[int]:
    """找出占用某端口的进程 PID 列表。"""
    pids: List[int] = []
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=15,
                creationflags=CREATE_NO_WINDOW)
            for line in (out.stdout or "").splitlines():
                if ":%d " % int(port) not in line and ":%d\t" % int(port) not in line:
                    continue
                parts = line.split()
                if len(parts) >= 5 and parts[-1].isdigit():
                    pids.append(int(parts[-1]))
        except Exception:
            pass
    else:
        try:
            out = subprocess.run(
                ["lsof", "-ti", "tcp:%d" % int(port)],
                capture_output=True, text=True, timeout=15)
            for line in (out.stdout or "").split():
                if line.strip().isdigit():
                    pids.append(int(line.strip()))
        except Exception:
            pass
    return pids


def port_free(port: int) -> bool:
    """端口是否空闲。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) != 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def kill_tree(pid: int, timeout: float = 8.0) -> bool:
    """杀掉进程及其所有子孙进程。返回是否杀成功。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
            return True
        except Exception:
            pass
    # 兜底：递归杀子进程
    try:
        out = subprocess.run(["wmic", "process", "where",
                              "ParentProcessId=%d" % pid, "get", "ProcessId"],
                             capture_output=True, text=True, timeout=timeout,
                             creationflags=CREATE_NO_WINDOW)
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                kill_tree(int(line), timeout)
    except Exception:
        pass
    try:
        os.kill(pid, 9)
        return True
    except Exception:
        return False


def free_port(port: int, wait: float = 6.0) -> bool:
    """确保端口空闲：被占就杀掉占用者。返回是否成功腾空。"""
    if port_free(port):
        return True
    for pid in _port_pids(port):
        kill_tree(pid)
    deadline = time.time() + wait
    while time.time() < deadline:
        if port_free(port):
            return True
        time.sleep(0.4)
    return port_free(port)


class _ConsoleProc:
    """起控制台的句柄。close() 会连树一起杀干净。"""

    def __init__(self, proc: subprocess.Popen, port: int,
                 inst_dir: str = ""):
        self.proc = proc
        self.port = int(port)
        self.inst_dir = inst_dir

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """等端口真的开始监听。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not port_free(self.port):
                return True
            if self.proc.poll() is not None:
                return False  # 进程已经死了
            time.sleep(0.35)
        return False

    def close(self) -> None:
        """收干净：先杀进程树，再确保端口腾空，最后删测试实例目录。"""
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=4)
                except Exception:
                    pass
                if self.proc.poll() is None:
                    kill_tree(self.proc.pid)
        except Exception:
            pass
        finally:
            try:
                if self.proc.poll() is None:
                    kill_tree(self.proc.pid)
            except Exception:
                pass
            free_port(self.port)
            # 删掉测试实例的隔离目录，别留垃圾
            if self.inst_dir:
                try:
                    import shutil
                    shutil.rmtree(self.inst_dir, ignore_errors=True)
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def run_console(port: int, cwd: str, python: Optional[str] = None,
                wait: bool = True, isolate: bool = True) -> _ConsoleProc:
    """起一个控制台实例。调用方负责 close()（或用 with）。

    isolate=True（默认）：
        给子进程设两个环境变量，让它跟生产实例彻底隔离 ——
          · STAFF_INSTANCE_TEST=1   → 用独立锁文件，不跟主实例抢锁
          · STAFF_INSTANCE_DIR=...  → state/outputs/logs 搬到独立目录
        不隔离的话，测试实例会被单实例闸门拦下（服务起不来 / 502），
        而且测试写的产出物会盖掉真实产出。

    只读目录（素材库、配置）不隔离 —— 测试要看到真实数据。
    """
    py = python or sys.executable
    # 先把端口腾干净 —— 不然测的是上一个残留实例
    free_port(port)
    cmd = [py, "run.py", "console", "--port", str(port), "--no-open"]
    env = os.environ.copy()
    if isolate:
        inst_dir = os.path.join(
            os.path.abspath(cwd), "data", "_test_instance_%d" % int(port))
        env["STAFF_INSTANCE_TEST"] = "1"
        env["STAFF_INSTANCE_DIR"] = inst_dir
    proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW)
    handle = _ConsoleProc(proc, port, env.get("STAFF_INSTANCE_DIR", ""))
    if wait:
        handle.wait_ready()
    return handle


if __name__ == "__main__":
    # 自检：python 清理工具.py 8903
    if len(sys.argv) > 1:
        p = int(sys.argv[1])
        print("端口 %d 占用者: %s" % (p, _port_pids(p)))
        print("腾空: %s" % free_port(p))
    else:
        print("用法: python 清理工具.py <端口>")

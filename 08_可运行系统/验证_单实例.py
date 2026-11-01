# -*- coding: utf-8 -*-
"""单实例闸门验证 —— 确认「不会再同时起多个控制台」。

背景（2026-10-31 现场事故）：
    4 个控制台同时在跑（8899 / 8900 / 8901 / 8903），
    用户反馈"一下子很多网站都分不清哪个是主页了"，
    其中 8901 还监听了 0.0.0.0 对外暴露。

本脚本验证四件事：
    ① 第一个实例能正常起（拿到锁）
    ② 第二个实例被拦住，**不占用新端口**，且提示指向已有实例
    ③ 端口数量始终为 1
    ④ 第一个实例退出后，锁被释放，可以重新启动
    ⑤ 强杀（SIGKILL）留下死锁时，下次启动能自动接管

跑完自动收干净进程（连进程树一起杀 + 腾空端口）。
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import importlib.util as ilu
_spec = ilu.spec_from_file_location(
    "cleanup_tool", os.path.join(HERE, "tools", "清理工具.py"))
_mod = ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
run_console, free_port, port_free = _mod.run_console, _mod.free_port, _mod.port_free

PY = sys.executable
PORT_A = 8961
PORT_B = 8962

OK = 0
FAILS = []


def check(cond, name, detail=""):
    global OK
    if cond:
        OK += 1
        print("  [✓] %s" % name)
    else:
        FAILS.append(name)
        print("  [✗] %s  %s" % (name, detail))


def start_bg(port):
    """后台起一个控制台，返回 Popen。输出重定向到临时文件（避免管道阻塞）。

    用 STAFF_INSTANCE_TEST=1 —— 两个测试实例共享同一个测试锁文件
    （console.test.lock.json），所以「第二个被拦住」照样能测出来，
    同时不会跟正在跑的主实例（8899，占着生产锁 console.lock.json）打架。
    """
    env = os.environ.copy()
    env["STAFF_INSTANCE_TEST"] = "1"
    env["STAFF_INSTANCE_DIR"] = os.path.join(
        os.path.abspath(HERE), "data", "_si_test_%d" % port)
    log = open(os.path.join(HERE, "_si_%d.log" % port), "wb")
    p = subprocess.Popen(
        [PY, "run.py", "console", "--port", str(port), "--no-open"],
        cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    p._logfile = log
    p._logpath = os.path.join(HERE, "_si_%d.log" % port)
    return p


def read_log(p):
    """读子进程输出文件。"""
    try:
        with open(p._logpath, "rb") as f:
            return f.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def main():
    print("=" * 60)
    print("  单实例闸门验证")
    print("=" * 60)

    procs = []
    try:
        print("=== ① 第一个实例 ===")
        p1 = start_bg(PORT_A)
        procs.append(p1)
        for _ in range(40):
            if not port_free(PORT_A):
                break
            time.sleep(0.4)
        check(not port_free(PORT_A), "第一个实例正常启动并监听 %d" % PORT_A)

        print("=== ② 第二个实例应被拦住 ===")
        p2 = start_bg(PORT_B)
        procs.append(p2)
        p2.wait(timeout=30)
        out = read_log(p2)
        check(p2.returncode == 0, "第二个实例以退出码 0 退出（不是报错崩溃）",
              "returncode=%s" % p2.returncode)
        check("已经有控制台在跑" in out or "不重复启动" in out,
              "提示「已经有控制台在跑」", out[:200].replace("\n", " "))
        check(str(PORT_A) in out, "提示里指明已有实例的端口", out[:200].replace("\n", " "))

        print("=== ③ 端口数量 ===")
        busy = [p for p in (PORT_A, PORT_B) if not port_free(p)]
        check(len(busy) == 1, "只有一个端口在监听", "实际监听=%s" % busy)
        check(port_free(PORT_B),
              "第二个实例确实没起服务（被拦住了）",
              "8962 仍在监听 —— 说明闸门没拦住")

        print("=== ④ 正常退出后释放锁 ===")
        try:
            p1.terminate()
            p1.wait(timeout=12)
        except Exception:
            try:
                _mod.kill_tree(p1.pid)
            except Exception:
                pass
        time.sleep(2)
        check(port_free(PORT_A), "第一个实例已退出，端口腾空")

        p3 = start_bg(PORT_A)
        procs.append(p3)
        for _ in range(40):
            if not port_free(PORT_A):
                break
            time.sleep(0.4)
        check(not port_free(PORT_A), "退出后可以重新启动（锁已释放）")

        print("=== ⑤ 强杀留死锁后能自动接管 ===")
        try:
            p3.kill()
            p3.wait(timeout=8)
        except Exception:
            pass
        time.sleep(2)
        p4 = start_bg(PORT_A)
        procs.append(p4)
        for _ in range(40):
            if not port_free(PORT_A):
                break
            time.sleep(0.4)
        check(not port_free(PORT_A), "强杀后仍可启动（死锁自动接管）")

    finally:
        for p in procs:
            try:
                if p.poll() is None:
                    _mod.kill_tree(p.pid)
            except Exception:
                pass
            try:
                p._logfile.close()
            except Exception:
                pass
        free_port(PORT_A)
        free_port(PORT_B)
        for d in ("_si_test_%d" % PORT_A, "_si_test_%d" % PORT_B):
            try:
                import shutil
                shutil.rmtree(os.path.join(HERE, "data", d), ignore_errors=True)
            except Exception:
                pass
        for port in (PORT_A, PORT_B):
            try:
                os.remove(os.path.join(HERE, "_si_%d.log" % port))
            except Exception:
                pass
        # 测试用独立锁文件，测完清掉
        try:
            lockp = os.path.join(HERE, "data", "state", "console.test.lock.json")
            if os.path.isfile(lockp):
                os.remove(lockp)
        except Exception:
            pass

    print()
    print("=" * 60)
    print("通过 %d 项，失败 %d 项" % (OK, len(FAILS)))
    if FAILS:
        for f in FAILS:
            print("  ✖ %s" % f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

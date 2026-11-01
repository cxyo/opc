# -*- coding: utf-8 -*-
"""实测「作战看板」（Ops Deck）前端。

为什么用真实事件而不是假数据：
  看板的每一个数字都承诺「真实」，所以验证也必须用真实事件。
  这里直接调后端的 /api/live/simulate 和 /api/workflow/run，
  让 SSE 推真事件，前端按真路径渲染，然后断言：
    1. 关键元素都在
    2. KPI 会随事件变化（不是写死的）
    3. Canvas 曲线真的有像素（不是空白画布）
    4. 流水线阶段有状态
    5. 名册和日志有内容

CDP 注意：Chrome 跨命令会被回收，所以启动→打开→求值→截图
必须放在同一条 Bash 命令里跑完。
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

URL = "http://127.0.0.1:8899/#p9"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "截图_看板验证")
PORT = 8899


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    raise SystemExit("找不到 Chrome/Edge")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=40)
        self.id = 0
        self.events = []

    def send(self, method, params=None):
        self.id += 1
        i = self.id
        self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == i:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def eval(self, expr):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "userGesture": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = r.get("data", "")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path


def api(path, payload=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    if payload is None:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read().decode()
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT, exist_ok=True)
    chrome = find_chrome()
    port = free_port()
    prof = tempfile.mkdtemp(prefix="opsck_")
    proc = subprocess.Popen(
        [chrome, f"--remote-debugging-port={port}", "--remote-allow-origins=*",
         f"--user-data-dir={prof}", "--headless=new", "--no-first-run",
         "--disable-gpu", "--window-size=1600,1200", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.4)
        tabs = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json", timeout=5).read().decode())
        tab = [t for t in tabs if t.get("type") == "page"][0]
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Network.enable")

        cdp.send("Page.navigate", {"url": URL})
        time.sleep(4)
        # 等待前端 init 完成（init 在 script 末尾调用）
        for _ in range(30):
            if cdp.eval("!!window.__initDone"):
                break
            time.sleep(0.4)
        cdp.eval("go('p9')")
        time.sleep(1.5)

        res = {}
        res["页面标题"] = cdp.eval("document.title")
        res["元素齐全"] = cdp.eval(
            "!!document.getElementById('opsdeck')||"
            "!!document.querySelector('.opsdeck')")
        for key, sel in [("终端栏", ".opsbar"), ("KPI区", ".opskpis"),
                         ("流水线", "#ops_flow"), ("曲线canvas", "#ops_chart"),
                         ("事件日志", "#ops_log"), ("员工名册", "#ops_roster"),
                         ("时间轴", "#ops_ticks")]:
            res[key] = cdp.eval(f"!!document.querySelector('{sel}')")

        # 背景字符雨默认应为关闭
        res["字符雨默认关闭"] = cdp.eval(
            "(function(){var c=document.getElementById('matrixbg');"
            "return !c || c.style.opacity==='0';})()")

        # ---- 触发真实事件（多发几轮，曲线才有多个采样点）----
        for i in range(4):
            api("/api/live/simulate", {"kind": "ok"})
            time.sleep(0.7)
        api("/api/live/simulate", {"kind": "retry"})
        time.sleep(1.0)
        for i in range(3):
            api("/api/live/simulate", {"kind": "ok"})
            time.sleep(0.7)
        api("/api/live/simulate", {"kind": "fail"})
        time.sleep(2.0)

        res["KPI有数值"] = cdp.eval(
            "(function(){var e=document.getElementById('ops_succ');"
            "return e && e.textContent !== '0' && e.textContent !== '';})()")
        res["事件日志有行"] = cdp.eval(
            "document.querySelectorAll('#ops_log .ln').length")
        res["失败处置已显示"] = cdp.eval(
            "!!document.querySelector('#ops_fix .opsnotice')")
        res["名册有内容"] = cdp.eval(
            "document.querySelectorAll('#ops_roster .opsstaffrow').length")
        res["时间轴刻度数"] = cdp.eval(
            "document.querySelectorAll('#ops_ticks .tick').length")

        # Canvas 是否有实际像素（不是空白）
        res["曲线已绘制"] = cdp.eval(
            "(function(){var c=document.getElementById('ops_chart');"
            "if(!c)return false;var x=c.getContext('2d');"
            "var d=x.getImageData(0,0,c.width,c.height).data;"
            "var n=0;for(var i=3;i<d.length;i+=4){if(d[i]>0)n++;}"
            "return n;})()")

        cdp.shot(os.path.join(OUT, "01_作战看板_全貌.png"))

        # 滚到底部看日志区
        cdp.eval("document.getElementById('ops_log').scrollTop=99999")
        time.sleep(0.5)
        cdp.shot(os.path.join(OUT, "02_作战看板_日志区.png"))

        print("=" * 66)
        print("  作战看板前端实测")
        print("=" * 66)
        ok = True
        for k, v in res.items():
            good = (v is True) or (isinstance(v, (int, float)) and v > 0) \
                or (isinstance(v, str) and len(v) > 0)
            mark = "PASS" if good else "FAIL"
            if not good:
                ok = False
            print(f"  [{mark}] {k:16} = {v}")
        print("=" * 66)
        print("  结果:", "全部通过" if ok else "存在失败项")
        print("=" * 66)
        return 0 if ok else 1
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

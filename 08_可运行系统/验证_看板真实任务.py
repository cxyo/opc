# -*- coding: utf-8 -*-
"""作战看板「真实任务」验证。

和「验证_作战看板.py」的区别：
  前者用 /api/live/simulate 发假事件，只验证渲染逻辑；
  这里跑**真实工作流**（真的调模型、真的写文件），
  验证在真数据下：曲线、名册、KPI、日志是否都如实反映。

这是给客户演示前的最终验收 —— 假数据好看不算数，
真数据下看板还站得住，才算做完了。
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

PORT = 8899
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "截图_看板验证")
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


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

    def eval(self, expr):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "userGesture": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r.get("data", "")))
        return path


def api(path, payload=None, timeout=120):
    url = f"http://127.0.0.1:{PORT}{path}"
    if payload is None:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode()
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def main():
    os.makedirs(OUT, exist_ok=True)
    chrome = find_chrome()
    port = free_port()
    prof = tempfile.mkdtemp(prefix="opsreal_")
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
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/#p9"})
        time.sleep(4)
        for _ in range(30):
            if cdp.eval("!!window.__initDone"):
                break
            time.sleep(0.4)
        cdp.eval("go('p9')")
        time.sleep(1.2)

        # ---- 跑真实工作流 ----
        print("→ 启动真实工作流（会真调模型）…")
        r = json.loads(api("/api/workflow/run",
                           {"theme": "tech", "with_video": False}, timeout=180))
        print("  工作流返回:", json.dumps(r, ensure_ascii=False)[:160])
        key = r.get("key")

        # 等工作流跑完（最多 6 分钟），期间持续刷新看板
        waited = 0
        done = False
        while waited < 360:
            time.sleep(5)
            waited += 5
            st = json.loads(api(f"/api/workflow/status?key={key}"))
            stt = st.get("status") or ""
            steps = st.get("steps") or []
            oks = len([x for x in steps if x.get("status") == "ok"])
            print(f"  [{waited:3d}s] status={stt} steps={oks}/{len(steps)}")
            if stt in ("done", "ok", "finished", "error", "failed", "cancelled"):
                done = (stt not in ("error", "failed"))
                break
        time.sleep(2)

        res = {}
        res["工作流跑完"] = done
        res["KPI花费"] = cdp.eval("document.getElementById('ops_balance').textContent")
        res["KPI完成数"] = cdp.eval("document.getElementById('ops_succ').textContent")
        res["KPI token"] = cdp.eval("document.getElementById('ops_tokens').textContent")
        res["KPI字数"] = cdp.eval("document.getElementById('ops_chars').textContent")
        res["日志行数"] = cdp.eval("document.querySelectorAll('#ops_log .ln').length")
        res["名册行数"] = cdp.eval("document.querySelectorAll('#ops_roster .opsstaffrow').length")
        res["流水线已激活"] = cdp.eval(
            "document.querySelectorAll('#ops_flow .opsstage.ok').length")
        res["时间轴刻度"] = cdp.eval(
            "document.querySelectorAll('#ops_ticks .tick').length")
        # 注意：顶层 const LV 不会挂到 window 上，
        # 必须直接引用标识符，window.LV 会是 undefined（这是验证脚本自己的坑）
        res["曲线采样点"] = cdp.eval(
            "(typeof LV!=='undefined' && LV.series) ? LV.series.length : -1")
        res["曲线首点"] = cdp.eval(
            "(typeof LV!=='undefined' && LV.series && LV.series.length) "
            "? JSON.stringify(LV.series[0]) : 'none'")
        res["曲线像素"] = cdp.eval(
            "(function(){var c=document.getElementById('ops_chart');"
            "if(!c)return 0;var x=c.getContext('2d');"
            "var d=x.getImageData(0,0,c.width,c.height).data;"
            "var n=0;for(var i=3;i<d.length;i+=4){if(d[i]>0)n++;}return n;})()")

        cdp.shot(os.path.join(OUT, "03_作战看板_真实任务后.png"))

        print("=" * 66)
        print("  真实任务下的看板状态")
        print("=" * 66)
        for k, v in res.items():
            print(f"  {k:16} = {v}")
        print("=" * 66)
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=6)
        except Exception:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

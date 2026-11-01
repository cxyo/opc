# -*- coding: utf-8 -*-
"""实测「企业知识库」页签（p11）前端。

为什么要实测：这一页是「通用版先跑通效果」的唯一落地口，
页面上写的每一句话都是对客户说的。所以必须验证：
  1. 元素齐全、页签能打开
  2. 资料列表真的列出来了（不是空壳）
  3. 建议问题跟客户自己的资料有关（不是写死的）
  4. 提问后答案显示、**出处也显示**
  5. 命中统计会更新
  6. 出错时（问资料里没有的东西）也要显示信息，不能白屏

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

PORT = int(os.environ.get("KB_PORT", "8903"))
URL = f"http://127.0.0.1:{PORT}/#p11"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "截图_知识库验证")


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

    # 自起服务 + 自收干净。
    # 之前这个脚本依赖 8903 上常驻的实例，自己不起服务，
    # 结果就是那个实例跑完没人管，一直占着端口（2026-10-31 现场事故）。
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "cleanup_tool", os.path.join(HERE, "tools", "清理工具.py"))
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    handle = _mod.run_console(PORT, cwd=HERE, python=sys.executable)

    port = free_port()
    prof = tempfile.mkdtemp(prefix="kbck_")
    proc = subprocess.Popen(
        [chrome, f"--remote-debugging-port={port}", "--remote-allow-origins=*",
         f"--user-data-dir={prof}", "--headless=new", "--no-first-run",
         "--disable-gpu", "--window-size=1600,1400", "about:blank"],
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

        cdp.send("Page.navigate", {"url": URL})
        time.sleep(4)
        for _ in range(30):
            if cdp.eval("!!window.__initDone"):
                break
            time.sleep(0.4)
        cdp.eval("go('p11')")
        time.sleep(2.0)

        res = {}
        res["页签可见"] = cdp.eval(
            "(function(){var e=document.getElementById('p11');"
            "return !!(e && e.classList.contains('on'));})()")
        for key, sel in [("目录路径框", "#kb_dir"), ("拖拽区", "#kb_drop"),
                         ("文档列表", "#kb_docs"), ("建议问题区", "#kb_suggest"),
                         ("提问框", "#kb_q"), ("答案区", "#kb_ans"),
                         ("命中统计区", "#kb_stats")]:
            res[key] = cdp.eval(f"!!document.querySelector('{sel}')")

        res["目录路径已填充"] = cdp.eval(
            "(function(){var e=document.getElementById('kb_dir');"
            "return !!(e && e.textContent && e.textContent.indexOf('客户资料')>=0);})()")
        res["文档条目数"] = cdp.eval(
            "document.querySelectorAll('#kb_docs .kbdoc').length")
        res["建议问题数"] = cdp.eval(
            "document.querySelectorAll('#kb_suggest .kbqchip').length")
        res["建议问题内容"] = cdp.eval(
            "(function(){var e=document.querySelector('#kb_suggest .kbqchip');"
            "return e?e.textContent:'';})()")

        # ---- 真实提问：问价格，看答案与出处 ----
        cdp.eval("kbAsk('这个打磨头多少钱')")
        time.sleep(9)
        res["答案有内容"] = cdp.eval(
            "(function(){var e=document.querySelector('#kb_ans .ansbox');"
            "return e?e.textContent.trim().length:0;})()")
        res["答案含出处标记"] = cdp.eval(
            "(function(){var e=document.querySelector('#kb_ans .ansbox');"
            "return e? (e.textContent.indexOf('[')>=0) : false;})()")
        res["出处条目数"] = cdp.eval(
            "document.querySelectorAll('#kb_ans .kbsrc').length")
        res["命中统计条目数"] = cdp.eval(
            "document.querySelectorAll('#kb_stats .kbhit').length")

        cdp.shot(os.path.join(OUT, "01_企业知识库_问答后.png"))

        # ---- 问资料里没有的东西：必须明确说没有，不能编 ----
        cdp.eval("kbAsk('公司年营业额是多少')")
        time.sleep(8)
        res["无据问题有明确提示"] = cdp.eval(
            "(function(){var e=document.querySelector('#kb_ans .ansbox');"
            "if(!e)return false;var t=e.textContent;"
            "return t.length>0 && (t.indexOf('没找到')>=0||t.indexOf('没有')>=0);})()")

        # ---- 换一个建议问题（模拟客户点 chip）----
        cdp.eval("document.querySelectorAll('#kb_suggest .kbqchip')[1].click()")
        time.sleep(9)
        res["点建议问题能出答案"] = cdp.eval(
            "(function(){var e=document.querySelector('#kb_ans .ansbox');"
            "return e?e.textContent.trim().length:0;})()")

        cdp.shot(os.path.join(OUT, "02_企业知识库_完整页.png"))

        print("=" * 66)
        print("  企业知识库页签实测")
        print("=" * 66)
        ok = True
        for k, v in res.items():
            good = (v is True) or (isinstance(v, (int, float)) and v > 0) \
                or (isinstance(v, str) and len(v) > 0)
            mark = "PASS" if good else "FAIL"
            if not good:
                ok = False
            print(f"  [{mark}] {k}: {v}")
        print("=" * 66)
        print("  结论：" + ("全部通过" if ok else "有未通过项"))
        print(f"  截图：{OUT}")
        return 0 if ok else 1
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        # 收干净服务进程树 + 腾空端口
        try:
            handle.close()
        except Exception:
            pass
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

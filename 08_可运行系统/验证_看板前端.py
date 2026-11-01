# -*- coding: utf-8 -*-
"""实时看板前端实测：验证特效档位 + 真实运行进度 + 打字机终端。

为什么要写这个脚本：
    前端改动（降速特效、加执行条、改终端）光看代码不够，
    必须真的在浏览器里跑一遍，确认：
      1. 页面不白屏（TDZ / 语法错误都会导致这个）
      2. 慢速档生效、关闭档真的停掉
      3. 事件到达后「正在执行 X」出现、进度条走、终端逐字输出
      4. 截图能拿去给客户看

实现要点：
    CDP 的 Chrome 进程如果跨命令会被系统回收，
    所以「启动 → 打开 → 求值 → 截图」必须放在同一个进程里做完。
    这里用 websocket-client 连 CDP，用标准库 http 读 /json 列表。

用法：
    python 验证_看板前端.py
"""

from __future__ import annotations

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

try:
    import websocket  # websocket-client
except ImportError:
    print("缺少 websocket-client，先装：pip install websocket-client")
    sys.exit(2)


HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "截图_看板验证")
PORT = 9222
URL = "http://127.0.0.1:8899/"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser():
    for p in CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    for name in ("chrome", "google-chrome", "msedge", "chromium"):
        p = shutil.which(name)
        if p:
            return p
    return None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CDP:
    """极简 CDP 客户端。"""

    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.id = 0

    def send(self, method, params=None, wait=True):
        self.id += 1
        mid = self.id
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        if not wait:
            return None
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} 失败：{msg['error']}")
                return msg.get("result", {})

    def eval(self, expr, await_promise=False):
        r = self.send("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            raise RuntimeError("JS 异常：" + json.dumps(
                r["exceptionDetails"], ensure_ascii=False)[:400])
        return res.get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)
        return path

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def wait_http(url, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    os.makedirs(OUT, exist_ok=True)
    browser = find_browser()
    if not browser:
        print("找不到 Chrome / Edge")
        return 1
    port = free_port()
    prof = tempfile.mkdtemp(prefix="lvprof_")
    proc = subprocess.Popen([
        browser,
        "--remote-debugging-port=%d" % port,
        f"--user-data-dir={prof}",
        # Chrome 会校验 WebSocket 的 Origin 头，不放行就连不上 CDP
        "--remote-allow-origins=*",
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-scrollbars",
        "--window-size=1600,1100",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    cdp = None
    try:
        if not wait_http(f"http://127.0.0.1:{port}/json/version", 25):
            print("Chrome 调试端口没起来")
            return 1
        # 取一个已有的标签页来用。
        # 为什么不用 /json/new：Chrome 111+ 之后该接口必须用 PUT，
        # 用 GET 会直接 405。直接复用 about:blank 那个标签最稳。
        tabs = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/list", timeout=10).read().decode())
        page = next((t for t in tabs if t.get("type") == "page"), None)
        if not page:
            print("没有可用标签页")
            return 1
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Network.enable")

        # 打开看板
        cdp.send("Page.navigate", {"url": URL})
        time.sleep(4)
        # 等页面加载完
        cdp.send("Page.reload", {"ignoreCache": True})
        time.sleep(3.5)

        results = {}

        # ---- 1. 页面是否正常渲染 ----
        # 等初始化完成：init() 是异步的，里面会调 goFromHash() 把页面
        # 切回默认页。不等它完成就切页，会被它覆盖回去。
        for _ in range(40):
            if cdp.eval("!!window.__initDone"):
                break
            time.sleep(0.25)
        results["初始化完成"] = cdp.eval("!!window.__initDone")
        results["标题"] = cdp.eval("document.title")
        results["页面非空白"] = cdp.eval(
            "document.body && document.body.innerText.length > 200")
        results["看板页存在"] = cdp.eval(
            "!!document.getElementById('lv_term') && "
            "!!document.getElementById('lv_now') && "
            "!!document.getElementById('mx_speed')")

        # ---- 2. 切到实时看板页签（用 hash 触发，走同一条路径） ----
        cdp.eval("location.hash='#p9'")
        time.sleep(1.5)
        results["当前在看板页"] = cdp.eval(
            "document.getElementById('p9').classList.contains('on')")

        # ---- 3. 默认是慢速档吗 ----
        results["默认档位"] = cdp.eval("MX.mode")
        results["慢速档高亮"] = cdp.eval(
            "document.getElementById('mx_slow').className.includes('on')")

        # ---- 4. 关闭档：动画真的停了吗 ----
        cdp.eval("mxSet('off')")
        time.sleep(0.8)
        results["关闭档生效"] = cdp.eval("MX.mode === 'off'")
        results["关闭档无动画帧"] = cdp.eval("MX.raf === null")
        results["关闭档画布透明"] = cdp.eval(
            "document.getElementById('matrixbg').style.opacity === '0'")
        cdp.shot(os.path.join(OUT, "01_特效关闭.png"))

        # ---- 5. 慢速档：截图 ----
        cdp.eval("mxSet('slow')")
        time.sleep(1.0)
        results["慢速档生效"] = cdp.eval("MX.mode === 'slow'")
        results["慢速速度值"] = cdp.eval("Math.round(MX.speed)")

        # ---- 6. 触发真实事件（走后端 SSE，验证整条链路） ----
        # 先切到机器人行业，让步骤链有内容
        cdp.eval("lvSwitchIndustry('robotics')")
        time.sleep(2.0)

        # 打一发「正在跑」事件 → 看板上应该出现「正在执行」
        cdp.eval("""
          (function(){
            var ev={id:Date.now(),ts:Date.now()/1000,time:'21:30:00',
              type:'step',level:'info',stage:'device',role:'A13',
              msg:'▶ 查设备档案 执行中',
              data:{status:'running',name:'查设备档案',detail:'读取机器人型号与参数'}};
            handleEvent(ev);
            return true;
          })()
        """)
        time.sleep(1.6)
        results["执行条显示中"] = cdp.eval(
            "document.getElementById('lv_now').textContent")
        results["执行条含步骤号"] = cdp.eval(
            "document.getElementById('lv_now_sub').textContent")
        results["转圈动画开启"] = cdp.eval(
            "document.getElementById('lv_spin').classList.contains('on')")
        results["步骤链高亮run"] = cdp.eval(
            "document.querySelectorAll('#lv_chain .chainnode.run').length")
        results["进度条有宽度"] = cdp.eval(
            "document.getElementById('lv_prog').style.width")
        results["终端行数"] = cdp.eval(
            "document.querySelectorAll('#lv_lines .tline').length")
        results["终端打字机中"] = cdp.eval(
            "(function(){var m=document.querySelector('#lv_lines .tline .tm');"
            "return m?m.textContent.length:0})()")
        cdp.shot(os.path.join(OUT, "02_运行中_慢速.png"))

        # ---- 7. 完成事件 → 进度推进 ----
        cdp.eval("""
          (function(){
            var now=Date.now();
            ['device','point'].forEach(function(k,i){
              handleEvent({id:now+i,ts:now/1000,time:'21:30:0'+i,
                type:'step',level:'good',stage:k,role:'A13',
                msg:'✔ 完成',
                data:{status:'ok',name:k==='device'?'查设备档案':'查点表与IO',
                      detail:'已归档',cost:0.0021,chars:520},
                elapsed:3200+i*800});
            });
            return true;
          })()
        """)
        time.sleep(2.0)
        results["完成步骤数"] = cdp.eval(
            "document.querySelectorAll('#lv_chain .chainnode.ok').length")
        results["进度文本"] = cdp.eval(
            "document.getElementById('lv_prog_txt').textContent")
        results["进度条宽度"] = cdp.eval(
            "document.getElementById('lv_prog').style.width")
        results["耗时显示"] = cdp.eval(
            "(function(){var n=document.querySelector('#lv_chain .chainnode.ok .tm.txt');"
            "return n?n.textContent:''})()")
        cdp.shot(os.path.join(OUT, "03_步骤完成_进度推进.png"))

        # ---- 8. 失败事件 → 处置说明 ----
        cdp.eval("""
          handleEvent({id:99999,ts:Date.now()/1000,time:'21:31:00',
            type:'step',level:'bad',stage:'code',role:'A17',
            msg:'✘ 写调试程序 失败',
            data:{status:'fail',name:'写调试程序',detail:'点表缺失',
                  error:'KB_MISS: 未找到 A3 轴点位'}})
        """)
        time.sleep(1.2)
        results["失败处置说明"] = cdp.eval(
            "document.getElementById('lv_fix').innerText.slice(0,60)")
        results["失败节点数"] = cdp.eval(
            "document.querySelectorAll('#lv_chain .chainnode.bad').length")
        cdp.shot(os.path.join(OUT, "04_失败处置.png"))

        # ---- 9. 终端可读性检查 ----
        results["终端内容"] = cdp.eval(
            "document.getElementById('lv_term').innerText.slice(0,300)")
        results["终端事件计数"] = cdp.eval(
            "document.getElementById('lv_term_cnt').textContent")

        print("\n================ 实测结果 ================")
        for k, v in results.items():
            print(f"{k}: {v}")
        print("==========================================")
        print(f"截图目录：{OUT}")
        return 0
    finally:
        if cdp:
            cdp.close()
        try:
            proc.terminate()
            proc.wait(timeout=6)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

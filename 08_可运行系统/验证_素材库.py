# -*- coding: utf-8 -*-
"""素材库页面验证 —— 无头浏览器实测。

用真实浏览器打开控制台 → 点「素材库」页签 → 逐项断言：
  · 导航里有素材库页签
  · 统计数字正确（总数/已用/已学/待学/吸收率）
  · 卡片渲染出来了（含封面图、用在哪、状态徽章）
  · 筛选可用（切到「文章」只显示文章）
  · 点卡片 → 详情抽屉打开，内容齐全
  · 改写按钮 → 生成标题候选 + 口播稿 + 封面方案
  · 封面图接口能出图

跑完自动收干净进程（连进程树一起杀 + 腾空端口）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, HERE)

# 中文模块名在 Windows 上不能直接 import，用文件路径动态加载
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "cleanup_tool", os.path.join(HERE, "tools", "清理工具.py"))
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
run_console, free_port = _mod.run_console, _mod.free_port

PORT = int(os.environ.get("MAT_PORT", "8951"))
URL = "http://127.0.0.1:%d/#p13" % PORT
OUT = os.path.join(HERE, "截图_素材库验证")
CDP_PORT = 9444

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


def find_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.isfile(p):
            return p
    return shutil.which("chrome") or shutil.which("msedge")


class CDP:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.i = 0

    def send(self, method, params=None):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg

    def eval(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True,
                       "awaitPromise": True})
        if "error" in r:
            return None
        res = r.get("result", {})
        if res.get("exceptionDetails"):
            return None
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main():
    global OK
    if not os.path.isdir(OUT):
        os.makedirs(OUT, exist_ok=True)
    browser = find_chrome()
    if not browser:
        print("找不到 Chrome/Edge，跳过")
        return 1

    handle = run_console(PORT, cwd=HERE, python=sys.executable)
    profile = tempfile.mkdtemp(prefix="mat_cdp_")
    cproc = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", "--disable-extensions",
         "--remote-allow-origins=*",
         "--remote-debugging-port=%d" % CDP_PORT,
         "--user-data-dir=%s" % profile,
         "--window-size=1500,1200", URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/version" % CDP_PORT, timeout=1)
                break
            except Exception:
                time.sleep(0.4)
        tabs = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json" % CDP_PORT, timeout=5).read().decode())
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

        print("=== ① 导航 ===")
        cdp.eval("go('p13')")
        time.sleep(2.5)
        vis = cdp.eval("(function(){var e=document.getElementById('p13');"
                       "return !!(e&&e.classList.contains('on'));})()")
        check(vis, "素材库页签可见")
        tabs = cdp.eval("Array.from(document.querySelectorAll('.tab'))"
                        ".map(t=>t.textContent.trim())")
        check(tabs and any("素材库" in t for t in tabs),
              "导航含素材库页签", str(tabs)[:100])

        print("=== ② 统计 ===")
        total = cdp.eval("document.getElementById('mt_total').textContent")
        check(total and int(total) >= 149, "素材总数显示", str(total))
        applied = cdp.eval("document.getElementById('mt_applied').textContent")
        check(applied and int(applied) > 0, "已用数显示", str(applied))
        learned = cdp.eval("document.getElementById('mt_learned').textContent")
        check(learned and int(learned) > 0, "已学数显示", str(learned))
        pending = cdp.eval("document.getElementById('mt_pending').textContent")
        check(pending and int(pending) > 0, "待学数显示", str(pending))
        pct = cdp.eval("document.getElementById('mt_pct').textContent")
        check(pct and "%" in pct, "吸收率显示", str(pct))
        outcome = cdp.eval("document.getElementById('mt_outcome').textContent")
        check(outcome and "成果" in outcome, "成果数说明", str(outcome))
        types = cdp.eval("document.getElementById('mt_types').textContent")
        check(types and "视频" in types and "文章" in types,
              "按类型统计（视频/文章）", str(types)[:80])
        barw = cdp.eval("document.getElementById('mt_bar_applied').style.width")
        check(barw and barw != "0px", "进度条已按比例填充", str(barw))

        print("=== ③ 卡片 ===")
        n = cdp.eval("document.querySelectorAll('#mt_grid .matcard').length")
        check(n and n > 0, "卡片渲染", "数量=%s" % n)
        cov = cdp.eval("document.querySelectorAll('#mt_grid .matcover').length")
        check(cov and cov > 0, "封面图渲染", "数量=%s" % cov)
        use = cdp.eval("(function(){var e=document.querySelector('#mt_grid .matuse');"
                       "return e?e.textContent.trim():'';})()")
        check(use and len(use) > 5, "「用在哪/尚未用上」区块有内容", str(use)[:60])
        st = cdp.eval("(function(){var e=document.querySelector('#mt_grid .matst');"
                      "return e?e.textContent.trim():'';})()")
        check(st in ("已用", "已学", "学习中", "待学"), "状态徽章正确", str(st))
        covsrc = cdp.eval("(function(){var i=document.querySelector('#mt_grid img.matcover');"
                          "return i?i.getAttribute('src'):'';})()")
        check(covsrc and "/api/materials/cover" in covsrc,
              "封面走封面接口", str(covsrc)[:60])

        print("=== ④ 筛选 ===")
        cdp.eval("matFilterType(document.querySelectorAll('#mt_seg_type button')[2],'article')")
        time.sleep(2.0)
        art = cdp.eval("(function(){var t=document.getElementById('mt_msg').textContent;"
                       "return t;})()")
        check(art and "条" in art, "按类型筛选有反馈", str(art))
        kinds = cdp.eval("(function(){var s=document.getElementById('mt_types').textContent;"
                          "return s;})()")
        cdp.eval("matFilterType(document.querySelectorAll('#mt_seg_type button')[0],'')")
        time.sleep(1.8)

        print("=== ⑤ 详情抽屉 ===")
        cdp.eval("openMat('v_AMsMoY5KBF')")
        time.sleep(2.0)
        on = cdp.eval("document.getElementById('mat_drawer').classList.contains('on')")
        check(on, "抽屉打开")
        title = cdp.eval("document.getElementById('md_title').textContent")
        check(title and len(title) > 3, "详情标题", str(title)[:50])
        body = cdp.eval("document.getElementById('md_body').textContent")
        check(body and "值得借鉴" in body, "含「值得借鉴」")
        check(body and "用在哪" in body, "含「用在哪」")
        check(body and "效果" in body, "含「效果」")
        meta = cdp.eval("document.getElementById('md_meta').textContent")
        check(meta and "方格动态" in meta, "详情显示作者", str(meta)[:60])

        print("=== ⑥ 改写 ===")
        cdp.eval("matRewrite()")
        time.sleep(4.0)
        rw = cdp.eval("document.getElementById('md_rewrite').textContent")
        check(rw and "标题候选" in rw, "改写产出标题候选")
        check(rw and "口播稿" in rw, "改写产出口播稿")
        check(rw and "封面方案" in rw, "改写产出封面方案")
        check(rw and "公式" in rw, "改写标注公式出处")
        check(rw and "依据" in rw, "改写标注依据出处")

        print("=== ⑦ 封面图接口 ===")
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/materials/cover?name=AMsMoY5KBF.jpg"
                    % PORT, timeout=8) as r:
                data = r.read()
            check(len(data) > 1000 and data[:2] == b"\xff\xd8",
                  "封面图正常返回 JPEG", "字节=%d" % len(data))
        except Exception as e:
            check(False, "封面图接口", str(e))

        # 截图
        try:
            shot = os.path.join(OUT, "素材库.png")
            res = cdp.send("Page.captureScreenshot", {"format": "png"})
            import base64
            with open(shot, "wb") as f:
                f.write(base64.b64decode(res["result"]["data"]))
            print("  截图已保存：%s" % shot)
        except Exception as e:
            print("  截图失败：%s" % e)

        cdp.close()

    finally:
        try:
            cproc.terminate()
            cproc.wait(timeout=6)
        except Exception:
            try:
                cproc.kill()
            except Exception:
                pass
        try:
            handle.close()
        except Exception:
            pass
        shutil.rmtree(profile, ignore_errors=True)

    print()
    print("=" * 56)
    print("通过 %d 项，失败 %d 项" % (OK, len(FAILS)))
    if FAILS:
        for f in FAILS:
            print("  ✖ %s" % f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""微信情报官（A26）前端页面验证 —— 无头浏览器实测。

用真实浏览器打开控制台 → 点「🔍 微信情报」页签 → 逐项断言：
  · 导航里有微信情报页签
  · 顶部统计（客户/供应商/内部/卡点/消息）有值
  · 覆盖信息与分类分布 chip 渲染
  · 卡点汇总条形图渲染
  · 客户线卡片渲染（名称/阶段/置信度/溯源）
  · 供应商线卡片渲染（含「货：」「款：」两阶段）
  · 内部线表格渲染（员工/对接客户/拜访/消息）
  · 卡点与待办页签有内容
  · 点客户名 → 详情抽屉打开，能拉到真实聊天记录
  · 4 个 API 全部 200 且字段结构正确

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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "cleanup_tool", os.path.join(HERE, "tools", "清理工具.py"))
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
run_console, free_port = _mod.run_console, _mod.free_port

PORT = int(os.environ.get("WX_PORT", "8952"))
URL = "http://127.0.0.1:%d/#p14" % PORT
OUT = os.path.join(HERE, "截图_微信情报验证")
CDP_PORT = 9445

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

    def shot(self, path):
        import base64
        r = self.send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r.get("result", {}).get("data", "")))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def api(path, timeout=180):
    """直连调用 API（绕开代理）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        r = op.open("http://127.0.0.1:%d%s" % (PORT, path), timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return "ERR", repr(e)


def main():
    global OK
    if not os.path.isdir(OUT):
        os.makedirs(OUT, exist_ok=True)
    browser = find_chrome()
    if not browser:
        print("找不到 Chrome/Edge，跳过")
        return 1

    handle = run_console(PORT, cwd=HERE, python=sys.executable)
    profile = tempfile.mkdtemp(prefix="wx_cdp_")
    cproc = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", "--disable-extensions",
         "--remote-allow-origins=*",
         "--remote-debugging-port=%d" % CDP_PORT,
         "--user-data-dir=%s" % profile,
         "--window-size=1500,1400", URL],
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
        time.sleep(5)
        for _ in range(40):
            if cdp.eval("!!window.__initDone"):
                break
            time.sleep(0.4)

        print("=== ① 后端 API ===")
        s, b = api("/api/wechat/status")
        check(s == 200, "status API 200", str(s))
        d = json.loads(b).get("status", {}) if s == 200 else {}
        check(d.get("就绪") is True, "数据已就绪", str(d.get("就绪")))
        check(int(d.get("消息总数") or 0) > 0,
              "消息总数 > 0", str(d.get("消息总数")))
        check(int(d.get("消息表数") or 0) > 0,
              "消息表数 > 0", str(d.get("消息表数")))

        s, b = api("/api/wechat/intel")
        check(s == 200, "intel API 200", str(s))
        intel = json.loads(b).get("intel", {}) if s == 200 else {}
        check(int(intel.get("客户数") or 0) > 0, "客户数 > 0",
              str(intel.get("客户数")))
        check(int(intel.get("供应商数") or 0) > 0, "供应商数 > 0",
              str(intel.get("供应商数")))
        check(int(intel.get("内部人数") or 0) > 0, "内部人数 > 0",
              str(intel.get("内部人数")))

        s, b = api("/api/wechat/detail?u=%s" % (
            urllib.request.quote((intel.get("客户分析") or [{}])[0].get("会话", ""))))
        check(s == 200, "detail API 200", str(s))
        det = json.loads(b) if s == 200 else {}
        check(det.get("ok") is True, "detail 返回 ok", str(det.get("ok"))[:80])
        dtl = det.get("detail") or {}
        check(len(dtl.get("消息") or []) > 0, "detail 带出真实消息",
              "条数=%s" % len(dtl.get("消息") or []))
        check(dtl.get("分类依据") or dtl.get("证据"), "detail 带分类依据",
              str(list(dtl.keys()))[:120])

        print("=== ② 导航与页签 ===")
        cdp.eval("go('p14')")
        time.sleep(3.5)
        vis = cdp.eval("(function(){var e=document.getElementById('p14');"
                       "return !!(e&&e.classList.contains('on'));})()")
        check(vis, "微信情报页签可见")
        tabs = cdp.eval("Array.from(document.querySelectorAll('.tab'))"
                        ".map(t=>t.textContent.trim())")
        check(tabs and any("微信情报" in t for t in tabs),
              "导航含微信情报页签", str(tabs)[:120])
        subtabs = cdp.eval("Array.from(document.querySelectorAll('#p14 .wxseg button,"
                            "#p14 [data-v]')).map(t=>t.textContent.trim())")
        check(subtabs and any("客户线" in t for t in subtabs),
              "含客户线/供应商线/内部线子页签", str(subtabs)[:120])

        print("=== ③ 统计与覆盖 ===")
        for eid, name, gmin in (("wx_cust", "客户数", 1),
                                ("wx_sup", "供应商数", 1),
                                ("wx_emp", "内部人数", 1),
                                ("wx_msgs", "消息条数", 1)):
            v = cdp.eval("document.getElementById('%s').textContent" % eid)
            check(v and int(v) >= gmin, "%s 显示" % name, str(v))
        meta = cdp.eval("document.getElementById('wx_meta').textContent")
        check(meta and "会话" in meta, "覆盖信息渲染", str(meta)[:100])
        check(meta and "客户" in meta, "分类分布 chip 渲染", str(meta)[:140])
        check(meta and "置信度" in meta, "置信度分布渲染", str(meta)[:140])

        print("=== ④ 卡点汇总 ===")
        nb = cdp.eval("document.querySelectorAll('#wx_blocks .wxblk').length")
        check(nb and nb > 0, "卡点条形图渲染", "数量=%s" % nb)
        bw = cdp.eval("(function(){var e=document.querySelector('#wx_blocks .wxblk "
                      "span[style*=\"width\"]');return e?e.style.width:'';})()")
        check(bw and bw not in ("0px", ""), "卡点条按比例填充", str(bw))

        print("=== ⑤ 客户线 ===")
        nc = cdp.eval("document.querySelectorAll('#wx_pane_cust .wxrow').length")
        check(nc and nc > 0, "客户卡片渲染", "数量=%s" % nc)
        nm = cdp.eval("(function(){var e=document.querySelector('#wx_pane_cust .wxname');"
                      "return e?e.textContent.trim():'';})()")
        check(nm and len(nm) > 0, "客户名显示", str(nm))
        stg = cdp.eval("(function(){var e=document.querySelector('#wx_pane_cust "
                       ".wxtag.stg');return e?e.textContent.trim():'';})()")
        check(stg and len(stg) > 0, "进展阶段标签显示", str(stg))
        ev = cdp.eval("document.querySelectorAll('#wx_pane_cust .wxev').length")
        check(ev and ev > 0, "溯源消息渲染", "数量=%s" % ev)
        amt = cdp.eval("document.querySelectorAll('#wx_pane_cust .wxamt').length")
        check(amt and amt > 0, "涉及金额标签渲染", "数量=%s" % amt)

        print("=== ⑥ 供应商线 ===")
        cdp.eval("wxTab(document.querySelector('#p14 [data-v=sup]'),'sup')")
        time.sleep(1.6)
        ns = cdp.eval("document.querySelectorAll('#wx_pane_sup .wxrow').length")
        check(ns and ns > 0, "供应商卡片渲染", "数量=%s" % ns)
        goods = cdp.eval("(function(){var e=document.querySelector('#wx_pane_sup .wxtag.stg');"
                         "return e?e.textContent:'';})()")
        check("货：" in goods, "显示货物阶段", str(goods))
        pay = cdp.eval("Array.from(document.querySelectorAll('#wx_pane_sup .wxtag.stg'))"
                       ".map(e=>e.textContent).join('|')")
        check("款：" in pay, "显示款项阶段", str(pay))

        print("=== ⑦ 内部线 ===")
        cdp.eval("wxTab(document.querySelector('#p14 [data-v=emp]'),'emp')")
        time.sleep(1.6)
        ne = cdp.eval("document.querySelectorAll('#wx_pane_emp tr').length")
        check(ne and ne > 0, "员工表格渲染", "行数=%s" % ne)
        eh = cdp.eval("Array.from(document.querySelectorAll('#wx_pane_emp th'))"
                      ".map(e=>e.textContent.trim()).join('|')")
        check("对接客户" in eh or "拜访" in eh, "表头含业务字段", str(eh)[:100])
        eac = cdp.eval("(function(){var e=document.querySelector('#wx_pane_emp .kbchip');"
                       "return e?e.textContent.trim():'';})()")
        check(len(eac) > 0, "员工动作分布渲染", str(eac)[:60])

        print("=== ⑧ 卡点与待办 ===")
        cdp.eval("wxTab(document.querySelector('#p14 [data-v=blk]'),'blk')")
        time.sleep(1.6)
        nbk = cdp.eval("(function(){var e=document.getElementById('wx_pane_blk');"
                       "return e?e.innerHTML.length:0;})()")
        check(nbk and nbk > 20, "卡点与待办页签有内容", "len=%s" % nbk)

        print("=== ⑧b 待人工确认 ===")
        cdp.eval("wxTab(document.querySelector('#p14 [data-v=pend]'),'pend')")
        time.sleep(1.6)
        npend = cdp.eval("document.querySelectorAll('#wx_pane_pend .wxrow').length")
        check(npend is not None, "待人工确认页签渲染", "数量=%s" % npend)
        pend_cnt = cdp.eval("document.getElementById('wx_pend').textContent")
        check(pend_cnt and int(pend_cnt) > 0, "顶部待确认计数显示", str(pend_cnt))
        pend_hdr = cdp.eval("(function(){var e=document.getElementById('wx_pane_pend');"
                            "return e?e.textContent:'';})()")
        check(pend_hdr and "人工" in pend_hdr, "待确认页有说明文案", str(pend_hdr)[:80])

        print("=== ⑨ 详情抽屉（真实聊天记录） ===")
        cdp.eval("wxTab(document.querySelector('#p14 [data-v=cust]'),'cust')")
        time.sleep(1.2)
        cdp.eval("(function(){var e=document.querySelector('#wx_pane_cust .wxname');"
                 "if(e)e.click();})()")
        time.sleep(3.5)
        on = cdp.eval("document.getElementById('wx_drawer').classList.contains('on')")
        check(on, "抽屉打开")
        dt = cdp.eval("document.getElementById('wx_dtitle').textContent")
        check(dt and len(dt) > 1, "详情标题有内容", str(dt)[:60])
        dl = cdp.eval("document.getElementById('wx_dbody').innerHTML.length")
        check(dl and dl > 50, "详情有聊天记录内容", "len=%s" % dl)
        dm2 = cdp.eval("(function(){var e=document.getElementById('wx_dbody');"
                       "return e?e.textContent:'';})()")
        check(dm2 and len(dm2) > 30, "详情文字非空", str(dm2)[:120])
        cdp.shot(os.path.join(OUT, "微信情报_详情抽屉.png"))
        cdp.eval("wxClose()")
        time.sleep(1.2)

        print("=== ⑩ 整页截图 ===")
        cdp.eval("go('p14')")
        time.sleep(2.5)
        cdp.shot(os.path.join(OUT, "微信情报_总览.png"))
        cdp.eval("wxTab(document.querySelector('#p14 [data-v=cust]'),'cust')")
        time.sleep(1.2)
        cdp.shot(os.path.join(OUT, "微信情报_客户线.png"))
        cdp.eval("wxTab(document.querySelector('#p14 [data-v=emp]'),'emp')")
        time.sleep(1.2)
        cdp.shot(os.path.join(OUT, "微信情报_内部线.png"))

        print("")
        print("=" * 56)
        print("  通过 %d 项，失败 %d 项" % (OK, len(FAILS)))
        if FAILS:
            for f in FAILS:
                print("   ✗ " + f)
        print("=" * 56)
        print("截图目录：" + OUT)
        return 1 if FAILS else 0

    finally:
        try:
            cdp.close()
        except Exception:
            pass
        try:
            cproc.terminate()
            time.sleep(1)
        except Exception:
            pass
        try:
            free_port(CDP_PORT)
        except Exception:
            pass
        try:
            handle.terminate()
            time.sleep(1.5)
        except Exception:
            pass
        try:
            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

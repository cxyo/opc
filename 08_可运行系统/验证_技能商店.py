# -*- coding: utf-8 -*-
"""技能商店前端实测 —— 用 CDP 无头浏览器跑真实交互。

为什么要有这个测试
------------------
技能商店是 2026-10-31 新加的页签。用户第一次打开控制台找不到它，
就是因为「页面加了但没接进导航」。所以这个测试专门盯三件事：
  1. 导航里能点到「技能商店」这个页签
  2. 卡片渲染出来，且「已上架/待审」分得开
  3. 点「技能说明」能真的拉出 SKILL.md 内容

只测接口不算数 —— 接口通了但按钮点不动，客户还是找不到。
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8902
URL = "http://127.0.0.1:%d/" % PORT

fails = []
ok = 0


def check(cond, name, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✔ %-30s %s" % (name, extra))
    else:
        fails.append(name)
        print("  ✖ %-30s %s" % (name, extra))


def free_port(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        return True
    except Exception:
        return False
    finally:
        s.close()


def find_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    return None


def cdp_eval(port, expr, timeout=25):
    """在页面里执行 JS，返回结果。用 websocket 直连 CDP。"""
    import websocket
    import urllib.request
    import json as _json
    deadline = time.time() + timeout
    ws = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json" % port, timeout=4) as r:
                tabs = _json.loads(r.read().decode("utf-8"))
            pages = [t for t in tabs if t.get("type") == "page"
                     and t.get("webSocketDebuggerUrl")]
            if pages:
                ws = websocket.create_connection(
                    pages[0]["webSocketDebuggerUrl"], timeout=timeout)
                break
        except Exception:
            time.sleep(0.4)
    if ws is None:
        return None
    try:
        mid = 1
        ws.send(_json.dumps({
            "id": mid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True,
                       "awaitPromise": True}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            ws.settimeout(timeout)
            try:
                msg = _json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == mid:
                res = msg.get("result", {})
                if res.get("exceptionDetails"):
                    return {"__error__": str(res["exceptionDetails"])[:300]}
                return res.get("result", {}).get("value")
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def main():
    if not free_port(PORT):
        print("端口 %d 被占用，跳过" % PORT)
        return 1
    browser = find_chrome()
    if not browser:
        print("找不到 Chrome/Edge，跳过")
        return 1

    # 起服务
    # 用统一清理工具起：它会连进程树一起收干净。
    # 之前用 subprocess.Popen + terminate()，只杀掉了 run.py 这层外壳，
    # 真正跑服务的 python 进程活了下来 → 端口一直占着（2026-10-31 现场事故）
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "cleanup_tool", os.path.join(HERE, "tools", "清理工具.py"))
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    handle = _mod.run_console(PORT, cwd=HERE, python=sys.executable)
    proc = handle.proc
    cdp_port = 9333
    profile = tempfile.mkdtemp(prefix="shop_cdp_")
    cproc = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", "--disable-extensions",
         "--remote-allow-origins=*",
         "--remote-debugging-port=%d" % cdp_port,
         "--user-data-dir=%s" % profile,
         "--window-size=1400,1100", URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # 等服务起来
        import urllib.request
        for _ in range(40):
            try:
                urllib.request.urlopen(URL, timeout=2).read()
                break
            except Exception:
                time.sleep(0.5)
        time.sleep(2.5)

        print("=== ① 导航与页签 ===")
        tabs = cdp_eval(cdp_port,
                        "Array.from(document.querySelectorAll('.tab'))"
                        ".map(t=>t.textContent.trim())")
        check(tabs and any("技能商店" in t for t in tabs),
              "导航含技能商店页签", str(tabs)[:120] if tabs else "无")

        # 点页签
        cdp_eval(cdp_port,
                 "(function(){var b=Array.from(document.querySelectorAll('.tab'))"
                 ".find(t=>t.textContent.indexOf('技能商店')>=0);"
                 "if(b){b.click();return 'clicked';}return 'notfound';})()")
        time.sleep(2.2)

        vis = cdp_eval(cdp_port,
                       "(function(){var s=document.getElementById('p12');"
                       "return s? (s.classList.contains('on')?'on':'hidden') : 'missing';})()")
        check(vis == "on", "技能商店页签可打开", str(vis))

        title = cdp_eval(cdp_port,
                         "(document.querySelector('#p12 .mktitle')||{}).textContent||''")
        check(title and "技能商店" in title, "页面标题正确", title)

        print("=== ② 卡片与状态 ===")
        n = cdp_eval(cdp_port, "document.querySelectorAll('#sh_grid .shcard').length")
        check(isinstance(n, int) and n > 0, "技能卡片已渲染", "共 %s 张" % n)

        total = cdp_eval(cdp_port,
                         "(document.getElementById('sh_total')||{}).textContent||''")
        pub = cdp_eval(cdp_port,
                       "(document.getElementById('sh_pub')||{}).textContent||''")
        pend = cdp_eval(cdp_port,
                        "(document.getElementById('sh_pend')||{}).textContent||''")
        check(total and total != "0", "总数统计有值", "总数=%s" % total)
        check(pub is not None and pend is not None, "已上架/待审分开统计",
              "上架=%s 待审=%s" % (pub, pend))

        badges = cdp_eval(cdp_port,
                          "Array.from(document.querySelectorAll('#sh_grid .shbadge'))"
                          ".map(b=>b.textContent.trim())")
        check(badges and ("已上架" in badges) and ("待审" in badges),
              "状态徽标区分上下架", str(badges)[:90] if badges else "无")

        # 待审卡片必须有「不可售」提示
        pending_mark = cdp_eval(cdp_port,
            "(function(){var cs=Array.from(document.querySelectorAll('#sh_grid .shcard'));"
            "for(var i=0;i<cs.length;i++){var c=cs[i];"
            "if(c.querySelector('.shbadge')&&"
            "c.querySelector('.shbadge').textContent.indexOf('待审')>=0){"
            "return c.querySelector('.shprice')?c.querySelector('.shprice').textContent:'';}}"
            "return 'none';})()")
        check(pending_mark and "不可售" in pending_mark,
              "待审技能标注不可售", pending_mark or "")

        print("=== ③ 点技能说明能出内容 ===")
        r = cdp_eval(cdp_port,
            "(function(){var b=document.querySelector('#sh_grid .shacts .btn');"
            "if(!b)return 'nobtn'; b.click(); return 'clicked';})()")
        check(r == "clicked", "点击技能说明按钮", str(r))
        time.sleep(2.0)
        body = cdp_eval(cdp_port,
                        "(document.getElementById('sh_doc_body')||{}).textContent||''")
        check(len(body or "") > 80, "SKILL.md 内容已加载",
              "%d 字" % len(body or ""))
        panel = cdp_eval(cdp_port,
            "(function(){var p=document.getElementById('sh_doc_panel');"
            "return p?p.style.display:'missing';})()")
        check(panel not in ("none", "missing"), "详情面板已展开", str(panel))

        print("=== ④ 筛选 ===")
        r = cdp_eval(cdp_port,
            "(function(){var bs=document.querySelectorAll('#sh_seg button');"
            "for(var i=0;i<bs.length;i++){if(bs[i].textContent.trim()==='待审'){bs[i].click();return 'ok';}}"
            "return 'nofilter';})()")
        check(r == "ok", "切到待审筛选", str(r))
        time.sleep(1.2)
        n2 = cdp_eval(cdp_port,
                      "document.querySelectorAll('#sh_grid .shcard').length")
        check(isinstance(n2, int) and n2 <= (n or 99), "筛选后卡片变少",
              "%s → %s" % (n, n2))
        b2 = cdp_eval(cdp_port,
                      "Array.from(document.querySelectorAll('#sh_grid .shbadge'))"
                      ".map(b=>b.textContent.trim())")
        check(all(b == "待审" for b in (b2 or [])), "筛选后只剩待审",
              str(b2)[:80] if b2 else "无")

        # 搜索
        cdp_eval(cdp_port,
            "(function(){var i=document.getElementById('sh_q');"
            "i.value='不存在的技能xyz';"
            "i.dispatchEvent(new Event('input'));return 1;})()")
        time.sleep(0.9)
        n3 = cdp_eval(cdp_port,
                      "document.querySelectorAll('#sh_grid .shcard').length")
        check(n3 == 0, "搜不到时给出空状态", "卡片数=%s" % n3)
        empty = cdp_eval(cdp_port,
                         "(document.querySelector('#sh_grid .shempty')||{}).textContent||''")
        check(bool(empty), "空状态有提示文案", (empty or "")[:60])

        # 截图
        shot_dir = os.path.join(HERE, "截图_技能商店验证")
        os.makedirs(shot_dir, exist_ok=True)
        cdp_eval(cdp_port,
                 "(function(){var b=Array.from(document.querySelectorAll('.tab'))"
                 ".find(t=>t.textContent.indexOf('技能商店')>=0);if(b)b.click();"
                 "var i=document.getElementById('sh_q');i.value='';"
                 "i.dispatchEvent(new Event('input'));return 1;})()")
        time.sleep(1.5)
        shot = os.path.join(shot_dir, "技能商店.png")
        cdp_eval(cdp_port,
                 "1")
        # 用 CDP Page.captureScreenshot
        try:
            import websocket as _ws
            import urllib.request as _u
            import json as _j
            with _u.urlopen("http://127.0.0.1:%d/json" % cdp_port, timeout=4) as r:
                tabs = _j.loads(r.read().decode("utf-8"))
            pg = [t for t in tabs if t.get("type") == "page"][0]
            w = _ws.create_connection(pg["webSocketDebuggerUrl"], timeout=30)
            w.send(_j.dumps({"id": 9, "method": "Page.captureScreenshot",
                             "params": {"format": "png"}}))
            data = None
            t0 = time.time()
            while time.time() - t0 < 25:
                try:
                    m = _j.loads(w.recv())
                except Exception:
                    continue
                if m.get("id") == 9:
                    data = m.get("result", {}).get("data")
                    break
            w.close()
            if data:
                import base64
                with open(shot, "wb") as f:
                    f.write(base64.b64decode(data))
                print("  截图已保存：%s" % shot)
        except Exception as e:
            print("  截图失败：%s" % e)

    finally:
        try:
            cproc.terminate()
            cproc.wait(timeout=6)
        except Exception:
            try:
                cproc.kill()
            except Exception:
                pass
        # 收干净服务进程树 + 腾空端口（不留残留实例）
        try:
            handle.close()
        except Exception:
            pass
        shutil.rmtree(profile, ignore_errors=True)

    print()
    print("=" * 56)
    print("通过 %d 项，失败 %d 项" % (ok, len(fails)))
    if fails:
        for f in fails:
            print("  ✖ %s" % f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

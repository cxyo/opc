#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Skill 展示页前端实测

为什么要有这个脚本
------------------
展示页是**客户能直接看见的东西**——它决定了客户对"你做的技能长什么样"
的第一印象。好看不是目的，**信息能不能一眼读懂**才是：
    · 待审 / 已上架，能不能一眼分开？
    · 一个包贵不贵、审了多少分，能不能一眼看到？
    · 点了链接能不能真的打开对应的 SKILL.md？

这三条必须真的在浏览器里跑一遍才算数。光看代码不够。

实现要点
--------
CDP 的 Chrome 进程如果跨命令会被系统回收，
所以「启动 → 打开 → 求值 → 截图」必须放在同一个进程里做完。

用法：
    python 验证_技能展示页.py
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
PROJECT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "截图_展示页验证")
GALLERY = os.path.join(PROJECT, "07_AI员工", "05_自动生成技能", "展示页.html")
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


def wait_cdp(port, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(0.4)
    return None


def evaluate(ws, expr, timeout=20):
    ws.send(json.dumps({
        "id": 1, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
    }))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            ws.settimeout(3)
            msg = json.loads(ws.recv())
        except Exception:
            continue
        if msg.get("id") == 1:
            r = msg.get("result", {}).get("result", {})
            if r.get("subtype") == "error":
                return {"__error__": r.get("description", "?")}
            return r.get("value")
    return None


def main():
    if not os.path.exists(GALLERY):
        print("✖ 展示页不存在：%s" % GALLERY)
        print("  先跑：python 08_可运行系统/tools/skill_gallery.py")
        return 1

    browser = find_browser()
    if not browser:
        print("✖ 找不到 Chrome/Edge")
        return 1

    os.makedirs(OUT, exist_ok=True)
    port = free_port()
    profile = tempfile.mkdtemp(prefix="cdp_gallery_")
    url = "file:///" + GALLERY.replace("\\", "/")

    proc = subprocess.Popen([
        browser, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        # Chrome 110+ 默认拒绝带 Origin 头的 CDP WebSocket 连接，
        # 必须显式放行，否则握手直接 403。
        "--remote-allow-origins=*",
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % profile,
        "--window-size=1280,1000", url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok, fails = 0, []
    try:
        targets = wait_cdp(port)
        if not targets:
            print("✖ CDP 没起来")
            return 1
        page = None
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                page = t
                break
        if not page:
            print("✖ 没找到 page target")
            return 1

        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20)
        time.sleep(1.5)

        print("=" * 60)
        print("Skill 展示页 · 前端实测")
        print("=" * 60)

        def check(name, expr, expect=None):
            nonlocal ok
            v = evaluate(ws, expr)
            good = (v is not None) if expect is None else (v == expect)
            if good:
                ok += 1
                print("  ✔ %-28s %s" % (name, "" if v is None else repr(v)[:70]))
            else:
                fails.append("%s（实际 %r，期望 %r）" % (name, v, expect))
                print("  ✖ %-28s 实际 %r" % (name, v))
            return v

        # 页面基本结构
        check("页面标题", "document.title", "AI 员工技能商店 · Skill 展示页")
        check("有卡片", "document.querySelectorAll('.card').length")
        check("统计块", "document.querySelectorAll('.stat').length", 4)
        check("搜索框", "!!document.getElementById('q')", True)
        check("筛选按钮组", "document.querySelectorAll('.seg button').length", 3)

        # 数据一致性：统计数字要对得上卡片数
        stat_total = evaluate(ws, "document.querySelector('.stat b').textContent")
        n_cards = evaluate(ws, "document.querySelectorAll('.card').length")
        if str(stat_total) == str(n_cards):
            ok += 1
            print("  ✔ %-28s 统计=%s 卡片=%s" % ("总数一致", stat_total, n_cards))
        else:
            fails.append("总数不一致：统计 %s vs 卡片 %s" % (stat_total, n_cards))
            print("  ✖ 总数不一致：统计 %s vs 卡片 %s" % (stat_total, n_cards))

        # 每张卡片要有名字、价格、审核分
        check("卡片有名称", "document.querySelectorAll('.card .cname').length === document.querySelectorAll('.card').length", True)
        check("卡片有价格", "document.querySelectorAll('.card .price').length === document.querySelectorAll('.card').length", True)
        check("卡片有审核分", "document.querySelectorAll('.card .score').length === document.querySelectorAll('.card').length", True)

        # 搜索交互：输入关键词后应过滤
        evaluate(ws, "(function(){var q=document.getElementById('q');"
                     "q.value='zzz-不存在的关键词';"
                     "q.dispatchEvent(new Event('input'));return true})()")
        time.sleep(0.4)
        vis_after = evaluate(ws, "Array.from(document.querySelectorAll('.card'))"
                               ".filter(c=>c.style.display!=='none').length")
        if vis_after == 0:
            ok += 1
            print("  ✔ %-28s 无匹配时全部隐藏" % "搜索过滤")
        else:
            fails.append("搜索过滤无效，仍有 %d 张可见" % vis_after)
            print("  ✖ 搜索过滤无效，仍有 %d 张可见" % vis_after)

        # 清空后应全部恢复
        evaluate(ws, "(function(){var q=document.getElementById('q');"
                     "q.value='';q.dispatchEvent(new Event('input'));return true})()")
        time.sleep(0.4)
        vis_reset = evaluate(ws, "Array.from(document.querySelectorAll('.card'))"
                               ".filter(c=>c.style.display!=='none').length")
        if vis_reset == n_cards:
            ok += 1
            print("  ✔ %-28s 全部恢复（%d）" % ("清空搜索", vis_reset))
        else:
            fails.append("清空搜索后只恢复 %d/%d" % (vis_reset, n_cards))
            print("  ✖ 清空搜索后只恢复 %d/%d" % (vis_reset, n_cards))

        # 状态筛选：点「待审」应只剩待审
        evaluate(ws, "(function(){var b=document.querySelectorAll('.seg button')[2];"
                     "b.click();return true})()")
        time.sleep(0.4)
        vis_pend = evaluate(ws, "Array.from(document.querySelectorAll('.card'))"
                              ".filter(c=>c.style.display!=='none')"
                              ".every(c=>c.getAttribute('data-status')==='待审')")
        if vis_pend is True:
            ok += 1
            print("  ✔ %-28s 只剩待审" % "状态筛选")
        else:
            fails.append("状态筛选无效")
            print("  ✖ 状态筛选无效")

        # 链接有效性：SKILL 链接应指向真实存在的文件
        href = evaluate(ws, "(document.querySelector('.card a.btn')||{}).href||''")
        if href:
            from urllib.parse import unquote, urlparse
            p = unquote(urlparse(href).path).lstrip("/")
            p = p.replace("/", os.sep)
            if os.path.exists(p):
                ok += 1
                print("  ✔ %-28s %s" % ("SKILL 链接有效", os.path.basename(p)))
            else:
                fails.append("SKILL 链接指向不存在的文件：%s" % p)
                print("  ✖ SKILL 链接失效：%s" % p)
        else:
            fails.append("卡片上没有 SKILL 链接")
            print("  ✖ 卡片上没有 SKILL 链接")

        # 截图前复位筛选到「全部」，否则截图反映的是测试点过的状态而不是默认态
        evaluate(ws, "(function(){var b=document.querySelectorAll('.seg button')[0];"
                     "b.click();return true})()")
        time.sleep(0.4)

        # 截图
        shot = os.path.join(OUT, "展示页.png")
        ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot",
                            "params": {"format": "png", "captureBeyondViewport": True}}))
        t0 = time.time()
        got = False
        while time.time() - t0 < 15:
            try:
                ws.settimeout(3)
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("id") == 2:
                with open(shot, "wb") as f:
                    f.write(base64.b64decode(msg["result"]["data"]))
                got = True
                break
        if got:
            ok += 1
            print("  ✔ %-28s %s" % ("截图", shot))
        else:
            fails.append("截图失败")

        ws.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)

    print("=" * 60)
    if fails:
        print("❌ 展示页实测失败 %d 项：" % len(fails))
        for f in fails:
            print("   · %s" % f)
        return 1
    print("✅ 展示页实测全部通过（%d 项断言）" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())

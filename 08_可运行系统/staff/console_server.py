"""控制台服务端：本地 HTTP 服务，让浏览器当指挥台。

只监听 127.0.0.1，不对外暴露（只给本机用）。
用 Python 标准库 http.server，不需要 pip install 任何东西。

启动：
    python run.py console           启动并自动打开浏览器
    python run.py console --port 8899
    python run.py console --no-open 不自动开浏览器

七页驾驶舱（全部操作都在网页上完成，不用敲命令）：
    1. 概览     —— 全局仪表盘
    2. 状态墙   —— 23 个员工的实时状态
    3. 派活台   —— 选岗位、写指令、一键下发
    4. 工作流   —— Coze 式一键成稿
    5. 产出台   —— 看正文、人工改、AI 返工
    6. 素材库   —— 图片视频画廊
    7. 人设档案 —— 人设/定位/风格/选题/经历
    8. 规划师   —— 巡检/整理/索引/搜索
    9. 系统管理 —— 初始化/恢复默认/备份/导出/导入
"""

import csv
import io
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from . import artifacts as artmod
from . import board as boardmod
from . import config as cfgmod
from . import console as consolemod
from . import delivery as delivmod
from . import industry as indmod
from . import knowledge as knowmod
from . import llm
from . import pipeline as piplmod
from . import planner as planmod
from . import profile as profmod
from . import roles as rolesmod
from . import telemetry

# 工作流运行状态（内存）：key -> 状态字典
_WF_STATES = {}

# worker 心跳：证明派活执行器还活着（排障用）
_WORKER_STATE = {"alive": False, "last_beat": 0.0, "pending": 0,
                 "last_error": ""}


def _safe(name, fallback="未命名"):
    """把标题变成安全的文件名。"""
    name = re.sub(r"[\\/:*?\"<>|]", "", (name or "").strip())
    name = re.sub(r"\s+", "_", name)
    return (name or fallback)[:40]


def _media_themes():
    """可用主题列表（延迟导入，避免启动时就加载 Pillow）。"""
    try:
        from . import media as mediamod
        return mediamod.list_themes()
    except Exception:
        return [{"key": "tech", "name": "冷调蓝"}, {"key": "dark", "name": "深色科技"},
                {"key": "light", "name": "清爽白"}, {"key": "warm", "name": "暖橙"}]


def _publish_log(cfg):
    try:
        from . import publish as pubmod
        return pubmod.read_publish_log(cfg)
    except Exception:
        return []


def _wechat_cfg(cfg):
    try:
        from . import publish as pubmod
        wc = pubmod.wechat_config(cfg)
        return {"appid": wc.get("appid", ""),
                "has_secret": bool(wc.get("secret"))}
    except Exception:
        return {"appid": "", "has_secret": False}


def _vendor_status():
    """检查图像/视频依赖是否就绪。"""
    out = {"pillow": False, "ffmpeg": False, "vendor_dir": ""}
    try:
        from . import media as mediamod
        try:
            from PIL import Image  # noqa
            out["pillow"] = True
        except Exception:
            pass
        out["ffmpeg"] = bool(mediamod._ffmpeg())
        out["vendor_dir"] = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
    except Exception:
        pass
    return out


def _live_status():
    """实时看板链路状态。

    这个字段是给「排障」用的：埋点代码里到处是 except Exception: pass
    （不能让埋点把主流程搞挂），代价是埋点可能静默失效。
    所以概览页要能一眼看出「实时链路是否正常」。
    """
    out = {"ok": False, "events": 0, "subscribers": 0, "detail": "未初始化"}
    try:
        from staff import live as livemod
        evs = livemod.BUS.recent()
        out["events"] = len(evs)
        out["subscribers"] = livemod.BUS.subscriber_count
        out["ok"] = True
        out["detail"] = "事件总线正常"
        # 最近一次 AI 调用事件
        for e in reversed(evs):
            if e.get("type") == "ai_call":
                out["last_ai"] = e.get("msg", "")[:80]
                break
    except Exception as e:
        out["detail"] = f"{type(e).__name__}: {e}"
    return out


class Handler(BaseHTTPRequestHandler):
    """处理控制台的三个请求：页面 / 下单 / 读台账。"""

    cfg = None

    def log_message(self, fmt, *args):
        pass      # 静音默认的访问日志

    # ---- 工具 ------------------------------------------------------

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _sse(self):
        """SSE 实时事件流。

        浏览器开一个 EventSource 连上来，之后后端每发生一件事，
        就往这条连接推一行 `data: {...}`，前端立刻收到。

        为什么要 SSE 而不是 WebSocket：
          本机控制台只需要「服务器 → 浏览器」单向推送，
          SSE 用浏览器原生 EventSource，不需要额外库、不需要握手，
          断线浏览器会自动重连。省掉一整套 WS 代码。
        """
        from staff import live as livemod
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = livemod.BUS.subscribe()
        try:
            # 先补发最近的历史，页面刚打开时不会是空白
            for ev in livemod.BUS.recent(limit=40):
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                                 .encode("utf-8"))
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()

            while True:
                try:
                    ev = q.get(timeout=15)
                    payload = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # 心跳，防止代理或浏览器把静默连接掐掉
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            livemod.BUS.unsubscribe(q)

    # ---- 路由 ------------------------------------------------------

    def _is_local_request(self):
        """请求是否来自本机。本机一律放行（自己在自己电脑上用）。"""
        try:
            host = self.client_address[0]
        except Exception:
            return False
        return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0")

    def _authorized(self):
        """外部访问需要口令。本机访问直接放行。"""
        if self._is_local_request():
            return True
        if not ACCESS_PASSWORD:
            return True
        # Cookie: staff_pwd=xxx
        cookie = self.headers.get("Cookie", "") or ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("staff_pwd="):
                if part.split("=", 1)[1] == ACCESS_PASSWORD:
                    return True
        # Authorization: Bearer xxx
        auth = (self.headers.get("Authorization", "") or "").strip()
        if auth.startswith("Bearer ") and auth[7:].strip() == ACCESS_PASSWORD:
            return True
        return False

    # ---- 访问口令 ----------------------------------------------------

    def _login(self):
        """校验口令，成功则下发 Cookie。"""
        data = self._body()
        pwd = str(data.get("password") or "").strip()
        if not ACCESS_PASSWORD:
            # 没设口令就不需要登录
            self._json({"ok": True, "note": "本系统未设置访问口令"})
            return
        if pwd != ACCESS_PASSWORD:
            self._json({"ok": False, "error": "口令错误"}, 403)
            return
        body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Set-Cookie",
            f"staff_pwd={ACCESS_PASSWORD}; Path=/; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(body)

    # ---- 访问口令 ----------------------------------------------------

    def _login_page(self):
        """口令输入页。"""
        html = """<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 员工系统 · 访问验证</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:radial-gradient(900px 500px at 20% -10%,#1b2450 0%,#0b1020 60%);
  color:#e8ecf8;font-family:-apple-system,"Microsoft YaHei",sans-serif;
  min-height:100vh;display:grid;place-items:center}
.card{background:#161d3a;border:1px solid #2a3358;border-radius:14px;padding:30px 32px;
  width:min(400px,90vw);box-shadow:0 8px 28px rgba(0,0,0,.4)}
h1{font-size:18px;margin-bottom:6px}
p{font-size:12.5px;color:#6b76a0;margin-bottom:18px}
input{width:100%;background:#0b1020;border:1px solid #2a3358;color:#e8ecf8;
  border-radius:9px;padding:11px 13px;font-size:14px;outline:none;margin-bottom:12px}
input:focus{border-color:#6366f1}
button{width:100%;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;border:0;
  border-radius:9px;padding:11px;font-size:14px;font-weight:600;cursor:pointer}
button:hover{filter:brightness(1.1)}
.err{color:#f87171;font-size:12px;margin-bottom:10px;min-height:16px}
.hint{margin-top:16px;font-size:11.5px;color:#6b76a0;line-height:1.7}
</style></head><body><div class="card">
<h1>🔐 AI 员工系统</h1>
<p>该系统设置了访问口令，请输入后继续。</p>
<div class="err" id="e"></div>
<input id="p" type="password" placeholder="访问口令" autofocus>
<button onclick="go()">进入系统</button>
<div class="hint">口令由部署方提供。如需开通或修改，请联系系统管理员。</div>
<script>
function go(){
  var v=document.getElementById('p').value.trim();
  if(!v){document.getElementById('e').textContent='请输入口令';return;}
  fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({password:v})}).then(function(r){return r.json()}).then(function(d){
    if(d.ok){location.href='/';}else{document.getElementById('e').textContent=d.error||'口令错误';}
  });
}
document.getElementById('p').addEventListener('keydown',function(e){
  if(e.key==='Enter')go();
});
</script></div></body></html>"""
        self._send(401, html)

    def do_GET(self):
        # 外部访问先过口令这一关
        if not self._authorized():
            self._login_page()
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._page()
        elif path == "/api/live/stream":
            self._sse()
        elif path == "/api/live/recent":
            from staff import live as livemod
            self._json({"ok": True, "events": livemod.BUS.recent(limit=120),
                        "subscribers": livemod.BUS.subscriber_count,
                        "worker": dict(_WORKER_STATE)})
        elif path == "/api/industry/list":
            self._industry_list()
        elif path == "/api/industry/current":
            self._json({"ok": True,
                        "current": indmod.current(self.cfg),
                        "pack": indmod.current_pack(self.cfg)})
        # ---- 企业知识库（老板自己的资料）----
        elif path == "/api/kb/scan":
            self._kb_scan()
        elif path == "/api/kb/stats":
            self._kb_stats()
        elif path == "/api/kb/questions":
            self._kb_questions()
        # ---- 素材库（用户喂进来的原始素材 + 学到了什么）----
        elif path == "/api/materials/overview":
            self._mat_overview()
        elif path == "/api/materials/list":
            self._mat_list()
        elif path == "/api/materials/detail":
            self._mat_detail()
        elif path == "/api/materials/rewrite":
            self._mat_rewrite()
        elif path == "/api/materials/cover":
            self._mat_cover()
        # ---- 微信情报（A26：客户/供应商/内部三线经营情报）----
        elif path == "/api/wechat/status":
            self._wx_status()
        elif path == "/api/wechat/intel":
            self._wx_intel()
        elif path == "/api/wechat/sync":
            self._wx_sync()
        elif path == "/api/wechat/detail":
            self._wx_detail()
        # ---- Skill 商店（我们自己做的技能商品目录）----
        elif path == "/api/shop/skills":
            self._shop_skills()
        elif path == "/api/shop/skill_doc":
            self._shop_skill_doc()
        elif path == "/api/market/list":
            from staff import market as mktmod
            self._json({"ok": True,
                        "categories": mktmod.categories(),
                        "providers": mktmod.list_all(self.cfg),
                        "connected": len(mktmod.connected(self.cfg))})
        elif path == "/api/ledger/balance":
            self._ledger_balance()
        elif path == "/api/ledger/export":
            self._ledger_export()
        elif path == "/api/ledger":
            self._ledger()
        elif path == "/api/board":
            self._board()
        elif path == "/api/orders":
            self._orders()
        elif path == "/api/my_orders":
            self._my_orders()
        elif path == "/api/artifacts":
            self._artifacts()
        elif path == "/api/artifact":
            self._artifact()
        elif path == "/api/edits":
            self._edits()
        # ---- 人设档案 ----
        elif path == "/api/profile":
            self._profile_get()
        elif path == "/api/profile/schema":
            self._json(profmod.schema())
        elif path == "/api/profile/completeness":
            self._json(profmod.profile_completeness(self.cfg))
        # ---- 媒体 ----
        elif path == "/api/media/gallery":
            self._media_gallery()
        elif path == "/api/media/file":
            self._media_file()
        elif path == "/api/media/themes":
            self._json({"themes": _media_themes()})
        elif path == "/api/workflow/status":
            self._workflow_status()
        # ---- 规划师 ----
        elif path == "/api/planner/inspect":
            self._planner_inspect()
        elif path == "/api/planner/search":
            self._planner_search()
        elif path == "/api/planner/backups":
            self._json({"backups": planmod.list_backups(self.cfg)})
        # ---- 系统管理 ----
        elif path == "/api/sys/info":
            self._sys_info()
        elif path == "/api/sys/publish_log":
            self._json({"rows": _publish_log(self.cfg)})
        elif path == "/api/sys/wechat":
            self._json(_wechat_cfg(self.cfg))
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    # ---- 行业包 ----

    def _industry_list(self):
        packs = indmod.list_industries(self.cfg)
        self._json({"ok": True, "current": indmod.current(self.cfg),
                    "industries": packs})

    # ---- 企业知识库（老板自己的资料）----
    #
    # 这一块是「通用版先跑通效果」的落地口：
    # 客户把产品手册/报价单/FAQ 丢进目录 → 建索引 → 直接提问 → 答案带出处。
    # 检索完全在本地（kb.py 零依赖 TF-IDF），只有命中的片段会发给模型，
    # 所以「资料不出本机」这句卖点是成立的。

    def _kb_scan(self):
        from staff import knowledge as kmod
        r = kmod.scan(self.cfg)
        # 绝对路径只留在后端，前端只显示文件名
        for d in r.get("docs", []):
            d.pop("path", None)
        self._json({"ok": True, **r})

    def _kb_stats(self):
        from staff import knowledge as kmod
        self._json({"ok": True, **kmod.stats(self.cfg)})

    def _kb_questions(self):
        from staff import knowledge as kmod
        self._json({"ok": True, "questions": kmod.demo_questions(self.cfg)})

    def _kb_ask(self):
        from staff import knowledge as kmod
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        q = (data.get("question") or "").strip()
        if not q:
            self._json({"ok": False, "error": "问题不能为空"}, 400)
            return
        try:
            top_k = int(data.get("top_k") or 5)
        except Exception:
            top_k = 5
        # 失败也返回 200：前端要把"没命中""没配 Key"和命中原文都显示出来，
        # 而不是弹一个报错框把内容藏掉
        self._json(kmod.ask(self.cfg, q, top_k=max(1, min(top_k, 10))))

    def _kb_search(self):
        from staff import knowledge as kmod
        data = self._body()
        q = (data.get("query") or "").strip()
        if not q:
            self._json({"ok": False, "error": "关键词不能为空"}, 400)
            return
        self._json(kmod.search(self.cfg, q))

    def _kb_upload(self):
        from staff import knowledge as kmod
        data = self._body()
        name = (data.get("filename") or "").strip()
        if not name:
            self._json({"ok": False, "error": "缺少文件名"}, 400)
            return
        self._json(kmod.save_upload(self.cfg, name, data.get("content_b64") or ""))

    # ---- 素材库 ------------------------------------------------------
    # 用户 2026-10-31 要求：原始素材（视频/文章/图片/链接）要能看到、
    # 要知道学到了多少、学会了什么、用在哪、什么效果。

    def _mat_overview(self):
        """素材库总览：喂了多少、学了多少、成果多少。"""
        from staff import materials as M
        try:
            st = M.stats()
            # 原始素材目录清单
            raw = {}
            try:
                for name in os.listdir(M.raw_dir()):
                    p = os.path.join(M.raw_dir(), name)
                    if os.path.isdir(p):
                        raw[name] = len([f for f in os.listdir(p)
                                         if os.path.isfile(os.path.join(p, f))])
            except Exception:
                pass
            self._json({"ok": True, "stats": st, "raw": raw,
                        "lib": M.lib_dir()})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _mat_list(self):
        """素材列表。支持按类型/状态筛选与关键词搜索。"""
        from urllib.parse import parse_qs, urlparse
        from staff import materials as M
        q = parse_qs(urlparse(self.path).query)
        kind = (q.get("kind", [""])[0] or "").strip()
        status = (q.get("status", [""])[0] or "").strip()
        kw = (q.get("q", [""])[0] or "").strip()
        try:
            items = M.search(kw, kind, status)
            out = []
            for it in items:
                out.append({
                    "id": it.get("id"),
                    "类型": it.get("类型"),
                    "标题": it.get("标题"),
                    "作者": it.get("作者"),
                    "日期": it.get("日期"),
                    "链接": it.get("链接"),
                    "类目": it.get("类目"),
                    "封面": it.get("封面"),
                    "数据": it.get("数据"),
                    "学习状态": it.get("学习状态"),
                    "值得借鉴": it.get("值得借鉴"),
                    "主要内容": it.get("主要内容"),
                    "用在哪": it.get("用在哪"),
                    "效果": it.get("效果"),
                    "待用": it.get("待用"),
                    "来源渠道": it.get("来源渠道"),
                    "主题标签": it.get("主题标签"),
                })
            self._json({"ok": True, "items": out, "total": len(out)})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _mat_detail(self):
        """单条素材详情（含使用记录与原始文件路径）。"""
        from urllib.parse import parse_qs, urlparse
        from staff import materials as M
        q = parse_qs(urlparse(self.path).query)
        iid = (q.get("id", [""])[0] or "").strip()
        if not iid:
            self._json({"ok": False, "error": "缺少 id"}, 400)
            return
        item = M.get(iid)
        if not item:
            self._json({"ok": False, "error": "找不到素材"}, 404)
            return
        self._json({"ok": True, "item": item})

    def _mat_rewrite(self):
        """把一条素材改写成我们自己的文案三件套。"""
        from urllib.parse import parse_qs, urlparse
        from staff import rewrite as rw
        q = parse_qs(urlparse(self.path).query)
        iid = (q.get("id", [""])[0] or "").strip()
        try:
            res = rw.rewrite(iid)
            self._json(res if res.get("ok")
                       else {"ok": False, "error": res.get("error")})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _mat_cover(self):
        """回传封面图（素材库目录下的图片）。"""
        from urllib.parse import parse_qs, urlparse
        from staff import materials as M
        q = parse_qs(urlparse(self.path).query)
        name = (q.get("name", [""])[0] or "").strip()
        if not name:
            self._json({"ok": False, "error": "缺少 name"}, 400)
            return
        # 只允许读 原始素材/视频封面 下的 jpg，防止路径穿越
        base = os.path.abspath(M.raw_dir("video"))
        target = os.path.abspath(os.path.join(base, os.path.basename(name)))
        if not target.startswith(base) or not os.path.isfile(target):
            self._json({"ok": False, "error": "封面不存在"}, 404)
            return
        try:
            with open(target, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    # ------------------------------------------------------------------
    # A26 微信情报官
    # ------------------------------------------------------------------

    def _wx_status(self):
        """微信数据状态：是否已解密、库指纹、缓存是否新鲜。"""
        try:
            from staff import wechat_intel as wi
            self._json({"ok": True, "status": wi.status()})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _wx_intel(self):
        """取经营情报（客户线 / 供应商线 / 内部线）。"""
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        force = (q.get("force", ["0"])[0] or "0") in ("1", "true", "yes")
        try:
            from staff import wechat_intel as wi
            data = wi.get_intel(force=force)
            self._json({"ok": bool(data.get("ok")), "intel": data})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _wx_sync(self):
        """增量同步：微信数据有变化就重算，否则走缓存。"""
        try:
            from staff import wechat_intel as wi
            data = wi.sync(verbose=False)
            self._json({"ok": bool(data.get("ok")), "intel": data})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _wx_detail(self):
        """单个会话明细：分类依据 + 命中消息 + 事实抽取。"""
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        username = (q.get("u", [""])[0] or "").strip()
        limit = int(q.get("n", ["60"])[0] or 60)
        if not username:
            self._json({"ok": False, "error": "缺少参数 u"}, 400)
            return
        try:
            from staff import wechat_intel as wi
            self._json({"ok": True, "detail": wi.session_detail(username, limit)})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _shop_skills(self):
        """技能商店列表：已上架 + 待审 + 统计。"""
        from staff import skillshop
        try:
            items = skillshop.collect()
            self._json({"ok": True, "items": items,
                        "stats": skillshop.stats(items)})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _shop_skill_doc(self):
        """读某个技能包里的文档（SKILL.md / 安装说明 / 抖音脚本）。"""
        from urllib.parse import parse_qs, urlparse
        from staff import skillshop
        q = parse_qs(urlparse(self.path).query)
        slug = (q.get("slug", [""])[0] or "").strip()
        fn = (q.get("file", ["SKILL.md"])[0] or "SKILL.md").strip()
        if not slug:
            self._json({"ok": False, "error": "缺少 slug"}, 400)
            return
        self._json(skillshop.read_doc(slug, fn))

    def do_POST(self):
        path = self.path.split("?")[0]
        # 登录接口不走鉴权（它就是用来换 Cookie 的）
        if path == "/api/login":
            self._login()
            return
        if not self._authorized():
            self._json({"ok": False, "error": "访问口令不正确或未登录"}, 401)
            return
        if path == "/api/order":
            self._order()
        elif path == "/api/live/simulate":
            self._live_simulate()
        # ---- 企业知识库 ----
        elif path == "/api/kb/index":
            self._json(knowmod.build_index(self.cfg))
        elif path == "/api/kb/ask":
            self._kb_ask()
        elif path == "/api/kb/search":
            self._kb_search()
        elif path == "/api/kb/upload":
            self._kb_upload()
        elif path == "/api/kb/reset":
            self._json(knowmod.reset(self.cfg))
        elif path == "/api/market/save":
            self._market_save()
        elif path == "/api/market/test":
            self._market_test()
        elif path == "/api/market/remove":
            self._market_remove()
        elif path == "/api/market/invoke":
            self._market_invoke()
        elif path == "/api/artifact/save":
            self._save_artifact()
        elif path == "/api/artifact/revise":
            self._revise_artifact()
        # ---- 人设档案 ----
        elif path == "/api/profile/save":
            self._profile_save()
        elif path == "/api/profile/reset":
            self._json(profmod.reset_profile(self.cfg))
        elif path == "/api/profile/import":
            self._profile_import()
        elif path == "/api/profile/import_seed":
            self._json(profmod.import_from_seed(self.cfg))
        # ---- 媒体 ----
        elif path == "/api/media/make":
            self._media_make()
        # ---- 工作流 ----
        elif path == "/api/workflow/run":
            self._workflow_run()
        # ---- 发布 ----
        elif path == "/api/publish/package":
            self._publish_package()
        elif path == "/api/publish/draft":
            self._publish_draft()
        # ---- 规划师 ----
        elif path == "/api/planner/organize":
            self._planner_organize()
        elif path == "/api/planner/plan":
            self._planner_plan()
        elif path == "/api/planner/index":
            self._planner_index()
        # ---- 系统管理 ----
        elif path == "/api/sys/init":
            self._sys_init()
        elif path == "/api/sys/restore":
            self._sys_restore()
        elif path == "/api/sys/backup":
            self._sys_backup()
        elif path == "/api/sys/export":
            self._sys_export()
        elif path == "/api/sys/import":
            self._sys_import()
        elif path == "/api/sys/wechat":
            self._sys_wechat()
        # ---- 行业包 / 交付包 ----
        elif path == "/api/industry/switch":
            self._industry_switch()
        elif path == "/api/industry/create":
            self._industry_create()
        elif path == "/api/delivery/build":
            self._delivery_build()
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    # ---- 行业包 ----

    def _industry_list(self):
        packs = indmod.list_industries(self.cfg)
        self._json({"ok": True, "current": indmod.current(self.cfg),
                    "industries": packs})

    # ---- 实时看板演示 ----

    def _live_simulate(self):
        """模拟一次成功 / 失败 / 自动重试。

        为什么要这个：
          客户演示最关心「失败了怎么办」，
          但真等一次真实失败不知道要多久，也不一定复现。
          所以做一个可控的演示开关：现场点一下，
          看板立刻按真实的事件格式发出失败事件，
          前端展示的处置说明跟真出问题时一模一样。
        """
        data = self._body()
        kind = (data.get("kind") or "fail").strip()
        try:
            from staff import live as livemod
            chain = indmod.chain(self.cfg)
            names = indmod.role_names(self.cfg)
            step = chain[0] if chain else {}
            role = step.get("role") or "A13"
            sname = step.get("name") or "第一步"

            if kind == "ok":
                livemod.emit_step(step.get("key", "step1"), sname, "ok",
                                  msg=f"✔ {sname} 完成",
                                  role=role,
                                  data={"detail": "演示：正常完成",
                                        "chars": 320, "cost": 0.0042,
                                        "tokens": 1268,
                                        "file": "demo_output.md"})
            elif kind == "retry":
                livemod.emit_step(step.get("key", "step1"), sname, "retry",
                                  msg=f"↻ {sname} 失败，正在自动重试",
                                  role=role,
                                  data={"detail": "演示：第 1 次调用超时",
                                        "attempt": 1, "max": 2,
                                        "error": "ReadTimeout: 模型网关 30 秒未返回"})
            else:
                livemod.emit_step(step.get("key", "step1"), sname, "fail",
                                  msg=f"✘ {sname} 失败",
                                  role=role,
                                  data={"detail": "演示：知识库检索没有命中",
                                        "error": "KB_MISS: 知识库中没有找到对应的设备档案",
                                        "chars": 0})
            self._json({"ok": True, "kind": kind, "step": sname, "role": role})
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    # ---- 账单对账 ----

    def _deepseek_keys(self):
        """按优先级收集候选 DeepSeek 官方 Key。

        为什么不能只读 config：
          配置里的 api_key 现在是 new-api 网关令牌（一把 Key 管多个渠道），
          拿它去调 api.deepseek.com 官方接口会 401。
          但网关的渠道表里存着真正的官方 Key（渠道 1 = DeepSeek 模板），
          从那儿取才对得上官方账单。

        返回 (key, 来源说明)
        """
        cands = []

        # 1) 配置里的直连 Key（老配置可能是官方 Key）
        k = (self.cfg.get("api_key") or "").strip()
        if k:
            cands.append((k, "工程配置 config.json"))

        # 2) new-api 网关的 DeepSeek 渠道 Key
        try:
            import sqlite3
            import subprocess
            import tempfile
            import os
            db = os.path.join(tempfile.gettempdir(), "newapi_ledger.db")
            subprocess.run(
                ["docker", "cp", "ai-hub-new-api:/data/one-api.db", db],
                capture_output=True, timeout=25)
            for suffix in ("-wal", "-shm"):
                subprocess.run(
                    ["docker", "cp", "ai-hub-new-api:/data/one-api.db" + suffix,
                     db + suffix],
                    capture_output=True, timeout=25)
            if os.path.exists(db):
                con = sqlite3.connect(db)
                try:
                    # 按 base_url 判定，不靠 type 数字（new-api 各版本 type 值会变）
                    rows = con.execute(
                        "SELECT name, key, base_url FROM channels "
                        "WHERE status=1 AND key IS NOT NULL AND key<>''").fetchall()
                    for nm, ck, bu in rows:
                        ck = (ck or "").strip()
                        if not ck:
                            continue
                        bu = (bu or "").lower()
                        if "deepseek" not in bu and "deepseek" not in nm.lower():
                            continue
                        cands.append((ck, f"new-api 网关渠道「{nm}」"))
                finally:
                    con.close()
        except Exception:
            pass

        return cands

    def _ledger_balance(self):
        """查 DeepSeek 账号余额 + 三方对账。

        为什么需要：
          模型平台账单是账号级的，本地台账是项目级的，
          两边数字不一样是常态（网关、Docker、其它脚本都用同一把 Key）。
          这里把「本地台账 / 网关侧记录 / 官方余额」三个口径摆在一起，
          客户问起来时能当场说清楚差在哪，而不是互相怀疑。
        """
        local_cost = 0.0
        local_calls = 0
        local_tokens = 0
        recs = []
        try:
            from staff import telemetry as telmod
            recs = telmod.read_all(self.cfg["paths"]["logs"])
            for r in recs:
                local_cost += float(r.get("cost_yuan") or 0)
                local_calls += 1
                local_tokens += int(r.get("tokens") or 0)
        except Exception:
            pass

        base = {"ok": True, "balances": [], "local_calls": local_calls,
                "local_tokens": local_tokens,
                "local_cost": round(local_cost, 6),
                "tried": [], "gw": None}

        for key, src in self._deepseek_keys():
            try:
                import urllib.request as _u
                req = _u.Request("https://api.deepseek.com/user/balance",
                                 headers={"Authorization": "Bearer " + key})
                with _u.urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode("utf-8", "ignore")
                data = json.loads(raw)
                infos = data.get("balance_infos") or []
                out = []
                for b in infos:
                    out.append({
                        "currency": b.get("currency", ""),
                        "total": b.get("total_balance", "0"),
                        "granted": b.get("granted_balance", "0"),
                        "topped_up": b.get("topped_up_balance", "0"),
                    })
                if out:
                    base["balances"] = out
                    base["balance_source"] = src
                    base["ok"] = True
                    break
                base["tried"].append(src + "：返回里没有余额信息")
            except Exception as e:
                base["tried"].append(f"{src}：{type(e).__name__}: {e}")

        if not base["balances"]:
            base["ok"] = False
            base["error"] = ("没找到可用的 DeepSeek 官方 Key。"
                             + ("试过：" + "；".join(base["tried"]) if base["tried"] else ""))

        # 网关侧汇总（能读到就带上，用于逐笔对账）
        try:
            import sqlite3
            import subprocess
            import tempfile
            import os
            db = os.path.join(tempfile.gettempdir(), "newapi_ledger.db")
            if not os.path.exists(db):
                subprocess.run(
                    ["docker", "cp", "ai-hub-new-api:/data/one-api.db", db],
                    capture_output=True, timeout=25)
            if os.path.exists(db):
                con = sqlite3.connect(db)
                try:
                    row = con.execute(
                        "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), "
                        "COALESCE(SUM(completion_tokens),0), COALESCE(SUM(quota),0) "
                        "FROM logs WHERE type=2").fetchone()
                    gw = {"calls": row[0], "prompt": row[1],
                          "completion": row[2], "quota": row[3],
                          "tokens": row[1] + row[2]}
                    # 网关第一条记录的时间 = 网关启用时刻。
                    # 之前的调用是直连官方 API，不在网关账上，
                    # 不按时间窗口对齐就直接比大小，一定会得出「差很多」的假结论。
                    first = con.execute(
                        "SELECT datetime(MIN(created_at),'unixepoch','localtime') "
                        "FROM logs WHERE type=2").fetchone()[0]
                    gw["since"] = first
                    base["gw"] = gw

                    # 同窗口对齐：只统计网关启用之后的本地调用
                    if first:
                        cut = first[:19]
                        after = [r for r in recs
                                 if (r.get("ts") or "")[:19] >= cut]
                        atok = sum(int(r.get("tokens") or 0) for r in after)
                        base["aligned"] = {
                            "since": first,
                            "local_calls": len(after),
                            "local_tokens": atok,
                            "gw_tokens": gw["tokens"],
                            "diff": atok - gw["tokens"],
                            "diff_pct": (round(
                                abs(atok - gw["tokens"]) / max(gw["tokens"], 1) * 100, 4)),
                            "before_calls": len(recs) - len(after),
                            "before_tokens": local_tokens - atok,
                        }
                finally:
                    con.close()
        except Exception as e:
            base["gw_error"] = f"{type(e).__name__}: {e}"

        self._json(base)

    def _ledger_export(self):
        """导出本地台账 CSV，方便跟平台账单逐行对。"""
        try:
            from staff import telemetry as telmod
            recs = telmod.read_all(self.cfg["paths"]["logs"])
            cols = ["ts", "role", "model", "tier", "status", "tokens",
                    "prompt_tokens", "completion_tokens", "cost_yuan",
                    "output_chars", "duration_s", "output_file", "error"]
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(cols)
            for r in recs:
                w.writerow([str(r.get(c, "")).replace("\n", " ") for c in cols])
            body = ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM，Excel 直接打开不乱码
            fn = f"本地台账_{time.strftime('%Y%m%d_%H%M%S')}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="ledger.csv"; '
                             f"filename*=UTF-8''{fn}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._json({"ok": False, "error": f"导出失败：{e}"}, 500)

    # ---- AI 超市 ----
    def _market_save(self):
        from staff import market as mktmod
        data = self._body()
        key = (data.get("key") or "").strip()
        if not key:
            self._json({"ok": False, "error": "缺少平台 key"}, 400)
            return
        res = mktmod.save_config(self.cfg, key, data.get("fields") or {},
                                 enabled=bool(data.get("enabled", True)))
        if res.get("ok"):
            try:
                from staff import live as livemod
                p = mktmod.get_provider(key) or {}
                livemod.emit("market",
                             f"接入平台：{p.get('name', key)}", "good",
                             data={"key": key})
            except Exception:
                pass
        self._json(res)

    def _market_test(self):
        from staff import market as mktmod
        data = self._body()
        key = (data.get("key") or "").strip()
        res = mktmod.test_connection(self.cfg, key)
        self._json(res)

    def _market_remove(self):
        from staff import market as mktmod
        data = self._body()
        key = (data.get("key") or "").strip()
        self._json(mktmod.remove_config(self.cfg, key))

    def _market_invoke(self):
        """调用一个已接入的平台（AI 员工用它的入口）。"""
        from staff import market as mktmod
        data = self._body()
        key = (data.get("key") or "").strip()
        text = data.get("text") or ""
        if not key:
            self._json({"ok": False, "error": "缺少平台 key"}, 400)
            return
        t0 = time.time()
        res = mktmod.invoke(self.cfg, key, text, data.get("extra") or {})
        res["elapsed"] = int((time.time() - t0) * 1000)
        try:
            from staff import live as livemod
            p = mktmod.get_provider(key) or {}
            livemod.emit("market",
                         (f"调用 {p.get('name', key)} "
                          f"{'成功' if res.get('ok') else '失败'}"),
                         "good" if res.get("ok") else "bad",
                         data={"key": key, "elapsed": res["elapsed"]})
        except Exception:
            pass
        self._json(res)

    def _industry_switch(self):
        data = self._body()
        key = (data.get("key") or "").strip()
        if not key:
            self._json({"ok": False, "error": "缺少行业 key"}, 400)
            return
        res = indmod.switch(self.cfg, key)
        if res.get("ok"):
            try:
                from staff import live as livemod
                # 切换行业后，岗位显示名跟着换（头盔厂的 A13 变「设备档案员」）
                livemod.set_role_names(indmod.role_names(self.cfg))
                livemod.emit("industry", f"已切换行业方案：{res.get('name', key)}", "good",
                             data={"key": key})
            except Exception:
                pass
        self._json(res)

    def _industry_create(self):
        data = self._body()
        key = (data.get("key") or "").strip()
        name = (data.get("name") or "").strip()
        if not key or not name:
            self._json({"ok": False, "error": "需要 key 和 name"}, 400)
            return
        res = indmod.create_pack(self.cfg, key, name,
                                 icon=data.get("icon") or "📦",
                                 desc=data.get("desc") or "",
                                 roles=data.get("roles") or [])
        self._json(res)

    def _delivery_build(self):
        data = self._body()
        out_root = (data.get("out_root") or "").strip() or None
        try:
            res = delivmod.build_delivery(self.cfg, out_root)
        except Exception as e:
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return
        if res.get("ok"):
            try:
                from staff import live as livemod
                livemod.emit("delivery", "客户交付包已生成", "good",
                             data={"dir": res.get("dir"),
                                   "stats": res.get("stats")})
            except Exception:
                pass
        self._json(res)

    # ---- 读 JSON body ------------------------------------------------

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else "{}"
            return json.loads(raw)
        except Exception as e:
            return {"__error__": f"请求解析失败：{e}"}

    # ---- 下单 --------------------------------------------------------

    def _order(self):
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return

        code = (data.get("code") or "").strip()
        task = (data.get("task") or "").strip()
        if not code or not task:
            self._json({"ok": False, "error": "缺少岗位或任务"}, 400)
            return

        reg = rolesmod.RoleRegistry(self.cfg["paths"]["roles"])
        if reg.get(code) is None:
            self._json({"ok": False, "error": f"没有这个岗位：{code}"}, 400)
            return

        rec = consolemod.submit_order(
            self.cfg["paths"]["state"], code, task,
            tier=data.get("tier") or "create",
            use_kb=bool(data.get("use_kb", True)),
            save_as=(data.get("save_as") or "").strip(),
            temp=float(data.get("temp") or 0.7),
        )
        self._json({"ok": True, "id": rec["id"], "code": code})

    # ---- 产出工作台 --------------------------------------------------

    def _artifacts(self):
        """列出全部产出文件。"""
        self._json(artmod.list_outputs(self.cfg))

    def _artifact(self):
        """读取一个产出文件的完整内容。"""
        from urllib.parse import parse_qs, urlparse
        rel = parse_qs(urlparse(self.path).query).get("path", [""])[0]
        r = artmod.read_output(self.cfg, rel)
        self._json(r, 200 if r.get("ok") else 400)

    def _edits(self):
        """人工修改台账。"""
        self._json({"edits": artmod.list_edits(self.cfg)})

    def _save_artifact(self):
        """人工直接改写产出文件。"""
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        r = artmod.save_output(
            self.cfg,
            (data.get("path") or "").strip(),
            data.get("content") or "",
            note=(data.get("note") or "人工修改").strip() or "人工修改",
        )
        self._json(r, 200 if r.get("ok") else 400)

    def _revise_artifact(self):
        """人工提意见 → 让 AI 返工。"""
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        r = artmod.revise_output(
            self.cfg,
            (data.get("path") or "").strip(),
            (data.get("instruction") or "").strip(),
            code=(data.get("code") or "").strip() or None,
            tier=data.get("tier") or "create",
            use_kb=bool(data.get("use_kb", True)),
            temp=float(data.get("temp") or 0.7),
        )
        self._json(r, 200 if r.get("ok") else 400)

    # ---- 各页数据 --------------------------------------------------

    def _page(self):
        b = boardmod.Board(self.cfg)
        html = consolemod.render_console(self.cfg, b.build())
        self._send(200, html)

    # ---- 人设档案 ---------------------------------------------------

    def _profile_get(self):
        self._json({"profile": profmod.load_profile(self.cfg),
                    "completeness": profmod.profile_completeness(self.cfg)})

    def _profile_save(self):
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        prof = data.get("profile")
        if not isinstance(prof, dict):
            self._json({"ok": False, "error": "档案格式不对"}, 400)
            return
        r = profmod.save_profile(self.cfg, prof)
        if r.get("ok"):
            r["completeness"] = profmod.profile_completeness(self.cfg)
        self._json(r, 200 if r.get("ok") else 400)

    def _profile_import(self):
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        payload = data.get("payload")
        if payload is None:
            payload = data.get("content")
        if not payload:
            self._json({"ok": False, "error": "没有收到内容"}, 400)
            return
        r = profmod.import_profile(self.cfg, payload,
                                   mode=data.get("mode") or "merge")
        if r.get("ok"):
            r["completeness"] = profmod.profile_completeness(self.cfg)
        self._json(r, 200 if r.get("ok") else 400)

    # ---- 媒体 -------------------------------------------------------

    def _media_gallery(self):
        """扫描产出目录里的图片和视频。"""
        from . import media as mediamod
        root = self.cfg["paths"]["outputs"]
        items = []
        exts = (".png", ".jpg", ".jpeg", ".gif", ".mp4", ".webm", ".mov")
        if os.path.isdir(root):
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d != "vendor"]
                for fn in filenames:
                    if not fn.lower().endswith(exts):
                        continue
                    full = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(full)
                        size = st.st_size
                        mt = time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(st.st_mtime))
                    except OSError:
                        size, mt = 0, ""
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    items.append({
                        "rel": rel, "name": fn,
                        "kind": "video" if fn.lower().endswith(
                            (".mp4", ".webm", ".mov")) else "image",
                        "size": size, "mtime": mt,
                        "folder": os.path.dirname(rel) or "(根目录)",
                    })
        items.sort(key=lambda x: x["mtime"], reverse=True)
        self._json({"items": items, "total": len(items),
                    "themes": _media_themes()})

    def _media_file(self):
        """把图片/视频直传给浏览器预览。"""
        from urllib.parse import parse_qs, urlparse
        rel = parse_qs(urlparse(self.path).query).get("path", [""])[0]
        root = os.path.realpath(self.cfg["paths"]["outputs"])
        if not rel:
            self._json({"ok": False, "error": "缺少路径"}, 400)
            return
        target = os.path.realpath(os.path.join(root, rel))
        if not target.startswith(root + os.sep):
            self._json({"ok": False, "error": "路径越界"}, 400)
            return
        if not os.path.isfile(target):
            self._json({"ok": False, "error": "文件不存在"}, 400)
            return
        ext = os.path.splitext(target)[1].lower()
        ctype = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".mp4": "video/mp4", ".webm": "video/webm",
                 ".mov": "video/quicktime"}.get(ext, "application/octet-stream")
        try:
            with open(target, "rb") as f:
                data = f.read()
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 400)
            return
        self._send(200, data, ctype)

    def _media_make(self):
        """给一篇产出配图配视频（或只生成封面）。"""
        from . import media as mediamod
        from . import publish as pubmod
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        rel = (data.get("path") or "").strip()
        theme = data.get("theme") or "tech"
        with_video = bool(data.get("with_video"))
        prof = profmod.load_profile(self.cfg)
        brand = (prof.get("persona") or {}).get("name") or ""

        # 正文来源：指定文件，或直接给 markdown 文本
        md = data.get("markdown") or ""
        if not md and rel:
            r = artmod.read_output(self.cfg, rel)
            if not r.get("ok"):
                self._json({"ok": False, "error": r.get("error")}, 400)
                return
            md = r.get("content") or ""

        if not md.strip():
            self._json({"ok": False, "error": "没有内容可以配图"}, 400)
            return

        title = data.get("title") or pubmod.extract_title(md)
        out = os.path.join(self.cfg["paths"]["outputs"], "视觉素材",
                           _safe(title)[:30])
        try:
            suite = mediamod.make_media_suite(md, out, title=title,
                                              theme=theme, brand=brand,
                                              with_video=with_video)
        except Exception as e:
            self._json({"ok": False, "error": f"生成失败：{e}"}, 500)
            return
        self._json({"ok": True, "cover": suite.get("cover"),
                    "cards": suite.get("cards"), "video": suite.get("video"),
                    "dir": suite.get("dir"), "title": title})

    # ---- 工作流 -----------------------------------------------------

    def _workflow_run(self):
        """一键成稿。耗时较长，用后台线程跑，前端轮询进度。"""
        from . import workflow as wfmod
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return

        key = f"wf_{int(time.time() * 1000) % 1000000}"
        state = {"key": key, "status": "running", "steps": [],
                 "message": "正在启动…", "result": None}
        _WF_STATES[key] = state

        topic = (data.get("topic") or "").strip()
        theme = data.get("theme") or "tech"
        with_video = bool(data.get("with_video"))

        def progress(step_key, status, detail=""):
            for s in state["steps"]:
                if s["key"] == step_key:
                    s["status"] = status
                    s["detail"] = detail
                    return
            state["steps"].append({"key": step_key, "status": status,
                                   "detail": detail})

        def run():
            state["message"] = "AI 员工正在干活…"
            try:
                wf = wfmod.Workflow(self.cfg)
                res = wf.run_article(topic=topic, theme=theme,
                                     with_video=with_video,
                                     progress=progress)
                state["result"] = res
                state["status"] = "done"
                state["message"] = "完成"
            except Exception as e:
                state["status"] = "error"
                state["message"] = f"失败：{e}"

        threading.Thread(target=run, daemon=True).start()
        self._json({"ok": True, "key": key, "message": "已启动"})

    def _workflow_status(self):
        # 不带 key 时列出全部工作流任务状态，而不是回 404
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        key = ""
        for part in q.split("&"):
            if part.startswith("key="):
                key = part[4:]
        if not key:
            items = [{"key": k, "state": (v.get("state") if isinstance(v, dict) else str(v))}
                     for k, v in _WF_STATES.items()]
            self._json({"ok": True, "tasks": items, "count": len(items)})
            return
        st = _WF_STATES.get(key)
        if not st:
            self._json({"ok": False, "error": "没有这个任务"}, 404)
            return
        self._json(st)

    # ---- 发布 -------------------------------------------------------

    def _publish_package(self):
        """生成发布包（HTML + 封面 + 图卡 + 指引）。"""
        from . import media as mediamod
        from . import publish as pubmod
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        rel = (data.get("path") or "").strip()
        md = data.get("markdown") or ""
        if not md and rel:
            r = artmod.read_output(self.cfg, rel)
            if not r.get("ok"):
                self._json({"ok": False, "error": r.get("error")}, 400)
                return
            md = r.get("content") or ""
        if not md.strip():
            self._json({"ok": False, "error": "没有内容"}, 400)
            return

        prof = profmod.load_profile(self.cfg)
        brand = (prof.get("persona") or {}).get("name") or ""
        title = data.get("title") or pubmod.extract_title(md)
        out = os.path.join(self.cfg["paths"]["outputs"], "发布包",
                           _safe(title)[:30])
        try:
            pkg = pubmod.build_package(md, out, title=title,
                                       theme=data.get("theme") or "tech",
                                       brand=brand,
                                       with_video=bool(data.get("with_video")))
        except Exception as e:
            self._json({"ok": False, "error": f"生成失败：{e}"}, 500)
            return
        pubmod.log_publish(self.cfg, pkg["meta"], status="待发布",
                           note="控制台生成")
        self._json({"ok": True, "dir": pkg["dir"], "html": pkg["html"],
                    "cover": pkg["cover"], "cards": pkg["cards"],
                    "meta": pkg["meta"], "guide": pkg["guide"]})

    def _publish_draft(self):
        """推到公众号草稿箱（需 appid/secret）。"""
        from . import publish as pubmod
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        wc = pubmod.wechat_config(self.cfg)
        if not wc.get("appid") or not wc.get("secret"):
            self._json({"ok": False,
                        "error": "还没填公众号 appid/secret，去「系统管理」页填一下"},
                       400)
            return
        rel = (data.get("path") or "").strip()
        md = data.get("markdown") or ""
        if not md and rel:
            r = artmod.read_output(self.cfg, rel)
            if not r.get("ok"):
                self._json({"ok": False, "error": r.get("error")}, 400)
                return
            md = r.get("content") or ""
        if not md.strip():
            self._json({"ok": False, "error": "没有内容"}, 400)
            return
        try:
            token = pubmod.get_access_token(wc["appid"], wc["secret"])
            thumb = ""
            cover = data.get("cover") or ""
            if cover and os.path.exists(cover):
                thumb = pubmod.upload_image(token, cover)
            html = pubmod.md_to_html(md)
            title = pubmod.extract_title(md)
            digest = pubmod.extract_digest(md)
            res = pubmod.create_draft(token, title, html,
                                       thumb_media_id=thumb,
                                       digest=digest)
            pubmod.log_publish(self.cfg,
                               {"title": title, "chars": len(md),
                                "cover": os.path.basename(cover or ""),
                                "cards": []},
                               status="已进草稿箱", note="API 推送")
            self._json({"ok": True, "media_id": res.get("media_id"),
                        "message": "已推到草稿箱，去公众号后台点发布"})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    # ---- 规划师 -----------------------------------------------------

    def _planner_inspect(self):
        self._json(planmod.inspect(self.cfg))

    def _planner_plan(self):
        data = self._body()
        if "__error__" in data:
            data = {}
        r = planmod.plan_organize(self.cfg,
                                  keep_backups=int(data.get("keep_backups") or 3),
                                  archive_days=int(data.get("archive_days") or 30))
        self._json(r)

    def _planner_organize(self):
        data = self._body()
        if "__error__" in data:
            data = {}
        actions = data.get("actions") or []
        if not actions:
            self._json({"ok": False, "error": "没有要执行的方案"}, 400)
            return
        r = planmod.apply_plan(self.cfg, actions,
                               dry_run=bool(data.get("dry_run")))
        self._json(r)

    def _planner_index(self):
        self._json(planmod.build_index(self.cfg))

    def _planner_search(self):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
        self._json(planmod.search_index(self.cfg, q))

    # ---- 系统管理 ---------------------------------------------------

    def _sys_info(self):
        cfg = self.cfg
        paths = cfg["paths"]
        base_url = (cfg.get("base_url") or "").rstrip("/")
        # 判断模型调用走的是直连还是本机网关，用于概览页一眼看清
        if base_url.endswith(":3001/v1"):
            llm_route = {"mode": "gateway", "label": "模型网关 new-api",
                         "target": base_url, "ok": True}
        elif base_url:
            llm_route = {"mode": "direct", "label": "直连 " + base_url.split("/")[2],
                         "target": base_url, "ok": bool(cfg.get("api_key"))}
        else:
            llm_route = {"mode": "none", "label": "未配置", "target": "", "ok": False}
        info = {
            "project_root": os.path.dirname(paths["outputs"]),
            "outputs": paths["outputs"],
            "state": paths["state"],
            "logs": paths["logs"],
            "has_key": bool(cfg.get("api_key")),
            "simulate": bool(cfg.get("simulate")),
            "daily_budget": cfg.get("daily_token_budget"),
            "judge_tier": cfg.get("judge_tier"),
            "roles_dir": paths["roles"],
            "role_count": len(rolesmod.RoleRegistry(paths["roles"]).all()),
            "profile_completeness": profmod.profile_completeness(cfg),
            "python": sys.version.split()[0],
            "vendor": _vendor_status(),
            "llm_route": llm_route,
            "live": _live_status(),
        }
        self._json(info)

    def _sys_init(self):
        r = planmod.init_workspace(self.cfg)
        self._json(r)

    def _sys_restore(self):
        data = self._body()
        if "__error__" in data:
            data = {}
        keep = data.get("keep_data")
        if keep is None:
            keep = True
        self._json(planmod.restore_defaults(self.cfg, keep_data=bool(keep)))

    def _sys_backup(self):
        data = self._body()
        if "__error__" in data:
            data = {}
        r = planmod.backup_all(self.cfg, tag=data.get("tag") or "")
        self._json(r)

    def _sys_export(self):
        data = self._body()
        if "__error__" in data:
            data = {}
        payload = planmod.export_data(self.cfg, kind=data.get("kind") or "all")
        self._json({"ok": True, "payload": payload,
                    "size": len(payload), "kind": data.get("kind") or "all"})

    def _sys_import(self):
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        payload = data.get("payload")
        if not payload:
            self._json({"ok": False, "error": "没有内容"}, 400)
            return
        self._json(planmod.import_data(self.cfg, payload,
                                       mode=data.get("mode") or "merge"))

    def _sys_wechat(self):
        data = self._body()
        if "__error__" in data:
            self._json({"ok": False, "error": data["__error__"]}, 400)
            return
        from . import publish as pubmod
        self._json(pubmod.save_wechat_config(self.cfg,
                                             data.get("appid") or "",
                                             data.get("secret") or ""))

    def _board(self):
        b = boardmod.Board(self.cfg)
        self._json(b.build())

    def _ledger(self):
        recs = telemetry.read_all(self.cfg["paths"]["logs"])
        rows = list(reversed(recs))[-80:]
        raw = ""
        p = os.path.join(self.cfg["paths"]["logs"], "telemetry.jsonl")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read()[-12000:]
        self._json({"rows": rows, "raw": raw})

    def _orders(self):
        """全部订单：待办 + 已完成。"""
        self._json({"orders": consolemod.read_orders(self.cfg["paths"]["state"])})

    def _my_orders(self):
        """派活台用：最近的订单，带产出预览和输出文件路径。

        这是「下了单看不到结果」的解药 —— 每单都带着正文回来。
        """
        orders = consolemod.read_orders(self.cfg["paths"]["state"])
        # 已完成的在前，最近的在前
        orders = sorted(orders, key=lambda o: (o.get("status") != "done",
                                               o.get("done_ts") or o.get("ts") or ""),
                        reverse=True)
        out = []
        for o in orders[:30]:
            rec = dict(o)
            rec["preview"] = (rec.get("result_preview") or "")[:1200]
            rec["chars"] = rec.get("result_chars") or len(rec.get("result_preview") or "")
            rec["is_rework"] = bool(rec.get("revise_from"))
            out.append(rec)
        self._json({"orders": out,
                    "pending": len([o for o in orders if o.get("status") == "pending"])})


def _worker(server, cfg, stop_event):
    """后台轮询派活队列。

    每一条产出的正文都会落盘 —— 之前只在传了 save_as 时才存，
    导致「钱花了但看不到东西」。现在默认自动存到产出目录。
    """
    pipe = piplmod.Pipeline(cfg)
    _WORKER_STATE["alive"] = True
    _WORKER_STATE["last_beat"] = time.time()
    _WORKER_STATE["last_error"] = ""
    while not stop_event.is_set():
        try:
            pend = consolemod.pending_orders(cfg["paths"]["state"])
        except Exception as e:
            _WORKER_STATE["last_error"] = f"{type(e).__name__}: {e}"
            pend = []
        _WORKER_STATE["pending"] = len(pend)

        for order in pend:
            _oid = order.get("id") or ""
            try:
                before_cost = llm.LEDGER.cost
                _live_emit_order(_oid, order["code"], "running",
                                 msg=f"派活：{order['code']} 开始执行",
                                 data={"task": order.get("task", "")[:60]})

                extra = ""
                if order.get("use_kb"):
                    extra = pipe.kb.context(order["task"], top_k=3)

                save_as = (order.get("save_as") or "").strip()
                subdir = None
                # 返工单和普通单用不同的 max_tokens：
                # 返工要把原文带进输出，必须给够，否则产出比原文还短（被截断）
                max_tokens = int(order.get("max_tokens") or 0)
                if order.get("revise_from"):
                    src = order["revise_from"]
                    folder = os.path.dirname(src)
                    stem = os.path.splitext(os.path.basename(src))[0]
                    stamp = time.strftime("%H%M%S")
                    save_as = f"{stem}_返工_{stamp}.md"
                    subdir = folder or None
                    max_tokens = max(max_tokens, 8000)
                elif not save_as:
                    # 普通单：文件名带岗位和时间
                    stamp = time.strftime("%H%M%S")
                    save_as = f"{order['code']}_{stamp}.md"
                    subdir = time.strftime("%Y%m%d")
                    max_tokens = max(max_tokens, 4000)

                content, path = pipe.run_role(
                    order["code"], order["task"],
                    extra_kb=extra,
                    tier=order.get("tier") or "create",
                    temperature=float(order.get("temp") or 0.7),
                    save_as=save_as,
                    subdir=subdir,
                    max_tokens=max_tokens,
                )
                cost = llm.LEDGER.cost - before_cost

                # 返工结果比原文短一半以上 = 大概率被截断，必须提示
                if order.get("revise_from"):
                    try:
                        src_path = os.path.join(cfg["paths"]["outputs"],
                                               order["revise_from"])
                        with open(src_path, "r", encoding="utf-8") as f:
                            orig_len = len(f.read())
                        if orig_len > 200 and len(content or "") < orig_len * 0.6:
                            rec = telemetry.Recorder(cfg["paths"]["logs"])
                            rec.begin(order["code"], order["task"][:60],
                                      tier=order.get("tier") or "")
                            rec.end(status="ok", cost_yuan=cost,
                                    output_chars=len(content or ""),
                                    output_file=path or "",
                                    issues=[f"返工产出仅 {len(content or '')} 字，"
                                            f"原文 {orig_len} 字，疑似被截断"])
                    except Exception:
                        pass

                consolemod.mark_done(cfg["paths"]["state"], order,
                                     content or "", cost_yuan=cost,
                                     output_file=path or "")
                # 落盘用量台账
                if llm.LEDGER.rows:
                    from datetime import datetime as _dt
                    d = _dt.now().strftime("%Y%m%d")
                    llm.LEDGER.dump_csv(
                        os.path.join(cfg["paths"]["logs"], f"usage_{d}.csv"))
                _live_emit_order(_oid, order["code"], "ok",
                                 msg=f"派活完成：{order['code']}，¥{cost:.6f}",
                                 data={"cost": round(cost, 6),
                                       "file": os.path.basename(path or "")})
            except Exception as e:
                consolemod.mark_done(cfg["paths"]["state"], order,
                                     f"执行失败：{e}", cost_yuan=0.0)
                rec = telemetry.Recorder(cfg["paths"]["logs"])
                rec.begin(order["code"], order["task"][:60],
                          tier=order.get("tier") or "")
                rec.fail(e, issues=[f"控制台派活失败：{type(e).__name__}"])
                _live_emit_order(_oid, order["code"], "fail",
                                 msg=f"派活失败：{order['code']} {e}",
                                 data={"error": str(e)[:200]})

        _WORKER_STATE["last_beat"] = time.time()
        time.sleep(2)
    _WORKER_STATE["alive"] = False


def _live_emit_order(order_id, code, status, msg="", data=None):
    """派活事件广播。单独抽出来是为了让 worker 主流程不被埋点拖累。"""
    try:
        from staff import live as livemod
        livemod.emit_order(order_id, code, status, msg=msg, data=data or {})
    except Exception:
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器。

    必须多线程：SSE 实时事件流是长连接，
    单线程的 HTTPServer 会让这条连接一直占着唯一的服务线程，
    页面上的其它接口（比如点派活按钮）就全部超时了。
    """

    daemon_threads = True
    allow_reuse_address = True


# ---------------- 访问口令（对外暴露时用） ----------------
#
# 为什么需要：
#   默认只监听 127.0.0.1，外部访问不到，安全。
#   但客户要部署到云服务器 / 让厂里同事通过局域网访问时，
#   就必须监听 0.0.0.0 —— 这时没有口令，谁拿到地址都能进，
#   能改配置、能派活、能看所有产出，等于把系统裸奔出去。
#
# 所以规则是：只要不是只监听本机，就必须有口令。
# 口令从环境变量读（部署时用环境变量注入，不写进配置文件）。

ACCESS_PASSWORD = os.environ.get("STAFF_ACCESS_PASSWORD", "").strip()


def _lan_ip() -> str:
    """取本机在局域网里的 IP（用于打印给客户看的访问地址）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def serve(cfg, port=8899, open_browser=True, host="127.0.0.1"):
    """启动控制台。返回 (server, stop_event)。

    host:
        127.0.0.1（默认）只允许本机访问，最安全
        0.0.0.0     允许局域网/云端访问，必须配 STAFF_ACCESS_PASSWORD
    """
    Handler.cfg = cfg
    Handler.host = host
    server = ThreadedHTTPServer((host, port), Handler)
    stop_event = threading.Event()

    # 实时看板埋点自检：确认事件链路是通的，通不过就明说
    try:
        from staff import live as livemod
        # 按当前行业设定岗位显示名
        livemod.set_role_names(indmod.role_names(cfg))
        chk = livemod.self_check()
        if not chk["ok"]:
            print("  ⚠️  实时看板埋点自检未通过：" + chk["detail"])
            print("      看板可能收不到事件，其它功能不受影响。")
    except Exception as e:
        print(f"  ⚠️  实时看板自检异常：{e}")

    t = threading.Thread(target=_worker, args=(server, cfg, stop_event),
                         daemon=True)
    t.start()

    url = f"http://{'127.0.0.1' if host in ('127.0.0.1', 'localhost') else _lan_ip()}:{port}/"
    print("=" * 62)
    print("  AI 员工控制台已启动")
    print("=" * 62)
    print(f"  地址：{url}")
    if host not in ("127.0.0.1", "localhost"):
        print("  模式：局域网/云端访问（外部设备可访问）")
        if ACCESS_PASSWORD:
            print("  访问口令：已设置（外部访问需要输入）")
        else:
            print("  ⚠️  未设置访问口令！任何知道地址的人都能进来。")
            print("      设置方法（Windows）：set STAFF_ACCESS_PASSWORD=你的口令")
            print("      （对外暴露前务必设置）")
    else:
        print("  （只监听本机，外部访问不到）")
    print()
    print("  在这个页面里：")
    print("    · 实时看板 —— 看 AI 员工此刻在跑哪一步、成功还是失败")
    print("    · 状态墙   —— 看每个员工在不在干活、烧了多少 token")
    print("    · 派活台   —— 选岗位、写指令、一键下发")
    print("    · 行业方案 —— 换个行业，整套 AI 员工自动换成那行的活")
    print()
    print("  按 Ctrl+C 停止。")
    print()
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    return server, stop_event
"""WSGI 适配层：把控制台的 http.server 实现包装成标准 WSGI 应用。

为什么要这个文件
----------------
控制台最早是用 Python 标准库的 ``http.server`` 写的，那个写法在
本机跑得很好（``python run.py console``），但一放到托管环境里就有麻烦：

- serv00 用 **Phusion Passenger** 托管 Python 网站，只认标准 WSGI
  协议（``application(environ, start_response)``），不认 http.server。
- 其他云平台（Render / Railway / Fly / Koyeb）也都是 WSGI + gunicorn/uWSGI。

与其为了部署去重写一遍几千行的路由逻辑，不如写一层薄适配：
把 WSGI 的 environ 还原成 http.server 那套 ``self.path`` /
``self.headers`` / ``self.rfile`` / ``self.wfile``，
再把 Handler 的输出收集成 WSGI 的响应。

这样一份业务代码，本机和云端都能跑。

关键差异点
----------
1. **SSE 长连接**：原实现直接往 ``self.wfile`` 写并死循环。
   WSGI 里必须改成「返回一个生成器」，由服务器负责迭代输出。
   所以这里把 ``_sse`` 的逻辑挪到生成器里，通过 ``self._sse_generator``
   钩子注入。
2. **鉴权**：原实现的 ``_is_local_request()`` 判断客户端是不是 127.0.0.1。
   云端所有请求都来自反向代理，本机判断永远为假 —— 这是对的（要口令），
   但本机自测时需要一个开关。这里用 ``STAFF_TRUST_LOCAL=1`` 控制。
3. **Content-Length**：WSGI 响应没有这个头时会用 chunked 传输，
   浏览器行为一致；SSE 也必须不带 Content-Length。

用法
----
    # 本机自测
    python staff/wsgi_app.py

    # Passenger（serv00）入口 passenger_wsgi.py
    from wsgi_app import application
"""

import io
import json
import os
import queue
import sys
import threading
import traceback

# 让 `from staff import xxx` 这种绝对导入在本文件被直接执行时也能工作
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ------------------------------------------------------------------
# 1. 把 http.server 的「写响应」接口抽成一���可收集的缓冲器
# ------------------------------------------------------------------

class _Collector:
    """模拟 http.server 的 wfile：把写进去的字节收集起来。

    http.server 的 ``self.wfile.write()`` 在 WSGI 下没有对应物，
    因为响应应该由 ``start_response`` + 返回的字节串组成。
    """

    def __init__(self):
        self.chunks = []
        self.headers = []          # [(key, value), ...]
        self.status = 200
        self.status_message = ""

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.chunks.append(data)
        return len(data)

    def flush(self):
        pass

    def getvalue(self):
        return b"".join(self.chunks)

    def reset(self):
        self.chunks = []
        self.headers = []
        self.status = 200
        self.status_message = ""


class _FakeHeaders:
    """模拟 ``self.headers``：支持 ``.get(name)``，大小写不敏感。"""

    def __init__(self, pairs):
        self._d = {}
        for k, v in pairs:
            self._d[k.lower()] = v

    def get(self, name, default=None):
        return self._d.get(str(name).lower(), default)

    def __getitem__(self, name):
        return self._d[str(name).lower()]

    def __contains__(self, name):
        return str(name).lower() in self._d

    def items(self):
        return self._d.items()


class _FakeRFile:
    """模拟 ``self.rfile``：只提供 read(n)，够 POST 解析用了。"""

    def __init__(self, data=b""):
        self._buf = io.BytesIO(data)

    def read(self, n=-1):
        return self._buf.read(n)

    def readline(self, limit=-1):
        return self._buf.readline(limit)


# ------------------------------------------------------------------
# 2. WSGI 主入口
# ------------------------------------------------------------------

def _make_handler_class():
    """在运行时构造 Handler 子类，避免 import 期就依赖 http.server 细节。"""
    from staff import console_server as cs

    class WSGIHandler(cs.Handler):
        """把 http.server 的 Handler 接到 WSGI 上。"""

        # 收集到的响应状态
        _collector = None
        # SSE 生成器（由 wsgi_app 注入）
        _sse_generator = None

        def setup(self):
            # 跳过 http.server 的 socket 初始化
            pass

        def finish(self):
            pass

        def handle(self):
            pass

        def log_message(self, fmt, *args):
            pass

        # -- http.server 的响应原语，改写成收集模式 --------------------

        def send_response(self, code, message=None):
            self._collector.status = int(code)
            if message is None:
                message = {
                    200: "OK", 401: "Unauthorized", 403: "Forbidden",
                    404: "Not Found", 500: "Internal Server Error",
                }.get(int(code), "OK")
            self._collector.status_message = message

        def send_header(self, keyword, value):
            self._collector.headers.append((keyword, value))

        def end_headers(self):
            pass

        def log_request(self, code="-", size="-"):
            pass

        # -- 覆写鉴权：云端全部视为外部请求 --------------------------

        def _is_local_request(self):
            """云端请求一律视为外部（必须过口令）。

            本机自测时设 ``STAFF_TRUST_LOCAL=1`` 放行，
            这样本地跑 WSGI 版和跑 http.server 版行为一致。
            """
            if os.environ.get("STAFF_TRUST_LOCAL", "").strip() == "1":
                return True
            try:
                host = self.client_address[0]
            except Exception:
                return False
            return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0")

        # -- SSE：改成生成器 -----------------------------------------

        def _sse(self):
            """原实现是死循环写 wfile；WSGI 下交给生成器。"""
            gen = self._sse_generator
            if gen is None:
                self._send(500, "SSE not available", "text/plain; charset=utf-8")
                return
            for chunk in gen(self):
                self.wfile.write(chunk)
                self.wfile.flush()

        # -- 允许覆写 client_address（做鉴权与日志用）-----------------

        def __getattr__(self, name):
            # BaseHTTPRequestHandler 有些属性在 WSGI 下不存在，
            # 缺了就给个安全默认值，避免 AttributeError 打断请求。
            if name in ("client_address", "server", "requestline", "command"):
                return getattr(self, "_" + name, ("127.0.0.1", 0))
            raise AttributeError(name)

    return WSGIHandler


def _environ_to_handler(handler_cls, environ, collector):
    """把 WSGI environ 灌进 Handler 实例。"""
    h = handler_cls.__new__(handler_cls)     # 绕过 http.server 的 __init__

    # 请求行信息
    h.command = environ.get("REQUEST_METHOD", "GET")
    h.path = environ.get("PATH_INFO", "/")
    qs = environ.get("QUERY_STRING", "")
    if qs:
        h.path = h.path + "?" + qs

    # 请求头
    headers = []
    for k, v in environ.items():
        if k.startswith("HTTP_"):
            name = k[5:].replace("_", "-").title()
            headers.append((name, v))
    if environ.get("CONTENT_TYPE"):
        headers.append(("Content-Type", environ["CONTENT_TYPE"]))
    if environ.get("CONTENT_LENGTH"):
        headers.append(("Content-Length", environ["CONTENT_LENGTH"]))
    h.headers = _FakeHeaders(headers)

    # 请求体
    try:
        n = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        n = 0
    body = environ["wsgi.input"].read(n) if n else b""
    h.rfile = _FakeRFile(body)

    # 客户端地址（云端是代理 IP）
    h._client_address = (environ.get("REMOTE_ADDR", "0.0.0.0"), 0)

    # 响应收集器
    h._collector = collector
    h.wfile = collector
    return h


def _sse_generator(handler):
    """SSE 事件流生成器（只负责产出字节）。

    响应头由 :func:`_handle_sse` 在调用 ``start_response`` 之前设置 ——
    WSGI 要求先 start_response 再迭代响应体，所以头部不能在这里设。

    逻辑与原 ``Handler._sse()`` 一致，但改成 yield，
    这样 WSGI 服务器可以边生成边推给浏览器。
    """
    from staff import live as livemod

    q = livemod.BUS.subscribe()
    try:
        # 先补发最近的历史，页面刚打开时不会是空白
        for ev in livemod.BUS.recent(limit=40):
            yield ("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8")
        yield b": connected\n\n"
        while True:
            try:
                ev = q.get(timeout=15)
                payload = json.dumps(ev, ensure_ascii=False)
                yield ("data: " + payload + "\n\n").encode("utf-8")
            except queue.Empty:
                # 心跳，防止代理或浏览器把静默连接掐掉
                yield b": ping\n\n"
    finally:
        livemod.BUS.unsubscribe(q)


def _build_config():
    """加载配置。失败时给出可读的错误而不是 traceback。"""
    from staff import config as cfgmod
    try:
        return cfgmod.load()
    except Exception as e:
        print("[wsgi] 配置加载失败：%s" % e)
        return None


# ------------------------------------------------------------------
# 3. WSGI 可调用对象（Passenger / gunicorn 直接用它）
# ------------------------------------------------------------------

def application(environ, start_response):
    """标准 WSGI 入口。

    serv00 的 passenger_wsgi.py 里写：
        from wsgi_app import application
    """
    handler_cls = _make_handler_class()
    collector = _Collector()

    cfg = _build_config()
    if cfg is None:
        body = b"config error"
        start_response("500 Internal Server Error", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    handler_cls.cfg = cfg
    handler_cls._sse_generator = staticmethod(_sse_generator)

    h = _environ_to_handler(handler_cls, environ, collector)
    handler_cls.host = "0.0.0.0"

    cors = _cors_headers(environ)

    # OPTIONS 预检：跨域时的 CORS 预检，直接回 204
    if environ.get("REQUEST_METHOD", "").upper() == "OPTIONS":
        start_response("204 No Content", cors)
        return [b""]

    # SSE 走特殊通道：把响应拆成「头部 + 生成器」
    if environ.get("PATH_INFO") == "/api/live/stream":
        return _handle_sse(h, collector, start_response, cors)

    try:
        if environ.get("REQUEST_METHOD", "GET") == "POST":
            h.do_POST()
        else:
            h.do_GET()
    except Exception:
        traceback.print_exc()
        collector.reset()
        collector.status = 500
        body = b"internal error"
        start_response("500 Internal Server Error", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ] + cors)
        return [body]

    body = collector.getvalue()
    headers = _finalize_headers(collector, len(body), cors)
    start_response(_status_line(collector), headers)
    return [body]


def _handle_sse(h, collector, start_response, cors=None):
    """SSE 专用：先发头部，再把生成器交给服务器迭代。

    WSGI 协议要求：先 ``start_response``，再返回可迭代对象。
    所以响应头必须在这里设，不能等到生成器里才设 ——
    否则头部会丢失，浏览器拿不到 ``text/event-stream`` 就不会按流解析。
    """
    headers = [
        ("Content-Type", "text/event-stream; charset=utf-8"),
        ("Cache-Control", "no-cache, no-store"),
        # serv00 官方文档指定的反缓冲头，缺了 SSE 会被 nginx 攒着不发
        ("X-Accel-Buffering", "no"),
    ]
    if cors:
        headers.extend(cors)
    start_response("200 OK", headers)

    def gen():
        try:
            for chunk in _sse_generator(h):
                yield chunk
        except Exception:
            pass

    return gen()


def _finalize_headers(collector, length, extra=None):
    """整理响应头。

    - SSE 不带 Content-Length（必须用 chunked 才能持续输出）
    - 普通响应补上 Content-Length
    - 统一加 Cache-Control: no-store（原来在 _send 里加）
    - extra：额外要加的头（比如 CORS）
    """
    out = []
    has_len = False
    has_cache = False
    for k, v in collector.headers:
        lk = k.lower()
        if lk == "content-length":
            has_len = True
        if lk == "cache-control":
            has_cache = True
        out.append((k, v))
    if not has_cache:
        out.append(("Cache-Control", "no-store"))
    if length is not None and not has_len:
        out.append(("Content-Length", str(length)))
    if extra:
        out.extend(extra)
    return out


def _status_line(collector):
    msg = collector.status_message or "OK"
    return "%d %s" % (collector.status, msg)


# ------------------------------------------------------------------
# 3.5 CORS：允许 GitHub Pages（cxyo.cn/opc）跨域调用本后端
# ------------------------------------------------------------------

def _cors_headers(environ):
    """生成 CORS 响应头。

    场景：控制台前端放在 GitHub Pages（https://cxyo.cn/opc/），
    后端跑在本机或 serv00（https://micky.serv00.net）。
    两者不同源，浏览器会先发 OPTIONS 预检。

    允许的来源用环境变量 ``STAFF_CORS_ORIGINS`` 控制（逗号分隔），
    默认允许 cxyo.cn 与 cxyo.github.io。
    """
    raw = os.environ.get("STAFF_CORS_ORIGINS", "").strip()
    if not raw:
        raw = "https://cxyo.cn,https://www.cxyo.cn,https://cxyo.github.io"
    allowed = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]

    origin = (environ.get("HTTP_ORIGIN") or "").strip().rstrip("/")
    # 未带 Origin（同源请求）不用 CORS 头
    if not origin:
        return []
    # 只回显白名单里的来源；不在白名单就不加 CORS 头（浏览器会拦）
    if origin not in allowed:
        return []
    return [
        ("Access-Control-Allow-Origin", origin),
        ("Access-Control-Allow-Credentials", "true"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
        ("Access-Control-Max-Age", "86400"),
        # 允许前端读取这些响应头
        ("Access-Control-Expose-Headers", "Content-Type, Content-Length"),
        # 顶层文档也要能嵌入（iframe 场景）
        ("X-Frame-Options", "SAMEORIGIN"),
    ]


# ------------------------------------------------------------------
# 4. 本机自测入口
# ------------------------------------------------------------------

def _self_test(port=8901):
    """用标准库 wsgiref 起一个本地 WSGI 服务器自测。

        python staff/wsgi_app.py
    """
    from wsgiref.simple_server import make_server

    cfg = _build_config()
    if cfg is None:
        return 1

    handler_cls = _make_handler_class()
    handler_cls.cfg = cfg
    handler_cls._sse_generator = staticmethod(_sse_generator)

    class _Adapter:
        """给 wsgiref 用的薄壳：注入 client_address 与 rfile。"""

        def __call__(self, environ, start_response):
            environ.setdefault("REMOTE_ADDR", "127.0.0.1")
            collector = _Collector()
            h = _environ_to_handler(handler_cls, environ, collector)
            if environ.get("PATH_INFO") == "/api/live/stream":
                return _handle_sse(h, collector, start_response)
            try:
                if environ.get("REQUEST_METHOD", "GET") == "POST":
                    h.do_POST()
                else:
                    h.do_GET()
            except Exception:
                traceback.print_exc()
                collector.reset()
                collector.status = 500
            body = collector.getvalue()
            start_response(_status_line(collector),
                           _finalize_headers(collector, len(body)))
            return [body]

    httpd = make_server("127.0.0.1", port, _Adapter())
    print("WSGI 自测服务器：http://127.0.0.1:%d/" % port)
    print("按 Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test(int(os.environ.get("WSGI_TEST_PORT", "8901"))))

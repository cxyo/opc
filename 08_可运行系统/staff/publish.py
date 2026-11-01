"""公众号发布：把 Markdown 产出变成「能直接发」的成品。

三条路，按可用性从低到高：

    1. 排版 HTML（一键复制）
       Markdown → 公众号内联样式 HTML，浏览器里「复制」，到公众号编辑器粘贴，
       样式全部保留（公众号不支持外链 CSS，只能内联）。
       这一步不需要任何密钥，永远可用。

    2. 草稿箱 API（真发布）
       填了公众号 appid/secret，就能直接把图文推进草稿箱，
       在公众号后台点一下就发出去。
       前提：公众号类型必须是「认证服务号」或「订阅号」，且 IP 白名单已配置。

    3. 发布台账
       每次发布记录都写进 data/state/publish_log.csv，可追溯。

为什么不做「完全自动发出去」：
    公众号开放接口没有「直接群发」权限给普通开发者，
    认证号才有 publish 接口，且需要人工点确认。
    这是平台的规则，不是技术限制 —— 所以本模块把「最后一步」留给人。
"""

import html
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# ---- 排版样式 ----------------------------------------------------------
# 公众号编辑器只认内联 style，所以这里每个标签都带完整样式。
# 字号行高按公众号正文阅读体验调过。

STYLES = {
    "h1": 'font-size:22px;font-weight:700;color:#1a1a1a;margin:32px 0 18px;line-height:1.5;border-left:4px solid #576B95;padding-left:12px;',
    "h2": 'font-size:19px;font-weight:700;color:#1a1a1a;margin:30px 0 14px;line-height:1.5;border-left:4px solid #576B95;padding-left:12px;',
    "h3": 'font-size:17px;font-weight:700;color:#1a1a1a;margin:24px 0 12px;line-height:1.6;',
    "p": 'font-size:16px;color:#3f3f3f;line-height:1.85;margin:0 0 18px;letter-spacing:0.5px;',
    "li": 'font-size:16px;color:#3f3f3f;line-height:1.85;margin:0 0 10px;padding-left:4px;',
    "blockquote": 'font-size:16px;color:#5a5a5a;line-height:1.8;margin:0 0 18px;padding:14px 18px;background:#f7f8fa;border-left:4px solid #576B95;border-radius:3px;',
    "code": 'font-size:14px;color:#c7254e;background:#f7f8fa;padding:2px 6px;border-radius:3px;font-family:Menlo,Consolas,monospace;',
    "pre": 'font-size:14px;color:#3f3f3f;background:#f7f8fa;padding:16px;border-radius:6px;overflow-x:auto;margin:0 0 18px;',
    "hr": 'border:none;border-top:1px solid #e8e8e8;margin:28px 0;',
    "strong": 'font-weight:700;color:#1a1a1a;',
}


def md_to_html(md):
    """把 Markdown 转成公众号可直接粘贴的内联样式 HTML。

    覆盖：标题、段落、有序/无序列表、引用、代码块、行内代码、
    加粗、斜体、分割线、图片、链接、表格（转成图片占位提示）。
    """
    md = (md or "").strip()
    if not md:
        return ""

    # 先抽出代码块，避免内部内容被误解析
    blocks = []

    def _grab_code(m):
        blocks.append((m.group(1), m.group(2)))
        return "\n@@CODEBLOCK@@%d@@\n" % (len(blocks) - 1)

    md = re.sub(r"```(\w*)\n(.*?)```", _grab_code, md, flags=re.S)

    lines = md.split("\n")
    out = []
    i = 0
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        # 代码块占位
        m = re.match(r"^@@CODEBLOCK@@(\d+)@@$", s)
        if m:
            close_lists()
            idx = int(m.group(1))
            if idx < len(blocks):
                lang, code = blocks[idx]
                code = html.escape(code.rstrip())
                out.append(f'<pre style="{STYLES["pre"]}"><code>{code}</code></pre>')
            i += 1
            continue

        if not s:
            close_lists()
            i += 1
            continue

        # 分割线
        if re.match(r"^(\-{3,}|\*{3,}|_{3,})$", s):
            close_lists()
            out.append(f'<hr style="{STYLES["hr"]}">')
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            close_lists()
            lvl = len(m.group(1))
            txt = _inline(m.group(2))
            out.append(f'<h{lvl} style="{STYLES[f"h{lvl}"]}">{txt}</h{lvl}>')
            i += 1
            continue

        # 引用
        if s.startswith(">"):
            close_lists()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f'<blockquote style="{STYLES["blockquote"]}">'
                       f'{_inline(" ".join(buf))}</blockquote>')
            continue

        # 无序列表
        if re.match(r"^[-*+]\s+", s):
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append('<ul style="margin:0 0 18px;padding-left:8px;">')
                in_ul = True
            txt = _inline(re.sub(r"^[-*+]\s+", "", s))
            out.append(f'<li style="{STYLES["li"]}">{txt}</li>')
            i += 1
            continue

        # 有序列表
        if re.match(r"^\d+[\.、]\s+", s):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append('<ol style="margin:0 0 18px;padding-left:8px;">')
                in_ol = True
            txt = _inline(re.sub(r"^\d+[\.、]\s+", "", s))
            out.append(f'<li style="{STYLES["li"]}">{txt}</li>')
            i += 1
            continue

        # 图片
        m = re.match(r"^!\[(.*?)\]\((.*?)\)$", s)
        if m:
            close_lists()
            alt, url = m.group(1), m.group(2)
            out.append(f'<p style="{STYLES["p"]}"><img src="{url}" '
                       f'alt="{html.escape(alt)}" style="max-width:100%;'
                       f'border-radius:6px;display:block;margin:0 auto;"/></p>')
            i += 1
            continue

        # 表格（公众号不支持表格，转成提示）
        if s.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            close_lists()
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i].strip())
                i += 1
            out.append(_table_to_html(tbl))
            continue

        # 普通段落
        close_lists()
        buf = [s]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", ">", "-", "*", "|", "```"))
                    or re.match(r"^\d+[\.、]\s+", nxt)
                    or re.match(r"^(\-{3,}|\*{3,})$", nxt)):
                break
            buf.append(nxt)
            i += 1
        out.append(f'<p style="{STYLES["p"]}">{_inline(" ".join(buf))}</p>')

    close_lists()
    return "\n".join(out)


def _inline(text):
    """行内格式：加粗、斜体、行内代码、链接。"""
    t = html.escape(text, quote=False)
    # 行内代码先占位，避免里面的符号被后续规则改坏
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return f"@@CODE{len(codes) - 1}@@"

    t = re.sub(r"`([^`]+)`", stash, t)
    # 链接
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               r'<a href="\2" style="color:#576B95;text-decoration:none;">\1</a>', t)
    # 加粗
    t = re.sub(r"\*\*([^*]+)\*\*", rf'<strong style="{STYLES["strong"]}">\1</strong>', t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)
    # 还原行内代码
    for i, c in enumerate(codes):
        t = t.replace(f"@@CODE{i}@@",
                      f'<code style="{STYLES["code"]}">{c}</code>')
    return t


def _table_to_html(rows):
    """公众号不支持 table，转成「可读的文本卡片」。"""
    cells = []
    for r in rows:
        if re.match(r"^\|[\s:\-|]+\|$", r):
            continue
        cs = [c.strip() for c in r.strip("|").split("|")]
        cells.append(cs)
    if not cells:
        return ""
    head = cells[0]
    body = cells[1:]
    parts = [f'<section style="border:1px solid #e8e8e8;border-radius:6px;'
             f'padding:14px 16px;margin:0 0 18px;">']
    parts.append(f'<p style="font-size:15px;font-weight:700;color:#1a1a1a;'
                 f'margin:0 0 10px;">{" / ".join(head)}</p>')
    for r in body:
        parts.append(f'<p style="font-size:14px;color:#5a5a5a;line-height:1.8;'
                     f'margin:0 0 6px;">{" · ".join(r)}</p>')
    parts.append("</section>")
    return "".join(parts)


def extract_title(md):
    """取文章标题。优先第一个一级标题，其次第一行。"""
    md = (md or "").strip()
    m = re.search(r"^#\s+(.+)$", md, re.M)
    if m:
        return m.group(1).strip()
    for ln in md.split("\n"):
        s = ln.strip()
        if s and not s.startswith(("#", ">", "-", "*", "|", "```")):
            return s[:60]
    return "未命名文章"


def extract_digest(md, limit=120):
    """自动生成摘要（公众号列表页显示的那段话）。"""
    md = (md or "")
    # 去掉标题行和格式符号
    body = re.sub(r"^#.*$", "", md, flags=re.M)
    body = re.sub(r"[*_`>#\-]", "", body)
    body = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", body)
    body = " ".join(x.strip() for x in body.split("\n") if x.strip())
    body = body.strip()
    if len(body) > limit:
        body = body[:limit].rstrip("，。、,") + "…"
    return body


def build_page(md, title=None, digest=None, author="", cover_image=None,
                show_source=True):
    """生成完整的公众号文章 HTML（可直接在浏览器打开预览）。"""
    title = title or extract_title(md)
    digest = digest or extract_digest(md)
    body_html = md_to_html(md)

    cover = ""
    if cover_image:
        import base64
        try:
            with open(cover_image, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(cover_image)[1].lower()
            mime = {".png": "image/png", ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg"}.get(ext, "image/png")
            cover = (f'<img src="data:{mime};base64,{b64}" '
                     f'style="width:100%;display:block;"/>')
        except Exception:
            cover = ""

    src_note = ""
    if show_source:
        src_note = ('<p style="font-size:13px;color:#9a9a9a;margin-top:40px;'
                    'padding-top:16px;border-top:1px solid #eee;text-align:center;">'
                    '本文由一人AI公司 AI 员工流水线生成，'
                    '经人工审核后发布。AI 辅助创作，已按平台要求标识。</p>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
</head>
<body style="margin:0;background:#fff;">
<div style="max-width:677px;margin:0 auto;padding:0 16px 40px;">
  {cover}
  <h1 style="font-size:22px;font-weight:700;color:#1a1a1a;margin:30px 0 10px;line-height:1.5;">{html.escape(title)}</h1>
  <p style="font-size:14px;color:#8a8a8a;margin:0 0 6px;">{html.escape(author)}</p>
  <p style="font-size:14px;color:#8a8a8a;margin:0 0 26px;">{datetime.now().strftime("%Y-%m-%d")}</p>
  {body_html}
  {src_note}
</div>
</body>
</html>"""


# ---- 草稿箱 API --------------------------------------------------------

API_BASE = "https://api.weixin.qq.com/cgi-bin"


def _api_get(url, params, timeout=20):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}",
                                 headers={"User-Agent": "one-ai-company/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _api_post(url, payload, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={
                                     "Content-Type": "application/json; charset=utf-8",
                                     "User-Agent": "one-ai-company/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_access_token(appid, secret):
    """取公众号 access_token。"""
    url = f"{API_BASE}/token"
    r = _api_get(url, {"grant_type": "client_credential",
                       "appid": appid, "secret": secret})
    if r.get("errcode"):
        raise RuntimeError(f"取 token 失败：{r.get('errmsg')}（errcode {r.get('errcode')}）")
    return r["access_token"]


def upload_image(token, image_path):
    """上传永久素材图片，返回 media_id。"""
    url = f"{API_BASE}/material/add_material"
    boundary = "----oneaicompany" + datetime.now().strftime("%H%M%S%f")
    with open(image_path, "rb") as f:
        img = f.read()
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (f'Content-Disposition: form-data; name="media"; '
             f'filename="cover.png"\r\n').encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += img + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{url}?access_token={token}&type=image", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": "one-ai-company/2.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    if res.get("errcode"):
        raise RuntimeError(f"上传封面失败：{res.get('errmsg')}")
    return res.get("media_id", "")


def create_draft(token, title, content_html, thumb_media_id="",
                 author="", digest="", content_source_url=""):
    """创建草稿。"""
    url = f"{API_BASE}/draft/add"
    article = {
        "title": title,
        "author": author,
        "digest": digest,
        "content": content_html,
        "content_source_url": content_source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    r = _api_post(url, {"articles": [article]})
    if r.get("errcode"):
        raise RuntimeError(f"创建草稿失败：{r.get('errmsg')}（errcode {r.get('errcode')}）")
    return r


def publish_draft(token, media_id):
    """发布草稿（仅认证号可用）。"""
    url = f"{API_BASE}/freepublish/submit"
    r = _api_post(url, {"media_id": media_id})
    if r.get("errcode"):
        raise RuntimeError(f"发布失败：{r.get('errmsg')}（errcode {r.get('errcode')}）")
    return r


# ---- 发布包 ------------------------------------------------------------

def build_package(md, out_dir, title=None, cover_image=None, theme="tech",
                   brand="", with_video=False):
    """生成一个完整发布包：HTML + 封面 + 图卡 + 元数据。

    返回 {"dir":..., "html":..., "cover":..., "cards":[...], "meta":...}
    """
    from . import media as mediamod

    os.makedirs(out_dir, exist_ok=True)
    title = title or extract_title(md)
    digest = extract_digest(md)

    cover = ""
    if cover_image and os.path.exists(cover_image):
        cover = cover_image
    else:
        try:
            cover = mediamod.make_cover(title, digest[:40],
                                        os.path.join(out_dir, "封面图.png"),
                                        theme=theme, brand=brand)
        except Exception:
            cover = ""

    page = build_page(md, title=title, digest=digest, author=brand,
                      cover_image=cover)
    html_path = os.path.join(out_dir, "公众号排版稿.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)

    # 纯文本版（有些编辑器不支持粘贴样式）
    txt_path = os.path.join(out_dir, "正文_纯文本.md")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(md)

    cards = []
    try:
        cards = mediamod.make_cards_from_article(
            md, os.path.join(out_dir, "图卡"), theme=theme, brand=brand)
    except Exception:
        cards = []

    video = ""
    if with_video:
        try:
            v = mediamod.make_video(md, os.path.join(out_dir, "短视频.mp4"),
                                    theme=theme, brand=brand)
            video = v or ""
        except Exception:
            video = ""

    meta = {
        "title": title,
        "digest": digest,
        "author": brand,
        "chars": len(md),
        "cover": os.path.basename(cover) if cover else "",
        "cards": [os.path.basename(c) for c in cards],
        "video": os.path.basename(video) if video else "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "复制 公众号排版稿.html 的正文部分到公众号编辑器即可发布",
    }
    meta_path = os.path.join(out_dir, "发布信息.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 发布指引
    guide = [
        f"# 发布指引 · {title}",
        "",
        "## 最快的方式（1 分钟）",
        "",
        f"1. 双击打开 `{os.path.basename(html_path)}`",
        "2. 在浏览器里全选正文（Ctrl+A），复制",
        "3. 打开公众号后台 → 新建图文 → 粘贴",
        "4. 封面图用 `封面图.png`，摘要可直接用下面这段",
        "",
        "## 摘要（公众号列表页显示）",
        "",
        f"> {digest}",
        "",
        "## 发布前必查",
        "",
        "- [ ] 文中所有 【待补：xxx】 都已填成你的真实经历",
        "- [ ] 勾选「AI 辅助创作」标识（平台强制要求）",
        "- [ ] 图片里的文字与正文一致，没有错别字",
        "",
        "## 也可以走 API 直接进草稿箱",
        "",
        "在控制台的「系统管理」里填公众号 appid / secret，",
        "再点「推到草稿箱」，图和文会自动上传到后台草稿箱。",
        "",
    ]
    guide_path = os.path.join(out_dir, "发布指引.md")
    with open(guide_path, "w", encoding="utf-8") as f:
        f.write("\n".join(guide))

    return {"dir": out_dir, "html": html_path, "cover": cover,
            "cards": cards, "video": video, "meta": meta,
            "meta_path": meta_path, "guide": guide_path}


def log_publish(cfg, meta, status="待发布", platform="公众号", note=""):
    """写发布台账。"""
    import csv
    p = os.path.join(cfg["paths"]["state"], "publish_log.csv")
    exists = os.path.exists(p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["日期", "标题", "平台", "状态", "字数", "封面",
                        "图卡数", "视频", "发布方式", "备注"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            meta.get("title", ""), platform, status,
            meta.get("chars", 0), meta.get("cover", ""),
            len(meta.get("cards", [])), meta.get("video", ""),
            note, "",
        ])


def read_publish_log(cfg, limit=60):
    """读发布台账。"""
    import csv
    p = os.path.join(cfg["paths"]["state"], "publish_log.csv")
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return list(reversed(rows))[:limit]


def wechat_config(cfg):
    """读公众号配置（存在 config.json 的 wechat 段）。"""
    return cfg.get("wechat", {}) or {}


def save_wechat_config(cfg, appid, secret):
    """保存公众号配置到 config.json。"""
    from . import config as cfgmod
    cfg["wechat"] = {"appid": (appid or "").strip(),
                     "secret": (secret or "").strip()}
    path = cfgmod.save(cfg)
    return {"ok": True, "path": path,
            "has_appid": bool(appid), "has_secret": bool(secret)}

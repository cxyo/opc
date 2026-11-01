"""图像与视频生成：让产出不只是干巴巴的文字。

三条产线：
    1. cover   —— 文章封面图（公众号横版 900x383 / 竖版 1080x1440）
    2. cards   —— 图文长图卡片（把文章拆成多张信息图，手机上能滑着看）
    3. video   —— 口播短视频（图片序列 + 字幕 + 转场，MP4）

设计取舍（重要）：
    - 全部本地渲染，不调任何付费接口 → 不烧 Token、不烧钱
    - 用 Pillow 画版式 + ffmpeg 合成，产出的是真 PNG / 真 MP4
    - 文字内容来自 AI 员工的产出，所以图里的字和文章是一致的

为什么不做 AI 绘图：
    本机没有配置绘图模型的 API Key（DeepSeek 不含生图）。
    真要接，最省事的是走本机 new-api 网关（:3001）或 Dify 工作流。
    这两条通道已在 README 里留好接口，配置好 Key 即可切换，
    本模块的版式渲染不受影响 —— 因为出图是「排版」，
    AI 只负责给画面元素，两者可以叠加。
"""

import os
import re
import subprocess
import tempfile

from . import profile as profmod

# ---- 主题 --------------------------------------------------------------
# 每个主题是一整套配色，保证一批图看起来是同一套视觉

THEMES = {
    "dark": {
        "bg": (18, 20, 28), "bg2": (28, 32, 44),
        "accent": (99, 102, 241), "accent2": (34, 211, 238),
        "text": (240, 242, 248), "sub": (158, 166, 190),
        "card": (32, 36, 50), "line": (52, 58, 78),
    },
    "light": {
        "bg": (248, 249, 252), "bg2": (238, 240, 247),
        "accent": (79, 70, 229), "accent2": (14, 165, 233),
        "text": (24, 28, 40), "sub": (98, 106, 128),
        "card": (255, 255, 255), "line": (216, 220, 232),
    },
    "warm": {
        "bg": (32, 24, 20), "bg2": (48, 36, 28),
        "accent": (249, 115, 22), "accent2": (251, 191, 36),
        "text": (250, 245, 238), "sub": (186, 168, 152),
        "card": (44, 34, 28), "line": (68, 54, 44),
    },
    "tech": {
        "bg": (8, 20, 33), "bg2": (14, 34, 56),
        "accent": (56, 189, 248), "accent2": (168, 85, 247),
        "text": (232, 244, 255), "sub": (140, 170, 200),
        "card": (16, 38, 62), "line": (30, 66, 102),
    },
}

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/Deng.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def _pil():
    """延迟导入 Pillow。没装就给出可执行的提示。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError(
            "缺少 Pillow 库。请在 08_可运行系统 目录执行：\n"
            "    python -m pip install --target vendor pillow imageio-ffmpeg"
        )


def _font(size, bold=False):
    Image, ImageDraw, ImageFont = _pil()
    cands = ([FONT_CANDIDATES[1], FONT_CANDIDATES[0]] if bold
             else [FONT_CANDIDATES[0], FONT_CANDIDATES[2], FONT_CANDIDATES[3]])
    for c in cands:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    for c in FONT_CANDIDATES:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _ffmpeg():
    """拿到 ffmpeg 可执行文件路径。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        # 退而求其次：找系统 PATH 里的 ffmpeg
        for p in ("ffmpeg", "ffmpeg.exe"):
            try:
                out = subprocess.run([p, "-version"], capture_output=True,
                                     timeout=10)
                if out.returncode == 0:
                    return p
            except Exception:
                continue
    return None


# ---- 文本工具 ----------------------------------------------------------

def _wrap(draw, text, font, max_width):
    """按像素宽度折行（中英文混排都能用）。"""
    lines = []
    for para in (text or "").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for ch in para:
            test = cur + ch
            try:
                w = draw.textlength(test, font=font)
            except Exception:
                w = len(test) * font.size
            if w > max_width and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        if cur:
            lines.append(cur)
    return lines


def _clean_title(text, maxlen=30):
    """从 markdown 里抽出干净的标题。"""
    t = (text or "").strip()
    t = re.sub(r"^#+\s*", "", t)
    t = re.sub(r"[*_`>\[\]]", "", t)
    t = re.sub(r"^\d+[\.、]\s*", "", t)
    t = t.strip().strip("《》\"'")
    if len(t) > maxlen:
        t = t[:maxlen] + "…"
    return t


def _gradient(size, c1, c2, diagonal=True):
    """生成渐变底。纯像素计算，比逐行画矩形快。"""
    Image, _, _ = _pil()
    w, h = size
    base = Image.new("RGB", size, c1)
    px = base.load()
    for y in range(h):
        for x in range(0, w, 2):
            if diagonal:
                t = (x / w * 0.5 + y / h * 0.5)
            else:
                t = y / h
            t = max(0.0, min(1.0, t))
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            px[x, y] = (r, g, b)
            if x + 1 < w:
                px[x + 1, y] = (r, g, b)
    return base


# ---- 1. 封面图 ---------------------------------------------------------

def make_cover(title, subtitle="", out_path=None, theme="tech",
               style="horizontal", brand=""):
    """生成一张文章封面图。

    style: horizontal（公众号横版 900x383）/ vertical（小红书竖版 1080x1440）
    返回输出文件路径。
    """
    Image, ImageDraw, ImageFont = _pil()
    th = THEMES.get(theme, THEMES["tech"])
    size = (900, 383) if style == "horizontal" else (1080, 1440)
    W, H = size

    img = _gradient(size, th["bg"], th["bg2"])
    d = ImageDraw.Draw(img)

    # 装饰：右上角光晕圆
    glow = Image.new("RGB", size, th["bg"])
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W * 0.55, -H * 0.35, W * 1.25, H * 0.65],
               fill=th["accent"])
    img = Image.blend(img, glow, 0.10)
    d = ImageDraw.Draw(img)

    # 左上角品牌条
    pad = int(W * 0.06)
    if brand:
        bf = _font(int(H * 0.062), bold=True)
        d.text((pad, int(H * 0.09)), brand, font=bf, fill=th["accent2"])
    # 强调色短横线
    d.rectangle([pad, int(H * 0.055), pad + int(W * 0.09), int(H * 0.065)],
                fill=th["accent"])

    # 主标题
    tsize = int(H * (0.135 if style == "horizontal" else 0.088))
    tf = _font(tsize, bold=True)
    lines = _wrap(d, _clean_title(title, 40), tf, int(W * 0.86))
    max_lines = 2 if style == "horizontal" else 4
    lines = lines[:max_lines]
    ty = int(H * (0.28 if style == "horizontal" else 0.34))
    for ln in lines:
        d.text((pad, ty), ln, font=tf, fill=th["text"])
        ty += int(tsize * 1.28)

    # 副标题
    if subtitle:
        sf = _font(int(H * 0.062))
        sl = _wrap(d, _clean_title(subtitle, 60), sf, int(W * 0.84))
        for ln in sl[:2]:
            d.text((pad, ty + int(H * 0.02)), ln, font=sf, fill=th["sub"])
            ty += int(H * 0.082)

    # 底部装饰条
    d.rectangle([0, H - int(H * 0.022), W, H], fill=th["accent"])
    d.rectangle([0, H - int(H * 0.022), int(W * 0.22), H], fill=th["accent2"])

    if not out_path:
        out_path = os.path.join(tempfile.gettempdir(), "cover.png")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


# ---- 2. 图文长图卡片 ---------------------------------------------------

def split_article(markdown, max_cards=9):
    """把一篇文章按小标题切成若干张卡片的内容。

    没有小标题就按字数硬切，保证每张卡信息量均衡。
    """
    md = (markdown or "").strip()
    if not md:
        return []

    chunks = []
    cur_title = ""
    cur_body = []

    for line in md.split("\n"):
        if re.match(r"^#{1,3}\s+", line):
            if cur_body and any(x.strip() for x in cur_body):
                chunks.append((cur_title, "\n".join(cur_body)))
            cur_title = re.sub(r"^#+\s*", "", line).strip()
            cur_body = []
        else:
            cur_body.append(line)
    if cur_body and any(x.strip() for x in cur_body):
        chunks.append((cur_title, "\n".join(cur_body)))

    if not chunks:
        # 无标题结构：按段落分组，每组约 350 字
        paras = [p for p in md.split("\n\n") if p.strip()]
        buf = []
        size = 0
        for p in paras:
            buf.append(p)
            size += len(p)
            if size > 350:
                chunks.append(("", "\n\n".join(buf)))
                buf, size = [], 0
        if buf:
            chunks.append(("", "\n\n".join(buf)))

    return chunks[:max_cards]


def make_card(title, body, out_path=None, theme="tech", index=1, total=1,
              brand=""):
    """生成一张竖版信息卡（1080x1440）。

    版面自适应：内容少时整体上移并收紧留白，避免大片空白。
    """
    Image, ImageDraw, ImageFont = _pil()
    th = THEMES.get(theme, THEMES["tech"])
    W, H = 1080, 1440
    pad = 70
    top = 60

    img = Image.new("RGB", (W, H), th["bg"])
    d = ImageDraw.Draw(img)

    # 顶部色带
    d.rectangle([0, 0, W, 14], fill=th["accent"])
    d.rectangle([0, 0, int(W * 0.34), 14], fill=th["accent2"])

    # 序号角标
    if total > 1:
        bf = _font(34, bold=True)
        badge = f"{index}/{total}"
        d.text((W - pad - d.textlength(badge, font=bf), 56),
               badge, font=bf, fill=th["sub"])

    # ---- 先算内容块高度，再决定整体纵向位置 ----
    tf = _font(64, bold=True)
    tl = _wrap(d, _clean_title(title, 34), tf, W - pad * 2)[:3]
    bf = _font(40)
    bl = _wrap(d, body, bf, W - pad * 2)

    title_h = len(tl) * 84
    body_h = len(bl) * 60
    brand_h = 66 if brand else 0
    block_h = brand_h + title_h + 58 + body_h

    # 顶部留白：内容少就多留一点，视觉更居中
    avail = H - top - 140
    y = top + max(0, int((avail - block_h) * 0.30))

    if brand:
        d.text((pad, y), brand, font=_font(36, bold=True), fill=th["accent2"])
        y += brand_h

    # 标题
    for ln in tl:
        d.text((pad, y), ln, font=tf, fill=th["text"])
        y += 84

    # 分隔线
    y += 18
    d.rectangle([pad, y, pad + 90, y + 6], fill=th["accent"])
    y += 40

    # 正文
    for ln in bl:
        s = ln.strip()
        if s.startswith(("•", "-", "*")):
            d.text((pad, y), "· " + s.lstrip("•-* "), font=bf, fill=th["text"])
        else:
            d.text((pad, y), ln, font=bf, fill=th["text"])
        y += 60

    # 底部
    d.rectangle([0, H - 10, W, H], fill=th["accent"])

    if not out_path:
        out_path = os.path.join(tempfile.gettempdir(), f"card_{index}.png")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def make_cards_from_article(markdown, out_dir, theme="tech", brand="",
                             max_cards=9):
    """把整篇文章拆成一组图卡。返回文件路径列表。"""
    chunks = split_article(markdown, max_cards=max_cards)
    if not chunks:
        return []
    os.makedirs(out_dir, exist_ok=True)
    out = []
    total = len(chunks)
    for i, (t, b) in enumerate(chunks, 1):
        p = os.path.join(out_dir, f"卡片{i:02d}.png")
        out.append(make_card(t or f"第{i}部分", b, p, theme=theme,
                             index=i, total=total, brand=brand))
    return out


# ---- 3. 短视频 ---------------------------------------------------------

def make_video(markdown, out_path, theme="tech", brand="", seconds_per_card=3.0,
               fps=24, width=1080, height=1920):
    """把文章做成一竖版短视频（MP4）。

    做法：每段文字渲染成一张帧图 → 帧序列 → ffmpeg 合成带淡入淡出的 MP4。
    没有 ffmpeg 时返回 None（不报错，调用方可以降级成只出图）。
    """
    ff = _ffmpeg()
    if not ff:
        return None

    Image, ImageDraw, ImageFont = _pil()
    chunks = split_article(markdown, max_cards=8)
    if not chunks:
        return None

    th = THEMES.get(theme, THEMES["tech"])
    tmp = tempfile.mkdtemp(prefix="vid_")
    frames = []
    total = len(chunks)
    per = max(1, int(seconds_per_card * fps))

    try:
        for i, (title, body) in enumerate(chunks, 1):
            img = Image.new("RGB", (width, height), th["bg"])
            d = ImageDraw.Draw(img)
            # 顶部渐变条
            top = _gradient((width, 260), th["accent"], th["accent2"])
            img.paste(top, (0, 0))

            pad = 84
            y = 340
            if brand:
                d.text((pad, y), brand, font=_font(52, bold=True),
                       fill=th["accent2"])
                y += 96

            tf = _font(84, bold=True)
            for ln in _wrap(d, _clean_title(title, 24), tf, width - pad * 2)[:3]:
                d.text((pad, y), ln, font=tf, fill=th["text"])
                y += 116
            y += 26
            d.rectangle([pad, y, pad + 120, y + 8], fill=th["accent2"])
            y += 56

            bf = _font(50)
            lh = 80
            maxl = (height - y - 320) // lh
            for ln in _wrap(d, body, bf, width - pad * 2)[:maxl]:
                d.text((pad, y), ln, font=bf, fill=th["text"])
                y += lh

            # 底部进度条
            d.rectangle([pad, height - 150, width - pad, height - 142],
                        fill=th["line"])
            d.rectangle([pad, height - 150,
                         pad + int((width - pad * 2) * i / total), height - 142],
                        fill=th["accent2"])
            nf = _font(38)
            d.text((pad, height - 120), f"{i} / {total}", font=nf, fill=th["sub"])

            fp = os.path.join(tmp, f"f{i:03d}.png")
            img.save(fp, "PNG")
            frames.append(fp)

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        # ffmpeg 合成：每帧 per 帧，带淡入淡出
        cmd = [ff, "-y", "-framerate", str(fps), "-i", os.path.join(tmp, "f%03d.png"),
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
               "-vf", f"fps={fps},fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, len(frames)*per/fps-0.4):.2f}:d=0.4",
               "-movflags", "+faststart", out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(out_path):
            # 退一步：不加淡入淡出再试
            cmd2 = [ff, "-y", "-framerate", str(fps), "-i",
                    os.path.join(tmp, "f%03d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
                    out_path]
            r2 = subprocess.run(cmd2, capture_output=True, timeout=600)
            if r2.returncode != 0 or not os.path.exists(out_path):
                return None
        return out_path
    except Exception:
        return None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def make_video_with_duration(frames_dir, out_path, fps=24, hold=3.0,
                              transition=0.4):
    """把一个目录里的图片合成视频（用于「多图成片」场景）。"""
    ff = _ffmpeg()
    if not ff:
        return None
    files = sorted(f for f in os.listdir(frames_dir)
                   if f.lower().endswith((".png", ".jpg", ".jpeg")))
    if not files:
        return None
    tmp = tempfile.mkdtemp(prefix="vid2_")
    try:
        for i, f in enumerate(files, 1):
            src = os.path.join(frames_dir, f)
            dst = os.path.join(tmp, f"f{i:03d}.png")
            if not os.path.exists(dst):
                os.symlink(src, dst) if not os.path.exists(dst) else None
        # 统一转成 png 序列名，避免格式问题
        for i, f in enumerate(files, 1):
            src = os.path.join(frames_dir, f)
            dst = os.path.join(tmp, f"f{i:03d}.png")
            if os.path.exists(src) and not os.path.exists(dst):
                from PIL import Image
                Image.open(src).convert("RGB").save(dst, "PNG")

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        cmd = [ff, "-y", "-framerate", str(fps), "-i",
               os.path.join(tmp, "f%03d.png"),
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
               "-movflags", "+faststart", out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode != 0 or not os.path.exists(out_path):
            return None
        return out_path
    except Exception:
        return None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 一站式：给一篇产出配全套素材 --------------------------------------

def make_media_suite(markdown, out_dir, title="", theme="tech", brand="",
                     with_video=True, video_seconds=3.0):
    """给一篇文章生成：封面 + 图卡组 + 短视频。

    返回 {"cover":..., "cards":[...], "video":..., "dir":...}
    """
    os.makedirs(out_dir, exist_ok=True)
    if not title:
        m = re.search(r"^#\s+(.+)$", markdown or "", re.M)
        title = m.group(1) if m else "未命名"

    # 品牌名取自档案
    if not brand:
        p = profmod.load_profile(_dummy_cfg())
        brand = (p.get("persona") or {}).get("name") or ""

    cover = make_cover(title, "", os.path.join(out_dir, "封面图.png"),
                       theme=theme, brand=brand)
    cards = make_cards_from_article(markdown, os.path.join(out_dir, "图卡"),
                                    theme=theme, brand=brand)
    video = None
    if with_video:
        video = make_video(markdown, os.path.join(out_dir, "短视频.mp4"),
                           theme=theme, brand=brand,
                           seconds_per_card=video_seconds)
    return {"cover": cover, "cards": cards, "video": video, "dir": out_dir,
            "title": title, "theme": theme}


def _dummy_cfg():
    return {"paths": {"state": os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "data", "state")}}


def list_themes():
    return [{"key": k, "name": {"dark": "深色科技", "light": "清爽白",
                                "warm": "暖橙", "tech": "冷调蓝"}[k]}
            for k in THEMES]

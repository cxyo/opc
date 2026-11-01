# -*- coding: utf-8 -*-
"""A28 商品视觉官 —— 淘宝/咸鱼主图 + 详情页长图生成。

背景
----
「一人AI公司 · AI 员工系统」要上架电商，定价 19800 元一套。
但这是**虚拟商品**：买家看不到实物，收到的是一份可运行的系统。
虚拟商品的图不能拍实物，只能靠**信息设计**把抽象价值变成可点击的画面。

本模块做四件事：
    1. 主图 5 张（淘宝规范：1:1、1200×1200、第 5 张强制纯白底）
    2. 详情页长图（淘宝 750px 宽，首屏 750×600）
    3. 咸鱼版（风格不同：突出「值不值」，合规边界更松但仍禁导流）
    4. 全部文案过一遍合规体检（极限词 / 促销标 / 站外导流）

为什么用 Pillow 画而不是 AI 生图
--------------------------------
    · AI 图会被判「AI 假图」，虚拟商品冒充实物是 20 万罚款线
    · 主图文字必须**逐字可控**：主图文字面积 ≤10-20%，超了就违规
    · 第 5 张强制 RGB(255,255,255) 纯白底、无文字无阴影，
      生成模型根本做不到这么精确
    · 尺寸必须精确命中 1200×1200 / 750×600
    所以：**版式用代码画，配色和结构可复核，改一个字重画一次。**

所有数据来自交付包实测（276 个文件 / 24 个岗位 / 6 套行业方案），
不编造用户数、不编造效果数字。

合规红线（与 taobao.py 的 RED 规则库一致）
--------------------------------------
    · 禁极限词：最 / 第一 / 顶级 / 全网最低 / 100% / 根治
    · 禁促销标：秒杀 / 包邮 / 满减
    · 禁站外导流：微信号 / 二维码 / 手机号 / 外部链接
    · AI 生成内容须标识（《人工智能生成合成内容标识办法》）
    · 虚拟商品须写明「一经售出不退不换」
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import taobao as tb

# ---------------------------------------------------------------------------
# 输出目录
# ---------------------------------------------------------------------------

def _project_root() -> str:
    """工程根目录（一人AI公司/）。

    为什么不能自己算
    ----------------
    原来这里是 `dirname(dirname(dirname(__file__)))` —— 源码模式下对，
    **打包成 exe 后就错了**：
    PyInstaller 把程序解到 `C:\\Users\\xxx\\AppData\\Local\\Temp\\_MEIxxxx\\`，
    `__file__` 指向那个临时目录，于是输出被写到
    `Temp\\05_产品交付\\上架素材\\商品图_淘宝`。
    后果很实际：客户在自己电脑上跑，图片生成完了，
    关掉再打开就找不到了（Temp 会被系统清理），资料全丢。

    现在统一走 `config.PROJECT` —— 那里的 `_resolve_root()`
    已经处理了 exe 定位（找 exe 同级的 `08_可运行系统/`）。
    """
    try:
        from . import config as cfg
        root = getattr(cfg, "PROJECT", "")
        if root and os.path.isdir(root):
            return root
    except Exception:
        pass
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(here)


def shots_dir(platform: str = "淘宝") -> str:
    """商品图输出目录。"""
    sub = "商品图_淘宝" if platform == "淘宝" else "商品图_咸鱼"
    return os.path.join(_project_root(), "05_产品交付", "上架素材", sub)


# ---------------------------------------------------------------------------
# 配色（浅底为主 —— 淘宝主图在手机端要看得清，深底显脏）
# ---------------------------------------------------------------------------

PALETTE = {
    "淘宝": {
        "bg": (255, 255, 255),
        "bg_soft": (245, 247, 251),
        "ink": (17, 24, 39),
        "ink2": (75, 85, 99),
        "brand": (37, 99, 235),
        "brand_dark": (29, 78, 216),
        "accent": (234, 88, 12),
        "ok": (22, 163, 74),
        "line": (226, 232, 240),
        "card": (255, 255, 255),
        "chip": (239, 246, 255),
        "chip_text": (29, 78, 216),
    },
    "咸鱼": {
        "bg": (255, 255, 255),
        "bg_soft": (254, 250, 240),
        "ink": (28, 25, 23),
        "ink2": (87, 83, 78),
        "brand": (217, 119, 6),
        "brand_dark": (180, 83, 9),
        "accent": (220, 38, 38),
        "ok": (22, 163, 74),
        "line": (240, 233, 220),
        "card": (255, 255, 255),
        "chip": (255, 247, 237),
        "chip_text": (180, 83, 9),
    },
}


# ---------------------------------------------------------------------------
# 绘图基础
# ---------------------------------------------------------------------------

def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError(
            "缺少 Pillow 库。请在 08_可运行系统 目录执行：\n"
            "  pip install Pillow")


def _font(size: int, bold: bool = False):
    Image, ImageDraw, ImageFont = _pil()
    cands = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/Deng.ttf",
    ]
    for c in cands:
        if os.path.isfile(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _text_w(draw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def _wrap(draw, text: str, font, max_w: int) -> List[str]:
    """按像素宽度折行（中文按字断，英文按词断）。"""
    lines: List[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if _text_w(draw, trial, font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _rrect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=outline, width=width)


def _chip(draw, x, y, text, font, bg, fg, pad=14, h=42):
    w = _text_w(draw, text, font)
    _rrect(draw, (x, y, x + w + pad * 2, y + h), h // 2, fill=bg)
    draw.text((x + pad, y + (h - 16) // 2), text, font=font, fill=fg)
    return x + w + pad * 2 + 10


def _card(draw, box, pal, radius=18, fill=None, outline=True):
    _rrect(draw, box, radius, fill=fill or pal["card"],
           outline=pal["line"] if outline else None, width=2)


def _save(img, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 主图 ≤3MB：PNG 太大就转 JPEG
    img.save(path, "PNG", optimize=True)
    if os.path.getsize(path) > 3 * 1024 * 1024:
        img.convert("RGB").save(path.replace(".png", ".jpg"), "JPEG",
                                quality=90, optimize=True)
        os.remove(path)
        path = path.replace(".png", ".jpg")
    return path


# ---------------------------------------------------------------------------
# 商品数据（全部来自交付包实测，可复核）
# ---------------------------------------------------------------------------

PRODUCT = {
    "name": "AI员工系统",
    "full_name": "一人AI公司 · AI 员工系统",
    "subtitle": "24 个 AI 岗位 · 网页控制台 · 换行业即换整套",
    "price": "19800",
    "price_label": "19800 元 / 套",
    "files": 276,
    "roles": 24,
    "industries": 6,
    "cost_per_article": "0.21",
    "article_minutes": "4",
    "delivery": "交付包 + 远程安装指导",
    "days": "7 天内交付",
    "platforms": 32,
    "lines": 85,
    "skills": 24,
    "free_models": "3",
}


# ---------------------------------------------------------------------------
# 文案（全部过合规）
# ---------------------------------------------------------------------------

MAIN_COPY = {
    "淘宝": [
        {
            "no": 1, "role": "搜索封面",
            "badge": "AI 员工系统",
            "title": "24 个 AI 员工\n装进一个网页控制台",
            "sub": "换行业就换整套岗位 · 交付包 + 远程安装指导",
            "chips": ["24 个岗位", "6 套行业方案", "7 天交付"],
            "note": "页面为示意图，非实物照片",
        },
        {
            "no": 2, "role": "交付清单",
            "badge": "买到手是什么",
            "title": "276 个文件\n开箱即用的完整系统",
            "sub": "不是提示词合集，是能跑起来的软件",
            "list": [
                "24 个 AI 岗位提示词（可导入扣子）",
                "网页控制台（11 个页签，全部点得到）",
                "6 套行业方案包（通用/机器人/头盔厂/电商/营销/卡诺普）",
                "免安装版 exe，双击就启动，不用装 Python",
                "24 个技能包 zip，可直接挂自动发货",
                "全套部署与使用文档",
            ],
            "note": "清单为交付包实测文件统计",
        },
        {
            "no": 3, "role": "使用场景",
            "badge": "打开就能用",
            "title": "填个选题\n4 分钟出一篇带图带视频的稿",
            "sub": "选题→写稿→质检→改写→去 AI 味→配图→配视频→排版",
            "metrics": [("10 步", "成稿链路"), ("11 个", "控制台页签"),
                        ("32 个", "已接平台")],
            "note": "示意图，非软件截图",
        },
        {
            "no": 4, "role": "信任背书",
            "badge": "交付与售后",
            "title": "按制造业老板的场景做的\n不是通用演示",
            "sub": "岗位按真实业务链排，交付物能直接发客户",
            "list": [
                "交付形式：交付包 + 远程安装指导",
                "交付时效：确认后 7 天内",
                "行业方案：机器人 / 头盔厂 / 卡诺普现场实测资料",
                "客户交付包：一键生成 7 个文件（HTML 直开）",
                "更新：按月迭代，售后问题 48 小时内回复",
            ],
            "note": "服务承诺以订单约定为准",
        },
        {
            "no": 5, "role": "白底图",
            "badge": "",
            "title": "",
            "sub": "",
            "list": [],
            "note": "",
            "white": True,
        },
    ],
    "咸鱼": [
        {
            "no": 1, "role": "搜索封面",
            "badge": "AI 员工系统",
            "title": "24 个 AI 员工\n一个网页就能指挥",
            "sub": "自己公司能用的整套系统",
            "chips": ["24 个岗位", "6 套行业", "远程装好"],
            "note": "示意图，非实物照片",
        },
        {
            "no": 2, "role": "交付清单",
            "badge": "里面有什么",
            "title": "276 个文件\n解压就能跑",
            "sub": "免安装版，双击启动",
            "list": [
                "24 个 AI 岗位提示词",
                "网页控制台（11 个页签）",
                "6 套行业方案包",
                "免安装版 exe（不用装环境）",
                "24 个技能包 zip",
                "部署文档全套",
            ],
            "note": "清单为交付包实测文件统计",
        },
        {
            "no": 3, "role": "使用场景",
            "badge": "实际怎么用",
            "title": "填个选题\n4 分钟出稿",
            "sub": "含封面、图卡、短视频、公众号排版",
            "metrics": [("10 步", "成稿链路"), ("0.21 元", "单篇成本"),
                        ("32 个", "已接平台")],
            "note": "示意图，非软件截图",
        },
        {
            "no": 4, "role": "信任背书",
            "badge": "怎么交付",
            "title": "远程装好\n不会用也能上手",
            "sub": "按你的行业换一套岗位",
            "list": [
                "交付：交付包 + 远程安装指导",
                "时效：确认后 7 天内",
                "含 3 套工业现场实测行业包",
                "一键生成客户交付包（7 个文件）",
                "按月迭代，售后 48 小时内回",
            ],
            "note": "服务承诺以订单约定为准",
        },
        {
            "no": 5, "role": "白底图",
            "badge": "", "title": "", "sub": "", "list": [], "note": "",
            "white": True,
        },
    ],
}

DETAIL_SLOTS_TB = [
    {
        "no": 1, "name": "钩子",
        "head": "给中小企业的老板：",
        "title": "不用招人，也能有一支\n24 人的内容与交付队伍",
        "sub": "选题、写稿、质检、配图、出视频、排版、客户交付材料\n"
               "—— 按制造业真实业务链排好的岗位，装在一个网页控制台里",
        # 图下已有「控制台界面示意图（非软件截图）」标注，这里不再重复
        "note": "",
    },
    {
        "no": 2, "name": "痛点具象化",
        "head": "你是不是也遇到过：",
        "title": "内容这块，一直靠老板一个人扛",
        "items": [
            "想发内容，但天天不知道写什么，写完也没人看",
            "外包给代运营，一个月几万块，出的东西自己不敢发",
            "让员工写，写出来全是 AI 味，还得自己重写一遍",
            "客户要操作手册、设备档案、故障速查，每次都要重新整理",
            "公众号排版、配图、剪视频，每样都要单独求人",
        ],
        "note": "",
    },
    {
        "no": 3, "name": "交付清单",
        "head": "19800 元一套，收到的是这些",
        "title": "276 个文件，分 8 类",
        "items": [
            "24 个 AI 岗位提示词（可导入扣子 / Claude Code 等平台）",
            "网页控制台 exe：11 个页签，全部操作点得到",
            "6 套行业方案包：通用办公 / 工业机器人 / 头盔厂 / 电商 / 市场营销 / 卡诺普·浙江亿朗",
            "3 套工业现场实测资料包（10 份现场文档）",
            "24 个技能包 zip（可挂自动发货）",
            "AI 超市：32 个主流平台 × 7 个分类，自填凭据，连通性自测",
            "禁区红线 85 条（红线 52 / 灰区 20 / 注意 13）",
            "全套部署文档：部署向导、客户部署手册、云端与局域网部署",
        ],
        "note": "清单为交付包实测文件统计",
    },
    {
        "no": 4, "name": "使用前后",
        "head": "用之前 / 用之后",
        "title": "差别在工作量，不在玄学",
        "items": [
            "用之前：写一篇公众号文章，搜集资料 1 小时 + 写 3 小时 + 改 3 遍 + 配图排版 1 小时",
            "用之后：填一个选题，跑 10 步链路，约 4 分钟出正文 + 封面 + 8 张图卡 + 1 个短视频 + 排版稿",
            "用之前：客户要一套交付材料，从零翻文档",
            "用之后：点一下生成 7 个文件（上岗速通 / 操作卡 / 故障速查 / 设备档案 / 知识库预览）",
            "用之前：单篇成本靠估",
            "用之后：账单对账三方对照（本地台账 / 网关侧 / 官方账单），同窗口误差 0.04%",
        ],
        "note": "耗时与成本为系统实测值，不同选题会有浮动",
    },
    {
        "no": 5, "name": "适用与不适用",
        "head": "先说清楚不适合谁",
        "title": "省得你买完再问",
        "items": [
            "适合：中小企业与制造业老板，要稳定产出内容和交付材料",
            "适合：一人团队或小团队，想把内容线全流程跑通",
            "适合：做企业 AI 服务的，需要一套能改行业包的现成骨架",
            "不适合：只想买几个提示词试试水",
            "不适合：要求开箱即用、但不愿意填自己行业资料的人",
            "不适合：期待装上就能自动赚钱的人",
        ],
        "note": "",
    },
    {
        "no": 6, "name": "交付与售后",
        "head": "怎么给你",
        "title": "交付包 + 远程安装指导",
        "items": [
            "交付形式：交付包 + 远程安装指导（可选上门，另议）",
            "交付时效：确认后 7 天内",
            "你这边需要：一个能上网的电脑、可用的模型 API Key",
            "换行业：把资料放进行业包目录，网页上一键切换整套岗位",
            "更新：按月迭代，买断后同一大版本免费更新",
            "退款：虚拟商品，一经售出不退不换；交付前可申请演示",
        ],
        "note": "以订单约定为准",
    },
    {
        "no": 7, "name": "声明",
        "head": "",
        "title": "",
        "items": [],
        "note": "",
        "legal": True,
    },
]


# ---------------------------------------------------------------------------
# 主图生成
# ---------------------------------------------------------------------------

def _main_white(img, pal, slot):
    """第 5 张：强制纯白底、无文字、无阴影、无边框。

    淘宝这一张的用途是进「猜你喜欢」公域流量池，所以图形本身
    必须能让人认出是「一套系统」——抽象方块阵认不出来。

    所以画的是**界面结构线框**：左侧页签栏 + 中间工作区 + 右侧指标栏。
    纯图形、纯几何、无任何文字，所以完全满足「无文字」的要求，
    但形状本身有辨识度。
    """
    Image, ImageDraw, ImageFont = _pil()
    W, H = img.size
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), fill=(255, 255, 255))

    # 外框：模拟一个应用窗口
    # 窗口占画面比例要够 —— 第一版只占 6.1% 的非白像素，
    # 低于淘宝「主体占 70%-80%」的实际观感要求，主体显得太小。
    bx0, by0 = W * 0.06, H * 0.08
    bx1, by1 = W * 0.94, H * 0.92
    _rrect(d, (bx0, by0, bx1, by1), 18, outline=(148, 163, 184), width=7)

    # 顶部标题条 + 三个圆点
    bar_h = (by1 - by0) * 0.10
    d.line((bx0, by0 + bar_h, bx1, by0 + bar_h), fill=(148, 163, 184), width=5)
    r = 15
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = bx0 + 44 + i * (r * 3)
        d.ellipse((cx, by0 + bar_h / 2 - r, cx + r * 2, by0 + bar_h / 2 + r),
                  fill=c)

    # 左侧页签栏
    side_x = bx0 + (bx1 - bx0) * 0.26
    d.line((side_x, by0 + bar_h, side_x, by1), fill=(148, 163, 184), width=5)
    ty = by0 + bar_h + 42
    for i in range(9):
        tw = (bx1 - bx0) * (0.11 if i % 3 else 0.15)
        _rrect(d, (bx0 + 36, ty, bx0 + 36 + tw, ty + 26), 13,
               fill=(37, 99, 235) if i == 1 else (203, 213, 225))
        ty += 54

    # 中间工作区：四块流程卡
    mid_x = side_x + 56
    mid_w = (bx1 - mid_x) * 0.52
    sy = by0 + bar_h + 48
    for i in range(4):
        _rrect(d, (mid_x, sy, mid_x + mid_w, sy + 74), 14,
               outline=(148, 163, 184), width=5)
        d.ellipse((mid_x + 28, sy + 26, mid_x + 56, sy + 54),
                  fill=(37, 99, 235) if i < 3 else (245, 158, 11))
        _rrect(d, (mid_x + 82, sy + 30, mid_x + 82 + mid_w * 0.44, sy + 48), 9,
               fill=(203, 213, 225))
        sy += 98

    # 右侧指标栏：四块
    rx = mid_x + mid_w + 52
    rw = bx1 - rx - 52
    cy = by0 + bar_h + 48
    for i in range(4):
        _rrect(d, (rx, cy, rx + rw, cy + 90), 14,
               outline=(148, 163, 184), width=5)
        _rrect(d, (rx + 26, cy + 24, rx + 26 + rw * 0.46, cy + 46), 11,
               fill=(37, 99, 235))
        _rrect(d, (rx + 26, cy + 58, rx + 26 + rw * 0.28, cy + 72), 7,
               fill=(203, 213, 225))
        cy += 110
    return img


def _draw_system_mock(d, pal, x0: int, y0: int, w: int, h: int,
                      labels: bool = True) -> None:
    """画一张「控制台界面示意图」。

    为什么画界面图而不是留白：
        淘宝主图要求主体占画面 70% 以上。纯文字主图既不达标，
        也没有说服力 —— 买家看不到「这是个能跑的东西」。

    为什么是**示意图**而不是截图：
        这是原创绘制的线框图，不是任何真实软件的截图，
        所以不构成「AI 假图冒充实物」。图下方标注「示意图」。

    这个图要画的东西：左侧页签栏 + 中间流水线 + 右侧指标卡，
    也就是产品真实存在的三块结构（11 页签 / 流水线 / 指标）。

    labels=False 时（矮高度场景）只画色块不画字 ——
    高度不够时塞文字会挤成一团，反而更难读。
    """
    # 外框
    _rrect(d, (x0, y0, x0 + w, y0 + h), 14, fill=pal["bg_soft"],
           outline=pal["line"], width=2)
    # 标题条
    bar_h = int(h * 0.09)
    d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=14,
                        fill=pal["bg_soft"], outline=pal["line"], width=2)
    d.rectangle((x0 + 2, y0 + 2, x0 + w - 2, y0 + bar_h), fill=pal["card"])
    d.line((x0, y0 + bar_h, x0 + w, y0 + bar_h), fill=pal["line"], width=2)
    # 窗口三个点
    r = max(4, int(bar_h * 0.16))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = x0 + 20 + i * (r * 3)
        d.ellipse((cx, y0 + bar_h // 2 - r, cx + r * 2, y0 + bar_h // 2 + r),
                  fill=c)

    # 左侧页签栏
    side_w = int(w * 0.22)
    d.rectangle((x0 + 2, y0 + bar_h + 2, x0 + side_w, y0 + h - 2),
                fill=pal["card"])
    d.line((x0 + side_w, y0 + bar_h, x0 + side_w, y0 + h), fill=pal["line"], width=2)
    tabs = ["概览", "作战看板", "状态墙", "派活台", "一键成稿",
            "产出台", "素材库", "人设档案", "规划师", "系统管理"]
    # 行距要能塞下 10 个页签，否则按可用高度缩减数量
    row_h = int((h - bar_h - int(h * 0.06)) / len(tabs))
    show_text = labels and row_h >= 22
    if show_text:
        tf = _font(max(11, min(20, row_h - 8)), True)
        ty = y0 + bar_h + int(h * 0.035)
        for i, t in enumerate(tabs):
            if ty > y0 + h - 16:
                break
            if i == 1:  # 当前选中项高亮
                d.rounded_rectangle((x0 + 8, ty - 3, x0 + side_w - 8, ty + row_h - 5),
                                    radius=6, fill=pal["chip"])
            d.text((x0 + 18, ty), t, font=tf,
                   fill=pal["chip_text"] if i == 1 else pal["ink2"])
            ty += row_h
    else:
        # 无文字模式：画一列色块表示页签
        ty = y0 + bar_h + int(h * 0.05)
        blk_h = max(4, row_h - 6)
        for i in range(len(tabs)):
            if ty > y0 + h - blk_h:
                break
            _rrect(d, (x0 + 16, ty, x0 + side_w - 16, ty + blk_h), 4,
                   fill=pal["chip_text"] if i == 1 else pal["line"])
            ty += row_h

    # 中间：执行流水线
    mid_x = x0 + side_w + int(w * 0.05)
    mid_w = int(w * 0.42)
    if labels:
        d.text((mid_x, y0 + bar_h + int(h * 0.035)), "执行流水线",
               font=_font(max(12, int(h * 0.042)), True), fill=pal["ink"])
    steps = [("选题审稿", "130.2s", True), ("撰写初稿", "88.6s", True),
             ("合规质检", "42.1s", True), ("去 AI 味定稿", "31.4s", True),
             ("配图排版", "19.8s", False)]
    sy = y0 + bar_h + int(h * (0.11 if labels else 0.06))
    avail = (y0 + h - 8) - sy
    step_h = int(avail / len(steps))
    show_step_text = labels and step_h >= 40
    for label, dur, done in steps:
        _rrect(d, (mid_x, sy, mid_x + mid_w, sy + step_h - 8), 8,
               fill=pal["card"], outline=pal["line"], width=2)
        dot = max(8, min(12, step_h // 3))
        col = pal["ok"] if done else (255, 189, 46)
        d.ellipse((mid_x + 14, sy + (step_h - 8) // 2 - dot // 2,
                   mid_x + 14 + dot, sy + (step_h - 8) // 2 + dot // 2), fill=col)
        if show_step_text:
            d.text((mid_x + 34, sy + (step_h - 8) // 2 - 10), label,
                   font=_font(max(11, int(h * 0.034))), fill=pal["ink"])
            d.text((mid_x + mid_w - 62, sy + (step_h - 8) // 2 - 10), dur,
                   font=_font(max(10, int(h * 0.032))), fill=pal["ink2"])
        sy += step_h

    # 右侧：指标卡
    rx = mid_x + mid_w + int(w * 0.05)
    rw = x0 + w - rx - int(w * 0.04)
    metrics = [("¥0.21", "单篇成本"), ("11", "页签"),
               ("32", "已接平台"), ("85", "禁区红线")]
    ch = int((h - bar_h - int(h * 0.12)) / 4) - 8
    cy = y0 + bar_h + int(h * 0.06)
    show_metric_text = labels and ch >= 46
    for val, lab in metrics:
        _rrect(d, (rx, cy, rx + rw, cy + ch), 8, fill=pal["card"],
               outline=pal["line"], width=2)
        if show_metric_text:
            d.text((rx + 14, cy + ch // 2 - 22), val,
                   font=_font(max(13, int(h * 0.048)), True), fill=pal["brand"])
            d.text((rx + 14, cy + ch // 2 + 8), lab,
                   font=_font(max(10, int(h * 0.03))), fill=pal["ink2"])
        else:
            _rrect(d, (rx + 12, cy + ch // 3, rx + 12 + rw * 0.42,
                       cy + ch // 3 + max(6, ch // 5)), 5, fill=pal["brand"])
            _rrect(d, (rx + 12, cy + ch // 3 + max(12, ch // 3),
                       rx + 12 + rw * 0.26,
                       cy + ch // 3 + max(12, ch // 3) + max(5, ch // 6)), 4,
                   fill=pal["line"])
        cy += ch + 8


def make_main_image(slot: Dict[str, Any], platform: str = "淘宝",
                    size: int = 1200) -> Any:
    """生成一张主图。"""
    Image, ImageDraw, ImageFont = _pil()
    pal = PALETTE[platform]
    img = Image.new("RGB", (size, size), pal["bg"])
    d = ImageDraw.Draw(img)

    if slot.get("white"):
        return _main_white(img, pal, slot)

    M = size // 12  # 边距
    # 顶部品牌条
    d.rectangle((0, 0, size, 10), fill=pal["brand"])
    if slot.get("badge"):
        _chip(d, M, M + 18, slot["badge"], _font(size // 42, True),
              pal["chip"], pal["chip_text"])

    y = M + 100
    # 标题
    tf = _font(size // 17, True)
    for line in slot["title"].split("\n"):
        d.text((M, y), line, font=tf, fill=pal["ink"])
        y += int(size / 17) + 14

    y += 10
    # 副标题
    sf = _font(size // 34)
    for line in _wrap(d, slot["sub"], sf, size - M * 2):
        d.text((M, y), line, font=sf, fill=pal["ink2"])
        y += int(size / 30)

    y += 24
    # chips  —— 先画 chip，再把 y 推到 chip 底部之下，
    # 否则示意图会盖住 chip 的下半截（第一版就踩了这个坑）。
    if slot.get("chips"):
        cf = _font(size // 40, True)
        cx = M
        for c in slot["chips"]:
            if cx + _text_w(d, c, cf) + 60 > size - M:
                cx = M
                y += 62
            cx = _chip(d, cx, y, c, cf, pal["chip"], pal["chip_text"])
        # chip 高度 42，向上留 8 的呼吸位
        y += 42 + 8 + 26

    # ---- 主体视觉：界面示意图（第 1、3 张）----
    show_mock = slot["no"] in (1, 3)
    mock_note_h = 34
    if show_mock:
        # 示意图高度要能同时容下 mock + 标注 + （第 3 张还有）指标块，
        # 否则三者会互相压。第 3 张有指标块，所以图矮一点。
        mock_h = int(size * (0.28 if slot.get("metrics") else 0.34))
        mock_y = y + 6
        _draw_system_mock(d, pal, M, mock_y, size - M * 2, mock_h)
        # 示意图标注 —— 必须在指标块**之前**画，且预留出它的高度，
        # 否则指标块会压住这行字（第一版踩过：只露出一半的「载」字）
        d.text((M, mock_y + mock_h + 8), "控制台界面示意图（非软件截图）",
               font=_font(size // 46), fill=(148, 163, 184))
        y = mock_y + mock_h + 8 + mock_note_h
    else:
        # 清单类主图：底部放指标块，填满空间
        pass

    # 指标块
    if slot.get("metrics"):
        mh = 170
        my = size - M - mh - 66
        # 指标块不能压到示意图标注上
        if my < y + 12:
            my = y + 12
        n = len(slot["metrics"])
        gap = 18
        bw = (size - M * 2 - gap * (n - 1)) // n
        for i, (val, lab) in enumerate(slot["metrics"]):
            x0 = M + i * (bw + gap)
            _card(d, (x0, my, x0 + bw, my + mh), pal)
            vf = _font(size // 20, True)
            lbf = _font(size // 42)
            d.text((x0 + (bw - _text_w(d, val, vf)) // 2, my + 34), val,
                   font=vf, fill=pal["brand"])
            d.text((x0 + (bw - _text_w(d, lab, lbf)) // 2, my + 108), lab,
                   font=lbf, fill=pal["ink2"])
        y = my + mh + 20

    # ---- 清单（第 2、4 张）----
    if slot.get("list"):
        lf = _font(size // 36)
        note_h = int(size / 44)
        # 清单型主图：上半部分放清单卡，下半部分放示意图，
        # 让「里面有什么」和「用起来什么样」同时可见。
        show_mock = slot["no"] in (2, 4)
        if show_mock:
            mock_h = int(size * 0.26)
            mock_y = size - M - note_h - 46 - mock_h - 18
            _draw_system_mock(d, pal, M, mock_y, size - M * 2, mock_h)
            d.text((M, mock_y + mock_h + 8), "控制台界面示意图（非软件截图）",
                   font=_font(size // 46), fill=(148, 163, 184))
            avail = mock_y - y - 10
        else:
            avail = size - M - note_h - 40 - y

        line_h = int(size / 32)
        max_items = max(1, avail // line_h)
        for item in slot["list"][:max_items]:
            d.ellipse((M + 4, y + 12, M + 16, y + 24), fill=pal["brand"])
            for j, line in enumerate(_wrap(d, item, lf, size - M * 2 - 40)):
                d.text((M + 34, y + j * line_h), line, font=lf, fill=pal["ink"])
                if j > 0:
                    y += line_h
            y += int(size / 30)

    # ---- 底部：价格 + 注释（分区，不重叠）----
    note_f = _font(size // 46)
    note_h = int(size / 44)
    if slot.get("note"):
        d.text((M, size - M - note_h), slot["note"], font=note_f,
               fill=(148, 163, 184))
    if slot["no"] == 1:
        pf = _font(size // 36, True)
        pw = _text_w(d, PRODUCT["price_label"], pf)
        px = size - M - pw
        py = size - M - note_h - 46
        d.rounded_rectangle((px - 18, py - 8, size - M + 12, py + 44), 12,
                            fill=pal["accent"])
        d.text((px, py), PRODUCT["price_label"], font=pf, fill=(255, 255, 255))
    return img


def build_main_images(platform: str = "淘宝", size: int = 1200) -> List[str]:
    """生成 5 张主图，返回文件路径列表。"""
    outdir = shots_dir(platform)
    paths: List[str] = []
    for slot in MAIN_COPY[platform]:
        img = make_main_image(slot, platform, size)
        p = os.path.join(outdir, f"主图{slot['no']}_{slot['role']}.png")
        paths.append(_save(img, p))
    return paths


# ---------------------------------------------------------------------------
# 详情页长图
# ---------------------------------------------------------------------------

def _detail_header(d, pal, W, slot, y_start: int) -> int:
    """画一个区块的标题，返回内容起始 y。

    注意（2026-10-31 修）：这个函数**只负责画自己那几行**，
    高度必须与 build_detail 里预估的完全一致，否则下一段会盖上来。
    所以两处用的是同一套常量：HEAD_H / TITLE_LINE_H / SUB_LINE_H。
    """
    M = 60
    y = y_start
    if slot.get("head"):
        d.rectangle((0, y_start - 40, W, y_start - 34), fill=pal["brand"])
        d.text((M, y), slot["head"], font=_font(30, True), fill=pal["brand"])
        y += HEAD_H
    tf = _font(44, True)
    for line in slot["title"].split("\n"):
        d.text((M, y), line, font=tf, fill=pal["ink"])
        y += TITLE_LINE_H
    y += TITLE_GAP
    return y


# 详情页排版常量 —— 预估与绘制必须共用，否则会重叠
HEAD_H = 52          # 区块眉题高度
TITLE_LINE_H = 60    # 标题每行高度
TITLE_GAP = 16       # 标题与正文间距
SUB_LINE_H = 40      # 副标题每行高度
ITEM_LINE_H = 36     # 条目每行高度
ITEM_GAP = 46        # 条目间距


def build_detail(platform: str = "淘宝", width: int = 750) -> str:
    """生成长图详情页。返回文件路径。

    排版分两趟：
        第一趟按同一套常量**预估**每个区块的高度 → 累加得到总高
        第二趟按同样规则**绘制**
    两趟共用 HEAD_H / TITLE_LINE_H / SUB_LINE_H / ITEM_LINE_H，
    所以不会出现「下一段盖住上一段」的重叠。

    第一版就是踩了这个坑：预估用了一个假 draw 对象，
    绘制时又用了另一套行高，结果首屏三行标题全糊在一起。
    """
    Image, ImageDraw, ImageFont = _pil()
    pal = PALETTE[platform]
    slots = DETAIL_SLOTS_TB
    M = 60
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def sub_lines(slot) -> List[str]:
        if not slot.get("sub"):
            return []
        return _wrap(probe, slot["sub"], _font(25), width - M * 2)

    def item_lines(slot) -> List[List[str]]:
        out = []
        lf = _font(25)
        for it in slot.get("items", []):
            out.append(_wrap(probe, it, lf, width - M * 2 - 44))
        return out

    # ---- 第一趟：预估高度 ----
    legal_text = (
        "本商品为虚拟商品，交付形式为系统交付包 + 远程安装指导，"
        "一经售出不退不换；交付前可申请演示。\n"
        "本商品由作者原创整理，非官方产品，与腾讯、WorkBuddy、DeepSeek、"
        "CodeBuddy 等厂商无任何隶属或合作关系。\n"
        "页面配图与示意图由 AI 辅助生成并经人工排版，已按《人工智能生成"
        "合成内容标识办法》标注；清单数据为交付包实测文件统计。\n"
        "商品需自行准备可用的模型 API Key，模型服务费用由使用者自行承担。"
    )

    plan = []
    for slot in slots:
        h = 40  # 顶部留白
        if slot.get("head"):
            h += HEAD_H
        h += len(slot["title"].split("\n")) * TITLE_LINE_H + TITLE_GAP
        h += len(sub_lines(slot)) * SUB_LINE_H + 16
        for lines in item_lines(slot):
            h += len(lines) * ITEM_LINE_H + ITEM_GAP
        if slot.get("legal"):
            # 卡片高度按**实际折行数**算，不能拍脑袋给一个固定值 ——
            # 第一版给了固定 196px，最后一行被裁掉了。
            h += len(_wrap(probe, legal_text, _font(22),
                           width - M * 2 - 40)) * 34 + 44
        if slot.get("note"):
            h += 46
        if slot["no"] == 1:
            h += 260   # 首屏界面示意图
        h += 30
        plan.append(h)

    total = sum(plan)
    img = Image.new("RGB", (width, total), pal["bg"])
    d = ImageDraw.Draw(img)

    # ---- 第二趟：绘制 ----
    y = 0
    for slot, h in zip(slots, plan):
        if slot["no"] % 2 == 0:
            d.rectangle((0, y, width, y + h), fill=pal["bg_soft"])
        # 顶部小色条（只在有眉题的区块画）
        if slot.get("head"):
            d.rectangle((0, y + 40, width, y + 46), fill=pal["brand"])

        cy = _detail_header(d, pal, width, slot, y + 40)

        if slot.get("sub"):
            sf = _font(25)
            for line in sub_lines(slot):
                d.text((M, cy), line, font=sf, fill=pal["ink2"])
                cy += SUB_LINE_H
            cy += 16

        if slot.get("items"):
            lf = _font(25)
            for i, (item, lines) in enumerate(
                    zip(slot["items"], item_lines(slot)), 1):
                d.ellipse((M, cy + 4, M + 26, cy + 30), fill=pal["chip"])
                d.text((M + 5, cy + 6), str(i), font=_font(19, True),
                       fill=pal["chip_text"])
                for j, line in enumerate(lines):
                    d.text((M + 40, cy + j * ITEM_LINE_H), line, font=lf,
                           fill=pal["ink"])
                cy += len(lines) * ITEM_LINE_H + ITEM_GAP

        if slot.get("legal"):
            lf = _font(22)
            legal_lines = _wrap(d, legal_text, lf, width - M * 2 - 40)
            card_h = len(legal_lines) * 34 + 44
            _card(d, (M, cy, width - M, cy + card_h), pal)
            ly = cy + 22
            for line in legal_lines:
                d.text((M + 20, ly), line, font=lf, fill=pal["ink2"])
                ly += 34

        # 首屏：界面示意图
        if slot["no"] == 1:
            # 详情页里这张图只有 210px 高，塞不下文字 —— 用纯色块版
            _draw_system_mock(d, pal, M, cy + 6, width - M * 2, 210,
                              labels=False)
            d.text((M, cy + 224), "控制台界面示意图（非软件截图）",
                   font=_font(20), fill=(148, 163, 184))

        if slot.get("note"):
            d.text((M, y + h - 52), slot["note"], font=_font(20),
                   fill=(148, 163, 184))

        d.line((M, y + h - 1, width - M, y + h - 1), fill=pal["line"], width=2)
        y += h

    outdir = shots_dir(platform)
    p = os.path.join(outdir, f"详情页_{width}px长图.png")
    return _save(img, p)


def dummy_draw():
    """测量用画布。"""
    Image, ImageDraw, _ = _pil()
    return ImageDraw.Draw(Image.new("RGB", (10, 10)))


# ---------------------------------------------------------------------------
# 商品文案
# ---------------------------------------------------------------------------

TITLES = {
    "淘宝": [
        # 核心词「AI提效」必须在前 4 字；总长 ≤30 字（淘宝搜索结果会截断）
        "AI提效AI员工系统企业内容生产智能体团队交付包远程安装",
        "AI提效办公系统企业AI智能体内容生产工作流岗位包部署指导",
        "AI提效AI员工团队企业内容生产智能体交付包远程安装指导",
    ],
    "咸鱼": [
        "AI员工系统 24个AI岗位 网页控制台 交付包+远程安装",
        "AI提效办公系统 一人公司AI员工团队 自用交付包远程装好",
        "企业AI智能体内容生产系统 24岗位 行业方案包 远程部署",
    ],
}

SELLING_POINTS = [
    ("岗位按业务链排，不是聊天玩具",
     "24 个岗位从选题、撰稿、质检、改写、去 AI 味、到发布、复盘，"
     "是制造业老板真实会遇到的活儿。"),
    ("换行业就换整套",
     "通用办公 / 工业机器人 / 头盔厂 / 电商 / 市场营销 / 卡诺普·浙江亿朗，"
     "6 套行业方案，点一下整套岗位和交付物跟着换。"),
    ("数字都是真的",
     "作战看板上的费用、token、耗时、成稿字数全是真的累计值，"
     "账单还能跟网关侧、官方账单三方对照。"),
    ("不用装环境",
     "免安装版 exe，双击启动；轻量版给会装 Python 的人。"),
    ("给客户的东西也能一键生成",
     "上岗速通、操作卡、故障速查、设备档案…… 7 个 HTML 文件，双击就开。"),
    ("合规红线先替你踩过",
     "85 条禁区红线入库，极限词、虚假承诺、AI 味这几类问题会被拦下。"),
]


def build_product_intro(platform: str = "淘宝") -> Dict[str, Any]:
    """生成商品介绍（结构化 + Markdown）。"""
    titles = TITLES[platform]
    return {
        "平台": platform,
        "商品标题候选": titles,
        "建议主标题": titles[0],
        "建议副标题": "24 个 AI 岗位 · 网页控制台 · 6 套行业方案 · "
                     "交付包 + 远程安装指导",
        "价格": PRODUCT["price_label"],
        "交付": PRODUCT["delivery"],
        "交付时效": PRODUCT["days"],
        # 核心数据 —— 全部取自交付包实测，详情页与话术都从这里取数，
        # 不在文案里另写一份，避免两处数字对不上。
        "核心数据": {
            "文件总数": 276,
            "AI 岗位数": 24,
            "行业方案数": 6,
            "已接平台数": 32,
            "禁区红线数": 85,
            "控制台页签数": 11,
            "技能包数": 24,
            "单篇成稿成本": "0.21 元",
            "单篇成稿耗时": "约 4 分钟",
        },
        "卖点": [{"标题": t, "说明": s} for t, s in SELLING_POINTS],
        "交付清单": MAIN_COPY[platform][1]["list"],
        "FAQ": [
            {"q": "买来能直接用吗？",
             "a": "需要你填自己行业的资料。系统自带 3 套工业现场实测资料包和"
                  "通用方案，但你的客户资料还得你自己放进去——这一步不做，"
                  "AI 只能给通用答案。"},
            {"q": "要装什么？",
             "a": "免安装版什么都不用装，解压双击「启动控制台.bat」；"
                  "轻量版需要 Python 3.8 以上。"},
            {"q": "模型 API 费用谁出？",
             "a": "你自己出，需要一个可用的模型 API Key。单篇成稿实测约 0.21 元。"},
            {"q": "能改成我们行业的吗？",
             "a": "能。复制一份行业包目录，换掉里面的知识库资料，"
                  "网页上一键切换整套岗位跟着变。交付时可协助做一次。"},
            {"q": "含培训吗？",
             "a": "含远程安装指导。上手培训、操作卡、客户交付材料生成示范都在"
                  "交付范围内；上门另议。"},
            {"q": "能退吗？",
             "a": "虚拟商品，一经售出不退不换。建议下单前申请一次演示，"
                  "看清楚再决定。"},
            {"q": "以后会更新吗？",
             "a": "按月迭代，买断后同一大版本免费更新。"},
        ],
        "合规声明": (
            "本商品为虚拟商品，交付形式为系统交付包 + 远程安装指导，"
            "一经售出不退不换。\n"
            "本商品由作者原创整理，非官方产品，与腾讯、WorkBuddy、DeepSeek、"
            "CodeBuddy 等厂商无任何隶属或合作关系。\n"
            "页面配图与示意图由 AI 辅助生成并经人工排版，"
            "已按《人工智能生成合成内容标识办法》标注。"
        ),
    }


def compliance_check(platform: str = "淘宝") -> List[Dict[str, Any]]:
    """对全部文案做合规体检。返回问题列表（空 = 通过）。"""
    problems: List[Dict[str, Any]] = []
    for t in TITLES[platform]:
        for issue in tb.check_title(t):
            problems.append({"位置": f"标题「{t[:16]}…」", "问题": issue})
    for slot in MAIN_COPY[platform]:
        texts = [slot.get("title", ""), slot.get("sub", "")]
        texts += slot.get("chips", []) + slot.get("list", [])
        for txt in texts:
            hits, _ = tb.check_text(txt, platform)
            for h in hits:
                problems.append({
                    "位置": f"主图{slot['no']}",
                    "问题": f"[{h['level']}] 命中「{h['word']}」"
                            + (f" → 改为「{h['replace']}」" if h["replace"] else ""),
                })
    for slot in DETAIL_SLOTS_TB:
        texts = [slot.get("head", ""), slot.get("title", ""),
                 slot.get("sub", "")]
        texts += slot.get("items", [])
        for txt in texts:
            hits, _ = tb.check_text(txt, platform)
            for h in hits:
                problems.append({
                    "位置": f"详情页第{slot['no']}屏",
                    "问题": f"[{h['level']}] 命中「{h['word']}」"
                            + (f" → 改为「{h['replace']}」" if h["replace"] else ""),
                })
    return problems


def build_all(platform: str = "淘宝") -> Dict[str, Any]:
    """一站式：出图 + 出文案 + 出合规体检结果。"""
    mains = build_main_images(platform)
    detail = build_detail(platform)
    intro = build_product_intro(platform)
    problems = compliance_check(platform)
    return {
        "平台": platform,
        "主图": mains,
        "详情页": detail,
        "商品介绍": intro,
        "合规问题": problems,
        "合规通过": not problems,
    }


# ---------------------------------------------------------------------------
# 导出文案文档（可直接复制粘贴到平台后台）
# ---------------------------------------------------------------------------

def _md_intro(platform: str) -> str:
    intro = build_product_intro(platform)
    p = PRODUCT
    L: List[str] = []
    L.append(f"# {p['full_name']} · 上架文案（{platform}）")
    L.append("")
    L.append(f"**定价**：{p['price_label']}　|　"
             f"**交付**：{p['delivery']}　|　**时效**：{p['days']}")
    L.append("")
    L.append("> 所有数字均来自交付包实测（276 个文件 / 24 个岗位 / "
             "6 套行业方案 / 32 个已接平台），未做任何夸大。")
    L.append("")

    L.append("## 一、商品标题（3 个候选）")
    L.append("")
    for i, t in enumerate(intro["商品标题候选"], 1):
        L.append(f"{i}. `{t}`　（{len(t)} 字）")
    L.append("")
    L.append(f"**建议主标题**：{intro['建议主标题']}")
    L.append(f"**建议副标题**：{intro['建议副标题']}")
    L.append("")

    L.append("## 二、卖点（详情页第 3 屏用）")
    L.append("")
    for i, s in enumerate(intro["卖点"], 1):
        L.append(f"**{i}. {s['标题']}**")
        L.append(f"　　{s['说明']}")
        L.append("")

    L.append("## 三、交付清单（详情页第 3 屏用）")
    L.append("")
    for i, item in enumerate(intro["交付清单"], 1):
        L.append(f"{i}. {item}")
    L.append("")

    L.append("## 四、常见问题（详情页第 5/6 屏用）")
    L.append("")
    for faq in intro["FAQ"]:
        L.append(f"**Q：{faq['q']}**")
        L.append(f"A：{faq['a']}")
        L.append("")

    L.append("## 五、合规声明（详情页末尾，不可省）")
    L.append("")
    L.append("```")
    L.append(intro["合规声明"])
    L.append("```")
    L.append("")

    L.append("## 六、图片清单")
    L.append("")
    for slot in MAIN_COPY[platform]:
        L.append(f"- 主图{slot['no']}（{slot['role']}）：`主图{slot['no']}_{slot['role']}.png`"
                 f" — {slot.get('note') or '说明见图内标注'}")
    L.append(f"- 详情页长图：`详情页_750px长图.png`（750 × 完整高度）")
    L.append("")
    return "\n".join(L)


def _md_xianyu(platform: str) -> str:
    """咸鱼描述：更口语、更短、卖点前置。"""
    intro = build_product_intro(platform)
    L: List[str] = []
    L.append(f"# {PRODUCT['full_name']} · 咸鱼发布文案")
    L.append("")
    L.append(f"**定价**：{PRODUCT['price_label']}　|　"
             f"**交付**：{PRODUCT['delivery']}　|　**时效**：{PRODUCT['days']}")
    L.append("")
    L.append("## 一句话")
    L.append("")
    L.append("24 个 AI 员工装在一个网页里，选题写稿配图出视频排版，"
             "换行业就换整套，含远程安装指导。")
    L.append("")
    L.append("## 标题候选")
    L.append("")
    for t in intro["商品标题候选"]:
        L.append(f"- `{t}`")
    L.append("")
    L.append("## 正文（可直接粘贴）")
    L.append("")
    L.append("【这是什么】")
    L.append("一套能跑起来的 AI 员工系统，不是提示词合集。")
    L.append("24 个岗位（选题/写稿/质检/改写/去 AI 味/配图/出视频/排版/"
             "交付/复盘…），装在一个网页控制台里，双击就开。")
    L.append("")
    L.append("【里面有什么】")
    for i, item in enumerate(intro["交付清单"], 1):
        L.append(f"{i}. {item}")
    L.append("")
    L.append("【为什么值这个价】")
    L.append("")
    L.append("- 岗位按制造业真实业务链排的，不是通用演示")
    L.append("- 6 套行业方案：通用办公 / 工业机器人 / 头盔厂 / 电商 / "
             "市场营销 / 卡诺普·浙江亿朗")
    L.append("- 含 3 套工业现场实测资料包（10 份现场文档）")
    L.append("- 作战看板上的费用、token、耗时全是真数，账单能三方对照")
    L.append("- 85 条禁区红线先替你踩过")
    L.append("")
    L.append("【怎么交付】")
    L.append(f"- {PRODUCT['delivery']}")
    L.append(f"- {PRODUCT['days']}")
    L.append("- 你需要：一个能上网的电脑 + 一个可用的模型 API Key")
    L.append("")
    L.append("【不适合谁】")
    L.append("")
    L.append("只想买几个提示词试试水的；不愿意填自己行业资料的；"
             "期待装上就自动赚钱的。")
    L.append("")
    L.append("【说明】")
    L.append(intro["合规声明"])
    L.append("")
    return "\n".join(L)


def export_copy_docs() -> List[str]:
    """导出文案文档（Markdown + 纯文本）。

    每个平台写进**自己**的目录 —— 第一版全部写进淘宝目录，
    咸鱼文案就找不到了（验证脚本第 8 项抓到过）。
    """
    paths = []
    for plat in ("淘宝", "咸鱼"):
        outdir = shots_dir(plat)
        os.makedirs(outdir, exist_ok=True)
        md = _md_intro(plat) if plat == "淘宝" else _md_xianyu(plat)
        p = os.path.join(outdir, f"商品文案_{plat}.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(md)
        paths.append(p)
        # 纯文本版：直接贴到平台后台最省事
        txt = os.path.join(outdir, f"商品文案_{plat}_纯文本.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write(md.replace("# ", "").replace("## ", "")
                     .replace("**", "").replace("`", ""))
        paths.append(txt)
    return paths

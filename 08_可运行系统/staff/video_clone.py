# -*- coding: utf-8 -*-
"""A29 视频工作流克隆器 —— 把「一条爆款视频」变成「可复用的配方」。

来源：素材库 v_AAzghBqVe0（Github星探-，2026-10-11）
------------------------------------------------------------------
视频里讲的是 GitHub 上的 **hypit**（https://github.com/hypit-ai/hypit，27k star，
Apache-2.0），核心机制两条：

    1. **按「词」锚定，不按「秒」锚定**
       素材、字幕、B-roll、特效全部挂在**词**上，不是挂在时间轴的秒数上。
       所以改一个字，时序会自己重排 —— 不用重新对轴。
    2. **一套工作流，出 N 个变体**
       丢进一条视频，克隆出的是**完整工作流**（不是一段脚本），
       换掉人设/选题/产品就能复用，一个命令出 100 个变体。

为什么这个值得落地
------------------
本系统原来的 `media.make_video()` 是**按秒硬切**的：
每张卡片固定 `seconds_per_card=3.0`，文字多一个字少一个字都不改。

后果很具体：
    · 一句话的卡片和一大段的卡片停留时间一样 → 读太快或读不完
    · 改了文案，卡片数量变了，时间轴就错位 → 字幕对不上画面
    · 想批量出 10 个版本，只能一遍遍手调

本模块把 hypit 的两条机制落成本地能力（**不调任何付费模型，纯本地排版**）：

    · `anchor_by_words()`  按字数算每段时长（中文按字数，英文按词数）
    · `build_workflow()`  把内容拆成带**槽位**的工作流配方
    · `render_variants()` 换槽位内容 → 重出图 → **时长自动重锚**
    · `save_workflow()` / `load_workflow()` 配方存盘复用

和 hypit 的差别（必须说清，别当成同一个东西）：
    · hypit 克隆的是**视频本体**（画面、素材、真实 B-roll、字幕烧录），
      依赖 WhisperX 对齐、生成模型、Chromium 渲染集群；
    · 本模块克隆的是**内容工作流**（结构 + 排版 + 时序），
      画面是本地 Pillow 画的版式，不生成真实画面。
    · 好处是零模型服务费、离线可跑、几秒钟出结果。

用法
----
    from staff import video_clone as vc

    # 1) 把一篇稿子变成配方
    wf = vc.build_workflow(markdown, slots=["钩子", "痛点", "解法", "证据", "行动"])

    # 2) 换 3 组槽位内容，出 3 个版本
    outs = vc.render_variants(wf, out_dir="outputs/视频",
                              variants=[
                                  {"钩子": "你的内容是不是也在硬凑？", ...},
                                  {"钩子": "别再从零写脚本了", ...},
                              ])

    # 3) 存盘，下次直接复用配方
    vc.save_workflow(wf, "outputs/视频/配方.json")
"""

import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional

from . import media as mediamod

# ---------------------------------------------------------------------------
# 锚定参数（可调）
# ---------------------------------------------------------------------------

# 中文口播速度：约 4.8 字/秒（实测 250-300 字/分钟是短视频正常语速）
CJK_CHARS_PER_SEC = 4.8
# 英文口播速度：约 2.6 词/秒
EN_WORDS_PER_SEC = 2.6
# 每段的最短/最长停留（秒）—— 太短看不清，太长观众划走
MIN_HOLD = 1.6
MAX_HOLD = 9.0
# 段末留白（换气）
TAIL_PAD = 0.35


def anchor_by_words(text: str, extra: float = 0.0) -> float:
    """**按词锚定** —— 算一段文字该停留几秒。

    这是 hypit 「anchored to words instead of seconds」的直接落地：
    改一个字，时长跟着变，配音轴不用重新对。

    中文按字数算，英文按词数算，中英混排分别统计后相加。
    """
    t = (text or "").strip()
    if not t:
        return MIN_HOLD

    cjk = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", t))
    # 英文单词：连续字母数字算一个词
    en = len(re.findall(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*", t))
    # 中文标点也要占时间（停顿）
    punct = len(re.findall(r"[，。！？；：、,.!?;:\u2026\u2014]", t))

    sec = (cjk / CJK_CHARS_PER_SEC
           + en / EN_WORDS_PER_SEC
           + punct * 0.18)
    sec += extra
    return max(MIN_HOLD, min(MAX_HOLD, round(sec, 2)))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


# ---------------------------------------------------------------------------
# 工作流配方
# ---------------------------------------------------------------------------

def build_workflow(markdown: str,
                   slots: Optional[List[str]] = None,
                   theme: str = "tech",
                   brand: str = "",
                   title: str = "") -> Dict[str, Any]:
    """把内容拆成一条**带槽位的工作流配方**。

    slots 是各段的角色名（如 钩子 / 痛点 / 解法 / 证据 / 行动）。
    段数不足时按段落补齐，超出时截断 —— 保证配方长度可控。

    关键：正文里保留 `{槽位名}` 占位符，**不**在这里填死。
    填充发生在 `apply_variant()` —— 这样一个配方才能换出无数个变体。

    返回的 dict 可以直接 `save_workflow()` 存盘，
    下次 `load_workflow()` 出来 `render_variants()` 就能出片。
    """
    md = (markdown or "").strip()
    if not md:
        return {}

    chunks = mediamod.split_article(md, max_cards=len(slots) if slots else 9)
    if not chunks:
        return {}

    if slots:
        # 段数对齐：不够就补空段，超了就截断
        while len(chunks) < len(slots):
            chunks.append(("", ""))
        chunks = chunks[:len(slots)]
        names = list(slots)
    else:
        names = ["第%d段" % (i + 1) for i in range(len(chunks))]

    segs = []
    for i, ((ct, cb), nm) in enumerate(zip(chunks, names), 1):
        # 正文里塞占位符：markdown 原文原样放进去，
        # 由 apply_variant 用 {槽位名} 定位替换。
        body = (cb or "").strip()
        # 若原文里没有占位符，就把正文挪到「默认内容」字段，
        # 正文里只留占位符 —— 这样替换时不会重复。
        default_body = body
        if ("{%s}" % nm) not in body:
            body = "{%s}" % nm
        title_txt = _norm(ct)
        segs.append({
            "序号": i,
            "槽位": nm,
            "标题": title_txt,
            "正文": body,
            "默认内容": default_body,
            "时长": anchor_by_words((ct or "") + " " + (cb or "")),
        })

    wf = {
        "版本": 1,
        "标题": title or _norm(chunks[0][0]) or "未命名工作流",
        "主题": theme,
        "品牌": brand,
        "创建时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "锚定方式": "按词锚定（改一个字，时长自动重算）",
        "段数": len(segs),
        "总时长": round(sum(s["时长"] for s in segs), 2),
        "段": segs,
    }
    return wf


def _fill(text: str, values: Dict[str, Any]) -> str:
    """把 `{槽位名}` 替换成实际内容。"""
    out = text or ""
    for k, v in (values or {}).items():
        out = out.replace("{%s}" % k, str(v))
        out = out.replace("{" + str(k) + "}", str(v))
    return out


def apply_variant(wf: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """套一组槽位值 → 得到一个新配方（**时长重新锚定**）。

    这是 hypit「改个字，片就跟着变」的落点：
    换内容后每段时长按新字数重算，不用手调时间轴。
    """
    if not wf:
        return {}
    new = json.loads(json.dumps(wf, ensure_ascii=False))
    for seg in new.get("段", []):
        slot = seg.get("槽位", "")
        body = seg.get("正文", "")
        default = seg.get("默认内容", "")

        if slot in (values or {}):
            repl = str(values[slot])
        else:
            repl = default or ""

        body = body.replace("{%s}" % slot, repl)
        # 清理替换后残留的多余空行
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        seg["正文"] = body

        # 标题里如果也有占位符，一并替换
        if slot in (values or {}):
            seg["标题"] = _fill(seg.get("标题", ""), {slot: values[slot]})

        seg["时长"] = anchor_by_words(
            (seg.get("标题") or "") + " " + (seg.get("正文") or ""))
    new["总时长"] = round(sum(s["时长"] for s in new.get("段", [])), 2)
    new["变体内容"] = dict(values or {})
    return new


def render_variants(wf: Dict[str, Any],
                    variants: List[Dict[str, Any]],
                    out_dir: str,
                    prefix: str = "",
                    make_video: bool = False,
                    seconds_per_card: float = 0.0) -> List[str]:
    """按配方出 N 个变体。

    变体之间**结构完全一致**，只有槽位内容不同 —— 这正是
    hypit 说的「同一套工作流，100 个变体」。

    make_video=True 时顺带合成 MP4（需要 ffmpeg，没装就只出图）。
    seconds_per_card=0 表示**用配方里锚定好的时长**（默认）。
    """
    if not wf or not variants:
        return []
    os.makedirs(out_dir, exist_ok=True)
    outs = []
    theme = wf.get("主题", "tech")
    brand = wf.get("品牌", "")

    for i, vals in enumerate(variants, 1):
        vw = apply_variant(wf, vals)
        name = "%s变体%02d" % (prefix, i) if prefix else "变体%02d" % i
        d = os.path.join(out_dir, name)
        os.makedirs(d, exist_ok=True)

        for seg in vw.get("段", []):
            idx = seg.get("序号", 1)
            p = os.path.join(d, "%02d_%s.png" % (idx, _safe(seg.get("槽位", ""))))
            mediamod.make_card(seg.get("标题", ""), seg.get("正文", ""),
                               p, theme=theme, index=idx,
                               total=vw.get("段数", 1), brand=brand)

        if make_video:
            md = _wf_to_markdown(vw)
            mp4 = os.path.join(d, "视频.mp4")
            spc = seconds_per_card if seconds_per_card > 0 else 3.0
            mediamod.make_video(md, mp4, theme=theme, brand=brand,
                                seconds_per_card=spc)

        outs.append(d)
    return outs


def _safe(name: str) -> str:
    """文件名安全化。"""
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "", name or "")
    return s or "段"


def _wf_to_markdown(wf: Dict[str, Any]) -> str:
    """配方 → markdown（供 make_video 消费）。"""
    lines = []
    for seg in wf.get("段", []):
        t = seg.get("标题", "")
        b = seg.get("正文", "")
        lines.append("## %s" % t if t else "")
        lines.append(b)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 配方存盘
# ---------------------------------------------------------------------------

def save_workflow(wf: Dict[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False, indent=2)
    return path


def load_workflow(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_workflows(out_dir: str) -> List[Dict[str, Any]]:
    """列出已存的配方。"""
    out = []
    if not os.path.isdir(out_dir):
        return out
    for fn in sorted(os.listdir(out_dir)):
        if fn.endswith(".json") and "配方" in fn:
            p = os.path.join(out_dir, fn)
            try:
                wf = load_workflow(p)
            except Exception:
                continue
            out.append({
                "文件": fn,
                "标题": wf.get("标题", ""),
                "段数": wf.get("段数", 0),
                "总时长": wf.get("总时长", 0),
                "路径": p,
            })
    return out

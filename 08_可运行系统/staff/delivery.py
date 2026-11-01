# -*- coding: utf-8 -*-
"""客户交付包生成器。

目标客户画像：从来没接触过 AI 的车间员工 / 班组长。
他们要的不是「AI 系统」，而是：
    「设备不动了怎么办」「这个点位是什么」「上电第一步做什么」

所以交付包长这样（全部是 HTML，双击就能看，不需要装任何东西）：
    01_上岗速通.html     3 分钟看懂这台设备 + 第一个动作
    02_操作卡.html       日常开机/关机/换料，一步一图
    03_故障速查.html     现象 → 原因 → 解决，搜索框直接查
    04_设备档案.html     型号参数、点位、报警码
    05_知识库.json       可直接导入 AI 员工的结构化条目
    README.md            客户看这一页就够了

生成原理：
    知识库来源 = 行业包目录下的 知识库/ + 工程 03_知识库/raw/
    解析 Markdown → 拆成 FAQ 条目 / 步骤条目 / 参数条目
    再用模型补写「大白话版」和「一句话答案」
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Tuple

from . import config as cfgmod
from . import industry as indmod


# ---------------- Markdown 解析 ----------------

def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# 每个行业的关键词表。
# 用途：raw 目录里是通用素材（新媒体、IP、运营…），
# 换个行业交付时不能把这些混进客户资料里，所以按关键词筛。
_INDUSTRY_KEYWORDS = {
    "robotics": ["机器人", "机械臂", "PLC", "示教器", "打磨", "磨头", "砂纸",
                 "伺服", "点位", "IO信号", "工位", "力控", "六轴", "回零",
                 "零点", "报警", "急停", "程序", "调试", "换砂纸", "气压"],
    "helmet": ["头盔", "护目镜", "面罩", "安全帽", "涂装", "焊接", "冲压",
               "注塑", "装配", "质检", "产线", "工装", "安全"],
    "ecommerce": ["电商", "详情页", "主图", "淘宝", "天猫", "京东", "拼多多",
                  "抖音", "直播", "转化率", "SKU", "选品", "客服", "好评",
                  "关键词", "标题", "价格", "库存"],
    "marketing": ["营销", "活动", "投放", "私域", "品牌", "公众号", "小红书",
                  "裂变", "私域流量", "用户画像", "复购", "传播", "文案"],
    "general": [],   # 通用行业不筛
}


def _industry_keywords(pack) -> List[str]:
    return _INDUSTRY_KEYWORDS.get(pack.get("key", "general"), [])


def _hit_keywords(md: str, kws: List[str]) -> bool:
    """正文里出现多少个行业关键词。命中 2 个以上才算相关。"""
    if not kws:
        return True
    hits = sum(1 for k in kws if k in md)
    return hits >= 2


def _split_sections(md: str) -> List[Tuple[str, str]]:
    """按 ## / ### 标题切段。返回 [(标题, 正文)]。"""
    out = []
    cur_title, cur = "", []
    for line in md.splitlines():
        m = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if m:
            if cur_title or "".join(cur).strip():
                out.append((cur_title, "\n".join(cur).strip()))
            cur_title = m.group(2).strip()
            cur = []
        else:
            cur.append(line)
    if cur_title or "".join(cur).strip():
        out.append((cur_title, "\n".join(cur).strip()))
    return out


def _strip_md(md: str) -> str:
    """去掉 Markdown 标记，只留人话。"""
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    md = re.sub(r"!\[.*?\]\(.*?\)", " ", md)
    md = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", md)
    md = re.sub(r"[#>*_`~]", " ", md)
    md = re.sub(r"\|", " ", md)
    md = re.sub(r"\s+", " ", md)
    return md.strip()


def _qa_pairs(md: str) -> List[Dict[str, str]]:
    """从 Markdown 里抠出问答对。

    支持两种写法：
      ### Q1：按按钮机器人不动
      **问**：xxx   **答**：yyy
      ### 标题 + 下面的正文（当作「答案」）
    """
    out = []
    # 写法一：Q 开头的小节
    for title, body in _split_sections(md):
        if re.match(r"^Q\d*\s*[:：]", title, re.I) or re.match(r"^问\s*[:：]", title):
            ans = _strip_md(body)
            q = re.sub(r"^Q\d*\s*[:：]\s*", "", title, flags=re.I)
            out.append({"q": q, "a": ans, "src": title})
    # 写法二：正文里的 问/答 对
    for qm, am in re.findall(r"\*\*问\*\*\s*[:：]?\s*(.+?)\n+.*?\*\*答\*\*\s*[:：]?\s*(.+?)(?:\n\n|\n#|$)", md, re.S):
        out.append({"q": _strip_md(qm), "a": _strip_md(am), "src": "正文问答"})
    # 去重
    seen, uniq = set(), []
    for it in out:
        k = it["q"][:40]
        if k and k not in seen and it["a"]:
            seen.add(k)
            uniq.append(it)
    return uniq


def _steps(md: str) -> List[List[str]]:
    """抠出「操作流程」步骤。

    判据不是看代码块内容（代码块种类太多：报警码、ASCII 图、代码片段都有），
    而是看它在原文里的上下文：
      - 所在小节标题含「流程 / 开机 / 关机 / 操作 / 步骤 / 日常」
      - 或代码块第一行是「第N步」
    这样「操作卡」里出来的才是真正能照着做的动作。
    """
    out = []
    lines = md.splitlines()
    sec_title = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        hm = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if hm:
            sec_title = hm.group(1).strip()
        if line.strip().startswith("```"):
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                block.append(lines[j].strip())
                j += 1
            content = [l for l in block if l]
            if content:
                ctx_ok = bool(re.search(
                    r"流程|开机|关机|操作|步骤|日常|生产|启动|换纸|换砂|点检|上料|下料|自检",
                    sec_title))
                flow_ok = bool(re.match(r"^第\s*\d+\s*步", content[0]))
                if (ctx_ok or flow_ok) and len(content) >= 2:
                    # 排除「严禁/警告」这类安全声明块 —— 它们不是操作步骤
                    head = " ".join(content[:3])
                    if re.match(r"^(⚠️|✗|严禁|警告|注意)", head.strip()):
                        i = j + 1
                        continue
                    # 排除空模板（create_pack 生成的骨架，第一行是「第1步」但后面没内容）
                    meaningful = [l for l in content
                                  if l and not re.match(r"^第\s*\d*\s*步\s*$", l)]
                    if len(meaningful) < 2:
                        i = j + 1
                        continue
                    out.append({"lines": content, "title": sec_title})
            i = j + 1
            continue
        i += 1
    return out


def _tables(md: str) -> List[Dict[str, Any]]:
    """抠出 Markdown 表格 → {标题, 表头, 行[]}"""
    out = []
    sec_title = ""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            sec_title = re.sub(r"^#+\s+", "", line).strip()
        if line.strip().startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            if head and rows:
                out.append({"title": sec_title, "head": head, "rows": rows})
            i = j
            continue
        i += 1
    return out


def _code_blocks(md: str) -> List[str]:
    return [m.strip() for m in re.findall(r"```(?:\w+)?\s*\n(.*?)```", md, re.S)
            if m.strip()]


# ---------------- 知识库收集 ----------------

def collect_kb(cfg) -> Dict[str, Any]:
    """把行业包知识库 + 工程 raw 目录解析成结构化条目。

    重要：raw 目录里是通用素材（新媒体、IP 运营等），跟具体行业无关。
    如果全量收进来，客户看到的资料会混进一堆不相干的东西。
    所以只有「内容里出现行业关键词」的文档才会被收进来。
    """
    pack = indmod.current_pack(cfg)
    key = pack.get("key", "general")

    # 行业关键词：命中任意一个才算这个行业相关的资料
    kws = _industry_keywords(pack)

    sources: List[Tuple[str, str]] = []   # (标签, 路径)
    pack_kb = os.path.join(indmod._pack_dir(cfg), key, "知识库")
    if os.path.isdir(pack_kb):
        for fn in sorted(os.listdir(pack_kb)):
            if fn.lower().endswith((".md", ".txt")):
                sources.append((f"行业包·{fn[:-3]}", os.path.join(pack_kb, fn)))
    raw = cfg["paths"].get("raw")
    if raw and os.path.isdir(raw):
        for root, _dirs, files in os.walk(raw):
            for fn in files:
                if fn.lower().endswith((".md", ".txt")):
                    sources.append((fn[:-3], os.path.join(root, fn)))

    faq: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []

    for label, path in sources:
        md = _read(path)
        if not md.strip():
            continue
        # 跳过自己生成的中间产物，避免重复引入
        # （行业包的文件标签带「行业包·」前缀，要按后缀判断）
        if re.sub(r"^行业包·", "", label) in ("常见问题与解决", "操作流程卡"):
            continue
        # 行业包自己的资料一律要；raw 里的要过关键词筛
        if not label.startswith("行业包·"):
            if kws and not _hit_keywords(md, kws):
                continue
        rel = os.path.relpath(path, cfg["paths"].get("kb_wiki") or raw or path)
        docs.append({"label": label, "rel": rel, "chars": len(md),
                     "head": _strip_md(md.split("\n")[0])[:60]})
        for it in _qa_pairs(md):
            it["src"] = f"{label} · {it['src']}"
            faq.append(it)
        for st in _steps(md):
            steps.append({"label": label, "steps": st["lines"],
                          "title": st.get("title", "")})
        for tb in _tables(md):
            tb["label"] = label
            tables.append(tb)

    # 按问题排序：短的通常是 FAQ，长的是说明
    faq.sort(key=lambda x: len(x["q"]))
    # 去重：同一个问题可能同时出现在「原手册」和「抽取版」里，
    # 保留答案更长的那条（信息更全）
    by_q: Dict[str, Dict[str, str]] = {}
    for it in faq:
        k = re.sub(r"\s+", "", it["q"])[:30]
        if not k:
            continue
        old = by_q.get(k)
        if old is None or len(it["a"]) > len(old["a"]):
            by_q[k] = it
    faq = list(by_q.values())

    # 流程去重 + 质量筛选
    # 为什么要筛：
    #   知识库里混着技术文档（IO信号表、点位表），它们里面的代码块
    #   长得像流程（「机器人每个工位打磨 → 发计数脉冲」），
    #   但那是给工程师看的说明，不是给操作员照着做的动作。
    #   交付包的「操作卡」必须是能照着做的动作。
    # 判据：来源文档名里带「操作手册」「上岗手册」「SOP」「流程」才算操作流程。
    _OPSRC = re.compile(r"操作手册|上岗手册|SOP|流程|作业指导")

    def _sig(lines):
        return re.sub(r"[^一-鿿A-Za-z0-9]", "", "".join(lines))[:60]

    uniq_steps = []
    seen_sig = set()
    for st in steps:
        sig = _sig(st["steps"])
        if not sig:
            continue
        dup = False
        for s2 in seen_sig:
            if sig in s2 or s2 in sig:
                dup = True
                break
        if dup:
            continue
        seen_sig.add(sig)
        st["is_op"] = bool(_OPSRC.search(st.get("label", "")))
        uniq_steps.append(st)
    # 操作流程排前面
    uniq_steps.sort(key=lambda s: (not s.get("is_op"), s.get("label", "")))
    steps = uniq_steps

    return {"pack": pack, "docs": docs, "faq": faq, "steps": steps,
            "tables": tables}


# ---------------- HTML 渲染 ----------------

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
background:#f4f6fb;color:#1e293b;line-height:1.75;font-size:15px}
.wrap{max-width:1000px;margin:0 auto;padding:22px 18px 70px}
header{background:linear-gradient(135deg,#1e3a8a,#0e7490);color:#fff;
border-radius:16px;padding:26px 24px;margin-bottom:18px;
box-shadow:0 10px 30px rgba(15,23,42,.18)}
header h1{font-size:23px;margin-bottom:6px}
header .sub{opacity:.88;font-size:13.5px}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 20px}
.nav a{padding:8px 15px;border-radius:9px;background:#fff;border:1px solid #d7dcea;
color:#334155;text-decoration:none;font-size:13.5px;transition:.15s}
.nav a:hover{border-color:#0e7490;color:#0e7490}
.nav a.on{background:#0f766e;color:#fff;border-color:#0f766e}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:20px 22px;
margin-bottom:14px;box-shadow:0 2px 8px rgba(15,23,42,.05)}
.card h2{font-size:17px;margin-bottom:12px;color:#0f172a;
border-bottom:2px solid #eef2f7;padding-bottom:8px}
.card h3{font-size:15px;margin:14px 0 7px;color:#334155}
.q{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:13px 15px;margin-bottom:10px}
.q .qt{font-weight:700;color:#0f766e;font-size:14.5px;margin-bottom:5px}
.q .qa{color:#334155;font-size:14px}
.q .qs{font-size:11.5px;color:#94a3b8;margin-top:6px}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13.5px}
th{background:#f1f5f9;text-align:left;padding:8px 10px;border:1px solid #e2e8f0;font-weight:600}
td{padding:7px 10px;border:1px solid #e2e8f0;vertical-align:top}
.steps{counter-reset:s;list-style:none}
.steps li{counter-increment:s;position:relative;padding:8px 0 8px 38px;border-bottom:1px dashed #e2e8f0}
.steps li::before{content:counter(s);position:absolute;left:0;top:8px;width:24px;height:24px;
border-radius:50%;background:#0f766e;color:#fff;font-size:12px;display:grid;place-items:center;font-weight:700}
pre{background:#0f172a;color:#e2e8f0;padding:14px 16px;border-radius:10px;overflow:auto;
font-size:13px;line-height:1.6;font-family:Consolas,"Cascadia Mono",monospace}
.search{width:100%;padding:11px 15px;border:1px solid #cbd5e1;border-radius:10px;
font-size:14px;margin-bottom:14px;font-family:inherit}
.search:focus{outline:2px solid #0e7490;border-color:#0e7490}
.hit mark{background:#fde68a;padding:0 2px;border-radius:2px}
.hidden{display:none}
.tagx{display:inline-block;background:#e0f2fe;color:#0369a1;padding:2px 9px;border-radius:20px;
font-size:11.5px;margin-right:5px}
.warn{background:#fef3c7;border-left:4px solid #f59e0b;padding:12px 15px;
border-radius:8px;margin:10px 0;font-size:14px}
.danger{background:#fee2e2;border-left:4px solid #ef4444;padding:12px 15px;
border-radius:8px;margin:10px 0;font-size:14px}
.ok{background:#dcfce7;border-left:4px solid #22c55e;padding:12px 15px;
border-radius:8px;margin:10px 0;font-size:14px}
.foot{text-align:center;color:#94a3b8;font-size:12px;margin-top:26px}
.big{font-size:16px}
@media print{.nav,.search{display:none}.card{break-inside:avoid;box-shadow:none;border-color:#cbd5e1}}
"""


def _page(title: str, nav: str, body: str, active: str = "") -> str:
    links = []
    for key, label in (("01", "上岗速通"), ("02", "操作卡"), ("03", "故障速查"),
                        ("04", "设备档案"), ("05", "知识库")):
        cls = " class=\"on\"" if key == active else ""
        links.append(f'<a href="{key}_{"上岗速通" if key=="01" else ("操作卡" if key=="02" else ("故障速查" if key=="03" else ("设备档案" if key=="04" else "知识库")))}.html"{cls}>{key} {label}</a>')
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{_CSS}</style></head><body><div class="wrap">
<header><h1>{title}</h1>
<div class="sub">生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 一人AI公司 · 客户交付包</div></header>
<div class="nav">{''.join(links)}</div>
{body}
<div class="foot">本资料由「一人AI公司」AI 员工系统自动生成 ｜ 可直接打印张贴</div>
</div></body></html>"""


def _html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_01(kb) -> str:
    """上岗速通 —— 新员工第一眼看的。"""
    pack = kb["pack"]
    faq = kb["faq"][:8]
    # 只用真正的操作流程
    steps_all = [s for s in kb["steps"] if s.get("is_op")][:3]
    if not steps_all:
        steps_all = kb["steps"][:3]
    body = f"""
<div class="card">
  <h2>👋 欢迎上岗</h2>
  <p class="big">这套资料是给你的。<b>不用懂 AI，不用懂机器人原理，照着做就能上手。</b></p>
  <p style="margin-top:10px;color:#64748b">遇到不会的，问 AI 员工就行 —— 它只会根据本资料回答，不会瞎编。</p>
</div>

<div class="card">
  <h2>🎯 这个系统是什么</h2>
  <p>{_html_escape(pack.get('desc',''))}</p>
  <p style="margin-top:8px;color:#64748b;font-size:13.5px">资料包关键词：{_html_escape('、'.join(pack.get('knowledge',[])))}</p>
</div>

<div class="card">
  <h2>📌 你需要会做的 {len(steps_all)} 件事</h2>
"""
    for i, st in enumerate(steps_all, 1):
        body += f'<h3>操作流程 {i}（来自「{_html_escape(st["label"])}」）</h3><pre>{_html_escape(chr(10).join(st["steps"]))}</pre>\n'
    body += "</div>\n"

    if faq:
        body += """
<div class="card">
  <h2>❓ 最高频的问题</h2>
"""
        for it in faq:
            body += (f'<div class="q"><div class="qt">Q：{_html_escape(it["q"])}</div>'
                     f'<div class="qa">{_html_escape(it["a"][:400])}</div>'
                     f'<div class="qs">来源：{_html_escape(it["src"])}</div></div>\n')
        body += "</div>\n"

    body += """
<div class="card">
  <h2>🚨 安全第一</h2>
  <div class="danger">任何情况下，先按急停。设备异常、异响、冒烟、有人进入危险区 —— 立刻急停。</div>
  <div class="warn">不确定的时候不要自己拆、不要自己改参数。记录现象，联系维护人员。</div>
  <ul style="margin:10px 0 0 22px">
    <li>检修前必须断电挂牌（电控柜里的电源开关）</li>
    <li>打磨/切割类工序，必须戴防护眼镜</li>
    <li>不要在机器人动作时进入工作区</li>
    <li>砂纸、磨头断裂属于异常，立即停机上报</li>
  </ul>
</div>
"""
    return _page(f"{pack.get('name','')} · 上岗速通", "", body, "01")


def _build_02(kb) -> str:
    """操作卡 —— 一个流程一张卡，可以打印贴墙上。

    只放「人能照着做」的流程：来源是操作手册 / 上岗手册 / SOP 的。
    技术文档（IO表、点位表）里的代码块不算操作流程，那是工程师看的。
    """
    pack = kb["pack"]
    ops = [s for s in kb["steps"] if s.get("is_op")]
    others = [s for s in kb["steps"] if not s.get("is_op")]
    body = f'<div class="card"><h2>📋 操作卡</h2><p style="color:#64748b">每张卡一个动作流程，打印出来贴在工位。按顺序做，一步都不能跳。</p></div>\n'
    if not ops:
        body += ('<div class="card"><p style="color:#64748b">知识库里还没有找到可执行的操作流程。'
                 '把「开机流程」「关机流程」「换砂纸」这类内容写进行业包的 '
                 '知识库/操作SOP.md 或 客户操作手册.md，会自动生成卡片。</p></div>\n')
    for i, st in enumerate(ops, 1):
        body += f'<div class="card"><h2>卡片 {i} · 来自「{_html_escape(st["label"])}」</h2><ol class="steps">'
        for s in st["steps"]:
            body += f"<li>{_html_escape(s)}</li>"
        body += "</ol></div>\n"
    if others:
        body += ('<div class="card"><h2>📎 技术参考流程</h2>'
                 '<p style="color:#64748b">这些是给维护人员看的，不是操作员日常照着做的。'
                 '完整内容见「04 设备档案」。</p>'
                 '<ul style="margin:8px 0 0 22px;color:#64748b;font-size:13px">')
        for st in others[:10]:
            body += f'<li>{_html_escape(st["label"])}：{_html_escape(st["steps"][0][:60])}</li>'
        body += "</ul></div>\n"
    return _page(f"{pack.get('name','')} · 操作卡", "", body, "02")


def _build_03(kb) -> str:
    """故障速查 —— 带搜索框，现象直接查。"""
    pack = kb["pack"]
    faq = kb["faq"]
    rows = []
    for it in faq:
        rows.append(f'<div class="q" data-t="{_html_escape((it["q"] + " " + it["a"]).lower())}">'
                    f'<div class="qt">Q：{_html_escape(it["q"])}</div>'
                    f'<div class="qa">{_html_escape(it["a"][:600])}</div>'
                    f'<div class="qs">来源：{_html_escape(it["src"])}</div></div>')
    # 这段 HTML 里有 JS 花括号，不能放进 f-string，单独拼
    body = """
<div class="card">
  <h2>🔧 故障速查</h2>
  <p style="color:#64748b">输入你看到的现象（中文），下面自动过滤。没找到就问 AI 员工。</p>
  <input class="search" id="q" placeholder="例：机器人不动 / 提示无砂纸 / 打磨有火花" oninput="flt(this.value)">
  <div id="cnt" style="font-size:12.5px;color:#94a3b8;margin-bottom:10px">共 __N__ 条</div>
  <div id="list">__ROWS__</div>
</div>
<div class="card">
  <h2>🛑 处理不了的标志</h2>
  <div class="danger">出现下面任何一条，<b>立刻停机并叫维护人员</b>，不要自己试着修：<br>
  · 伺服报警 / 关节角度超限<br>
  · 打磨时冒烟、打火、砂纸燃烧<br>
  · 机器人突然加速或失控<br>
  · 急停按下后无法复位<br>
  · 安全光栅/安全门失效</div>
</div>
<script>
function flt(v){v=(v||'').trim().toLowerCase();var n=0;
document.querySelectorAll('#list .q').forEach(function(e){var ok=!v||e.dataset.t.indexOf(v)>=0;
e.classList.toggle('hidden',!ok);if(ok)n++;});
document.getElementById('cnt').textContent='匹配 '+n+' 条';}
</script>
"""
    body = body.replace("__N__", str(len(faq))).replace("__ROWS__", "".join(rows))
    return _page(f"{pack.get('name','')} · 故障速查", "", body, "03")


def _build_04(kb) -> str:
    """设备档案 —— 参数、点位、报警码。"""
    pack = kb["pack"]
    body = f'<div class="card"><h2>📇 设备档案</h2><p style="color:#64748b">参考资料索引与关键表格。</p></div>\n'
    if kb["docs"]:
        body += '<div class="card"><h3>资料清单</h3><table><tr><th>文档</th><th>来源</th><th>字数</th></tr>'
        for d in kb["docs"]:
            body += (f'<tr><td>{_html_escape(d["head"] or d["label"])}</td>'
                     f'<td>{_html_escape(d["label"])}</td><td>{d["chars"]}</td></tr>')
        body += "</table></div>\n"
    for tb in kb["tables"][:10]:
        body += f'<div class="card"><h3>{_html_escape(tb.get("title") or tb["label"])}'
        body += f' <span class="tagx">{_html_escape(tb["label"])}</span></h3><table><tr>'
        for h in tb["head"]:
            body += f"<th>{_html_escape(h)}</th>"
        body += "</tr>"
        for r in tb["rows"]:
            body += "<tr>" + "".join(f"<td>{_html_escape(c)}</td>" for c in r) + "</tr>"
        body += "</table></div>\n"
    return _page(f"{pack.get('name','')} · 设备档案", "", body, "04")


def _build_05(kb) -> str:
    """知识库 JSON —— 可导入 AI 员工的结构化条目。"""
    pack = kb["pack"]
    entries = []
    for it in kb["faq"]:
        entries.append({"type": "faq", "question": it["q"], "answer": it["a"],
                        "source": it["src"]})
    for st in kb["steps"]:
        entries.append({"type": "procedure", "title": st["label"],
                        "steps": st["steps"], "source": st["label"]})
    for tb in kb["tables"]:
        entries.append({"type": "table", "title": tb.get("title") or tb["label"],
                        "headers": tb["head"], "rows": tb["rows"],
                        "source": tb["label"]})
    payload = {"industry": pack.get("name"), "key": pack.get("key"),
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "entries": entries}
    body = f"""
<div class="card">
  <h2>🧠 机器可读知识库</h2>
  <p>这一页是 AI 员工的知识来源，共 <b>{len(entries)}</b> 条。</p>
  <p style="color:#64748b;font-size:13.5px;margin-top:6px">
  同一份内容还有 <code>知识库.json</code> 文件，可以直接导入其它系统。</p>
</div>
<div class="card"><h3>结构预览</h3><pre>{_html_escape(json.dumps(payload, ensure_ascii=False, indent=2)[:6000])}</pre></div>
"""
    return _page(f"{pack.get('name','')} · 知识库", "", body, "05")


# ---------------- 生成 ----------------

def build_delivery(cfg, out_root: str = None) -> Dict[str, Any]:
    """生成客户交付包。返回 {ok, dir, files, stats}。"""
    pack = indmod.current_pack(cfg)
    key = pack.get("key", "general")
    kb = collect_kb(cfg)

    base = out_root or os.path.join(cfg["paths"]["outputs"], "客户交付包")
    outdir = os.path.join(base, f"交付包_{key}")
    os.makedirs(outdir, exist_ok=True)

    files = {
        "01_上岗速通.html": _build_01(kb),
        "02_操作卡.html": _build_02(kb),
        "03_故障速查.html": _build_03(kb),
        "04_设备档案.html": _build_04(kb),
        "05_知识库.html": _build_05(kb),
    }
    for name, content in files.items():
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            f.write(content)

    # 可导入的知识库 JSON
    entries = []
    for it in kb["faq"]:
        entries.append({"type": "faq", "question": it["q"], "answer": it["a"],
                        "source": it["src"]})
    for st in kb["steps"]:
        entries.append({"type": "procedure", "title": st["label"],
                        "steps": st["steps"], "source": st["label"]})
    payload = {"industry": pack.get("name"), "key": key,
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "entries": entries}
    with open(os.path.join(outdir, "知识库.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # README
    readme = f"""# {pack.get('name','')} · 客户交付包

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
> 行业：{pack.get('name','')}（{key}）

## 给谁看

**客户方**：车间操作员、班组长、设备维护人员。
不需要安装任何软件，双击 HTML 文件用浏览器打开即可。

## 文件说明

| 文件 | 给谁 | 干什么 |
|---|---|---|
| `01_上岗速通.html` | 新员工第一天 | 3 分钟看懂这套系统 + 最常问的问题 + 安全红线 |
| `02_操作卡.html` | 操作员 | 一个流程一张卡，可打印贴工位 |
| `03_故障速查.html` | 操作员 | 输入现象直接查解决办法 |
| `04_设备档案.html` | 维护人员 | 参数、点位表、报警码 |
| `05_知识库.html` | 技术对接人 | 结构化知识库预览 |
| `知识库.json` | 系统对接 | 可直接导入 AI 员工 / 知识库系统 |

## 怎么用

1. 把整个文件夹发给客户，或拷到客户电脑
2. 双击 `01_上岗速通.html` 开始
3. 打印 `02_操作卡.html` 的卡片，贴在对应工位

## 知识库来源

"""
    for d in kb["docs"]:
        readme += f"- **{d['label']}**（{d['chars']} 字）—— {d['head']}\n"
    readme += f"""
## 统计

- 问答条目：{len(kb['faq'])} 条
- 操作流程：{len(kb['steps'])} 套
- 参数表格：{len(kb['tables'])} 张
- 源文档：{len(kb['docs'])} 篇

---
*由「一人AI公司」AI 员工系统自动生成*
"""
    with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)

    # 打成 zip 方便发
    zip_path = os.path.join(base, f"交付包_{key}.zip")
    try:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        shutil.make_archive(os.path.join(base, f"交付包_{key}"), "zip",
                            root_dir=base, base_dir=f"交付包_{key}")
    except Exception:
        zip_path = ""

    return {
        "ok": True,
        "dir": outdir,
        "zip": zip_path,
        "files": sorted(os.listdir(outdir)),
        "stats": {"faq": len(kb["faq"]), "steps": len(kb["steps"]),
                  "tables": len(kb["tables"]), "docs": len(kb["docs"])},
    }

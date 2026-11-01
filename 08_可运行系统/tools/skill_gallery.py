#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Skill 展示页生成器

做什么
------
把 `_待审/` 和 `_已上架/` 里的 Skill 包扫一遍，
生成一个**能直接双击打开**的 HTML 商品目录页。

为什么要有
----------
这是流水线的**最后一公里**：
搜索 → 下载 → 做包 → 写脚本 → 审核，全做完，
客户能看见的仍然只是一堆文件夹。
展示页把这堆文件夹变成**能扫、能比、能点**的商品列表。

页面上有什么
------------
    · 卡片网格：名称、一句话简介、评分、定价、标签
    · 状态区分：**待审**（橙色）与 **已上架**（绿色）——一眼看出哪些能卖
    · 筛选：按状态、按关键词搜
    · 每张卡片直接给出「看 SKILL.md / 看抖音脚本」的入口
    · 顶部统计：总数 / 已上架 / 待审 / 平均评分

数据来源是真实的包目录，不手写——
    扫到多少包就显示多少包，一个包都不编。

用法
----
    python skill_gallery.py            # 生成到 07_AI员工/05_自动生成技能/展示页.html
    python skill_gallery.py --open     # 生成后用系统默认浏览器打开
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(PROJECT, "08_可运行系统"))

SKILL_ROOT = os.path.join(PROJECT, "07_AI员工", "05_自动生成技能")
PENDING_DIR = os.path.join(SKILL_ROOT, "_待审")
PUBLISHED_DIR = os.path.join(SKILL_ROOT, "_已上架")
OUT_HTML = os.path.join(SKILL_ROOT, "展示页.html")


# ---------------------------------------------------------------- 采集


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _parse_frontmatter(text):
    """从 SKILL.md 里抠出 YAML frontmatter 的几个字段。"""
    if not text.startswith("---"):
        return {}
    parts = text.split("---")
    if len(parts) < 3:
        return {}
    fm = parts[1]
    out = {}
    for k in ("name", "description", "version", "author", "license"):
        m = re.search(r"^%s:\s*(.+)$" % k, fm, re.M)
        if m:
            out[k] = m.group(1).strip().strip('"').strip("'")
    m = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n)+)", fm, re.M)
    if m:
        out["tags"] = [t.strip().strip("-").strip() for t in m.group(1).strip().split("\n")]
    return out


def _pick_brief(fm, hub, skill_txt):
    """挑一句能看的中文简介。

    2026-10-31 起改为**委托 staff.skillshop** ——
    控制台里的「技能商店」页签和这个独立展示页必须显示一样的内容。
    两边各写一套清洗逻辑，早晚会出现"控制台干净、展示页脏"的情况。
    """
    from staff import skillshop
    return skillshop._pick_brief(fm, hub, skill_txt)


def _is_readable_brief(s):
    """判断一句话能不能当简介给人看。"""
    if not s or len(s) < 8:
        return False
    # 图片 URL / 纯链接
    if re.match(r"^https?://", s) and re.search(r"\.(gif|png|jpg|jpeg|webp|svg)", s, re.I):
        return False
    if re.match(r"^https?://\S{40,}$", s):
        return False
    # 原始 HTML 标签
    if re.match(r"^<[a-zA-Z!/]", s):
        return False
    if re.search(r"<(p|div|img|br|span|a|center|align)\b", s, re.I):
        return False
    # 几乎全是符号
    if not re.search(r"[一-鿿A-Za-z]{3,}", s):
        return False
    return True


def collect():
    """扫两个目录，收集所有 Skill 包。"""
    items = []
    for status, base in (("已上架", PUBLISHED_DIR), ("待审", PENDING_DIR)):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            pkg = os.path.join(base, name)
            if not os.path.isdir(pkg):
                continue
            skill_path = os.path.join(pkg, "SKILL.md")
            if not os.path.exists(skill_path):
                continue
            txt = _read(skill_path)
            fm = _parse_frontmatter(txt)
            hub = {}
            hubp = os.path.join(pkg, "clawhub.json")
            if os.path.exists(hubp):
                try:
                    hub = json.loads(_read(hubp))
                except Exception:
                    hub = {}
            audit = {}
            ap = os.path.join(pkg, "_审核报告.json")
            if os.path.exists(ap):
                try:
                    audit = json.loads(_read(ap))
                except Exception:
                    audit = {}

            brief = _pick_brief(fm, hub, txt)
            desc = fm.get("description", "") or hub.get("description", "")

            price = (hub.get("pricing") or {}).get("price")
            items.append({
                "dir_name": name,
                "status": status,
                "name": hub.get("displayName") or fm.get("name") or name,
                "slug": fm.get("name") or name,
                "brief": brief,
                "full_desc": desc,
                "version": fm.get("version") or hub.get("version") or "1.0.0",
                "author": fm.get("author") or hub.get("author") or "-",
                "license": fm.get("license") or hub.get("license") or "未标注",
                "tags": fm.get("tags") or hub.get("tags") or [],
                "price": price,
                "price_yuan": ("¥%.1f" % (price / 100.0)) if isinstance(price, int) else "面议",
                "repo": hub.get("repository", ""),
                "score": audit.get("总分"),
                "passed": audit.get("是否通过"),
                "problems": audit.get("阻断问题") or [],
                "warnings": audit.get("提醒") or [],
                "has_script": os.path.exists(os.path.join(pkg, "抖音脚本.md")),
                "has_install": os.path.exists(os.path.join(pkg, "安装说明.md")),
                "path": os.path.relpath(pkg, SKILL_ROOT).replace("\\", "/"),
            })
    return items


# ---------------------------------------------------------------- 渲染

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
 background:#f5f6f8;color:#1a1d23;padding:28px 20px 60px}
.wrap{max-width:1180px;margin:0 auto}
header{margin-bottom:26px}
h1{font-size:26px;font-weight:700;letter-spacing:-.3px}
.sub{color:#6b7280;font-size:14px;margin-top:7px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0 22px}
.stat{background:#fff;border:1px solid #e4e7ec;border-radius:10px;padding:12px 18px;
 min-width:104px}
.stat b{display:block;font-size:22px;font-weight:700;line-height:1.2}
.stat span{font-size:12px;color:#6b7280}
.stat.g b{color:#059669}.stat.o b{color:#d97706}
.bar{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;align-items:center}
input[type=search]{flex:1;min-width:220px;padding:10px 14px;border:1px solid #d5d9e0;
 border-radius:8px;font-size:14px;background:#fff;outline:none}
input[type=search]:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.12)}
.seg{display:flex;border:1px solid #d5d9e0;border-radius:8px;overflow:hidden;background:#fff}
.seg button{border:0;background:#fff;padding:10px 16px;font-size:13px;cursor:pointer;
 color:#4b5563;border-right:1px solid #e4e7ec}
.seg button:last-child{border-right:0}
.seg button.on{background:#1a1d23;color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
.card{background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:18px;
 display:flex;flex-direction:column;gap:11px;transition:.15s}
.card:hover{border-color:#b9c0cc;box-shadow:0 4px 16px rgba(16,24,40,.07)}
.chead{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.cname{font-size:16px;font-weight:650;line-height:1.35}
.badge{font-size:11px;padding:3px 9px;border-radius:20px;white-space:nowrap;
 font-weight:600;flex-shrink:0}
.badge.pub{background:#dcfce7;color:#047857}
.badge.pend{background:#fef3c7;color:#b45309}
.cbrief{font-size:13px;color:#4b5563;line-height:1.62;min-height:42px}
.cmeta{display:flex;flex-wrap:wrap;gap:6px;font-size:11.5px;color:#6b7280}
.mrow{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:auto;
 padding-top:11px;border-top:1px solid #eef0f3}
.price{font-size:17px;font-weight:700;color:#dc2626}
.price small{font-size:11px;color:#9ca3af;font-weight:400;margin-left:3px}
.score{font-size:12px;padding:2px 8px;border-radius:6px;font-weight:600}
.score.ok{background:#dcfce7;color:#047857}
.score.no{background:#fee2e2;color:#b91c1c}
.links{margin-left:auto;display:flex;gap:7px}
a.btn{font-size:12px;padding:6px 11px;border-radius:7px;text-decoration:none;
 border:1px solid #d5d9e0;color:#374151;background:#fff}
a.btn:hover{border-color:#3b82f6;color:#1d4ed8}
.empty{text-align:center;padding:70px 20px;color:#9ca3af;background:#fff;
 border:1px dashed #d5d9e0;border-radius:12px}
.empty b{display:block;font-size:16px;color:#6b7280;margin-bottom:8px}
.foot{margin-top:30px;text-align:center;font-size:12px;color:#9ca3af}
.warn{margin-top:8px;font-size:11.5px;color:#b45309;background:#fffbeb;
 border-left:2px solid #f59e0b;padding:5px 9px;border-radius:0 5px 5px 0;line-height:1.5}
@media(max-width:600px){.stats{gap:8px}.stat{min-width:88px;padding:10px 13px}}
"""


def render(items):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_pub = len([i for i in items if i["status"] == "已上架"])
    n_pend = len([i for i in items if i["status"] == "待审"])
    scores = [i["score"] for i in items if isinstance(i["score"], int)]
    avg = round(sum(scores) / len(scores), 1) if scores else "-"

    cards = []
    for it in items:
        badge = ("<span class='badge pub'>已上架</span>" if it["status"] == "已上架"
                 else "<span class='badge pend'>待人工审核</span>")
        sc = ""
        if isinstance(it["score"], int):
            sc = ("<span class='score ok'>审核 %d 分</span>" % it["score"]
                  if it["passed"] else "<span class='score no'>审核 %d 分</span>" % it["score"])
        tags = "".join("<span>%s</span>" % html.escape(str(t))
                       for t in (it["tags"] or [])[:4])
        links = []
        links.append("<a class='btn' href='%s/SKILL.md'>SKILL</a>"
                     % html.escape(it["path"]))
        if it["has_script"]:
            links.append("<a class='btn' href='%s/抖音脚本.md'>抖音脚本</a>"
                         % html.escape(it["path"]))
        if it["has_install"]:
            links.append("<a class='btn' href='%s/安装说明.md'>安装</a>"
                         % html.escape(it["path"]))
        warn = ""
        if it["problems"]:
            warn = ("<div class='warn'>⚠ %s</div>"
                    % html.escape("；".join(it["problems"])[:120]))
        elif it["warnings"]:
            warn = ("<div class='warn'>⚠ %s</div>"
                    % html.escape(it["warnings"][0][:120]))
        cards.append("""<div class='card' data-status='%s' data-key='%s'>
  <div class='chead'><div class='cname'>%s</div>%s</div>
  <div class='cbrief'>%s</div>
  <div class='cmeta'><span>v%s</span><span>%s</span><span>协议 %s</span>%s</div>
  %s
  <div class='mrow'>
    <span class='price'>%s<small>%s</small></span>%s
    <div class='links'>%s</div>
  </div>
</div>""" % (
            it["status"],
            html.escape((it["name"] + " " + it["brief"] + " " +
                         " ".join(str(t) for t in it["tags"])).lower()),
            html.escape(it["name"]), badge,
            html.escape(it["brief"]) or "<i style='color:#9ca3af'>（无简介）</i>",
            html.escape(str(it["version"])), html.escape(it["author"]),
            html.escape(str(it["license"])), tags,
            warn,
            html.escape(it["price_yuan"]),
            "起" if isinstance(it["price"], int) else "",
            sc, "".join(links),
        ))

    body = "".join(cards) or (
        "<div class='empty'><b>还没有 Skill 包</b>"
        "跑一次流水线就能自动生成：<br>"
        "<code>python 08_可运行系统/tools/auto_pipeline.py</code></div>")

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 员工技能商店 · Skill 展示页</title>
<style>%s</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>AI 员工技能商店</h1>
  <div class="sub">GitHub 项目自动转制 · 每个包都经过结构 / 红线 / 可用性 / 诚实性四关审核 · 生成于 %s</div>
</header>
<div class="stats">
  <div class="stat"><b>%d</b><span>技能总数</span></div>
  <div class="stat g"><b>%d</b><span>已上架</span></div>
  <div class="stat o"><b>%d</b><span>待人工审核</span></div>
  <div class="stat"><b>%s</b><span>平均审核分</span></div>
</div>
<div class="bar">
  <input type="search" id="q" placeholder="搜索技能名称、简介或标签…">
  <div class="seg">
    <button class="on" data-f="all">全部</button>
    <button data-f="已上架">已上架</button>
    <button data-f="待审">待审</button>
  </div>
</div>
<div class="grid" id="grid">%s</div>
<div class="foot">「待审」包**不会**出现在客户可见的商店里 —— 流水线只产出候选，上架必须人工签字。<br>
自动生成不代表能卖：README 写得漂亮 ≠ 装得上。实机装一次再上架。</div>
</div>
<script>
(function(){
  var cards=[].slice.call(document.querySelectorAll('.card'));
  var q=document.getElementById('q'), segs=[].slice.call(document.querySelectorAll('.seg button'));
  var cur='all';
  function apply(){
    var kw=(q.value||'').trim().toLowerCase();
    cards.forEach(function(c){
      var okS = cur==='all' || c.getAttribute('data-status')===cur;
      var okK = !kw || c.getAttribute('data-key').indexOf(kw)>=0;
      c.style.display = (okS&&okK)?'':'none';
    });
  }
  q.addEventListener('input',apply);
  segs.forEach(function(b){b.addEventListener('click',function(){
    segs.forEach(function(x){x.classList.remove('on')});b.classList.add('on');
    cur=b.getAttribute('data-f');apply();
  })});
})();
</script>
</body>
</html>""" % (CSS, now, len(items), n_pub, n_pend, avg, body)


def main():
    ap = argparse.ArgumentParser(description="生成 Skill 展示页")
    ap.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    ap.add_argument("--out", default=OUT_HTML, help="输出路径")
    args = ap.parse_args()

    items = collect()
    html_text = render(items)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_text)

    n_pub = len([i for i in items if i["status"] == "已上架"])
    n_pend = len([i for i in items if i["status"] == "待审"])
    print("✔ 展示页已生成：%s" % args.out)
    print("   共 %d 个技能（已上架 %d / 待审 %d）" % (len(items), n_pub, n_pend))

    if args.open:
        try:
            os.startfile(args.out)      # noqa  (Windows)
        except Exception:
            print("   （自动打开失败，请手动双击）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

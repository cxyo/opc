# -*- coding: utf-8 -*-
"""Skill 商店 —— 数据层。

为什么单独抽一个模块
--------------------
之前技能商店只有 `tools/skill_gallery.py` 一个入口，
它生成一个独立的 `展示页.html` 给客户看。

问题：**控制台首页里没有技能商店的入口**。
客户打开 127.0.0.1:8899 看到的是「AI 超市」（第三方大模型平台货架），
跟我们的 Skill 商品目录完全是两回事 —— 这是真实的用户困惑。

所以把数据层抽出来，让：
  ① 控制台页签（实时读盘，永远最新）
  ② 独立展示页（给客户发链接 / 打印）
两边共用 `collect()`，不会出现两边数据不一致。

放在 staff/ 下而不是 tools/，是因为 exe 打包时只打 staff/，
放 tools/ 的话免安装版里这个功能直接消失。
"""

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, "..", ".."))


def _find_skill_root() -> str:
    """找出技能包根目录。

    为什么不能直接用 PROJECT：
      `PROJECT` 是从代码文件位置往上推的。
      源码运行时没问题，但**免安装版 exe 运行时，代码被解到临时目录**，
      PROJECT 就指向一个不存在的地方 —— 技能商店会显示为空。

    所以按顺序试几个候选位置，第一个真实存在的 wins：
      ① 源码布局：<project>/07_AI员工/05_自动生成技能
      ② exe 布局：exe 同级目录下的 07_AI员工/05_自动生成技能
      ③ 可运行系统往上两层的布局（交付包里的形态）
    """
    candidates = [
        os.path.join(PROJECT, "07_AI员工", "05_自动生成技能"),
    ]
    # exe 同级（及往上两层）
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for base in (exe_dir, os.path.dirname(exe_dir)):
        candidates.append(os.path.join(base, "07_AI员工", "05_自动生成技能"))
    # 从当前工作目录找（客户双击 exe 时 cwd 常见是 exe 目录）
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, "07_AI员工", "05_自动生成技能"))
    candidates.append(os.path.join(cwd, "..", "07_AI员工", "05_自动生成技能"))

    for c in candidates:
        if os.path.isdir(c):
            return os.path.abspath(c)
    # 都没找到就返回源码位置的猜测值，collect() 会扫不到东西但不会崩
    return os.path.abspath(candidates[0])


SKILL_ROOT = _find_skill_root()
PENDING_DIR = os.path.join(SKILL_ROOT, "_待审")
PUBLISHED_DIR = os.path.join(SKILL_ROOT, "_已上架")
OUT_HTML = os.path.join(SKILL_ROOT, "展示页.html")


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """从 SKILL.md 里抠出 YAML frontmatter 的几个字段。"""
    if not text.startswith("---"):
        return {}
    parts = text.split("---")
    if len(parts) < 3:
        return {}
    fm = parts[1]
    out: Dict[str, Any] = {}
    for k in ("name", "description", "version", "author", "license"):
        m = re.search(r"^%s:\s*(.+)$" % k, fm, re.M)
        if m:
            out[k] = m.group(1).strip().strip('"').strip("'")
    m = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n)+)", fm, re.M)
    if m:
        out["tags"] = [t.strip().strip("-").strip()
                       for t in m.group(1).strip().split("\n")]
    return out


def _is_readable_brief(s: str) -> bool:
    """判断一句话能不能当简介给人看。

    实测踩过的坑（都不是假想）：
      · 图片 URL —— 有些项目的 description 是一张 GIF 地址
      · 原始 HTML —— 有些项目直接贴 `<p align="center">` 标签
      · 几乎全是符号 —— 看着有内容，其实什么都没说
    """
    if not s or len(s) < 8:
        return False
    if re.match(r"^https?://", s) and \
            re.search(r"\.(gif|png|jpg|jpeg|webp|svg)", s, re.I):
        return False
    if re.match(r"^https?://\S{40,}$", s):
        return False
    if re.match(r"^<[a-zA-Z!/]", s):
        return False
    if re.search(r"<(p|div|img|br|span|a|center|align)\b", s, re.I):
        return False
    if not re.search(r"[一-鿿A-Za-z]{3,}", s):
        return False
    return True


def _pick_brief(fm: Dict[str, Any], hub: Dict[str, Any],
                skill_txt: str) -> str:
    """挑一句能看的中文简介。

    为什么要专门挑：
      GitHub 项目的 description 大多是英文，
      直接摆到中文商品页上，客户第一眼就看不懂。

    为什么要逐行扫：
      README 的用途段开头经常是噪声行，**第一行不一定是要简介的那句**：
        · `# 项目名`             —— 标题行
        · `<img src=...>`        —— HTML 标签残留
        · `**English** | [简体中文](...)` —— 语言切换行
      实测 GEOInflow 的用途段第一行就是 `# GEOInflow`，
      只取第一行会让卡片上写着「GEOInflow」这种等于没说的简介。

    优先顺序：
      ① SKILL.md 里我们自己写的中文说明（最准）
      ② clawhub.json 里的中文部分
      ③ 英文原文兜底
    """
    m = re.search(r"##\s*这个技能做什么\s*\n+(.+)", skill_txt)
    if m:
        for raw in m.group(1).strip().split("\n"):
            line = raw.strip()
            if not line:
                continue
            # 跳过标题行 / 整行 HTML / 语言切换行 / 徽章行
            if line.startswith("#"):
                continue
            if re.match(r"^<[a-zA-Z!/][^>]*>$", line):
                continue
            if re.match(r"^\*?\*?[A-Za-z一-鿿]{2,12}\*?\*?\s*\|", line):
                continue
            if line.startswith("![") or line.startswith("[!["):
                continue
            # 去掉行内 HTML 标签与 markdown 强调符号
            line = re.sub(r"<[^>]+>", "", line).strip()
            line = re.sub(r"^#+\s*", "", line).strip()
            # markdown 强调：先去掉包裹符号，再去掉残留的 ** 和 *
            # （README 里的 "**Digital human from one photo**" 就是这么来的）
            line = line.replace("**", "").replace("__", "")
            line = line.strip("*_`").strip()
            line = re.sub(r"^[\"'“‘]+|[\"'”’]+$", "", line).strip()
            if len(line) >= 8 and _is_readable_brief(line):
                return line[:120]
    desc = hub.get("description", "") or ""
    # ② 图片的 alt 属性 —— 实测 mcode-stack 的简介就在这：
    #    <img src="hero.png" alt="MCode Stack: the workflows and skills ...">
    #    作者把说明写在了 alt 里，图片本身反而是噪声。
    alts = re.findall(r'alt="([^"]{15,200})"', desc) or \
        re.findall(r'alt="([^"]{15,200})"', skill_txt)
    for a in alts:
        a = a.replace("**", "").replace("__", "").strip("*_` ").strip()
        if _is_readable_brief(a):
            return a[:120]
    zh = re.findall(r"[一-鿿][一-鿿，。；：、（）—\s]{10,}", desc)
    if zh:
        t = re.sub(r"\s+", " ", zh[0]).strip()
        if _is_readable_brief(t):
            return t[:120]
    d2 = re.sub(r"<[^>]+>", "", fm.get("description", "") or desc).strip()
    d2 = re.sub(r"^#+\s*", "", d2)
    t = re.split(r"[。\n]", d2)[0].strip()
    if _is_readable_brief(t):
        return t[:110]
    return "（该项目的简介不可用，需人工补一句中文说明）"


def collect() -> List[Dict[str, Any]]:
    """扫两个目录，收集所有 Skill 包。"""
    items: List[Dict[str, Any]] = []
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
            hub: Dict[str, Any] = {}
            hubp = os.path.join(pkg, "clawhub.json")
            if os.path.exists(hubp):
                try:
                    hub = json.loads(_read(hubp))
                except Exception:
                    hub = {}
            audit: Dict[str, Any] = {}
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
                "price_yuan": ("¥%.1f" % (price / 100.0))
                if isinstance(price, int) else "面议",
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


def stats(items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """商店统计：给控制台顶部指标用。"""
    items = items if items is not None else collect()
    pub = [x for x in items if x["status"] == "已上架"]
    pend = [x for x in items if x["status"] == "待审"]
    passed = [x for x in items if x.get("passed")]
    scores = [x["score"] for x in items if isinstance(x.get("score"), (int, float))]
    return {
        "总数": len(items),
        "已上架": len(pub),
        "待审": len(pend),
        "审核通过": len(passed),
        "均分": round(sum(scores) / len(scores), 1) if scores else None,
        "有抖音脚本": len([x for x in items if x.get("has_script")]),
        "有安装说明": len([x for x in items if x.get("has_install")]),
    }


def read_doc(slug: str, filename: str = "SKILL.md") -> Dict[str, Any]:
    """读某个包里的一个文档（给控制台预览用）。

    只允许读白名单里的文件名，且必须落在包目录内 —— 防目录穿越。
    """
    allow = ("SKILL.md", "README.md", "安装说明.md", "抖音脚本.md",
             "clawhub.json", "_审核报告.json")
    if filename not in allow:
        return {"ok": False, "error": "不允许读这个文件"}
    for base in (PUBLISHED_DIR, PENDING_DIR):
        pkg = os.path.join(base, slug)
        if not os.path.isdir(pkg):
            continue
        p = os.path.join(pkg, filename)
        if not os.path.exists(p):
            continue
        # 二次确认没跳出包目录
        if os.path.commonpath([os.path.abspath(p), os.path.abspath(pkg)]) \
                != os.path.abspath(pkg):
            return {"ok": False, "error": "路径越界"}
        return {"ok": True, "filename": filename, "content": _read(p),
                "status": "已上架" if base == PUBLISHED_DIR else "待审"}
    return {"ok": False, "error": "找不到这个技能包：%s" % slug}

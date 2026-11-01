#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GitHub 项目 → Skill 包 → 抖音脚本 → 审核（单仓完整处理）

为什么是一条链
--------------
拆开卖不成立：
  · 只转 Skill —— 客户不知道这东西能干什么，转化不了
  · 只做视频 —— 有流量但没有商品承接
  · 只做 Skill —— 有商品但没有流量来源

所以一个仓库进来，必须走完四步，产出四样东西：
  ① Skill 包    —— 能上架、能发货、能安装（商品）
  ② 抖音脚本   —— 能直接开录的文案 + 分镜（流量）
  ③ 审核报告   —— 说清楚哪里能用、哪里不能用（防止自己翻车）
  ④ 源信息     —— 原始数据，可追溯（避免"这包哪来的"）

铁律：自动产出**默认进待审区**，不直接上架。
    理由很实在：README 写得漂亮 ≠ 装得上、跑得通、对客户有用。
    自动上架一次翻车，客户的信任就没了。

用法
----
    python repo_to_skill.py owner/repo
    python repo_to_skill.py owner/repo --no-llm      # 不调模型，纯模板
    python repo_to_skill.py --from-radar             # 批量处理雷达候选
"""

import base64
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))          # 一人AI公司/
sys.path.insert(0, os.path.join(PROJECT, "08_可运行系统"))

from staff import config as cfgmod                      # noqa: E402

# 输出根目录：待审区
OUT_ROOT = os.path.join(PROJECT, "07_AI员工", "05_自动生成技能")
PENDING = os.path.join(OUT_ROOT, "_待审")
PUBLISHED = os.path.join(OUT_ROOT, "_已上架")
LOG = os.path.join(OUT_ROOT, "流水线日志.jsonl")

AUTHOR = "lobster-skill"
AUTHOR_NAME = "龙虾指令集"
VERSION = "1.0.0"

# 自动生成包的默认定价（未人工定价前不上架）
DEFAULT_PRICE_CENTS = 1990      # ¥19.9 —— 单卖引流价，见规划文档「变现」章节

# 绝对禁止：自动生成的 Skill 里必须内联这些红线
BANNED_IN_SKILL = [
    "不承诺效果（不写「保证」「100%」「必定」「稳赚」）",
    "不代替人工做金额、合同、赔付的决定",
    "不上传用户数据到第三方（除非项目本身就是云服务且已说明）",
    "不绕过付费墙、不破解、不代理他人账号",
    "不群控、不批量私信、不刷量",
]


# ---------------------------------------------------------------- HTTP


def _headers():
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-staff-repo-to-skill",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def api_json(path, timeout=25):
    """调 GitHub API。限流时返回 {} 而不是抛异常（调用方会走降级）。"""
    try:
        req = urllib.request.Request("https://api.github.com" + path, headers=_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            return {}
        raise
    except Exception:
        return {}


def fetch_page(full_name, timeout=25):
    """抓 GitHub 仓库页面 HTML（降级用，不限流也不需要 token）。"""
    url = "https://github.com/%s" % full_name
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_page_meta(html):
    """从 HTML 里抠出星数 / fork 数 / 描述 / 协议。

    为什么要有这条路：GitHub API 未认证时 60 次/小时就限流了，
    而客户的电脑不会配 token。**整条流水线必须在没有 token 的情况下也能跑**，
    否则客户拿到手就是坏的。
    """
    out = {"stars": 0, "forks": 0, "desc": "", "license": "", "language": ""}
    m = re.search(r'id="repo-stars-counter-star"[^>]*title="([\d,]+)"', html)
    if m:
        out["stars"] = int(m.group(1).replace(",", ""))
    m = re.search(r'id="repo-network-counter"[^>]*title="([\d,]+)"', html)
    if m:
        out["forks"] = int(m.group(1).replace(",", ""))
    m = re.search(r'<meta name="description" content="([^"]*)"', html)
    if m:
        d = m.group(1)
        # GitHub 的 description meta 形如 "owner/repo: 说明 Contribute..."
        d = re.sub(r"^[^:]+:\s*", "", d)
        d = re.split(r"\s+Contribute\b", d)[0]
        out["desc"] = d.strip()
    m = re.search(r'([A-Za-z0-9\.\-]+) license', html)
    if m:
        lic = m.group(1).lower()
        out["license"] = {"mit": "MIT", "apache": "Apache-2.0",
                          "bsd": "BSD-3-Clause", "gpl": "GPL-3.0",
                          "mpl": "MPL-2.0", "isc": "ISC"}.get(lic, "")
    m = re.search(r'itemprop="programmingLanguage">([^<]+)<', html)
    if m:
        out["language"] = m.group(1).strip()
    return out


def fetch_repo_meta(full_name):
    """取仓库元信息。优先 API（准），失败降级抓页面（够用且不限流）。

    两个降级都失败时返回空 dict —— **不是错误**。
    星数拿不到就标"未获取"，绝不用别处的数字凑。
    （实测环境里 api.github.com 会限流、github.com 主站不通，
      只有 codeload 稳定可用，所以这条降级链是必需的，不是锦上添花。）
    """
    try:
        info = api_json("/repos/%s" % full_name)
    except Exception:
        info = {}
    if info and info.get("stargazers_count") is not None:
        return {
            "stars": info.get("stargazers_count") or 0,
            "forks": info.get("forks_count") or 0,
            "license": ((info.get("license") or {}) or {}).get("spdx_id") or "",
            "url": info.get("html_url") or ("https://github.com/%s" % full_name),
            "desc": (info.get("description") or "").strip(),
            "topics": info.get("topics") or [],
            "pushed": (info.get("pushed_at") or "")[:10],
            "source": "api",
        }
    try:
        html = fetch_page(full_name)
        pm = parse_page_meta(html)
        pm["url"] = "https://github.com/%s" % full_name
        pm["topics"] = re.findall(r'data-ga-click="topic_click"[^>]*>([^<]+)<', html)[:8]
        pm["pushed"] = ""
        pm["source"] = "html"
        return pm
    except Exception:
        return {
            "stars": 0, "forks": 0, "license": "", "url":
            "https://github.com/%s" % full_name, "desc": "", "topics": [],
            "pushed": "", "source": "none",
        }


def download_repo(full_name, timeout=90, max_bytes=60 * 1024 * 1024):
    """下载仓库源码包，**只在内存里处理，不落盘**。

    为什么不落盘：
      1. 热门仓库解压后动辄几百 MB，每天自动跑会把磁盘塞满
      2. 解压完还要删，删目录在客户环境里容易被安全策略拦（实测踩过）
      3. 我们真正需要的只有 README + 文件清单 —— zip 的 namelist 就够，
         检测"仓库里有没有可执行脚本"根本不需要真的解压

    超时与体积上限（**自动化任务的生命线**）：
      早先 timeout=300 且不限体积，结果一个慢仓库就把整批挂住——
      实测一次批处理跑了 16 分钟只出了 1 个包。
      现在：单次下载超时 90 秒、总体积上限 60MB，
      超了直接跳过这个仓，**不能因为一个仓拖死整批**。

    返回：{"branch", "readme_name", "readme_text", "files", "bytes"}
    """
    # 候选分支：main / master 只是绝大多数，不是全部。
    # 实测 anhao/ai-pose-gallery-zh 两个都不是，default_branch 又因 API 限流拿不到，
    # 结果整仓 404。加几个常见分支名兜底 —— 每个分支一次 HEAD 请求很快。
    branches = ["main", "master", "dev", "develop", "release", "trunk"]
    # default_branch 只影响试哪个分支，拿不到就用默认候选分支试，
    # 不值得为它多花 25 秒（实测 API 会限流，等下去只是白等）。
    try:
        default = api_json("/repos/%s" % full_name, timeout=8).get("default_branch")
    except Exception:
        default = None
    if default and default not in branches:
        branches.insert(0, default)

    last = None
    data = None
    used = None
    # 分开记：分支不存在是常态（试 6 个分支总有 5 个没有），
    # 体积超限才是真正该告诉用户的原因。
    # 之前混在一起报，682MB 的仓库最后报的是"trunk 不存在"，
    # 用户完全看不出真实问题。
    missing = []
    oversize = []
    errors = []
    for br in branches:
        url = "https://codeload.github.com/%s/zip/refs/heads/%s" % (full_name, br)
        try:
            # 第一步：HEAD 探分支存不存在。
            #   codeload 的 HEAD **不返回 Content-Length**（实测），
            #   所以它只能用来判断"这个分支有没有"，不能用来看体积。
            exists = False
            try:
                hreq = urllib.request.Request(url, headers={
                    "User-Agent": "ai-staff-repo-to-skill"}, method="HEAD")
                with urllib.request.urlopen(hreq, timeout=25) as hr:
                    exists = (hr.status == 200)
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    missing.append(br)
                    continue
                # 其他 HTTP 错误（比如 403/429）不排除，交给下面的 GET 处理
            except Exception:
                exists = True   # HEAD 探不了就当它存在，交给 GET 判定

            if not exists:
                missing.append(br)
                continue

            # 第二步：GET。**GET 才给真实的 Content-Length**。
            #   实测 ai-pose-gallery-zh 的 main 分支是 682MB，
            #   HEAD 不给体积、GET 给 —— 不按 GET 的数字拦，
            #   就会把 682MB 拉进内存，之前整批跑 16 分钟就是栽在这。
            req = urllib.request.Request(url, headers={
                "User-Agent": "ai-staff-repo-to-skill"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                clen = r.headers.get("Content-Length")
                if clen and clen.isdigit() and int(clen) > max_bytes:
                    oversize.append("%s 分支 %d MB" % (
                        br, int(clen) // 1024 // 1024))
                    data = None
                    continue
                data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                oversize.append("%s 分支超过 %d MB" % (br, max_bytes // 1024 // 1024))
                data = None
                continue
            if not data or data[:2] != b"PK":
                errors.append("%s 分支返回的不是 zip" % br)
                data = None
                continue
            used = br
            break
        except Exception as e:
            errors.append("%s: %s" % (br, str(e)[:80]))
            data = None
    if data is None:
        # 按**重要性**排：体积超限 > 真错误 > 分支不存在
        parts = []
        if oversize:
            parts.append("仓库太大（%s），超过 %d MB 上限 —— "
                         "这种仓库不适合直接做成 Skill 包，得先瘦身"
                         % ("、".join(oversize), max_bytes // 1024 // 1024))
        if errors:
            parts.append("下载报错：" + "；".join(errors[:3]))
        if missing:
            parts.append("试过的分支：%s" % "、".join(missing))
        raise RuntimeError("下载失败 —— " + " | ".join(parts))

    readme_name, readme_text = "", ""
    files = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                files.append(n)
                base = os.path.basename(n)
                if not readme_name and base.lower() in (
                        "readme.md", "readme_cn.md", "readme.rst",
                        "readme.txt", "readme"):
                    try:
                        readme_text = z.read(n).decode("utf-8", "replace")
                        readme_name = base
                    except Exception:
                        pass
    except Exception as e:
        raise RuntimeError("解析仓库压缩包失败：%s" % e)

    return {"branch": used, "readme_name": readme_name,
            "readme_text": readme_text, "files": files, "bytes": len(data)}


# ---------------------------------------------------------------- README 解析


def pick_readme(root):
    """挑主 README。"""
    for name in ("README.md", "README_CN.md", "readme.md", "README.rst",
                 "README.txt", "README"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    return name, f.read()
            except Exception:
                continue
    return "", ""


def extract_sections(md):
    """把 README 切成 用途 / 安装 / 用法 / 依赖 几段。

    为什么要切：
      SKILL.md 要的不是 README 全文，而是"这个工具怎么用、怎么装、
      什么时候不能用"。README 里的市场宣传和徽章对客户没用。
    """
    out = {"purpose": "", "install": "", "usage": "", "deps": "", "limits": ""}
    lines = md.split("\n")
    cur = None
    buf = {k: [] for k in out}
    # 常见标题别名
    alias = {
        "purpose": ("about", "introduction", "intro", "what is", "overview",
                    "what it does", "features", "简介", "介绍", "功能", "特性",
                    "为什么", "用途"),
        "install": ("install", "installation", "getting started", "setup",
                    "quick start", "quickstart", "usage", "安装", "部署", "快速开始",
                    "使用", "上手"),
        "usage": ("usage", "how to use", "example", "examples", "tutorial",
                  "使用", "用法", "示例", "教程"),
        "deps": ("require", "requirements", "dependencies", "dependency",
                 "prerequisite", "依赖", "环境要求", "前置"),
        "limits": ("limitations", "caveats", "warning", "known issues",
                   "disclaimer", "限制", "注意", "警告"),
    }
    for ln in lines:
        m = re.match(r"^#{1,4}\s+(.+?)\s*$", ln)
        if m:
            title = m.group(1).strip().lower()
            cur = None
            for key, names in alias.items():
                if any(title.startswith(n) or n in title for n in names):
                    cur = key
                    break
            continue
        if cur:
            buf[cur].append(ln)
    for k in out:
        txt = "\n".join(buf[k]).strip()
        out[k] = re.sub(r"\n{3,}", "\n\n", clean_readme_text(txt))[:2500]
    # 用途段抓不到就从开头取
    if not out["purpose"]:
        out["purpose"] = clean_readme_text(
            "\n".join(lines[:40]).strip())[:1200]
    # 口播用的一句话 —— purpose 可能有 2500 字，念出来是两分钟，不成脚本
    out["purpose_short"] = _first_sentence(out["purpose"])
    return out


def _first_sentence(text, limit=90):
    """从一段文字里取第一句完整的话，用作口播稿。

    为什么单独做：
      README 的用途段常有 2500 字，直接塞进口播稿会变成两分钟，
      根本念不完。口播只要**一句话讲清这玩意干嘛的**。

    跳过这些行（实测都真出现过）：
      · 标题行 `# XXX`
      · 语言切换行 `**English** | [简体中文](...)`
      · 链接列表行 `[Quick start](...) · [Deployment](...)`
      · markdown 徽章 / HTML 标签
    """
    if not text:
        return ""
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if re.match(r"^<[a-zA-Z!/][^>]*>$", line):
            continue
        if re.match(r"^\*?\*?[A-Za-z一-鿿]{2,12}\*?\*?\s*\|", line):
            continue
        if line.startswith("![") or line.startswith("[!["):
            continue
        # 链接列表行：一行里有多个 [xxx](yyy)
        if len(re.findall(r"\[[^\]]+\]\([^)]+\)", line)) >= 2:
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        line = re.sub(r"^#+\s*", "", line).strip()
        # 去 markdown 强调（README 里 "**Digital human from one photo**" 就是这么来的）
        line = line.replace("**", "").replace("__", "")
        line = line.strip("*_`").strip()
        line = re.sub(r"^[\"'“‘]+|[\"'”’]+$", "", line).strip()
        if len(line) < 12:
            continue
        # 取第一句（英文按句号，中文按句号/问号/叹号）
        m = re.match(r"^(.{12,%d}?)[.。!?！？]\s" % limit, line)
        s = m.group(1) if m else line[:limit]
        s = s.strip()
        if len(s) >= 12:
            return s
    # 整段都是图片/HTML 时，简介藏在 alt 属性里
    # （实测 mcode-stack 就是这样：<img alt="MCode Stack: the workflows and skills ...">）
    alts = re.findall(r'alt="([^"]{15,200})"', text)
    for a in alts:
        a = a.replace("**", "").strip("*_` ").strip()
        if len(a) >= 12:
            return a[:90]
    return ""


# README 里三类噪声，做成商品就没法看：
#   ① 图片/GIF 链接 —— 有些项目的首行就是一张演示图
#   ② 原始 HTML 标签 —— 有人直接贴 `<p align="center">`
#   ③ 徽章行 —— 一排 `[![badge](...)](...)`，纯噪音
def clean_readme_text(text):
    """把 README 正文里的图片链接 / HTML 标签 / 徽章行清掉。"""
    if not text:
        return ""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        # 整行就是一张图片 / GIF
        if re.match(r"^!\[[^\]]*\]\([^)]+\)\s*$", s):
            continue
        # 整行是徽章排
        if s.count("![") >= 2 and s.count("](") >= 2:
            continue
        # 行内剥掉 HTML 标签，但保留标签里的文字
        s = re.sub(r"<[^>]{1,80}>", " ", s)
        s = re.sub(r"^\s*<[^>]{1,80}>\s*$", "", s)
        # 剥掉裸 URL（保留 https:// 开头的完整链接在别处已经用过了）
        s = re.sub(r"(?<!\()https?://\S{30,}", " ", s)
        out.append(s)
    txt = "\n".join(out)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def detect_commands(md, files=None):
    """从 README / 仓库文件清单里找可执行命令，用来写"怎么用"。"""
    cmds = []
    for m in re.finditer(r"```(?:bash|sh|shell|console|powershell)?\s*\n"
                         r"((?:\$\s*[^\n]*\n|[^\n`$][^\n]*\n){1,6})```", md):
        block = m.group(1)
        for ln in block.split("\n"):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if re.match(r"^(pip|npm|npx|yarn|uv|brew|docker|git|python|node|go|cargo|"
                        r"curl|wget|chmod|mkdir|cp|export|cd|sh|bash)\b", ln):
                cmds.append(ln)
        if len(cmds) >= 8:
            break
    # 仓库根目录里的可执行脚本（用 zip 的文件清单判断，不用真的解压）
    for n in (files or []):
        base = os.path.basename(n)
        if base.endswith((".sh", ".py", ".bat", ".cmd")):
            cmds.append("./" + base)
    seen, out = set(), []
    for c in cmds:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:8]


def slugify(name):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "skill"


def _snippet(text, n=400):
    """压成一行短文本（去换行、截断）。"""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return t[:n]


def pick_role_name(full_name, desc):
    """给项目起一个"岗位式"的中文职能名。

    为什么：客户买的不是"一个 GitHub 项目"，是"一个能替我干活的岗位"。
    所以包装名必须是岗位语汇，而不是项目原名。
    """
    blob = (full_name + " " + desc).lower()
    rules = [
        (["meeting", "minutes", "transcript", "会议", "纪要"], "会议纪要员"),
        (["scrape", "crawl", "spider", "爬", "抓取"], "信息采集员"),
        (["translate", "翻译"], "翻译员"),
        (["summar", "摘要", "总结"], "摘要员"),
        (["pdf", "docx", "office", "文档"], "文档处理员"),
        (["video", "tts", "数字人", "digital-human", "avatar", "直播"], "视频制作员"),
        (["image", "绘图", "画图", "diffusion"], "绘图员"),
        (["email", "邮件", "mail"], "邮件助手"),
        (["excel", "xlsx", "表格", "sheet"], "表格处理员"),
        (["mcp", "mcp-server"], "工具接驳员"),
        (["skill", "技能"], "技能封装员"),
        (["agent", "智能体", "bot"], "任务执行员"),
        (["seo", "geo", "推广", "营销"], "推广文案员"),
        (["data", "分析", "analytics"], "数据分析员"),
        (["code", "编程", "repo", "代码"], "代码助手"),
    ]
    for keys, role in rules:
        if any(k in blob for k in keys):
            return role
    return "任务执行员"


# ---------------------------------------------------------------- 生成产物


def build_skill_md(meta, sec, cmds, role_name, extra_notes=""):
    """生成 SKILL.md。"""
    desc = meta.get("desc") or "（该项目未提供描述）"
    lines = []
    lines.append("---")
    lines.append("name: %s" % meta["slug"])
    lines.append("description: %s —— %s。%s" % (
        role_name, desc,
        "自动从 GitHub 项目 %s 生成，待人工确认后使用。" % meta["full_name"]))
    lines.append("version: %s" % VERSION)
    lines.append("author: %s" % AUTHOR)
    lines.append("tags:")
    for t in meta.get("tags", [])[:6]:
        lines.append("  - %s" % t)
    lines.append("license: %s" % (meta.get("license") or "SEE_PARENT_REPO"))
    lines.append("---")
    lines.append("")
    lines.append("# %s · %s" % (role_name, meta["full_name"]))
    lines.append("")
    lines.append("> %s ｜ 来源：%s ｜ 自动生成时间：%s"
                 % (meta.get("group_name", "GitHub 项目"), meta["url"],
                    meta.get("at", "")))
    lines.append("")
    lines.append("## 这个技能做什么")
    lines.append("")
    # 先给一句话摘要（客户第一眼要的就是这个），
    # 完整的用途描述放到折叠区里 —— 2500 字直接摊开会把人劝退
    short = sec.get("purpose_short")
    full = sec.get("purpose") or desc
    if short:
        lines.append("**%s**" % short)
        lines.append("")
        if full and full != short:
            lines.append("<details>")
            lines.append("<summary>看完整说明（来自项目 README）</summary>")
            lines.append("")
            lines.append(full)
            lines.append("")
            lines.append("</details>")
    else:
        lines.append(full)
    lines.append("")
    # 能包装成什么岗位 —— 客户看完这段就知道该拿它干嘛，
    # 这是"这是个大趋势"和"这我能用"之间的那一步。
    if meta.get("sellable_as"):
        lines.append("**可包装成**：%s" % meta["sellable_as"])
        lines.append("")
    lines.append("## 使用前提")
    lines.append("")
    if sec.get("deps"):
        lines.append(sec["deps"])
    else:
        lines.append("**必须自带**：")
        lines.append("- 目标环境的运行权限（本地命令行或目标平台账号）")
        lines.append("- 该项目 README 中列出的全部依赖与凭据")
        lines.append("")
        lines.append("> 缺依赖就装不上，本技能会直接失败而不是降级乱跑。")
    lines.append("")
    lines.append("## 安装与调用")
    lines.append("")
    if cmds:
        lines.append("```bash")
        for c in cmds:
            lines.append(c)
        lines.append("```")
    else:
        lines.append("（原项目未在 README 中给出命令行示例 —— **这需要人工补齐后才能上架**）")
    lines.append("")
    if sec.get("usage"):
        lines.append("## 使用方法")
        lines.append("")
        lines.append(sec["usage"])
        lines.append("")
    if sec.get("limits"):
        lines.append("## 已知限制")
        lines.append("")
        lines.append(sec["limits"])
        lines.append("")
    lines.append("## 绝对禁止")
    lines.append("")
    for i, b in enumerate(BANNED_IN_SKILL, 1):
        lines.append("%d. %s" % (i, b))
    lines.append("")
    lines.append("## 降级行为")
    lines.append("")
    lines.append("| 情况 | 处理 |")
    lines.append("|---|---|")
    lines.append("| 依赖缺失或凭据无效 | 明确报出缺什么，**不用通用知识替代真实结果** |")
    lines.append("| 输出涉及金额、合同、承诺 | 标记「需人工确认」，不自动执行 |")
    lines.append("| 原项目行为与本说明冲突 | 以原项目实际行为为准，并提示人工复核 |")
    lines.append("")
    if extra_notes:
        lines.append("## 人工审核备注")
        lines.append("")
        lines.append(extra_notes)
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("标签：#自动生成 #GitHub项目 #待人工审核")
    return "\n".join(lines)


def build_clawhub(meta, role_name, price_cents=DEFAULT_PRICE_CENTS):
    return {
        "name": meta["slug"],
        "displayName": "%s · %s" % (role_name, meta.get("display", meta["full_name"])),
        "version": VERSION,
        "description": (meta.get("desc") or "")[:180],
        "author": AUTHOR,
        "license": meta.get("license") or "SEE_PARENT_REPO",
        "pricing": {"model": "one-time", "price": price_cents},
        "tags": meta.get("tags", [])[:6],
        "minOpenClawVersion": "1.2.0",
        "repository": meta["url"],
        "status": "pending-review",
        "generatedAt": meta.get("at", ""),
    }


def build_readme(meta, sec, cmds, role_name):
    lines = []
    lines.append("# %s —— 让它变成你能用的岗位" % role_name)
    lines.append("")
    lines.append("> 由 GitHub 项目 [%s](%s) 自动转换，**待人工确认后上架**。"
                 % (meta["full_name"], meta["url"]))
    lines.append("")
    lines.append("## 这个技能替你干什么")
    lines.append("")
    lines.append(sec.get("purpose") or meta.get("desc") or "（待补充）")
    lines.append("")
    lines.append("## 适合谁 / 不适合谁")
    lines.append("")
    lines.append("**适合**：")
    lines.append("- 有明确重复性工作、想交给 AI 执行的中小企业")
    lines.append("- 愿意自己准备依赖与凭据的动手型用户")
    lines.append("")
    lines.append("**不适合**：")
    lines.append("- 完全不想碰环境配置的人（装不上就是装不上）")
    lines.append("- 需要法律/财务专业结论的场景（本技能只做信息处理）")
    lines.append("")
    if cmds:
        lines.append("## 怎么用")
        lines.append("")
        lines.append("```bash")
        for c in cmds:
            lines.append(c)
        lines.append("```")
        lines.append("")
    lines.append("## 来源与授权")
    lines.append("")
    lines.append("- 原项目：%s" % meta["url"])
    lines.append("- 协议：%s" % (meta.get("license") or "未标注 —— **上架前必须确认**"))
    lines.append("- 本包装的是**调用方式与工作流**，不重新分发原项目代码")
    lines.append("")
    return "\n".join(lines)


def build_install(meta, role_name):
    lines = []
    lines.append("# %s · 安装说明" % role_name)
    lines.append("")
    lines.append("## 适用客户端")
    lines.append("")
    lines.append("- 腾讯 QClaw（小龙虾）")
    lines.append("- OpenClaw")
    lines.append("- 小龙虾 AI（Web / iOS）")
    lines.append("")
    lines.append("## 安装步骤")
    lines.append("")
    lines.append("1. 下载并解压本技能包")
    lines.append("2. 放进技能目录（各客户端路径不同，见下方说明）")
    lines.append("3. 重启客户端")
    lines.append("")
    lines.append("## 前置条件（**缺一个都装不上**）")
    lines.append("")
    lines.append("- 原项目 %s 的运行环境与依赖" % meta["full_name"])
    if meta.get("license") not in ("MIT", "MIT-0", "Apache-2.0", "BSD-3-Clause", "GPL-3.0"):
        lines.append("- ⚠️ 原项目协议为 **%s**，商用前请自行确认授权范围"
                     % (meta.get("license") or "未标注"))
    lines.append("- 需要的账号、API Key 或本地服务")
    lines.append("")
    lines.append("## 常见装不上的原因")
    lines.append("")
    lines.append("| 现象 | 原因 | 处理 |")
    lines.append("|---|---|---|")
    lines.append("| 提示找不到命令 | 依赖没装 | 按原项目 README 装依赖 |")
    lines.append("| 提示缺凭据 | 没配 Key | 按原项目 README 配好 |")
    lines.append("| 装上了但跑不出结果 | 上游接口变了 | 打开原项目 issue 找维护者 |")
    lines.append("")
    return "\n".join(lines)


def build_video_script(meta, sec, cmds, role_name):
    """生成抖音 / 视频号口播脚本。

    参照对标调研里转发率最高的内容结构（第二批 63 条，转/赞 25.36 那条）：
        **先接住情绪，再给下一步** —— 不是纯项目介绍。
    所以脚本固定三段：
        ① 钩子（接住"我想找个现成工具"的念头）
        ② 是什么 + 怎么用（给硬信息，带数字）
        ③ 下一步 + 边界（**说什么人不该用它** ← 这是我们的差异化）

    2026-10-31 改动：
      钩子与封面不再写死话术，改由 `title_engine` 按**对标调研得出的公式**
      生成候选（公式 A~H + 封面模板 T1~T4）。
      之前那套写死的钩子只有一句话，等于把 76 条视频的调研结论浪费了。
      每条候选都带公式出处与数据依据，发布时能知道为什么这么写。
    """
    name = meta["full_name"]
    stars = meta.get("stars", 0)
    # 星数拿不到就说拿不到 —— 不写"数千""上万"这种模糊词去凑
    stars_txt = "{:,}".format(stars) if stars else "星数未获取"
    lines = []
    lines.append("# %s · 短视频脚本" % name)
    lines.append("")
    lines.append("> 角色：%s ｜ 语气：干脆、有数字、不吹 ｜ 时长目标：45~60 秒" % role_name)
    lines.append("")
    lines.append("## 候选标题（按 76 条对标视频的公式生成）")
    lines.append("")
    try:
        import title_engine as TE
        facts = TE.collect_facts()
        cands = TE.titles_for_package(meta, facts, role_name)
        cover = TE.cover_for_package(meta, facts)
        for i, t in enumerate(cands[:5], 1):
            lines.append("%d. **%s**" % (i, t["标题"]))
            lines.append("   - 公式 %s（%s）｜%s ｜ 依据：%s"
                         % (t["公式"], t["公式名"], t["验证强度"], t["依据"]))
        lines.append("")
        lines.append("> 选一条当口播第一句；带「待验证」的是公式 A/B（各只有 1 个数据点），"
                     "建议优先用「已验证」的。")
    except Exception as e:
        cands = []
        cover = {}
        lines.append("（标题候选生成失败：%s）" % e)
        lines.append("")
    lines.append("## 口播稿")
    lines.append("")
    lines.append("**【前 3 秒 · 钩子】**")
    lines.append("")
    # 钩子优先用公式 F（先接住情绪再给下一步，转发率最高那条的结构）
    hook = ""
    for t in cands:
        if t["公式"] == "F":
            hook = t["标题"]
            break
    if not hook and cands:
        hook = cands[0]["标题"]
    if hook:
        lines.append("「%s」" % hook)
    else:
        lines.append("「这个 GitHub 项目已经 %s 星了，%s，**你今天就能用起来**。」"
                     % (stars_txt, meta.get("group_name", "AI 项目")))
    lines.append("")
    lines.append("**【第 4~20 秒 · 是什么】**")
    lines.append("")
    lines.append("「%s」" % (meta.get("desc") or "它是一个 AI 相关项目"))
    lines.append("")
    lines.append("「它的价值在于：%s」"
                 % (sec.get("purpose_short") or sec.get("purpose") or "把这件事自动化了"))
    lines.append("")
    lines.append("**【第 21~40 秒 · 怎么用】**")
    lines.append("")
    if cmds:
        lines.append("「安装就这几行：」")
        lines.append("")
        for c in cmds[:4]:
            lines.append("`%s`" % c)
        lines.append("")
    lines.append("**【第 41~60 秒 · 下一步 + 边界】**")
    lines.append("")
    lines.append("「我已经把它**封装成了一个可以直接安装的 Skill**，装上就有这个岗位。」")
    lines.append("")
    lines.append("「但先说清楚：**这活不适合**%s。」"
                 % meta.get("not_for", "完全不想碰环境配置的人"))
    lines.append("")
    lines.append("「适合的是：%s。」" % meta.get("for_who", "有重复性工作、愿意自己准备依赖的中小企业主"))
    lines.append("")
    lines.append("## 录屏清单")
    lines.append("")
    lines.append("| 时间 | 画面 | 要点 |")
    lines.append("|---|---|---|")
    lines.append("| 0-3s | GitHub 仓库页（★ 数可见） | 数字要清楚 |")
    lines.append("| 3-10s | 快速滚动 README 关键段 | 鼠标框住用途段 |")
    lines.append("| 10-25s | 终端跑第一条命令 | **必须真跑，不要录屏剪辑假象** |")
    lines.append("| 25-40s | 真实输出结果 | 真实效果，不修饰 |")
    lines.append("| 40-55s | 展示页里这个 Skill 的卡片 | 指向「装上就能用」 |")
    lines.append("| 55-60s | 一句边界提醒 + 引导关注 | 说不适合谁 |")
    lines.append("")
    lines.append("## 封面")
    lines.append("")
    if cover:
        lines.append("模板：**%s %s**（%s）"
                     % (cover.get("模板", ""), cover.get("模板名", ""),
                        cover.get("适用", "")))
        lines.append("")
        lines.append("- 版式：%s" % cover.get("版式", ""))
        lines.append("- 出处：%s" % cover.get("出处", ""))
        lines.append("- 我们的版本：%s" % cover.get("我们的版本", ""))
        lines.append("- 大字文案：**%s**" % cover.get("大字文案", ""))
        lines.append("")
        lines.append("画面清单：")
        for s in cover.get("画面清单", []):
            lines.append("- %s" % s)
    else:
        lines.append("- 主体：**真实终端跑通的画面**（不是插画）")
        lines.append("- 大字：`%s` + 关键数字" % name)
        lines.append("- 底部一行：`★%s ｜ %s`" % (stars_txt if stars else "—",
                                                meta.get("license") or "协议待确认"))
    lines.append("")
    lines.append("## 禁忌")
    lines.append("")
    lines.append("- 不说「保证」「必火」「稳赚」")
    lines.append("- 不夸大效果（跑不通的别录）")
    lines.append("- 不念未经核实的数字")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 审核


def audit(pkg_dir, meta):
    """审核自动生成的包。返回 (报告dict, 是否通过)。

    审核四关：
      ① 结构完整   —— 文件齐不齐、frontmatter 合不合法
      ② 红线       —— 用项目自己的禁区清单扫一遍
      ③ 可用性     —— 有没有安装说明、有没有命令、依赖说没说清
      ④ 诚实性     —— 有没有把「待人工确认」的东西说成确定的

    红线扫描的范畴（这一步很容易做错，必须说清楚）：
      · **阻断级**：只扫**我们自己写的** SKILL.md 正文 —— 那是我们要对客户承诺的东西
      · **提示级**：原项目 README 的命中只作提示
        理由：平台红线管的是"你在小红书发内容时不能干什么"，
        而一个 GitHub 项目 README 里出现"直播""视频""代理"这类词，
        跟平台违规完全是两回事。**拿平台红线去扫英文 README 是范畴错配**，
        早先版本因此误报了 27 条，直接把合格包判成 0 分。
      · 关键词太短的（如"AI""视频"）本身歧义大，只在**长词（≥4 字）**命中时提示。
    """
    problems, warnings = [], []
    score = 100

    # ① 结构
    for f in ("SKILL.md", "clawhub.json", "README.md", "安装说明.md", "抖音脚本.md"):
        if not os.path.exists(os.path.join(pkg_dir, f)):
            problems.append("缺少文件：%s" % f)
            score -= 15
    skill_path = os.path.join(pkg_dir, "SKILL.md")
    skill_txt = ""
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            skill_txt = f.read()
        parts = skill_txt.split("---")
        fm = parts[1] if len(parts) > 2 else ""
        if not fm.strip():
            problems.append("SKILL.md 缺少 frontmatter")
            score -= 20
        else:
            for key in ("name:", "description:", "version:"):
                if key not in fm:
                    problems.append("frontmatter 缺少 %s" % key)
                    score -= 8
        if len(skill_txt) < 800:
            warnings.append("SKILL.md 内容偏短（%d 字），可能没抓到有用信息" % len(skill_txt))
            score -= 6

    # ② 红线
    #    按**适用域**分流，这是审核能不能用的关键：
    #      · product 域（商品本体红线：洗稿/破解/外呼/去标识…）
    #        → 命中即阻断。SKILL.md 就是在卖这个商品，撞上就是真风险。
    #      · publish 域（内容发布红线：标题党/擦边/私信导流…）
    #        → **只作提示**。平台红线管的是"你发到小红书的内容"，
    #          拿它去判一份产品文档是范畴错配——"## 绝对禁止"里的
    #          "绝对"会被判成"抖音标题党"，"不写「100%」"会被判成
    #          "淘宝极限词"。这些是**我们自己写的合规声明**。
    banned_hits = []
    publish_hits = []
    readme_hits = []
    try:
        from staff import kb as kbmod
        cfg = cfgmod.load()
        idx = kbmod.ForbiddenIndex.from_dir(
            os.path.join(cfgmod.PROJECT, "03_知识库", "wiki", "10_禁区"))

        # 阻断级：商品本体红线
        for m in idx.match(skill_txt, scope="product", level_filter="红线"):
            banned_hits.append("%s｜命中词「%s」｜%s" % (m.get("platform", "?"),
                                                       m.get("hit_keyword", ""),
                                                       (m.get("forbid") or "")[:30]))
        if banned_hits:
            problems.append("SKILL.md 命中商品红线 %d 处" % len(banned_hits))
            score -= 30 * len(banned_hits)

        # 提示级：内容发布红线，只在包里提个醒
        publish_kws = []
        for m in idx.match(skill_txt, scope="publish"):
            kw = m.get("hit_keyword", "")
            publish_kws.append(kw)
            publish_hits.append("%s｜命中词「%s」" % (m.get("platform", "?"), kw))
        if publish_hits:
            warnings.append("SKILL.md 含 %d 个内容发布域关键词（%s）—— "
                            "这些条款管的是发到平台的内容，**不阻断上架**，"
                            "但抖音脚本发布前要再确认一次"
                            % (len(publish_hits),
                               "、".join(sorted(set(publish_kws))[:6])))

        # 提示级：原项目 README 的命中，只报长词（≥4 字），避免"视频""直播"这类歧义词误报
        orig = os.path.join(pkg_dir, "_原README.md")
        if os.path.exists(orig):
            with open(orig, "r", encoding="utf-8") as f:
                rtxt = f.read()
            for m in idx.match(rtxt):
                kw = m.get("hit_keyword", "")
                if len(kw) >= 4:
                    readme_hits.append("%s｜%s" % (kw, (m.get("forbid") or "")[:40]))
            if readme_hits:
                warnings.append("原项目 README 命中 %d 处禁区关键词（多为英文项目用词撞词，"
                                "**上架前人工确认一次**）" % len(readme_hits))
    except Exception as e:
        warnings.append("红线扫描未执行：%s" % e)

    # ③ 可用性
    if "```bash" not in skill_txt:
        warnings.append("没有可执行命令 —— 客户装完不知道怎么用")
        score -= 12
    if "使用前提" not in skill_txt:
        warnings.append("没写使用前提 —— 缺依赖时会一头雾水")
        score -= 8
    lic = meta.get("license") or ""
    if not lic:
        warnings.append("原项目未标注协议 —— **上架前必须确认授权**")
        score -= 10
    elif lic not in ("MIT", "MIT-0", "Apache-2.0", "BSD-3-Clause", "GPL-3.0",
                     "BSD-2-Clause", "ISC", "Unlicense", "MPL-2.0"):
        warnings.append("原项目协议是 %s，非常见宽松协议，商用需确认" % lic)
        score -= 6

    # ④ 诚实性
    if "待人工" not in skill_txt and "待审" not in skill_txt:
        warnings.append("缺少「待人工确认」标记，容易被误当成已验证产品")
        score -= 5

    passed = (not problems) and score >= 60
    report = {
        "包目录": pkg_dir,
        "总分": max(0, score),
        "是否通过": passed,
        "阻断问题": problems,
        "提醒": warnings,
        "红线命中_阻断级": banned_hits,
        "红线命中_提示级": publish_hits[:10],
        "原README命中_提示级": readme_hits[:10],
        "审核时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return report, passed


# ---------------------------------------------------------------- 主流程


def process(full_name, use_llm=True, out_root=None, keep_src=False, radar=None):
    """处理一个仓库，产出 Skill 包 + 抖音脚本 + 审核报告。

    radar: 雷达扫描出的候选信息（可选）。传进来能补上
           「可包装成什么岗位」「热度评分」这类上下文——
           这些字段 GitHub API 里没有，只有雷达知道。
           不传也能跑，只是 SKILL.md 里少一段"这玩意儿能做成什么"，
           客户看完不知道该拿它干嘛。
    """
    pending = out_root or PENDING
    os.makedirs(pending, exist_ok=True)

    out = {"repo": full_name, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # 1. 仓库信息（API 优先，限流则降级抓页面，全失败则留空但不中断）
    info = fetch_repo_meta(full_name)
    out["meta_source"] = info.get("source")
    out["stars"] = info.get("stars") or 0
    out["stars_known"] = info.get("source") != "none"
    out["forks"] = info.get("forks") or 0
    out["license"] = info.get("license") or ""
    out["url"] = info.get("url")
    out["desc"] = (info.get("desc") or "").strip()
    out["topics"] = info.get("topics") or []
    out["pushed"] = info.get("pushed") or ""

    # 2. 下载（内存处理，不落盘）
    try:
        repo = download_repo(full_name)
    except Exception as e:
        out["error"] = "下载失败：%s" % e
        return out
    out["branch"] = repo["branch"]
    out["files"] = len(repo["files"])
    out["bytes"] = repo["bytes"]

    # 3. README
    rname, md = repo["readme_name"], repo["readme_text"]
    out["readme"] = rname
    out["readme_chars"] = len(md)
    if not md:
        out["error"] = "没有找到 README（该项目可能没有说明文件）"
        return out

    sec = extract_sections(md)
    cmds = detect_commands(md, repo["files"])
    out["commands"] = cmds
    out["sections"] = {k: len(v) for k, v in sec.items()}

    # 元数据没拿到时，描述从 README 兜底（比空着强，且是原文不是编的）
    if not out["desc"]:
        out["desc"] = _snippet(re.sub(r"^#.*$", "", md, count=1).strip(), 180)

    # 4. 角色命名与 slug
    role_name = pick_role_name(full_name, out["desc"] + " " + md[:500])
    slug = slugify(full_name.split("/")[-1])
    # slug 前缀加日期，避免与手工技能重名
    slug = "gh-%s" % slug

    meta = {
        "full_name": full_name,
        "slug": slug,
        "desc": out["desc"],
        "url": out["url"],
        "license": out["license"],
        "stars": out["stars"],
        "tags": (out["topics"] or ["github", "auto-generated"])[:6],
        "group_name": "GitHub 项目",
        "at": out["at"],
        "display": full_name.split("/")[-1],
    }

    # 雷达信息并入 meta —— 这几个字段 GitHub API 里没有，
    # 只有雷达知道（这个项目属于哪一类、能包装成什么岗位、评分多少）。
    # 少了它们，客户看完 SKILL.md 不知道该拿这东西干嘛。
    if radar:
        meta["group_name"] = radar.get("group_name") or meta["group_name"]
        meta["sellable_as"] = radar.get("sellable_as") or ""
        meta["radar_score"] = radar.get("score")
        meta["forks"] = radar.get("forks") or 0
        meta["lang"] = radar.get("lang") or ""
        meta["created"] = radar.get("created") or ""
        out["sellable_as"] = meta["sellable_as"]
        out["radar_score"] = meta["radar_score"]
        out["group_name"] = meta["group_name"]

    # 5. 生成产物
    pkg = os.path.join(pending, slug)
    os.makedirs(pkg, exist_ok=True)
    extra = ""
    if use_llm:
        extra = _llm_notes(meta, sec, cmds, role_name)
    with open(os.path.join(pkg, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(build_skill_md(meta, sec, cmds, role_name, extra))
    with open(os.path.join(pkg, "clawhub.json"), "w", encoding="utf-8") as f:
        json.dump(build_clawhub(meta, role_name), f, ensure_ascii=False, indent=2)
    with open(os.path.join(pkg, "README.md"), "w", encoding="utf-8") as f:
        f.write(build_readme(meta, sec, cmds, role_name))
    with open(os.path.join(pkg, "安装说明.md"), "w", encoding="utf-8") as f:
        f.write(build_install(meta, role_name))
    with open(os.path.join(pkg, "抖音脚本.md"), "w", encoding="utf-8") as f:
        f.write(build_video_script(meta, sec, cmds, role_name))
    # 保留原 README 供审核复查（红线提示级要用），也方便人工核对转换有没有失真
    with open(os.path.join(pkg, "_原README.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(pkg, "_源信息.json"), "w", encoding="utf-8") as f:
        json.dump({**meta, **out}, f, ensure_ascii=False, indent=2)

    # 6. 打包
    zip_path = os.path.join(pending, slug + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(pkg)):
            z.write(os.path.join(pkg, fn), "%s/%s" % (slug, fn))
    out["pkg"] = pkg
    out["zip"] = zip_path

    # 7. 审核
    report, passed = audit(pkg, meta)
    out["audit"] = report
    out["passed"] = passed
    with open(os.path.join(pkg, "_审核报告.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 8. 源码不落盘，无需清理

    # 9. 流水线日志
    #    注意：编排器（auto_pipeline）也会写日志，
    #    这里只记「单仓执行结果」，编排器记的是「批次视角」，
    #    两条用 src 字段区分，月底做统计时不会重复计数。
    os.makedirs(OUT_ROOT, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"src": "repo_to_skill", "at": out["at"],
                            "repo": full_name, "slug": slug,
                            "passed": passed, "score": report["总分"]},
                           ensure_ascii=False) + "\n")
    return out


def _llm_notes(meta, sec, cmds, role_name):
    """用模型补一段人工审核备注（帮审核的人快速看懂这个项目能干嘛）。"""
    cfg = cfgmod.load()
    if not cfg.get("api_key") and not cfg.get("simulate"):
        return ""
    try:
        from staff import llm as llmmod
        sysmsg = ("你是技术产品助理。读一段 GitHub 项目的 README 摘要，"
                  "用中文写 3-5 句话：这个项目解决什么问题、适合谁用、"
                  "**什么情况下不该用**。不许吹，不许编数字。")
        user = ("项目：%s\n简介：%s\n用途段：%s\n命令：%s"
                % (meta["full_name"], meta["desc"],
                   (sec.get("purpose") or "")[:1200], "\n".join(cmds[:5])))
        content, _u = llmmod.chat([{"role": "user", "content": user}],
                                   cfg=cfg, system=sysmsg, temperature=0.3,
                                   max_tokens=500, role="A25")
        return content.strip()
    except Exception:
        return ""


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = [a for a in argv if a.startswith("--")]
    if not args:
        print("用法：python repo_to_skill.py owner/repo [--no-llm]")
        return 1
    use_llm = "--no-llm" not in flags
    for full in args:
        r = process(full, use_llm=use_llm)
        if r.get("error"):
            print("✖ %s —— %s" % (full, r["error"]))
            continue
        a = r.get("audit", {})
        print("✔ %s" % full)
        print("   ★%s ｜ 命令 %s 条 ｜ README %s 字"
              % (r.get("stars"), len(r.get("commands", [])), r.get("readme_chars")))
        print("   包：%s" % r.get("pkg"))
        print("   审核：%s 分 ｜ %s" % (a.get("总分"),
                                        "通过" if r.get("passed") else "未通过"))
        for p in a.get("阻断问题", []):
            print("      ✖ %s" % p)
        for w in a.get("提醒", []):
            print("      ⚠ %s" % w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

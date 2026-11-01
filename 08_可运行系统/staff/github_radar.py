"""GitHub 项目雷达：每天自动找出最火最热门的 AI 相关项目，并给出可商品化评分。

背景
----
2026-10-30 对标调研（第二批 63 条视频号情报）确认：
  · GitHub 项目分享类内容占 63 条里的 33 条，是这个赛道流量池里最大的
  · 这类内容平均转发/点赞 10.9，全批平均 10.79 —— 属于最稳的选题类型
  · WorkBuddy 生态类内容转发/点赞 16.5，全部分类最高

于是「每天自动找一个爆火 GitHub 项目 → 做成 Skill → 卖给客户」这条路成立。
但**"最火"不能只用 star 数判断** —— star 高的项目往往是：
  · 要付费 API key 的（客户拿不到就没法用）
  · 是个库不是一个能用的工具（客户不会用）
  · 英文文档看不懂（我们的客户是中小企业主）
  · 已经有人做成 Skill 卖过了（我们没有差异化）

所以本模块做四件事：

    ① 发现  —— 多个查询词并行搜近 N 天新建的 AI 项目
    ② 评分  —— 热度 / 中文可用性 / 可包装性 / 风险 四个维度
    ③ 排序  —— 只输出"今天值得动的 N 个"，其余归档观察
    ④ 落盘  —— 候选清单写 JSON，给下游（转 Skill / 出脚本 / 审核）用

核心判据
--------
  · **热度只是入场券，不是结论**：star 增速比 star 总数更能反映"正在火"
  · **能包装成岗位才值钱**：一个能变成"某个岗位的技能"的项目，才有溢价
  · **拿不到就不能卖**：需要付费 key、需要自己写代码、需要 GPU 的，先降权
  · **不猜数据**：评分只用接口真实返回的字段，一个数都不补

用法
----
    python -m staff.github_radar                    # 扫一轮，输出候选清单
    python -m staff.github_radar --days 7 --top 10  # 指定窗口与条数
    python -m staff.github_radar --markdown         # 顺便出 Markdown 报告
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"

# 查询词：覆盖我们真正能包装成岗位的方向，而不是泛泛的 "AI"
#
# 为什么不用一个大宽泛的词：
#   "AI agent" 搜出来一大半是框架和教程，做不成客户能用的东西。
#   拆成下面几组，每组对应一种可售卖的形态。
QUERY_GROUPS = [
    {
        "key": "agent",
        "name": "AI 智能体 / 员工",
        "q": "agent in:name,description,readme AI agent",
        "sellable_as": "某个岗位的执行技能（如：自动调研、自动日报）",
    },
    {
        "key": "mcp",
        "name": "MCP 工具 / 插件",
        "q": "mcp server in:name,description",
        "sellable_as": "给现有 AI 员工装一个新本领",
    },
    {
        "key": "skill",
        "name": "Skill / 技能包",
        "q": "skill claude OR agent skill in:name,description",
        "sellable_as": "开箱即用的岗位技能",
    },
    {
        "key": "workflow",
        "name": "工作流自动化",
        "q": "workflow automation AI in:name,description,readme",
        "sellable_as": "把老板的重复流程固化成一个岗位",
    },
    {
        "key": "document",
        "name": "文档 / 知识库处理",
        "q": "document AI rag parser in:name,description",
        "sellable_as": "企业知识库相关岗位的技能",
    },
    {
        "key": "video",
        "name": "视频 / 内容生成",
        "q": "video generation AI in:name,description",
        "sellable_as": "内容生产类岗位的技能",
    },
]

# 包装方向细化：按项目描述里的关键词，判断它**具体**能做成什么。
#
# 为什么不用分组里的固定话术：
#   「某个岗位的执行技能（如：自动调研、自动日报）」——这话对每个项目都一样，
#   客户看完还是不知道这东西能干嘛。包装方向必须**具体到能想象**。
#   规则很简单：命中哪个词，就说哪个具体的岗位方向。
PACK_RULES = [
    (["rag", "retrieval", "knowledge base", "knowledge-base", "kb", "文档问答",
      "知识库", "检索"], "企业知识库问答：把老板的资料变成能问的知识库"),
    (["document parser", "pdf", "ocr", "解析", "文档识别", "抽取"],
     "文档处理岗：把 PDF/扫描件变成结构化数据"),
    (["meeting", "会议", "纪要", "transcri", "summary", "总结"],
     "会议纪要岗：录音转写 + 自动出纪要待办"),
    (["daily report", "日报", "report", "报表", "周报"],
     "日报岗：自动汇总数据出日报"),
    (["research", "调研", "research agent", "deep research"],
     "调研岗：自动做竞品/行业调研并出报告"),
    (["email", "邮件", "outreach", "cold email", "mailing"],
     "邮件岗：批量起草与跟进邮件"),
    (["crawler", "爬虫", "scraper", "scrape", "crawl"],
     "数据采集岗：定时抓取公开数据入库"),
    (["translate", "翻译", "i18n", "多语言"],
     "翻译岗：批量翻译并保留格式"),
    (["seo", "关键词", "关键词", "keyword", "ranking", "搜索排名"],
     "SEO 岗：关键词研究 + 排名追踪"),
    (["video", "视频", "tts", "配音", "数字人", "digital human", "avatar"],
     "内容生产岗：视频/配音/数字人生成"),
    (["image", "图像", "图片", "绘画", "画图"],
     "图片岗：批量出图与素材处理"),
    (["resume", "简历", "招聘", "jd", "招聘"],
     "招聘岗：JD 生成与简历初筛"),
    (["test", "测试", "unit test", "e2e", "自动化测试"],
     "测试岗：自动写测试并跑回归"),
    (["deploy", "部署", "ci/cd", "运维", "docker", "k8s"],
     "运维岗：部署流水线与故障自愈"),
    (["crm", "客户", "销售", "sales", "线索", "lead"],
     "销售岗：线索整理与跟进提醒"),
    (["finance", "财务", "发票", "报销", "会计", "invoice", "accounting"],
     "财务岗：票据识别与账目核对"),
    (["code review", "代码审查", "review", "lint", "静态检查"],
     "代码审查岗：提交前自动挑毛病"),
    (["prompt", "提示词", "prompt engineering", "角色扮演", "roleplay"],
     "岗位提示词包：开箱即用的行业话术"),
    (["browser", "浏览器", "自动化操作", "playwright", "selenium", "爬"],
     "浏览器操作岗：自动点网页填表单"),
    (["notion", "obsidian", "飞书", "feishu", "钉钉", "企业微信", "jira"],
     "办公协同岗：打通 Notion/飞书/企微/Jira"),
]


def _refine_sellable(desc, topics, group_sellable):
    """根据项目描述 + topics + README 关键词，判断它**具体**能做成什么岗位。

    命中具体规则就用具体说法；
    一个都没命中才退回分组默认话术（并明确写"待人工细化"）。
    """
    blob = " ".join([desc or ""] + list(topics or [])).lower()
    for kws, role in PACK_RULES:
        for k in kws:
            if k.lower() in blob:
                return "%s" % role
    return "%s（**待人工细化包装方向**）" % group_sellable


# 中文可用性关键词：命中说明作者面向中文用户，或有中文文档
CN_HINTS = ["中文", "chinese", "zh-cn", "简体中文", "国内", "微信", "公众号",
            "中文文档", "汉化"]

# 可包装性信号：说明它能变成一个"能交付给客户的岗位"
SELLABLE_HINTS = ["cli", "command line", "install", "pip install", "npm install",
                  "docker", "self-host", "self host", "api key", "webui",
                  "web ui", "gui", "plugin", "extension", "workflow",
                  "自动化", "批量", "batch", "tutorial", "教程", "guide"]

# 风险信号：命中就降权（不是一票否决，但要让排到后面去）
RISK_HINTS = ["gpu", "cuda", "nvidia", "fine-tune", "finetune", "training",
               "cuda", "显存", "训练", "dataset", "数据集", "paper", "论文",
               "awesome", "list of", "roadmap", "roadmap", "collection of",
               "awesome-list", "interview", "learn", "教程系列", "学习路线"]

# 硬性风险：需要付费 key 且没有免费额度说明 → 基本不能直接卖给客户
PAID_HINTS = ["api key", "openai key", "anthropic key", "付费", "paid tier",
              "subscription", "premium", "pro account", "credits required"]


def _headers():
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-staff-github-radar",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # 有 token 就用，没有也能跑（未认证 10 次/分钟搜索配额，够每天用）
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def _api(path, params=None, timeout=25):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _since(days):
    d = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return d


def _snippet(text, n=400):
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return t[:n]


def _cn_score(repo, readme):
    """中文可用性 0~1。"""
    blob = " ".join([
        repo.get("description") or "",
        repo.get("name") or "",
        (readme or "")[:4000].lower(),
    ]).lower()
    hits = sum(1 for k in CN_HINTS if k.lower() in blob)
    if repo.get("language") == "Chinese":
        hits += 2
    return min(1.0, hits / 3.0)


def _sellable_score(repo, readme):
    """可包装成岗位的程度 0~1。"""
    blob = (" ".join([
        repo.get("description") or "",
        (readme or "")[:6000],
    ])).lower()
    hits = sum(1 for k in SELLABLE_HINTS if k.lower() in blob)
    # 有中文 README 段落说明的更可能能直接包装
    return min(1.0, hits / 5.0)


def _risk_score(repo, readme):
    """风险 0~1，越高越不建议现在做。"""
    blob = (" ".join([
        repo.get("description") or "",
        (readme or "")[:6000],
    ])).lower()
    risk = 0.0
    for k in RISK_HINTS:
        if k.lower() in blob:
            risk += 0.12
    for k in PAID_HINTS:
        if k.lower() in blob:
            risk += 0.15
    # 仓库太大通常意味着复杂度高，不适合"下载就能用"
    size = repo.get("size") or 0
    if size > 200000:
        risk += 0.15
    elif size > 50000:
        risk += 0.08
    if not repo.get("archived") is False and repo.get("archived"):
        risk += 0.3
    return min(1.0, risk)


def _readme(repo_full_name, branch=None):
    """取 README 文本（尽力而为，拿不到就空字符串）。"""
    try:
        r = _api("/repos/%s/readme" % repo_full_name)
    except Exception:
        return ""
    import base64
    try:
        return base64.b64decode(r.get("content") or "").decode("utf-8", "replace")
    except Exception:
        return ""


def search_group(g, days=7, min_stars=20, per_page=15):
    """搜索一个查询组。"""
    q = "%s created:>%s stars:>=%d" % (g["q"], _since(days), min_stars)
    try:
        data = _api("/search/repositories", {
            "q": q, "sort": "stars", "order": "desc", "per_page": per_page})
    except urllib.error.HTTPError as e:
        return {"group": g["key"], "error": "HTTP %s %s" % (e.code, e.reason)}
    except Exception as e:
        return {"group": g["key"], "error": str(e)}
    return {"group": g["key"], "items": data.get("items") or []}


def score_repo(repo, group):
    """给单个仓库打分。返回 (总分, 明细)。"""
    stars = repo.get("stargazers_count") or 0
    forks = repo.get("forks_count") or 0

    # 热度：星数取对数，避免头部项目碾压一切；再叠加 fork 活跃度
    import math
    heat = min(1.0, math.log10(max(stars, 1)) / 5.0)          # 10万星 ≈ 1.0
    heat = min(1.0, heat * 0.85 + min(1.0, forks / max(stars, 1) * 3) * 0.15)

    readme = ""
    # 只给前 12 个候选拉 README（省 API 配额），其余按 0 分处理
    cn = sell = risk = 0.0
    readme = _readme(repo["full_name"])
    cn = _cn_score(repo, readme)
    sell = _sellable_score(repo, readme)
    risk = _risk_score(repo, readme)

    total = heat * 0.40 + cn * 0.20 + sell * 0.25 + (1 - risk) * 0.15

    return round(total * 100, 1), {
        "heat": round(heat, 3),
        "chinese": round(cn, 3),
        "sellable": round(sell, 3),
        "risk": round(risk, 3),
        "readme_len": len(readme),
    }


def scan(days=7, top=10, min_stars=20, with_readme=True):
    """跑一轮完整扫描。

    返回候选清单（已排序、已去重、已附评分）。
    """
    seen = {}
    group_err = []
    for g in QUERY_GROUPS:
        r = search_group(g, days=days, min_stars=min_stars)
        if r.get("error"):
            group_err.append(r)
            continue
        for it in r.get("items", []):
            fn = it.get("full_name")
            if not fn:
                continue
            if fn in seen:
                # 同一个仓库命中多个组：合并分组标签
                gk = it.get("_group")
                if gk and gk not in seen[fn]["groups"]:
                    seen[fn]["groups"].append(gk)
                continue
            it["_group"] = g["key"]
            it["_group_name"] = g["name"]
            it["_sellable_as"] = g.get("sellable_as", "")
            seen[fn] = it

    cands = []
    for fn, it in seen.items():
        if not with_readme:
            continue
        total, detail = score_repo(it, it["_group"])
        # 包装方向按项目描述细化，不给所有人同一句套话
        sellable = _refine_sellable(
            it.get("description"), it.get("topics"), it["_sellable_as"])
        cands.append({
            "full_name": fn,
            "url": it.get("html_url"),
            "desc": _snippet(it.get("description")),
            "stars": it.get("stargazers_count") or 0,
            "forks": it.get("forks_count") or 0,
            "lang": it.get("language") or "",
            "created": (it.get("created_at") or "")[:10],
            "pushed": (it.get("pushed_at") or "")[:10],
            "license": ((it.get("license") or {}) or {}).get("spdx_id") or "",
            "topics": it.get("topics") or [],
            "groups": [it["_group"]],
            "group_name": it["_group_name"],
            "sellable_as": sellable,
            "score": total,
            "detail": detail,
        })

    cands.sort(key=lambda x: -x["score"])
    return cands[:top], {"errors": group_err, "scanned": len(seen),
                         "days": days, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ---------------------------------------------------------------- 落盘

def out_dir():
    """情报雷达的落盘目录。

    为什么走 config.ROOT
    ------------------
    原来这里是 `dirname(dirname(__file__)) + data/state`。
    源码模式下对，**打包成 exe 后就错**：
    PyInstaller 把程序解到 `Temp\\_MEIxxxx\\`，那里根本没有 `data/state`，
    于是情报数据被写进临时目录 —— 关掉程序就没了。
    config.ROOT 已经处理了 exe 的目录布局判定。
    """
    try:
        from . import config as cfgmod
        root = getattr(cfgmod, "ROOT", "")
        if root:
            d = os.path.join(root, "data", "state")
            os.makedirs(d, exist_ok=True)
            return d
    except Exception:
        pass
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "state")
    os.makedirs(d, exist_ok=True)
    return d


def save(cands, meta, path=None):
    p = path or os.path.join(out_dir(), "github_radar.json")
    payload = {"meta": meta, "candidates": cands}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return p


def render_markdown(cands, meta):
    """出 Markdown 报告，给短视频官当脚本素材。"""
    lines = ["# GitHub 项目雷达 · %s" % meta.get("at", ""), ""]
    lines.append("> 窗口：近 %s 天新建 ｜ 扫描 %s 个仓库 ｜ 候选 %s 条"
                 % (meta.get("days"), meta.get("scanned"), len(cands)))
    lines.append("> 评分 = 热度 40%% + 中文可用性 20%% + 可包装性 25%% + 低风险 15%%")
    lines.append("")
    lines.append("| # | 项目 | 分数 | 星 | 定位 | 可包装成 |")
    lines.append("|---|---|---|---|---|---|")
    for i, c in enumerate(cands, 1):
        lines.append("| %d | [%s](%s) | **%.1f** | %s | %s | %s |" % (
            i, c["full_name"], c["url"], c["score"], c["stars"],
            c["group_name"], c["sellable_as"]))
    lines.append("")
    lines.append("## 逐条明细")
    for i, c in enumerate(cands, 1):
        d = c["detail"]
        lines.append("")
        lines.append("### %d. %s（%.1f 分）" % (i, c["full_name"], c["score"]))
        lines.append("- 链接：%s" % c["url"])
        lines.append("- 简介：%s" % (c["desc"] or "（无）"))
        lines.append("- 星/叉：%s / %s ｜ 语言：%s ｜ 协议：%s"
                     % (c["stars"], c["forks"], c["lang"] or "—", c["license"] or "—"))
        lines.append("- 创建 %s ｜ 最近推送 %s" % (c["created"], c["pushed"]))
        lines.append("- 命中分组：%s ｜ 可包装成：%s" % (c["group_name"], c["sellable_as"]))
        lines.append("- 热度 %.2f ｜ 中文 %.2f ｜ 可包装 %.2f ｜ 风险 %.2f"
                     % (d["heat"], d["chinese"], d["sellable"], d["risk"]))
        if c["topics"]:
            lines.append("- 标签：%s" % ", ".join(c["topics"][:10]))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    days, top, md = 7, 10, False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--days":
            days = int(args[i + 1]); i += 2; continue
        if args[i] == "--top":
            top = int(args[i + 1]); i += 2; continue
        if args[i] == "--markdown":
            md = True; i += 1; continue
        i += 1

    cs, mt = scan(days=days, top=top)
    p = save(cs, mt)
    print("扫描 %s 个仓库，候选 %s 条 → %s" % (mt["scanned"], len(cs), p))
    for i, c in enumerate(cs, 1):
        print("  %2d. %-42s %5.1f  ★%-6s  %s" %
              (i, c["full_name"], c["score"], c["stars"], c["group_name"]))
    if mt.get("errors"):
        print("查询组报错：", mt["errors"])
    if md:
        mp = p.replace(".json", ".md")
        with open(mp, "w", encoding="utf-8") as f:
            f.write(render_markdown(cs, mt))
        print("Markdown：", mp)

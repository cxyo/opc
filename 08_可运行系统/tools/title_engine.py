# -*- coding: utf-8 -*-
"""标题 / 封面生成器 —— 把 76 条对标视频的调研结论变成可执行代码。

这块为什么存在
--------------
76 条视频号链接全部抓取、四分类归档、转发率/收藏率逐条算过，
结论写进了 `03_知识库/wiki/07_选题与素材/标题句式库.md` 与
`对标分析_2026-10-30_第二批63条视频.md`。

但结论停在文档里 = 没有用。
真正能落到流水线上的，是下面这两张表：

  公式 A~H  —— 标题/口播的句式，每条带出处与转/赞数据
  模板 T1~T4 —— 封面版式，每条带适用场景

本模块把两张表变成函数：
  titles_for_package()  给一个 Skill 包，产出 5 条候选标题（各带公式出处）
  cover_for_package()   给一个 Skill 包，产出封面版式建议 + 画面清单

硬规矩（跟整个项目一致）
------------------------
1. **只用实测数字**。岗位数、行业包数从代码里实时数出来；
   token 成本从 telemetry 读。拿不到就写"未获取"，绝不编。
2. 每条标题都要标出**用了哪个公式、依据是哪条视频**。
   客户/同事看了知道这不是我编的。
3. 公式 A/B 只有 1 个数据点，标注保持"待验证"，不冒充已验证。
"""

import json
import os
import re
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

# 公式表 / 封面模板 / 实测数据采集 —— 统一从 staff.title_rules 取。
# 2026-10-31：这些定义原本在本文件里，但免安装版（exe）不打包 tools/ 目录，
# 导致素材库的「改写」功能在 exe 里失效。现在定义搬到 staff/，两边共用一份。
_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))
from staff.title_rules import FORMULAS, COVERS, collect_facts  # noqa: E402

# ------------------------------------------------------------------
# 公式表：结构 + 出处 + 验证强度
# ------------------------------------------------------------------
# verified: "已验证"(≥2 数据点) / "待验证"(1 数据点)
# ------------------------------------------------------------------
# 真实数据采集
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# 标题生成
# ------------------------------------------------------------------

def _fmt_num(v):
    """数字加千分位；None 直接返回 None（让上层写「未获取」）。"""
    if v is None:
        return None
    try:
        return "{:,}".format(int(v))
    except Exception:
        return str(v)


def titles_for_package(meta: Dict[str, Any],
                       facts: Optional[Dict[str, Any]] = None,
                       role_name: str = "") -> List[Dict[str, Any]]:
    """给一个 Skill 包产出候选标题。

    meta 至少要有：full_name / desc / stars / group_name / sellable_as
    facts 默认现场采集；也可传入快照（测试用）。

    返回每条包含：标题正文 / 公式 / 依据 / 验证强度 / 用途
    """
    facts = facts if facts is not None else collect_facts()
    out: List[Dict[str, Any]] = []

    name = meta.get("full_name") or "该项目"
    short = name.split("/")[-1]
    sell = meta.get("sellable_as") or "AI 员工岗位"
    stars = _fmt_num(meta.get("stars"))
    roles = _fmt_num(facts.get("岗位数"))
    packs = _fmt_num(facts.get("行业包数"))
    combos = _fmt_num(facts.get("岗位乘行业"))
    cost = (facts.get("token成本") or {})
    tokens = _fmt_num(cost.get("累计token"))
    money = cost.get("累计花费")
    deploy = facts.get("部署耗时") or {}
    deploy_s = deploy.get("秒")

    def add(text, fkey, note=""):
        f = FORMULAS[fkey]
        out.append({
            "标题": text,
            "公式": fkey,
            "公式名": f["name"],
            "依据": f["src"],
            "数据": f["data"],
            "验证强度": f["verified"],
            "用途": f["use"],
            "备注": note,
        })

    # ── 公式 D：规模数字 + 零门槛部署 ──────────────────────
    if combos:
        add("%s套AI员工，%s就能跑" % (combos, "客户电脑双击即用"),
            "D", "规模词来自实测岗位数×行业包数")
    elif roles and packs:
        add("%s个岗位 / %s个行业包，解压即用" % (roles, packs),
            "D", "规模词来自实测岗位数与行业包数")

    # ── 公式 E：真实成本对比（只用实测值）──────────────────
    if tokens:
        add("跑完一次真实任务，%s token" % tokens,
            "E", "token 来自 telemetry 累计读数")
    if money:
        add("部署一整套AI员工，成本%s" % money,
            "E", "花费来自 telemetry 累计读数")
    if deploy_s:
        add("部署一整套AI员工，%s秒跑起来" % deploy_s,
            "E", "耗时来自实测部署脚本")

    # ── 公式 A：碰到我的 X，你的 Y 算是 Z 了 ────────────────
    add("碰到我的%s，你的交付周期算是结束了" % sell,
        "A", "公式 A 只有 1 个数据点，标注为待验证")

    # ── 公式 G：交付方 vs 卖课方 ────────────────────────────
    add("同行卖AI课，我给%s交付了AI员工" % (short or "工厂"),
        "G", "强调可验收，不讲部署教学")

    # ── 公式 F：先接住情绪，再给下一步（口播结构用）────────
    if stars:
        add("想找个现成AI工具的，这个%s星项目今天就能用起来" % stars,
            "F", "先接住念头再给下一步，全批转/赞最高那条的结构")

    # ── 公式 C：反向许愿 ───────────────────────────────────
    add("别再自己写%s了，这个岗位现在是AI员工" % role_name or
        "别再自己写文档了，这个岗位现在是AI员工",
        "C", "反向用法，零情绪词")

    # ── 公式 B：今年的 X 把我变成了 Y（走封面）──────────────
    add("今年的AI把我变成了\"一人公司\"", "B", "封面大字专用")
    add("今年的自动化把我变成了\"包工头\"", "B", "封面大字专用")

    return out


def cover_for_package(meta: Dict[str, Any],
                      facts: Optional[Dict[str, Any]] = None,
                      prefer: str = "") -> Dict[str, Any]:
    """给一个 Skill 包产出封面建议。

    prefer 指定模板键（T1~T4）；不给就按「哪个模板最适合这个包」自动选。
    返回：模板键 / 版式 / 为什么 / 画面清单 / 大字文案
    """
    facts = facts if facts is not None else collect_facts()
    sell = meta.get("sellable_as") or "AI 员工岗位"
    short = (meta.get("full_name") or "").split("/")[-1]
    roles = _fmt_num(facts.get("岗位数")) or "N"
    packs = _fmt_num(facts.get("行业包数")) or "N"
    combos = _fmt_num(facts.get("岗位乘行业")) or "N"
    cost = (facts.get("token成本") or {})
    tokens = _fmt_num(cost.get("累计token")) or "未获取"

    # 自动选模板：优先 T3（可信度最高），有规模数字则 T4
    if not prefer:
        if combos and combos != "N":
            prefer = "T4"
        else:
            prefer = "T3"

    c = COVERS.get(prefer) or COVERS["T3"]

    # 画面清单：每个模板要拍什么
    shots = {
        "T1": [
            "顶部标题条：「AI 员工系统真实成本表」",
            "左列：各工具单价（截图/文字，标来源）",
            "右列：整套系统跑一次真实任务的实际开销",
            "底部一行：数据采集时间，让客户知道不是抄的",
        ],
        "T2": [
            "真人半身像（你自己，工位/车间实景更可信）",
            "满屏大字，关键词高亮变色",
            "大字：`%s`" % ("今年的自动化把我变成了\"包工头\""),
            "角标小字：`%s 套 AI 员工，全流程可验收`" % combos,
        ],
        "T3": [
            "**真实终端录屏**（作战看板 / 派活过程）占画面 2/3",
            "文件树或看板局部，能看到真目录真文件",
            "底部一行结论：`%s 岗位 / %s token / 部署 %s`"
            % (roles, tokens, "实测秒数"),
            "**不要用 AI 插画** —— 第二批 63 条全是插画，真实画面才是稀缺的",
        ],
        "T4": [
            "深色网格底",
            "黄绿大字，数量词最大：`%s`" % combos,
            "副标题：`%s 个岗位 × %s 个行业包`" % (roles, packs),
            "下方嵌一张真实界面截图（别插画）",
        ],
    }

    return {
        "模板": prefer,
        "模板名": c["name"],
        "版式": c["layout"],
        "出处": c["src"],
        "适用": c["fit"],
        "我们的版本": c["for_us"],
        "画面清单": shots.get(prefer, []),
        "大字文案": {
            "T1": "AI 员工系统真实成本表",
            "T2": "今年的自动化把我变成了\"包工头\"",
            "T3": "%s 步 / %s token / 部署实测秒数" % ("实测步数", tokens),
            "T4": "%s 套 AI 员工" % combos,
        }.get(prefer, ""),
        "技能对象": sell,
        "项目名": short,
    }


def render_package_titles(meta, facts=None, role_name="") -> str:
    """把候选标题渲染成可直接贴进 SKILL.md 的 Markdown。"""
    facts = facts if facts is not None else collect_facts()
    ts = titles_for_package(meta, facts, role_name)
    cv = cover_for_package(meta, facts)

    lines = ["## 候选标题（按对标调研结论生成）", ""]
    lines.append("> 每条都标了用了哪个公式、依据哪条对标视频。")
    lines.append("> 公式 A/B 目前**只有 1 个数据点**，属待验证；D/E/F/H 各有 2-5 个数据点，已验证。")
    lines.append("")
    for i, t in enumerate(ts, 1):
        lines.append("%d. **%s**" % (i, t["标题"]))
        lines.append("   - 公式 %s（%s）｜%s" % (t["公式"], t["公式名"], t["验证强度"]))
        lines.append("   - 依据：%s" % t["依据"])
        lines.append("   - 数据：%s" % t["数据"])
        if t.get("备注"):
            lines.append("   - 备注：%s" % t["备注"])
    lines.append("")
    lines.append("## 封面建议（%s %s）" % (cv["模板"], cv["模板名"]))
    lines.append("")
    lines.append("- 版式：%s" % cv["版式"])
    lines.append("- 出处：%s" % cv["出处"])
    lines.append("- 适用：%s" % cv["适用"])
    lines.append("- 我们的版本：%s" % cv["我们的版本"])
    lines.append("- 大字文案：**%s**" % cv["大字文案"])
    lines.append("")
    lines.append("画面清单：")
    for s in cv["画面清单"]:
        lines.append("- %s" % s)
    lines.append("")
    lines.append("### 实测数据（本页所有数字的来源）")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append("| 岗位数 | %s |" % (facts.get("岗位数") or "未获取"))
    lines.append("| 行业包数 | %s |" % (facts.get("行业包数") or "未获取"))
    lines.append("| 岗位 × 行业 | %s |" % (facts.get("岗位乘行业") or "未获取"))
    tc = facts.get("token成本") or {}
    lines.append("| 累计 token | %s |" % (_fmt_num(tc.get("累计token")) or "未获取"))
    lines.append("| 累计花费 | %s |" % (tc.get("累计花费") or "未获取"))
    dp = facts.get("部署耗时") or {}
    lines.append("| 部署耗时 | %s |" % (dp.get("秒") or "未获取"))
    lines.append("| 采集时间 | %s |" % facts.get("采集时间"))
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    f = collect_facts()
    print("== 实测数据 ==")
    for k, v in f.items():
        print("  %-10s %s" % (k, v))
    print()
    demo = {
        "full_name": "SonicloudTech/mouse-mcp",
        "stars": 1234,
        "group_name": "MCP 服务",
        "sellable_as": "鼠标自动化岗",
    }
    print(render_package_titles(demo, f, "鼠标自动化"))

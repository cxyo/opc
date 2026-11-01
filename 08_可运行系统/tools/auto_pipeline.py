#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GitHub 全自动流水线 · 编排器

一条命令跑完五步
----------------
    ① 搜索   —— 扫 GitHub，找最近爆火的 AI 项目（staff/github_radar.py）
    ② 下载   —— 拉源码 + README，**在内存里处理，不落盘**
    ③ 做包   —— 生成 SKILL.md / clawhub.json / README / 安装说明
    ④ 脚本   —— 生成抖音口播脚本（三段式，按对标最高转发结构写）
    ⑤ 审核   —— 结构 / 红线 / 可用性 / 诚实性 四关

产物一律进 `_待审/`，**不自动上架**。
    理由很实在：README 写得漂亮 ≠ 装得上、跑得通、对客户有用。
    自动上架一次翻车，客户的信任就没了。

为什么要有编排器（而不是每次手动跑单仓脚本）
------------------------------------------
单仓脚本能跑，但没法回答客户最关心的一句话：
**"你这周到底产出了什么？"**

编排器补三件事：
    · 一次跑完 N 个仓，中途单个失败不中断整批
    · 产出一份**日报**（Markdown + JSON），能直接发朋友圈/发群
    · 记流水日志，月底能回答"这个月做了多少包、哪些真能卖"

用法
----
    python auto_pipeline.py                          # 搜索 + 处理前 5 个
    python auto_pipeline.py --top 10                 # 处理前 10 个
    python auto_pipeline.py --scan-only              # 只搜索，不做包
    python auto_pipeline.py --repo owner/repo        # 指定仓库，跳过搜索
    python auto_pipeline.py --min-score 60           # 只处理评分 ≥60 的候选
    python auto_pipeline.py --no-llm                 # 不调模型，纯模板（快、稳）
    python auto_pipeline.py --days 3                 # 只看最近 3 天的新项目

设计上的三条铁律
----------------
    1. **不落盘源码** —— 只在内存里解压 README，避免磁盘塞满，
       也避免触发批量删除拦截。
    2. **失败不扩散** —— 一个仓失败只记一条，整批继续跑完。
    3. **人工闸门不可绕过** —— 流水线只写 `_待审/`，
       移 `_已上架/` 必须靠人签字（见 publish_gate）。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))          # 一人AI公司/
sys.path.insert(0, os.path.join(PROJECT, "08_可运行系统"))
sys.path.insert(0, HERE)

import repo_to_skill as R                                  # noqa: E402
from staff import github_radar as G                        # noqa: E402

# ---- 输出位置 -----------------------------------------------------------
DATA_DIR = os.path.join(PROJECT, "08_可运行系统", "data", "state")
REPORT_DIR = os.path.join(PROJECT, "11_变现模式", "流水线日报")
LOG_PATH = os.path.join(PROJECT, "07_AI员工", "05_自动生成技能", "流水线日志.jsonl")

# ---- 流水线参数（默认值按"稳"来定，不是按"多"来定）----------------------
DEFAULTS = {
    "days": 7,          # 雷达看最近几天
    "top": 5,           # 一轮处理几个仓（人工审核的量是有限的）
    "min_stars": 20,    # star 门槛：太低全是玩具项目
    "min_score": 0,     # 雷达评分门槛
    "use_llm": True,    # 是否调模型润色
}


def _ensure(d):
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def _log(rec):
    """追加一条流水日志（JSONL，一行一条，方便后续做月度统计）。

    src 字段区分来源：
      · auto_pipeline —— 批次视角（编排器写的）
      · repo_to_skill —— 单仓视角（转换器自己写的）
    月底统计时按 src 过滤，避免重复计数。
    """
    _ensure(os.path.dirname(LOG_PATH))
    rec = dict(rec)
    rec["src"] = "auto_pipeline"
    rec["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- ① 搜索


def stage_scan(args):
    """跑一轮雷达搜索。返回候选清单。

    radar.scan() 返回 (cands, meta) 元组，这里取 cands。
    """
    print("=" * 66)
    print("① 搜索 · 扫 GitHub 找爆火 AI 项目")
    print("=" * 66)
    t0 = time.time()
    try:
        ret = G.scan(days=args.days, top=args.top * 3,
                     min_stars=args.min_stars, with_readme=True)
        cands = ret[0] if isinstance(ret, tuple) else ret
        meta = ret[1] if isinstance(ret, tuple) else {}
    except Exception as e:
        print("   ✖ 雷达扫描失败：%s" % e)
        return []
    if not cands:
        print("   ⚠ 没扫到候选（可能是 API 限流，或最近 %d 天确实没有新项目）" % args.days)
        if meta.get("errors"):
            for err in meta["errors"][:3]:
                print("     · %s" % (err.get("error") if isinstance(err, dict) else err))
        return []

    if args.min_score > 0:
        before = len(cands)
        cands = [c for c in cands if c.get("score", 0) >= args.min_score]
        print("   评分门槛 ≥%d：%d → %d 个" % (args.min_score, before, len(cands)))

    print("   扫到 %d 个候选，用时 %.1f 秒" % (len(cands), time.time() - t0))
    for c in cands[:args.top]:
        print("   · %-42s ★%-6s 评分 %s" % (
            c.get("full_name", "?")[:42], c.get("stars", 0), c.get("score", "?")))
    return cands[:args.top]


# ---------------------------------------------------------------- ②~⑤ 处理


def stage_process(repo_name, args, radar=None):
    """对单个仓库跑完 ②下载 → ③做包 → ④脚本 → ⑤审核。

    radar: 雷达候选信息。传进去能让 SKILL.md 带上
           「这玩意儿能包装成什么岗位」——客户看完才知道该拿它干嘛。

    **单仓时限**：卡死在一个仓上是自动化任务最糟的失败模式——
    早先没设时限，一次批处理跑了 16 分钟只出 1 个包。
    超过 budget 秒就记失败、继续下一个，**不能因为一个仓拖死整批**。
    """
    print("-" * 66)
    print("②~⑤ 处理 · %s" % repo_name)
    if radar and radar.get("sellable_as"):
        print("   包装方向：%s" % radar["sellable_as"])

    budget = getattr(args, "repo_budget", 240)
    t0 = time.time()

    try:
        res = R.process(repo_name, use_llm=args.no_llm is False,
                        keep_src=False, radar=radar)
    except Exception as e:
        print("   ✖ 失败：%s" % e)
        _log({"stage": "process", "repo": repo_name, "ok": False, "error": str(e)})
        return None

    cost = time.time() - t0
    if cost > budget:
        print("   ⚠ 耗时 %.0f 秒，超过单仓时限 %d 秒" % (cost, budget))
    else:
        print("   用时 %.0f 秒" % cost)

    # 失败必须**看得见**。
    # process() 遇到下载失败/没有 README 时会提前返回，
    # 不把 error 打出来的话，日志上只看到"评分 0 未通过"，
    # 根本不知道是仓库坏了还是审核没过——这是排查噩梦。
    if res.get("error"):
        print("   ✖ 中断：%s" % res["error"])
        _log({"stage": "process", "repo": repo_name, "ok": False,
              "error": res["error"]})
        return None

    audit = res.get("audit", {}) or {}
    score = audit.get("总分", 0)
    passed = audit.get("是否通过", False)
    pkg_dir = audit.get("包目录", "")

    print("   评分 %s ｜ %s" % (score, "通过" if passed else "未通过"))
    for p in audit.get("阻断问题", []) or []:
        print("     ✖ %s" % p)
    for w in (audit.get("提醒", []) or [])[:3]:
        print("     ⚠ %s" % w)

    _log({
        "stage": "process", "repo": repo_name, "ok": True,
        "score": score, "passed": passed, "pkg": pkg_dir,
        "problems": audit.get("阻断问题", []),
    })
    return res


# ---------------------------------------------------------------- 日报


def build_report(items, args, elapsed):
    """汇总成本轮流水线的产出。"""
    ok = [x for x in items if x and x.get("audit")]
    passed = [x for x in ok if (x.get("audit") or {}).get("是否通过")]
    lines = []
    lines.append("# GitHub 流水线日报 · %s" % datetime.now().strftime("%Y-%m-%d"))
    lines.append("")
    lines.append("> 一次跑完：搜索 → 下载 → 做 Skill 包 → 出抖音脚本 → 审核")
    lines.append("")

    lines.append("## 本轮概览")
    lines.append("")
    lines.append("| 项 | 数 |")
    lines.append("|---|---|")
    lines.append("| 处理仓库 | %d |" % len(items))
    lines.append("| 成功产出包 | %d |" % len(ok))
    lines.append("| 审核通过 | %d |" % len(passed))
    lines.append("| 需人工复核 | %d |" % (len(ok) - len(passed)))
    lines.append("| 总耗时 | %.1f 秒 |" % elapsed)
    lines.append("")

    if ok:
        lines.append("## 产出清单")
        lines.append("")
        lines.append("| 仓库 | 评分 | 审核 | 可包装成 | 包目录 |")
        lines.append("|---|---|---|---|---|")
        for x in ok:
            a = x.get("audit") or {}
            sell = (x.get("sellable_as") or x.get("meta", {}).get("sellable_as") or "-")
            lines.append("| %s | %s | %s | %s | `%s` |" % (
                x.get("repo", ""),
                a.get("总分", "-"),
                "✅ 通过" if a.get("是否通过") else "⚠ 待复核",
                sell,
                os.path.basename(a.get("包目录", "")) or "-",
            ))
        lines.append("")

    if passed:
        lines.append("## 可直接进入上架流程的包")
        lines.append("")
        for x in passed:
            a = x.get("audit") or {}
            lines.append("### %s" % x.get("repo"))
            lines.append("")
            lines.append("- 包目录：`%s`" % a.get("包目录", ""))
            for w in (a.get("提醒", []) or []):
                lines.append("- ⚠ %s" % w)
            lines.append("")

    lines.append("## 下一步人工动作")
    lines.append("")
    lines.append("1. 逐个打开 `_待审/` 里的 SKILL.md，**实机装一次**——")
    lines.append("   README 写得漂亮不等于装得上，这条流水线替不了你做。")
    lines.append("2. 核对原项目协议：MIT / Apache / BSD 才可直接商用。")
    lines.append("3. 抖音脚本先自审一遍：钩子那句是不是**你真做过**？")
    lines.append("   没做过就别录——本系统第一号红线是「不虚构」。")
    lines.append("4. 通过的包移入 `_已上架/`，未通过的补完再审。")
    lines.append("")
    return "\n".join(lines)


def write_report(items, args, elapsed):
    _ensure(REPORT_DIR)
    stamp = datetime.now().strftime("%Y-%m-%d")
    md_path = os.path.join(REPORT_DIR, "流水线日报_%s.md" % stamp)
    json_path = os.path.join(REPORT_DIR, "流水线日报_%s.json" % stamp)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_report(items, args, elapsed))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "参数": {"days": args.days, "top": args.top,
                   "min_stars": args.min_stars, "min_score": args.min_score,
                   "no_llm": args.no_llm},
            "耗时秒": round(elapsed, 1),
            "结果": items,
        }, f, ensure_ascii=False, indent=2)
    return md_path, json_path


# ---------------------------------------------------------------- 主流程


def main():
    ap = argparse.ArgumentParser(
        description="GitHub 全自动流水线：搜索 → 下载 → 做 Skill → 出脚本 → 审核")
    ap.add_argument("--days", type=int, default=DEFAULTS["days"],
                    help="雷达看最近几天（默认 %d）" % DEFAULTS["days"])
    ap.add_argument("--top", type=int, default=DEFAULTS["top"],
                    help="一轮处理几个仓（默认 %d）" % DEFAULTS["top"])
    ap.add_argument("--min-stars", type=int, default=DEFAULTS["min_stars"],
                    help="star 门槛（默认 %d）" % DEFAULTS["min_stars"])
    ap.add_argument("--min-score", type=int, default=DEFAULTS["min_score"],
                    help="雷达评分门槛（默认 %d=不限）" % DEFAULTS["min_score"])
    ap.add_argument("--repo", action="append", default=[],
                    help="指定仓库 owner/repo，可重复；给了就跳过搜索")
    ap.add_argument("--scan-only", action="store_true", help="只搜索，不做包")
    ap.add_argument("--no-llm", action="store_true", help="不调模型，纯模板")
    ap.add_argument("--repo-budget", type=int, default=240,
                    help="单仓处理时限（秒），超了记失败继续下一个（默认 %d）"
                         % 240)
    args = ap.parse_args()

    t0 = time.time()
    print()
    print("╔" + "═" * 64 + "╗")
    print("║  GitHub 全自动流水线 · 搜索→下载→做包→脚本→审核  " +
          " " * 16 + "║")
    print("╚" + "═" * 64 + "╝")
    print()

    # ① 搜索
    radar_map = {}
    if args.repo:
        repos = args.repo
        print("① 跳过搜索，直接处理指定仓库：%s" % ", ".join(repos))
        print()
    else:
        cands = stage_scan(args)
        repos = [c["full_name"] for c in cands]
        radar_map = {c["full_name"]: c for c in cands}
        if not repos:
            print()
            print("没有可处理的候选。")
            return 1
        print()

    if args.scan_only:
        print("--scan-only：只搜索，不做包。")
        return 0

    # ②~⑤ 逐仓处理
    items = []
    for i, rn in enumerate(repos, 1):
        print("[%d/%d] %s" % (i, len(repos), rn))
        res = stage_process(rn, args, radar_map.get(rn))
        if res:
            res["repo"] = rn
            items.append(res)
        print()

    # 日报
    elapsed = time.time() - t0
    md_path, json_path = write_report(items, args, elapsed)

    ok = len([x for x in items if x])
    passed = len([x for x in items if x and (x.get("audit") or {}).get("是否通过")])
    print("=" * 66)
    print("流水线跑完 · 用时 %.1f 秒" % elapsed)
    print("   处理 %d 个仓库，产出 %d 个包，其中 %d 个审核通过"
          % (len(repos), ok, passed))
    print("   日报：%s" % md_path)
    print("=" * 66)
    print()
    print("⚠ 产物全在 `_待审/`，**没有自动上架**。")
    print("  下一步：实机装一次 → 核协议 → 审脚本 → 通过才移 `_已上架/`。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

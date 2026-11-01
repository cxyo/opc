"""命令行入口。

设计目标：用户只需要记住 5 个命令。

    python run.py doctor      看系统状态（第一次跑这个）
    python run.py today       跑今天的完整内容链路
    python run.py product     生成商品详情页
    python run.py ask A07 "写一篇..."   单独叫某个员工干活
    python run.py check "文字"  查违规
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from . import config as cfgmod
from . import kb as kbmod
from . import llm
from . import roles as rolesmod
from . import pipeline as piplmod


def _print_header(t):
    print()
    print("=" * 62)
    print("  " + t)
    print("=" * 62)
    print()


def _dump_usage(cfg):
    """把本次会话的消耗明细落盘，供 `run.py usage` 查询与对账。"""
    if not llm.LEDGER.rows:
        return None
    from datetime import datetime as _dt
    d = _dt.now().strftime("%Y%m%d")
    p = os.path.join(cfg["paths"]["logs"], f"usage_{d}.csv")
    llm.LEDGER.dump_csv(p)
    return p


def cmd_cost(args):
    """成本清单：每个岗位的 token 与人民币花费。"""
    _print_header("成本清单")
    cfg = cfgmod.load()
    peak = args.peak

    from . import cost as costmod

    if not peak:
        now = llm.is_peak_time()
        print(f"当前时段：{'高峰' if now else '空闲'}（单价相差一倍）")
        print("下面按【空闲时段】计算 —— 晚上和周末跑最便宜。想看高峰价加 --peak\n")

    rows = costmod.estimate_all_roles(cfg, peak=peak)

    print("【一】21 个岗位 · 单次典型调用费用")
    print(f"{'编号':<6}{'岗位':<14}{'档位':<8}{'输入(命中)':<12}{'输入(未命中)':<14}"
          f"{'输出':<9}{'总token':<10}{'费用(元)':<10}")
    print("-" * 88)
    total_tok = 0
    total_cost = 0.0
    for r in rows:
        tier = r["tier"] + ("+思考" if r["thinking"] else "")
        print(f"{r['code']:<7}{r['name']:<15}{tier:<9}{r['input_hit']:<13}"
              f"{r['input_miss']:<15}{r['output']:<10}{r['total_tokens']:<11}"
              f"{r['cost']:<11.5f}")
        total_tok += r["total_tokens"]
        total_cost += r["cost"]
    print("-" * 88)
    print(f"{'合计':<7}{'21 个岗位各跑一次':<23}{'':<22}{'':<12}{total_tok:<11}{total_cost:<11.5f}")
    print(f"\n平均单次调用：¥{total_cost/len(rows):.5f}")

    print("\n\n【二】整轮链路费用")
    daily = costmod.estimate_daily_chain(cfg, peak=peak)
    print(f"\n日常内容链路（8 步，出一篇完整文章）")
    print(f"{'步骤':<8}{'岗位':<8}{'任务':<16}{'模型':<18}{'token':<10}{'费用(元)':<10}")
    print("-" * 76)
    for s in daily["steps"]:
        print(f"{s['code']:<9}{s['name']:<9}{s['title']:<17}{s['model']:<19}"
              f"{s['total_tokens']:<11}{s['cost']:<11.5f}")
    print("-" * 76)
    print(f"{'合计':<9}{'':<9}{'':<17}{'':<19}{daily['total_tokens']:<11}{daily['total_cost']:<11.5f}")

    prod = costmod.estimate_product_chain(cfg, peak=peak)
    print(f"\n商品链路（2 步，出一份详情页）")
    print(f"{'步骤':<8}{'岗位':<8}{'任务':<16}{'模型':<18}{'token':<10}{'费用(元)':<10}")
    print("-" * 76)
    for s in prod["steps"]:
        print(f"{s['code']:<9}{s['name']:<9}{s['title']:<17}{s['model']:<19}"
              f"{s['total_tokens']:<11}{s['cost']:<11.5f}")
    print("-" * 76)
    print(f"{'合计':<9}{'':<9}{'':<17}{'':<19}{prod['total_tokens']:<11}{prod['total_cost']:<11.5f}")

    print("\n\n【三】质检官档位对比（质检是成本大头，可自己选）")
    modes = costmod.compare_judge_modes(cfg, peak=peak)
    print(f"{'档位':<16}{'说明':<26}{'模型':<20}{'token':<10}{'费用(元)':<11}")
    print("-" * 84)
    for m in modes:
        mark = " ←当前" if m["tier"] == cfg.get("judge_tier", "judge") else ""
        print(f"{m['tier']:<17}{m['label']:<27}{m['model']:<21}"
              f"{m['total_tokens']:<11}{m['cost']:<11.5f}{mark}")
    print("\n切换方式：编辑 config.json 里的 \"judge_tier\"")
    print("  judge      = v4-pro + 思考，质量最高，最贵")
    print("  judge_econ = flash + 思考，均衡（推荐默认）")
    print("  judge_fast = flash 不思考，最便宜，适合先跑通流程")

    print("\n\n【四】一个月要花多少")
    print(f"{'场景':<28}{'每天':<14}{'每月(30天)':<14}{'建议充值':<12}")
    print("-" * 70)
    scenarios = [
        ("只跑文章链路（1 篇/天）", daily["total_cost"]),
        ("文章 + 商品详情页（各1 次/天）", daily["total_cost"] + prod["total_cost"]),
        ("文章 + 商品 + 手动问 5 次员工",
         daily["total_cost"] + prod["total_cost"] + 5 * total_cost / len(rows)),
        ("全 21 岗位各跑一次 + 文章链路",
         total_cost + daily["total_cost"]),
    ]
    for label, per_day in scenarios:
        per_month = per_day * 30
        print(f"{label:<30}¥{per_day:<12.4f}¥{per_month:<12.2f}¥{per_month*1.3:<10.2f}")
    print("\n建议充值 = 月花费 × 1.3（留30% 波动余量），最小充 10 元起")
    print(f"\n导出 CSV：python run.py cost-export")
    print()
    return 0


def cmd_export_cost(args):
    """把成本清单导出成 CSV + Markdown。"""
    _print_header("导出成本清单")
    cfg = cfgmod.load()
    from . import cost as costmod
    import csv as _csv

    outdir = os.path.join(cfgmod.ROOT, "data", "reports")
    os.makedirs(outdir, exist_ok=True)
    peak = args.peak
    slot = "高峰" if peak else "空闲"

    rows = costmod.estimate_all_roles(cfg, peak=peak)
    csv_path = os.path.join(outdir, f"员工费用清单_{slot}时段.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["编号", "岗位", "层级", "自动化等级", "档位", "模型", "思考模式",
                    "输入token(缓存命中)", "输入token(未命中)", "输出token",
                    "总token", "费用(元)", "单价时段"])
        for r in rows:
            w.writerow([r["code"], r["name"], r["layer"], r["automation"], r["tier"],
                        r["model"], "是" if r["thinking"] else "否",
                        r["input_hit"], r["input_miss"], r["output"],
                        r["total_tokens"], f"{r['cost']:.6f}",
                        "高峰" if peak else "空闲"])
    print(f"CSV 已导出：{csv_path}")

    daily = costmod.estimate_daily_chain(cfg, peak=peak)
    prod = costmod.estimate_product_chain(cfg, peak=peak)
    md_path = os.path.join(outdir, f"员工费用清单_{slot}时段.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# AI 员工费用清单\n\n")
        f.write(f"> 生成时间：{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}　"
                f"> 单价时段：{'高峰' if peak else '空闲'}\n\n")
        f.write("## 一、21 个岗位单次典型调用\n\n")
        f.write("| 编号 | 岗位 | 档位 | 模型 | 输入(命中) | 输入(未命中) | 输出 | 总 token | 费用(元) |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            tier = r["tier"] + ("+思考" if r["thinking"] else "")
            f.write(f"| {r['code']} | {r['name']} | {tier} | {r['model']} | {r['input_hit']} | "
                    f"{r['input_miss']} | {r['output']} | {r['total_tokens']} | {r['cost']:.5f} |\n")
        f.write(f"\n**21 岗位各跑一次合计：{sum(r['total_tokens'] for r in rows)} tokens，"
                f"¥{sum(r['cost'] for r in rows):.4f}**\n\n")
        f.write("## 二、日常内容链路（8 步）\n\n")
        f.write("| 步骤 | 岗位 | 任务 | 模型 | token | 费用(元) |\n|---|---|---|---|---|---|\n")
        for s in daily["steps"]:
            f.write(f"| {s['code']} | {s['name']} | {s['title']} | {s['model']} | "
                    f"{s['total_tokens']} | {s['cost']:.5f} |\n")
        f.write(f"\n**合计：{daily['total_tokens']} tokens，¥{daily['total_cost']:.4f}**\n\n")
        f.write("## 三、商品链路（2 步）\n\n")
        f.write("| 步骤 | 岗位 | 任务 | 模型 | token | 费用(元) |\n|---|---|---|---|---|---|\n")
        for s in prod["steps"]:
            f.write(f"| {s['code']} | {s['name']} | {s['title']} | {s['model']} | "
                    f"{s['total_tokens']} | {s['cost']:.5f} |\n")
        f.write(f"\n**合计：{prod['total_tokens']} tokens，¥{prod['total_cost']:.4f}**\n\n")

        modes = costmod.compare_judge_modes(cfg, peak=peak)
        f.write("## 四、质检官档位对比\n\n")
        f.write("| 档位 | 说明 | 模型 | token | 费用(元) |\n|---|---|---|---|---|\n")
        for m in modes:
            f.write(f"| {m['tier']} | {m['label']} | {m['model']} | "
                    f"{m['total_tokens']} | {m['cost']:.5f} |\n")
        f.write(f"\n当前档位：`{cfg.get('judge_tier', 'judge')}`\n\n")

        all_cost = sum(r["cost"] for r in rows)
        f.write("## 五、月度花费与充值建议\n\n")
        f.write("| 场景 | 每天 | 每月(30天) | 建议充值 |\n|---|---|---|---|\n")
        scenarios = [
            ("只跑文章链路（1 篇/天）", daily["total_cost"]),
            ("文章 + 商品详情页（各 1 次/天）", daily["total_cost"] + prod["total_cost"]),
            ("文章 + 商品 + 手动问 5 次员工",
             daily["total_cost"] + prod["total_cost"] + 5 * all_cost / len(rows)),
            ("全 21 岗位各跑一次 + 文章链路", all_cost + daily["total_cost"]),
        ]
        for label, per_day in scenarios:
            per_month = per_day * 30
            f.write(f"| {label} | ¥{per_day:.4f} | ¥{per_month:.2f} | ¥{per_month*1.3:.2f} |\n")
        f.write("\n建议充值 = 月花费 × 1.3（留 30% 波动余量）\n")
    print(f"Markdown 已导出：{md_path}")
    print()
    return 0


def cmd_usage(args):
    """查看真实消耗台账。"""
    _print_header("消耗台账")
    cfg = cfgmod.load()
    logdir = cfg["paths"]["logs"]
    files = sorted([f for f in os.listdir(logdir) if f.startswith("usage_")]) if os.path.isdir(logdir) else []
    if not files:
        print("还没有真实消耗记录。充值后跑一次 `python run.py today` 就会生成。")
        print()
        return 0
    import csv as _csv
    total_cost = 0.0
    total_tok = 0
    by_role = {}
    for fn in files:
        p = os.path.join(logdir, fn)
        with open(p, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                total_cost += float(row.get("cost_yuan") or 0)
                total_tok += int(row.get("total") or 0)
                k = row.get("role") or "-"
                a = by_role.setdefault(k, {"calls": 0, "tok": 0, "cost": 0.0})
                a["calls"] += 1
                a["tok"] += int(row.get("total") or 0)
                a["cost"] += float(row.get("cost_yuan") or 0)
    print(f"台账文件：{len(files)} 个（{files[0]} ~ {files[-1]}）\n")
    print(f"{'岗位':<10}{'调用次数':<12}{'token':<12}{'费用(元)':<12}")
    print("-" * 48)
    for k in sorted(by_role):
        a = by_role[k]
        print(f"{k:<11}{a['calls']:<13}{a['tok']:<13}{a['cost']:<13.5f}")
    print("-" * 48)
    print(f"{'合计':<11}{'':<12}{total_tok:<12}{total_cost:<12.5f}")
    print()
    return 0


def cmd_daily_sync(args):
    """每日自动同步任务（当前只有 A26 微信情报官）。

    和 `today` 内容链路的区别：
      · `today` 产文章，要调模型、要花钱
      · `daily-sync` 只读本机数据刷新看板，**零 token 零费用**

    所以可以挂 Windows 计划任务每天跑，不心疼。
    """
    from . import wechat_intel as wi

    _print_header("每日自动同步")
    reg = rolesmod.RoleRegistry(
        os.path.join(cfgmod.load()["paths"]["roles"]))
    tasks = reg.sync_descriptions()
    if not tasks:
        print("没有登记每日同步任务。")
        print()
        return 0

    force = bool(getattr(args, "force", False))
    rc = 0
    for code, desc in tasks:
        print("▶ %s  %s" % (code, desc))
        if code == "A26":
            try:
                if force:
                    print("   强制重算中…")
                    data = wi.build(verbose=True)
                    if data.get("ok"):
                        wi.save_cache(data)
                        data["本次变化"] = ["强制重算"]
                        data["缓存命中"] = False
                else:
                    data = wi.sync(verbose=True)
                if not data.get("ok"):
                    print("   ❌ 同步失败：%s" % (data.get("错误") or "未知"))
                    rc = 1
                    continue
                if data.get("缓存命中"):
                    print("   ✅ 数据无变化，沿用缓存")
                else:
                    print("   ✅ 已更新：%s" %
                          ("、".join(data.get("本次变化") or []) or "全量"))
                print("   客户 %d · 供应商 %d · 内部 %d · 有卡点客户 %d" % (
                    data.get("客户数", 0), data.get("供应商数", 0),
                    data.get("内部人数", 0), data.get("有卡点的客户数", 0)))
                # 顺手导一份老板能直接看的简报
                out = os.path.join(cfgmod.load()["paths"]["outputs"],
                                   "微信情报简报.md")
                os.makedirs(os.path.dirname(out), exist_ok=True)
                _write_wechat_brief(data, out)
                print("   📄 简报：%s" % out)
            except Exception as e:
                print("   ❌ 异常：%s" % e)
                rc = 1
        else:
            print("   ⏭  还没有对应的执行器，跳过")
        print()

    _print_header("同步完成")
    if rc == 0:
        print("全部完成 ✅")
    else:
        print("有任务失败 ❌")
    print()
    return rc


def cmd_credit(args):
    """A27 积分管家 —— 每日签到、积分余额、缓存命中率。

    对应视频《把 WorkBuddy 100 积分用出 5000 积分的效果》那套机制：
      · 自动签到：直接调官方客户端同一个接口领每日积分（幂等）
      · 缓存命中率：读上游 usage 帧的 prompt_cache_hit_tokens 字段

    整个流程零 token 零费用，可以挂计划任务每天跑。
    """
    from . import workbuddy_credit as wbc

    action = getattr(args, "action", "status")
    _print_header("积分管家")

    if action == "checkin":
        res = wbc.checkin(force=bool(getattr(args, "force", False)),
                          dry_run=bool(getattr(args, "dry_run", False)))
        if not res.get("签到"):
            print("❌ %s" % (res.get("msg") or "没有可用账号"))
            print()
            return 1
        for row in res["签到"]:
            icon = {"成功": "✅", "已签到": "☑️", "失败": "❌",
                    "试运行": "🔍"}.get(row.get("结果"), "·")
            print("%s %s —— %s" % (icon, row.get("昵称"), row.get("说明")))
            if row.get("连续天数"):
                print("   连续 %s 天 · 累计 %s 积分" %
                      (row.get("连续天数"), row.get("累计积分")))
        if res.get("总积分"):
            print()
            print("🎁 本次共获得 %s 积分" % res["总积分"])
        print()
        return 0 if res.get("ok") else 1

    if action == "cache":
        mode = getattr(args, "probe", False)
        if mode:
            res = wbc.probe_cache_hit(
                rounds=int(getattr(args, "rounds", 3) or 3),
                model=getattr(args, "model", "") or "glm-5.3-flash")
            print("模型：%s（前缀约 %s token）" % (
                res.get("模型"), res.get("前缀token估算")))
            print()
            for row in res.get("轮次", []):
                if row.get("错误"):
                    print("第%s轮 ❌ %s" % (row["轮"], row["错误"]))
                    continue
                print("第%s轮  命中 %s / 未命中 %s  命中率 %s  积分 %s  每积分token %s" % (
                    row["轮"], row["命中"], row["未命中"], row["命中率"],
                    row["积分"], row["每积分token"]))
            print()
            print("💡 %s" % res.get("结论", ""))
            print()
            return 0 if res.get("ok") else 1

        state = wbc.read_cache_state()
        if not state:
            print("还没有缓存统计数据。")
            print("跑 `python run.py credit cache --probe` 可以实测一次命中率。")
            print()
            return 0
        print(json.dumps(state, ensure_ascii=False, indent=2))
        print()
        return 0

    # status / report
    data = wbc.report() if action == "report" else wbc.status()
    if not data.get("ok"):
        print("❌ 读不到账号：%s" % (data.get("msg") or "客户端可能没登录"))
        print("   客户端：%s" % data.get("客户端"))
        print("   凭据目录：%s" % data.get("凭据目录"))
        print()
        return 1

    print("客户端：%s" % data.get("客户端"))
    print("账号数：%d" % data.get("账号数", 0))
    print()
    for acc in data.get("账号", []):
        print("▶ %s" % acc.get("昵称"))
        print("   状态：%s" % ("已签到" if acc.get("已签到") else "未签到"))
        print("   连续 %s 天 · 今日 %s 积分 · 累计 %s 积分" % (
            acc.get("连续天数"), acc.get("今日积分"), acc.get("累计积分")))
        week = acc.get("本周进度") or []
        if week:
            marks = " ".join("✅" if d else "⬜" for d in week)
            print("   本周：%s" % marks)
        if acc.get("活动时间"):
            print("   活动截止：%s" % acc["活动时间"])
        if acc.get("错误"):
            print("   ⚠️ %s" % acc["错误"])
        print()

    if action == "report":
        stats = data.get("历史统计") or {}
        if stats.get("天数"):
            print("近 30 天：签到 %s 天 · 累计获得 %s 积分 · 平均每天 %s" % (
                stats.get("天数"), stats.get("累计获得"), stats.get("平均每天")))
            print()
        cache = data.get("缓存") or {}
        if cache:
            print("缓存命中率：%s" % (
                f"{cache.get('cache_hit_ratio', 0) * 100:.1f}%"
                if cache.get("cache_hit_ratio") else "未记录"))
            print()

    _print_header("完成")
    print("签到执行：python run.py credit checkin")
    print()
    return 0


def cmd_shots(args):
    """A28 商品视觉官 —— 淘宝/咸鱼主图、详情页与上架文案。

    定价 19800 元一套，卖的是交付包 + 远程安装指导。
    所有数字都取自交付包实测，不夸大。
    """
    from . import product_shots as ps

    action = getattr(args, "action", "build")
    plat_opt = getattr(args, "platform", "淘宝")
    plats = ["淘宝", "咸鱼"] if plat_opt == "全部" else [plat_opt]

    _print_header("商品视觉官")
    rc = 0
    for plat in plats:
        print("▶ %s" % plat)
        if action == "check":
            probs = ps.compliance_check(plat)
            if probs:
                for p in probs:
                    print("   ⚠️ %s | %s" % (p["位置"], p["问题"]))
                rc = 1
            else:
                print("   ✅ 合规体检通过（极限词 / 促销标 / 站外导流 / 标题长度）")
            print()
            continue

        if action in ("build", "copy"):
            if action == "build":
                r = ps.build_all(plat)
                for path in r["主图"]:
                    print("   🖼 %s" % os.path.basename(path))
                print("   📄 %s" % os.path.basename(r["详情页"]))
                if not r["合规通过"]:
                    rc = 1
                    for p in r["合规问题"]:
                        print("   ⚠️ %s | %s" % (p["位置"], p["问题"]))
                else:
                    print("   ✅ 合规体检通过")
            for path in ps.export_copy_docs():
                if plat in path:
                    print("   📝 %s" % os.path.basename(path))
            print()
    _print_header("完成")
    print("输出目录：%s" % ps.shots_dir("淘宝"))
    print()
    return rc


def cmd_vclone(args):
    """A29 视频工作流克隆器 —— 命令行入口。

    来源：素材库 v_AAzghBqVe0（hypit 的「按词锚定 + 一套工作流出 N 个变体」）。
    本地落地版：不调任何付费模型，纯本地排版，离线可跑。

    用法：
        python run.py vclone demo
        python run.py vclone build --src 稿子.md --slots 钩子,痛点,解法
        python run.py vclone variants --workflow 配方.json --variants 变体.json
        python run.py vclone list --out outputs/视频
    """
    from . import video_clone as vc
    from . import config as cfgmod

    action = getattr(args, "action", "demo") or "demo"
    out_dir = getattr(args, "out", "") or os.path.join(
        cfgmod.ROOT, "data", "outputs", "视频")

    _print_header("视频工作流克隆器")

    # ---------------- demo ----------------
    if action == "demo":
        md = ("## 钩子\n你的内容是不是也在硬凑？\n\n"
              "## 痛点\n每天写稿两小时，出来的还是那套话。\n\n"
              "## 解法\n把流程固化下来，一次设置长期用。\n\n"
              "## 证据\n实测 24 个岗位跑完一篇文章只要 0.21 元。\n\n"
              "## 行动\n现在就试一次。")
        slots = ["钩子", "痛点", "解法", "证据", "行动"]
        wf = vc.build_workflow(md, slots=slots, brand="AI员工系统")
        print("配方：%s（%d 段，总时长 %.2f 秒）"
              % (wf["标题"], wf["段数"], wf["总时长"]))
        for s in wf["段"]:
            print("   %d. %-4s %5.2fs  %s"
                  % (s["序号"], s["槽位"], s["时长"],
                     s["默认内容"][:26].replace("\n", " ")))
        print()
        outs = vc.render_variants(wf, out_dir=out_dir, prefix="演示", variants=[
            {"钩子": "你的内容是不是也在硬凑？",
             "痛点": "每天写稿两小时，出来的还是那套话。"},
            {"钩子": "别再从零写脚本了，这套配方能直接套。",
             "痛点": "每天写稿两小时，出来的还是那套话。"},
        ])
        print("   出了 %d 个变体：" % len(outs))
        for d in outs:
            print("      %s" % os.path.basename(d))
        print()
        print("   变体之间结构完全一致，只有槽位内容不同；")
        print("   每段时长按新字数自动重算（改一个字，时长就跟着变）。")
        print()
        print("输出目录：%s" % out_dir)
        return 0

    # ---------------- build ----------------
    if action == "build":
        src = getattr(args, "src", "")
        if not src or not os.path.isfile(src):
            print("❌ 需要 --src 指定稿子文件")
            return 1
        with open(src, "r", encoding="utf-8", errors="replace") as f:
            md = f.read()
        slots = [s.strip() for s in
                 (getattr(args, "slots", "") or "").split(",") if s.strip()]
        wf = vc.build_workflow(md, slots=slots or None,
                               title=getattr(args, "title", ""))
        if not wf:
            print("❌ 稿子解析不出内容")
            return 1
        os.makedirs(out_dir, exist_ok=True)
        p = vc.save_workflow(wf, os.path.join(out_dir, "配方.json"))
        print("   段数：%d　总时长：%.2f 秒" % (wf["段数"], wf["总时长"]))
        for s in wf["段"]:
            print("   %d. %-4s %5.2fs" % (s["序号"], s["槽位"], s["时长"]))
        print()
        print("配方已存：%s" % p)
        return 0

    # ---------------- variants ----------------
    if action == "variants":
        wfp = getattr(args, "workflow", "")
        varp = getattr(args, "variants", "")
        if not wfp or not os.path.isfile(wfp):
            print("❌ 需要 --workflow 指定配方 JSON")
            return 1
        wf = vc.load_workflow(wfp)
        if not wf:
            print("❌ 配方读不出来：%s" % wfp)
            return 1
        variants = []
        if varp and os.path.isfile(varp):
            with open(varp, "r", encoding="utf-8") as f:
                variants = json.load(f)
        if not variants:
            print("❌ 需要 --variants 指定变体 JSON（数组）")
            return 1
        outs = vc.render_variants(wf, variants, out_dir=out_dir,
                                  make_video=getattr(args, "video", False))
        print("   出了 %d 个变体：" % len(outs))
        for d in outs:
            print("      %s" % d)
        print()
        print("输出目录：%s" % out_dir)
        return 0

    # ---------------- list ----------------
    items = vc.list_workflows(out_dir)
    if not items:
        print("   还没有配方。")
        print("   先跑：python run.py vclone build --src 你的稿子.md")
        return 0
    for it in items:
        print("   %-28s %d 段  %6.2f 秒  %s"
              % (it["文件"], it["段数"], it["总时长"], it["标题"]))
    return 0


def cmd_wechat(args):
    """A26 微信情报官 —— 命令行入口。

    用途：
        python run.py wechat status        看数据是否就绪、覆盖多少
        python run.py wechat sync          增量同步（有变化才重算）
        python run.py wechat detail --session <username>
        python run.py wechat export        导出老板能直接看的 Markdown 简报
    """
    from . import wechat_intel as wi

    action = getattr(args, "action", "status") or "status"

    # ---------------- status ----------------
    if action == "status":
        _print_header("微信情报 · 状态")
        st = wi.status()
        if not st.get("就绪"):
            print("❌ 微信数据还没解密。")
            print("   先跑解密流程：python tools/wechat_decrypt.py")
            print("   数据目录：%s" % st.get("数据目录"))
            print()
            return 1
        print(f"数据目录：{st['数据目录']}")
        print(f"缓存新鲜：{'是' if st['缓存新鲜'] else '否（微信有新消息，可跑 sync）'}")
        print()
        print(f"{'会话数':<12}{st['会话数']}")
        print(f"{'消息表数':<12}{st['消息表数']}")
        print(f"{'消息总数':<12}{st['消息总数']}")
        print()
        if st.get("按库"):
            print("按库分布：")
            for fn, n in sorted(st["按库"].items(), key=lambda x: -x[1]):
                print(f"  {fn:<20}{n}")
        print()
        return 0

    # ---------------- sync ----------------
    if action == "sync":
        _print_header("微信情报 · 增量同步")
        data = wi.sync(verbose=True)
        if not data.get("ok"):
            print("❌ 同步失败：%s" % (data.get("错误") or "未知原因"))
            print()
            return 1
        if data.get("缓存命中"):
            print("✅ 数据无变化，用的是缓存（省了 2 分钟）")
        else:
            print("✅ 已重算：%s" % ", ".join(data.get("本次变化") or []) or "全量")
        print()
        print("客户 %d · 供应商 %d · 内部 %d · 有卡点客户 %d" % (
            data.get("客户数", 0), data.get("供应商数", 0),
            data.get("内部人数", 0), data.get("有卡点的客户数", 0)))
        print()
        return 0

    # ---------------- detail ----------------
    if action == "detail":
        _print_header("微信情报 · 会话明细")
        u = (getattr(args, "session", "") or "").strip()
        if not u:
            print("需要 --session <username>")
            print("先跑 `python run.py wechat status`，"
                  "再用 `python run.py wechat export` 看会话清单。")
            print()
            return 1
        d = wi.session_detail(u, int(getattr(args, "limit", 60) or 60))
        if d.get("ok") is False:
            print("❌ %s" % (d.get("错误") or "读取失败"))
            return 1
        print(f"{d.get('名称')}  [{d.get('关系')}]  置信度 {d.get('置信度')}")
        print(f"会话 {u} · 消息 {len(d.get('消息') or [])} 条")
        print()
        if d.get("证据"):
            print("判定依据：")
            for e in d["证据"]:
                print("  · %s" % e)
            print()
        a = d.get("分析") or {}
        if a:
            print("事实抽取：")
            for k in ("进展阶段", "货物阶段", "款项阶段"):
                if a.get(k):
                    print("  %s：%s" % (k, a[k]))
            if a.get("涉及金额"):
                print("  涉及金额：%s" %
                      "、".join(x["原文"] for x in a["涉及金额"]))
            if a.get("涉及数量"):
                print("  涉及数量：%s" %
                      "、".join(x["原文"] for x in a["涉及数量"]))
            if a.get("卡点"):
                print("  卡点：%s" %
                      "、".join("%s（%s）" % (x["原因"], "、".join(x.get("命中词", [])))
                              for x in a["卡点"]))
            print()
        print("最近消息：")
        for m in (d.get("消息") or [])[-12:]:
            ts = time.strftime("%Y-%m-%d %H:%M",
                               time.localtime(m.get("ts") or 0))
            print("  [%s] %s %s" % (ts, m.get("type_name", ""),
                                    (m.get("body") or "")[:60]))
        print()
        return 0

    # ---------------- export ----------------
    if action == "export":
        _print_header("微信情报 · 导出简报")
        data = wi.get_intel()
        intel = data.get("intel", data)
        if not intel.get("ok"):
            print("❌ %s" % (intel.get("错误") or "还没有情报数据"))
            return 1
        out = getattr(args, "out", "") or os.path.join(
            cfgmod.load()["paths"]["outputs"], "微信情报简报.md")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        _write_wechat_brief(intel, out)
        print("✅ 已导出：%s" % out)
        print()
        return 0

    return 0


def _write_wechat_brief(intel, path):
    """把情报写成老板能直接看的 Markdown 简报。"""
    L = []
    A = L.append
    A("# 微信情报简报")
    A("")
    A("> 生成时间：%s　|　耗时 %s 秒" %
      (intel.get("生成时间", "?"), intel.get("耗时秒", "?")))
    A("")
    cov = intel.get("覆盖", {})
    A("覆盖 %s 个会话 / %s 条消息（有消息 %s 个）　|　"
      "联系人 %s　|　群 %s" %
      (cov.get("会话数", 0), cov.get("消息条数", 0),
       cov.get("有消息会话", 0), cov.get("联系人", 0), cov.get("群", 0)))
    A("")
    dist = intel.get("分类分布", {})
    if dist:
        A("**分类分布**：" + "　".join("%s %d" % (k, v)
                                      for k, v in sorted(dist.items(),
                                                         key=lambda x: -x[1])))
        A("")
    A("---")
    A("")

    # 客户线
    A("## 一、客户对接情况")
    A("")
    cust = intel.get("客户分析", [])
    if not cust:
        A("（未识别出客户会话）")
    else:
        A("| 客户 | 进展阶段 | 置信度 | 消息数 | 卡点 | 最后活跃 |")
        A("|---|---|---|---|---|---|")
        for c in cust:
            blk = "、".join(b.get("原因", "") for b in (c.get("卡点") or []))
            A("| %s | %s | %s | %s | %s | %s |" % (
                c.get("名称", ""), c.get("进展阶段", ""),
                c.get("置信度", ""), c.get("消息数", ""),
                blk or "-", c.get("最后活跃", "") or "-"))
    A("")

    # 供应商线
    A("## 二、供应商货与款")
    A("")
    sup = intel.get("供应商分析", [])
    if not sup:
        A("（未识别出供应商会话）")
    else:
        A("| 供应商 | 货物阶段 | 款项阶段 | 置信度 | 消息数 | 最后活跃 |")
        A("|---|---|---|---|---|---|")
        for s in sup:
            A("| %s | %s | %s | %s | %s | %s |" % (
                s.get("名称", ""), s.get("货物阶段", ""),
                s.get("款项阶段", ""), s.get("置信度", ""),
                s.get("消息数", ""), s.get("最后活跃", "") or "-"))
    A("")

    # 内部线
    A("## 三、内部员工与业务动作")
    A("")
    emp = intel.get("员工分析", [])
    if not emp:
        A("（未识别出内部同事会话）")
    else:
        A("| 员工 | 对接客户数 | 拜访次数 | 消息总量 | 动作分布 |")
        A("|---|---|---|---|---|")
        for e in emp:
            acts = "、".join("%s×%s" % (k, v)
                             for k, v in (e.get("动作分布") or {}).items())
            A("| %s | %s | %s | %s | %s |" % (
                e.get("姓名", ""), e.get("对接客户数", 0),
                e.get("拜访次数", 0), e.get("消息总量", 0), acts or "-"))
    A("")

    # 卡点
    A("## 四、需要老板介入的卡点")
    A("")
    blocks = intel.get("卡点汇总", [])
    if not blocks:
        A("（聊天记录里没有识别到明确卡点）")
    else:
        for name, n in blocks:
            A("- **%s** —— %d 处" % (name, n))
    A("")
    for c in cust:
        for b in (c.get("卡点") or []):
            ev = ""
            for e in (c.get("溯源") or []):
                if e.get("原因") == b.get("原因"):
                    ev = e.get("片段", "")
                    break
            A("- %s（%s）：%s%s" % (
                c.get("名称", ""), b.get("原因", ""),
                "、".join(b.get("命中词", [])),
                ("　←　「%s」" % ev[:60]) if ev else ""))
    A("")

    # 久未联系
    stale = intel.get("久未联系客户", [])
    if stale:
        A("## 五、久未联系的客户")
        A("")
        for s in stale:
            A("- %s（%s）　最后消息：%s" %
              (s.get("名称", ""), s.get("阶段", ""), s.get("最后消息", "")))
        A("")

    A("---")
    A("")
    A("> 数据来源：本机微信聊天记录（只读，不上传）。"
      "私人会话自动排除，不进业务视图。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def cmd_doctor(args):
    """系统体检。"""
    _print_header("系统体检")
    cfg = cfgmod.load()

    print(f"配置文件：{cfgmod.CONFIG_PATH}")
    print(f"  存在：{'是' if os.path.exists(cfgmod.CONFIG_PATH) else '否（用默认值）'}")
    print(f"密钥：{cfgmod.mask_key(cfg.get('api_key'))}")
    print(f"模型：{cfg['model']} / 判定模型 {cfg['model_tier']['judge']}")
    print(f"模拟模式：{'是' if cfg.get('simulate') else '否'}")

    print("\n--- API 连通性 ---")
    ok, msg = llm.health_check(cfg)
    print(f"  {'✅' if ok else '❌'} {msg}")

    print("\n--- 岗位加载 ---")
    reg = rolesmod.RoleRegistry(cfg["paths"]["roles"])
    roles = reg.all()
    print(f"  加载 {len(roles)} 个岗位")
    if roles:
        print(f"  首个：{roles[0].code} {roles[0].name}（人设 {len(roles[0].persona)} 字）")
        bad = [r.code for r in roles if len(r.persona) < 300]
        if bad:
            print(f"  ⚠️ 人设过短：{', '.join(bad)}")
        else:
            print("  ✅ 全部人设解析正常")

    print("\n--- 禁区索引 ---")
    fi = kbmod.ForbiddenIndex.from_dir(cfg["paths"]["forbidden"])
    st = fi.stats()
    print(f"  共 {st['total']} 条红线")
    if st["by_platform"]:
        for k, v in st["by_platform"].items():
            print(f"    {k}: {v} 条")
    if st["by_level"]:
        print(f"  分级：{st['by_level']}")
    if st["total"] < 10:
        print("  ⚠️ 禁区条目偏少，检查路径或文件是否完整")

    print("\n--- 知识库 ---")
    kb = kbmod.KB()
    n1 = kb.add_dir(cfg["paths"]["seeds"], source="种子")
    n2 = kb.add_dir(cfg["paths"]["kb_wiki"], source="wiki")
    print(f"  种子 {n1} 篇 / wiki {n2} 篇 / 共 {len(kb.docs)} 篇")

    print("\n--- 种子文件完整性 ---")
    for name in ["账号定位卡", "风格指纹卡", "选题池", "质检标准库", "平台规则库"]:
        p = os.path.join(cfg["paths"]["seeds"], name + ".md")
        if not os.path.exists(p):
            print(f"  ❌ {name}.md 缺失")
            continue
        t = open(p, "r", encoding="utf-8").read()
        pending = t.count("【待填") + t.count("【待补")
        if pending:
            print(f"  ⚠️ {name}.md 还有 {pending} 处待填")
        else:
            print(f"  ✅ {name}.md")

    print("\n--- 输出目录 ---")
    for k in ["outputs", "logs", "state"]:
        p = cfg["paths"][k]
        cnt = len(os.listdir(p)) if os.path.isdir(p) else 0
        print(f"  {k}: {p}（{cnt} 个文件）")

    print()
    if not ok:
        print("⚠️  API 不可用。可先用 STAFF_SIMULATE=1 或在 config 里开 simulate 跑通流程。")
    else:
        print("✅  系统就绪。下一个命令：python run.py today")
    print()
    return 0 if ok else 1


def cmd_today(args):
    _print_header("今日内容链路（8 步）")
    cfg = cfgmod.load()
    p = piplmod.Pipeline(cfg)
    topic = args.topic
    if not topic:
        pool = p._read_topic_pool()
        if pool:
            print("提示：用 --topic 指定选题，或用 --auto 自动取第一条")
    res = p.daily(topic=topic)
    _print_header("完成")
    print(f"选题：{res['topic']}")
    print(f"输出目录：{res['outdir']}")
    print("\n文件：")
    for f in res["files"]:
        print(f"  {os.path.basename(f)}")
    print(f"  {os.path.basename(res['summary'])}")
    print(f"\ntoken：{llm.LEDGER.summary()}")
    _dump_usage(cfg)
    print("\n下一步：读定稿 → 填【待补】→ 勾 AI 声明 → 手动发布")
    print()
    return 0


def cmd_product(args):
    _print_header("商品详情页生成")
    cfg = cfgmod.load()
    p = piplmod.Pipeline(cfg)
    res = p.product(product_name=args.name, price=args.price)
    print(f"详情页：{res['detail']}")
    print(f"合规复核：{res['check']}")
    print(f"\ntoken：{llm.LEDGER.summary()}")
    _dump_usage(cfg)
    print()
    return 0


def cmd_ask(args):
    _print_header(f"{args.code} 干活")
    cfg = cfgmod.load()
    p = piplmod.Pipeline(cfg)
    kb_ctx = ""
    if args.use_kb:
        kb_ctx = p.kb.context(args.task, top_k=5)
        if kb_ctx:
            print(f"（已注入 {len(kb_ctx)} 字知识库上下文）")
    content, path = p.run_role(
        args.code, args.task, extra_kb=kb_ctx,
        tier=args.tier, temperature=args.temp, save_as=args.save)
    print()
    print(content)
    if path:
        print(f"\n--- 已保存：{path} ---")
    print(f"token：{llm.LEDGER.summary()}")
    _dump_usage(cfg)
    print()
    return 0


def cmd_check(args):
    _print_header("合规预检")
    cfg = cfgmod.load()
    fi = kbmod.ForbiddenIndex.from_dir(cfg["paths"]["forbidden"])
    text = args.text
    if args.file:
        text = open(args.file, "r", encoding="utf-8").read()
    hits = fi.match(text, platform=args.platform)
    st = fi.stats()
    print(f"禁区库共 {st['total']} 条，命中 {len(hits)} 条")
    if not hits:
        print("✅ 未命中已知红线（不等于绝对合规，AI 味与错误仍需质检官）")
        print()
        return 0
    for h in hits:
        print(f"\n【{h['level']}】{h['platform']}")
        print(f"  禁止：{h['forbid']}")
        print(f"  处罚：{h['penalty']}")
        print(f"  命中词：{h['hit_keyword']}")
        print(f"  来源：{h['source_file']}")
    print()
    return 1


def cmd_roles(args):
    _print_header("岗位清单")
    cfg = cfgmod.load()
    reg = rolesmod.RoleRegistry(cfg["paths"]["roles"])
    if args.code:
        r = reg.get(args.code)
        print(f"{r.code} {r.name}（{r.layer} / 自动化 {r.automation}）")
        print("\n--- 建议问题 ---")
        for q in r.quick_questions:
            print(f"  · {q}")
        print("\n--- 输入变量 ---")
        for v in r.input_vars:
            print(f"  · {v}")
        print(f"\n人设长度：{len(r.persona)} 字")
    else:
        print(reg.summary_table())
        print(f"\n共 {len(reg.all())} 个。查看详情：python run.py roles A10")
    print()
    return 0


def cmd_search(args):
    _print_header(f"检索：{args.query}")
    cfg = cfgmod.load()
    p = piplmod.Pipeline(cfg)
    hits = p.search(args.query, top_k=args.top)
    if not hits:
        print("没有命中")
    for i, (d, s) in enumerate(hits, 1):
        print(f"\n[{i}] {d.title}  相关度 {s:.3f}")
        print(f"    来源：{d.source}")
        print("    " + d.text[:300].replace("\n", "\n    ") + "...")
    print()
    return 0


def cmd_forbidden(args):
    _print_header("禁区清单")
    cfg = cfgmod.load()
    fi = kbmod.ForbiddenIndex.from_dir(cfg["paths"]["forbidden"])
    st = fi.stats()
    print(f"共 {st['total']} 条")
    print(f"按平台：{st['by_platform']}")
    print(f"按分级：{st['by_level']}")
    if args.platform:
        print(f"\n--- {args.platform} ---")
        for e in fi.by_platform(args.platform):
            print(f"[{e['level']}] {e['forbid']}")
            print(f"         判定词：{', '.join(e['keywords'])}")
    print()
    return 0


def cmd_shop(args):
    """智能体商品化：列出可售 SKU / 生成商品卡 / 导出货品清单。"""
    from . import shop as shopmod

    cfg = cfgmod.load()

    if args.export:
        outdir = os.path.join(cfg["paths"]["outputs"], "文章_商品清单")
        paths = shopmod.export_all(outdir)
        _print_header("已导出")
        for p in paths:
            print(f"  {os.path.basename(p)}")
        print(f"\n目录：{outdir}")
        print()
        return 0

    if args.code:
        sku = shopmod.get_sku(args.code)
        if not sku:
            print(f"没有这个 SKU：{args.code}")
            print(f"可用编号：{', '.join(s['code'] for s in shopmod.list_skus())}")
            print()
            return 1
        print(shopmod.sku_detail_md(sku))
        return 0

    _print_header("智能体货品清单")
    print("货架上摆的不是文章，是能替客户干活的智能体。")
    print("对外名称一律用业务语言——客户买的不是 AI，是「有人替我干完这件事」。\n")
    print(shopmod.sku_table())

    print(f"\n\n共 {len(shopmod.list_skus())} 款可售服务")
    print("\n【引流品】（不收费，作用是让客户进得来）")
    for m in shopmod.LEAD_MAGNETS:
        print(f"  · {m['name']} —— {m['why']}")

    print("\n【明确不做】")
    for n in shopmod.NOT_DOING:
        print(f"  ❌ {n['item']} —— {n['why']}")

    print("\n查看单个商品卡：python run.py shop A07")
    print("导出完整货品清单：python run.py shop --export")
    print()
    return 0


def cmd_skill(args):
    """技能打包：把 21 份岗位说明书变成可上架的 Skill 包。"""
    _print_header("技能打包")
    print("把岗位说明书（SKILL.md 原料）转成能上架、能自动发货的技能包。")
    print("每个包 = SKILL.md + clawhub.json + README.md + 安装说明.md + zip\n")

    tool = os.path.join(cfgmod.PROJECT, "07_AI员工", "tools", "skill_pack.py")
    if not os.path.exists(tool):
        print(f"找不到打包工具：{tool}")
        return 1

    import subprocess
    argv = []
    if args.only:
        argv += [args.only]
    if args.list:
        argv += ["--list"]

    py = sys.executable or "python"
    try:
        r = subprocess.run([py, tool] + argv, cwd=os.path.dirname(tool))
        return r.returncode
    except Exception as e:
        print(f"打包失败：{e}")
        return 1


def cmd_matrix(args):
    """矩阵账号体检：5 个平台的冲突、违规与整改清单。"""
    from . import matrix_audit as ma

    _print_header("矩阵账号体检")
    rows = ma.audit_accounts()
    print("淘宝 / 闲鱼 / 小红书 / 抖音 / 公众号 —— 逐条比对平台规则。\n")

    print(f"{'平台':<8}{'🔴红线':>8}{'🟡灰区':>8}{'风险分':>10}")
    print("-" * 38)
    for r in rows:
        print(f"{r['platform']:<8}{r['red']:>8}{r['yellow']:>8}"
              f"{r['score']:>8}/100")
    print()

    tm = ma.audit_trademark()
    print(f"【商标】{tm['risk']} 「{tm['word']}」已查到 {len(tm['findings'])} 条注册记录")
    for f in tm["findings"]:
        print(f"  · {f['class']} —— {f['holder']}（{f['status']}）")
    print()
    print("  最优解：注册自己的商标（35+9+41 类，约 ¥900-1200，6-9 个月）")
    print("  备选名：" + " / ".join(c["name"] for c in tm["name_candidates"]))
    print()

    todo = ma.todo_list()
    p0 = [t for t in todo if t["pri"] == "P0"]
    print(f"【整改】共 {len(todo)} 项，P0 {len(p0)} 项：")
    for t in p0:
        print(f"  □ {t['item']}　`{t['cost']}`")
    print()

    if args.export:
        cfg = cfgmod.load()
        outdir = os.path.join(cfg["paths"]["outputs"], "数据_矩阵合规")
        files = ma.export_all(outdir)
        print("已导出：")
        for p in files:
            print(f"  {os.path.basename(p)}")
        print(f"\n目录：{outdir}")
        print()
    else:
        print("导出完整报告：python run.py matrix --export")
        print()
    return 0


def cmd_taobao(args):
    """淘宝视觉官：主图方案 + 详情页脚本 + 标题 + 合规体检。"""
    from . import taobao as tb

    if args.check:
        _print_header("淘宝文案合规体检")
        hits, ok = tb.check_text(args.check)
        print(f"检查文案：{args.check[:60]}")
        print()
        if not hits:
            print("✅ 未命中已知红线")
            print("   注意：AI 标识、虚拟商品声明需人工确认")
            print()
            return 0
        for h in hits:
            rep = f" → 建议改为「{h['replace']}」" if h["replace"] else ""
            print(f"[{h['level']}] {h['cat']}：{h['word']}{rep}")
        print()
        print("---")
        print("人工确认项：")
        print("  □ AI 生成示意图是否已加「AI 辅助生成」角标")
        print("  □ 详情页是否已写「虚拟商品一经售出不退不换」")
        print("  □ 是否声明「非官方产品，与品牌厂商无隶属关系」")
        print("  □ 全程是否无微信/二维码/手机号")
        print()
        return 1

    _print_header("淘宝视觉官")
    print("主图 5 张分工 + 详情页 7 屏脚本 + 标题候选 + 合规体检。")
    print()

    m = tb.build_main_images(args.line, args.product)
    d = tb.build_detail(args.line)
    t = tb.build_title(args.line, args.product)

    print(f"产品线：{m['line']}　产品：{m['product'] or '（未填）'}")
    print()
    print("【主图 5 张分工】")
    for im in m["images"]:
        print(f"  第 {im['no']} 张 · {im['role']} —— {im['job']}")
    print()
    print(f"  第 1 张尺寸：{tb.SPEC['main_1x1']['size']}（最低 {tb.SPEC['main_1x1']['min']}）")
    print(f"  详情页宽度：{tb.SPEC['detail_width']}px　首屏 {tb.SPEC['detail_first_screen']}")
    print(f"  视频主图：{tb.SPEC['video_main']['duration']}，首帧与第 1 张一致")
    print()

    print("【详情页 7 屏】")
    for s in d["screens"]:
        print(f"  第 {s['no']} 屏 · {s['name']}")
    print()

    print("【标题候选】")
    for c in t["candidates"]:
        mark = "✅" if c["ok"] else "⚠️"
        print(f"  {mark} {c['title']}（{c['len']} 字）")
        for i in c["issues"]:
            print(f"      {i}")
    print()
    print("  关键词：" + "、".join(t["keywords"]))
    print()

    if args.line == "robot":
        print("【工业机器人资料包 · 版权红线】")
        for c in tb.PRODUCT_LINES["robot"]["critical"]:
            print(f"  {c}")
        print()

    if args.export:
        cfg = cfgmod.load()
        outdir = os.path.join(cfg["paths"]["outputs"], "文章_淘宝视觉方案")
        files = tb.export_plan(outdir, args.line, args.product)
        print("已导出：")
        for p in files:
            print(f"  {os.path.basename(p)}")
        print(f"\n目录：{outdir}")
        print()
    else:
        print("导出完整方案：python run.py taobao --export")
        print()
    return 0


def cmd_funnel(args):
    """成交漏斗体检：找出断在哪一环。"""
    from . import funnel as funnelmod

    cfg = cfgmod.load()
    report = funnelmod.build_report(cfg)

    _print_header("成交漏斗体检")
    print("货 → 引流 → 试 → 成交 → 留")
    print("每一环读的是系统里的真实数据，读不到就标「未开始」，不编。\n")

    for s in report["stages"]:
        bar = "█" * (s["status"] // 10) + "░" * (10 - s["status"] // 10)
        print(f"  {s['name']}  {bar}  {s['status']:>3}%  {s['title']}")
        print(f"      缺口：{s['gap']}")

    b = report["bottleneck"]
    print()
    if b:
        print(f"瓶颈 → {b['name']}｜{b['title']}")
        print(f"  现在就做：")
        print(f"    {b['action']}")

    if report.get("radar_actions"):
        print("\n外部情报里的三件待办（来源：run.py radar）：")
        for i, r in enumerate(report["radar_actions"], 1):
            print(f"  {i}. 【{r['name']}】{r['action']}")

    if report["notes"]:
        print("\n数据说明：")
        for n in report["notes"]:
            print(f"  · {n}")

    if args.export:
        # 目录名和文件名都不带日期数字（规划师 R1/R2），体检结果是"最新一份"，
        # 直接覆盖更新，比堆一串历史快照更好找。
        outdir = os.path.join(cfg["paths"]["outputs"], "数据_漏斗体检")
        os.makedirs(outdir, exist_ok=True)
        p = os.path.join(outdir, "成交漏斗体检.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(report["markdown"])
        print(f"\n已导出：{p}")

    print("\n完整报告：python run.py funnel --export")
    print()
    return 0


def cmd_batch2(args):
    """第二批 8 条情报：逐条分析 + WorkBuddy 变现定价校准 + FDE 方向。"""
    from . import radar_batch2 as b2

    _print_header("第二批 8 条情报")
    print("这一批出了两条直接命中你生意的情报：")
    print("  · 鉴锋：怎么用 WorkBuddy 打造一个月入过万的 Skill")
    print("  · 青青：收录近 3000 个真实创业项目的网站")
    print()

    print("【逐条结论】")
    for it in b2.ITEMS:
        print(f"  {it['priority'][:6]} {it['author']} —— {it['what'][:44]}")
    print()

    c = b2.PRICING_CALIBRATION
    print("【定价校准】")
    print(f"  {c['diagnostic']['verdict']}")
    print(f"  现状：{c['diagnostic']['current']}")
    for f in c["platform_facts"][:4]:
        print(f"    · {f['item']}：{f['value']}")
    print()
    print("  方案：")
    for s in c["plan"]["steps"]:
        print(f"    {s['step']}  {s['price']}")
    print()

    fde = b2.FDE
    print("【FDE 方向】")
    print(f"  {fde['origin']}")
    print(f"  {fde['your_angle']['insight']}")
    print("  但不是现在做——先验证 Skill 能被陌生人买。")
    print()

    if args.export:
        cfg = cfgmod.load()
        outdir = os.path.join(cfg["paths"]["outputs"], "数据_第二批情报")
        files = b2.export_all(outdir)
        print("已导出：")
        for p in files:
            print(f"  {os.path.basename(p)}")
        print(f"\n目录：{outdir}")
        print()
    else:
        print("导出完整报告：python run.py batch2 --export")
        print()
    return 0


def cmd_console(args):
    """启动网页控制台：状态墙 + 派活台 + 台账。

    单实例闸门：同一套系统只允许一个控制台在跑。
    已经有实例时不再开第二个（2026-10-31 现场同时起了 4 个，
    用户反馈"分不清哪个是主页"，其中一个还监听了 0.0.0.0 对外暴露）。
    """
    from . import console_server as srv
    from . import single_instance as si

    cfg = cfgmod.load()
    port = int(args.port)
    host = getattr(args, "host", "127.0.0.1")

    # ---- 单实例闸门：先抢锁，抢不到就指路，别开第二个 ----
    gate = si.acquire(port, host)
    if not gate.get("ok"):
        lock = gate.get("lock") or {}
        print("")
        print("  ⛔ 已经有控制台在跑，不重复启动")
        print("     " + si.describe_running())
        print("")
        print("     想关掉它？关掉那个进程即可（PID %s）。" % lock.get("pid"))
        print("     要换端口重启？先关掉旧的，再 `python run.py console --port %d`。"
              % (port + 1))
        print("")
        # 顺手把浏览器打开到已有的那个，省得用户自己找
        if not args.no_open:
            try:
                import webbrowser
                webbrowser.open(lock.get("url") or ("http://127.0.0.1:%d/" % port))
            except Exception:
                pass
        return 0

    try:
        server, stop_event = srv.serve(cfg, port=port,
                                       open_browser=not args.no_open,
                                       host=host)
    except OSError as e:
        si.release()
        print(f"❌ 端口 {port} 启动失败：{e}")
        print(f"   换个端口：python run.py console --port {port + 1}")
        return 1
    except Exception:
        si.release()
        raise
    try:
        # 用 serve_forever 而不是 handle_request 循环：
        # handle_request() 一次只接受一个连接，SSE 长连接会占住它，
        # 页面上的其它接口（点派活、切页签）就全超时了。
        # ThreadingHTTPServer 会为每个连接开线程，serve_forever 不会阻塞。
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n控制台已停止。")
    finally:
        try:
            stop_event.set()
            server.shutdown()
        except Exception:
            pass
        si.release()
    return 0


def cmd_board(args):
    """AI 员工状态墙：谁在干活、有没有偷懒、产出多少、完成没完成。"""
    from . import board as boardmod

    _print_header("AI 员工状态墙")
    cfg = cfgmod.load()
    b = boardmod.Board(cfg)
    data = b.build()

    if args.html:
        out = os.path.join(cfg["paths"]["outputs"], "状态墙.html")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(b.render_html(data))
        print(f"网页已生成：{out}")
        print("双击用浏览器打开，就是你的 AI 员工主页。")
        print()
    print(b.render_text(data))
    return 0


def cmd_assign(args):
    """给 AI 员工下达指令：选岗位 + 派任务 + 看结果。"""
    from . import board as boardmod

    cfg = cfgmod.load()
    registry = rolesmod.RoleRegistry(cfg["paths"]["roles"])

    # 列出所有岗位供选择
    if args.list or not args.code:
        _print_header("可下达指令的 AI 员工")
        for r in registry.all():
            print(f"  {r.code}  {r.name:<12} {r.layer:<12} "
                  f"自动化 {r.automation}　—— {r.quick_questions[0] if r.quick_questions else '（无示例）'}")
        print()
        if not args.code:
            print("用法：python run.py assign A07 \"帮我写一篇关于XX的文章\"")
            print("     python run.py assign --list        看全部岗位和示例指令")
            return 0

    role = registry.get(args.code)
    if role is None:
        print(f"没有这个岗位：{args.code}")
        print("跑 python run.py assign --list 看全部岗位。")
        return 1

    _print_header(f"下达指令 → {role.code} {role.name}")
    print(f"  任务：{args.task}")
    print()

    pipe = piplmod.Pipeline(cfg)
    print("  正在调用……（真实调用，会消耗 token）\n")
    try:
        content, path = pipe.run_role(
            role.code, args.task,
            extra_kb=pipe.kb.context(args.task, top_k=3) if args.use_kb else "",
            tier=args.tier, temperature=args.temp,
            save_as=args.save,
        )
    except Exception as e:
        print(f"\n  ❌ 调用失败：{e}")
        print("     报错已记入台账，状态墙里能看到这次异常。")
        return 1

    if path:
        print(f"  ✅ 完成，产出已保存：{path}")
    else:
        print("  ✅ 完成")
    print()
    print("-" * 62)
    print(content)
    print("-" * 62)
    print()
    print(f"本次消耗：{llm.LEDGER.summary()}")
    _dump_usage(cfg)
    print("跑 python run.py board 看状态墙更新。")
    return 0


def cmd_radar(args):
    """情报雷达：把外部视频情报翻译成「我该动哪个岗位」。"""
    from . import radar as radarmod

    cfg = cfgmod.load()
    report = radarmod.Radar(cfg).inspect()

    _print_header("情报雷达")
    print("把外部视频情报归类，落到你的岗位上。")
    print("每类给：可抄什么 / 不抄什么 / 现在就做哪件事。\n")

    print(report["markdown"])

    if args.export:
        # 栏目化：每期存一份（带期号），同时更新 latest。
        # 理由：2026-10-31 实测 —— 封面带「009期」角标的内容转/赞 14.03，
        # 期数是订阅钩子（观众知道下一期会来）。系统侧要有历史才能做周报。
        outdir = os.path.join(cfg["paths"]["outputs"], "文章_情报雷达")
        os.makedirs(outdir, exist_ok=True)
        issue = report.get("期号") or "000期"
        p_issue = os.path.join(outdir, "情报雷达_%s.md" % issue)
        with open(p_issue, "w", encoding="utf-8") as f:
            f.write(report["markdown"])
        p = os.path.join(outdir, "情报雷达.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(report["markdown"])
        # 记期号状态，供下次对比是否新的一期
        state_path = os.path.join(outdir, "_期号.json")
        prev = {}
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
            except Exception:
                prev = {}
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump({"期号": issue, "周次": report.get("周次", ""),
                           "情报条数": report.get("total", 0),
                           "上次生成": time.strftime("%Y-%m-%d %H:%M:%S")},
                          f, ensure_ascii=False, indent=1)
        except Exception:
            pass
        print("已导出：%s" % p)
        print("          %s" % p_issue)
        print()

    return 0


def cmd_demo(args):
    """结果前置：让客户不花钱就看到「他的文案会被判什么问题」。"""
    cfg = cfgmod.load()
    pipeline = piplmod.Pipeline(cfg)

    _print_header("结果前置演示")
    print("这个命令的用途：给潜在客户一段他自己的文案，30 秒内让他看见")
    print("「这段东西会被判违规/被看出是 AI 写的」。他看见问题，才会愿意付钱解决。\n")

    if args.file:
        if not os.path.exists(args.file):
            print(f"文件不存在：{args.file}")
            return 1
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    elif args.text:
        text = args.text
    else:
        print("用法：python run.py demo --file 你的文案.md")
        print("  或：python run.py demo --text \"直接粘贴一段文案\"")
        print()
        return 1

    if not text.strip():
        print("文案是空的，没法检测。")
        return 1

    print(f"文案长度：{len(text)} 字\n")
    print("  [1/2] A10 质检官 三合一检测 ...", flush=True)

    hits = pipeline.forbidden.match(text)
    fctx = pipeline._forbidden_context(hits)
    report, _ = pipeline.run_role(
        "A10",
        "对下面这段文案做三合一检测：违规检测、AI味检测、错误检测。\n"
        "输出格式：\n"
        "## 一、违规检测\n逐条列出命中项，标注【严重】/【注意】，没有就写「未发现违规」\n"
        "## 二、AI味检测\n列出所有AI痕迹句（原文引用），给出改法\n"
        "## 三、错误检测\n列出事实错误、逻辑矛盾、表述含糊处\n"
        "## 四、修改建议（按优先级排序，最多10条）\n"
        "## 五、结论\n给出 【通过】/【修改后通过】/【不通过】 和英文 verdict_code\n\n"
        f"【系统预检已命中的红线（需你复核，不要机械照抄）】\n{fctx or '系统预检未命中，但不代表无违规，请独立判断'}\n\n"
        f"【待检测文案】\n{text}",
        extra_kb=fctx, tier="judge", temperature=0.3, max_tokens=3000)

    print("  [2/2] A08 改稿官 生成改写示范 ...", flush=True)
    fixed, _ = pipeline.run_role(
        "A08",
        "根据下面的质检报告，把原文改写成合规、没有AI味的版本。"
        "输出：①改写后全文 ②逐条列出改了什么、为什么改。\n"
        "不要删掉原有的具体细节；不要新增任何编造的事实。\n\n"
        f"【原文】\n{text}\n\n【质检报告】\n{report}",
        tier="create", temperature=0.6, max_tokens=5000)

    # 组装演示包
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    outdir = os.path.join(cfg["paths"]["outputs"], "数据_演示包")
    os.makedirs(outdir, exist_ok=True)

    p1 = os.path.join(outdir, f"质检报告_{ts}.md")
    with open(p1, "w", encoding="utf-8") as f:
        f.write(report)

    p2 = os.path.join(outdir, f"改写示范_{ts}.md")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(fixed)

    demo = f"""# 发布前合规体检 · 演示报告

体检时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## 一、你这段文案的体检结果

{report}

---

## 二、逐条改写示范

{fixed}

---

## 三、说明

- 以上检测由 AI 辅助完成，最终是否违规由平台判定。
- 这份报告是**免费演示**：你不需要付费就能先看看自己的文案有什么问题。
- 想在每次发布前都过一遍，或需要逐条改写：见货架页「发布前合规体检」。

⚠️ 本报告由 AI 辅助创作。
"""
    p3 = os.path.join(outdir, f"演示包_{ts}.md")
    with open(p3, "w", encoding="utf-8") as f:
        f.write(demo)

    print(f"\n命中红线：{len(hits)} 条（系统预检）")
    if not hits:
        print("  ⚠️ 预检命中 0 条不代表没问题。")
        print("     预检只查「诱导类/谣言类」等有明确关键词的条目，")
        print("     「最强/第一/100%承诺」这类绝对化用语靠质检官独立判断。")
    print()
    print("--- 体检报告（节选） ---")
    print(report[:1500])
    if len(report) > 1500:
        print("\n…（报告已完整保存）")

    print("\n\n--- 已导出 ---")
    for p in (p3, p1, p2):
        print(f"  {os.path.basename(p)}")
    print(f"\n目录：{outdir}")
    print(f"token：{llm.LEDGER.summary()}")
    print("\n下一步：把演示包里的「演示报告」发给客户看，这是结果前置的成交入口。")
    print()
    return 0


def cmd_config(args):
    _print_header("配置")
    cfg = cfgmod.load()
    if args.set:
        for kv in args.set:
            if "=" not in kv:
                print(f"格式应为 key=value，收到：{kv}")
                continue
            k, v = kv.split("=", 1)
            if k == "api_key":
                cfg["api_key"] = v.strip()
            elif k == "simulate":
                cfg["simulate"] = v.lower() in ("1", "true", "yes")
            elif k in ("model", "reasoner_model", "base_url"):
                cfg[k] = v.strip()
            else:
                print(f"未知配置项：{k}")
        cfgmod.save(cfg)
        print(f"已保存到 {cfgmod.CONFIG_PATH}")
    else:
        print(json.dumps({k: v for k, v in cfg.items() if k != "paths"},
                         ensure_ascii=False, indent=2))
        print(f"\n配置文件路径：{cfgmod.CONFIG_PATH}")
    print()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="run.py", description="一人AI公司 · 本地 AI 员工系统")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("doctor", help="系统体检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("today", help="跑今日完整内容链路")
    p.add_argument("--topic", help="指定选题（不填则自动取）")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("product", help="生成商品详情页")
    p.add_argument("--name", help="资料包名称")
    p.add_argument("--price", default="49")
    p.set_defaults(func=cmd_product)

    p = sub.add_parser("ask", help="单独叫某个员工干活")
    p.add_argument("code", help="岗位编号，如 A07")
    p.add_argument("task", help="任务描述")
    p.add_argument("--use-kb", action="store_true", help="注入知识库上下文")
    p.add_argument("--save", help="保存为指定文件名")
    p.add_argument("--tier", default="create", choices=["scan", "create", "judge"])
    p.add_argument("--temp", type=float, default=0.7)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("check", help="合规预检")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--file", help="检查文件内容")
    p.add_argument("--platform", help="限定平台")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("roles", help="查看岗位")
    p.add_argument("code", nargs="?", help="岗位编号，留空显示全部")
    p.set_defaults(func=cmd_roles)

    p = sub.add_parser("search", help="检索知识库")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("forbidden", help="查看禁区清单")
    p.add_argument("--platform")
    p.set_defaults(func=cmd_forbidden)

    p = sub.add_parser("shop", help="智能体货品清单：可售SKU / 商品卡")
    p.add_argument("code", nargs="?", help="岗位编号，留空显示全部 SKU")
    p.add_argument("--export", action="store_true", help="导出商品卡与话术脚本")
    p.set_defaults(func=cmd_shop)

    p = sub.add_parser("funnel", help="成交漏斗体检：货→引流→试→成交→留")
    p.add_argument("--export", action="store_true", help="导出体检报告 Markdown")
    p.set_defaults(func=cmd_funnel)

    p = sub.add_parser("demo", help="结果前置演示：拿客户文案生成体检+改写示范")
    p.add_argument("--file", help="文案文件路径")
    p.add_argument("--text", help="直接粘贴文案")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("console", help="启动网页控制台（状态墙 + 派活台 + 台账）")
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.add_argument("--host", default="127.0.0.1",
                   help="监听地址。默认 127.0.0.1 只允许本机访问；"
                        "改成 0.0.0.0 可让局域网/云端访问"
                        "（此时务必设置环境变量 STAFF_ACCESS_PASSWORD）")
    p.set_defaults(func=cmd_console)

    p = sub.add_parser("board", help="AI 员工状态墙：谁在干活、有没有偷懒、产出多少")
    p.add_argument("--html", action="store_true", help="生成网页版状态墙（可当主页）")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("assign", help="给 AI 员工下达指令")
    p.add_argument("code", nargs="?", help="岗位编号，如 A07")
    p.add_argument("task", nargs="?", help="任务描述")
    p.add_argument("--list", action="store_true", help="列出全部岗位与示例指令")
    p.add_argument("--use-kb", action="store_true", help="自动挂知识库上下文")
    p.add_argument("--save", help="产出保存为指定文件名")
    p.add_argument("--tier", default="create",
                   choices=["scan", "create", "judge", "judge_econ", "judge_fast"],
                   help="模型档位：scan 便宜/create 正常/judge 最强")
    p.add_argument("--temp", type=float, default=0.7)
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("radar", help="情报雷达：外部视频情报 → 我该动哪个岗位")
    p.add_argument("--export", action="store_true", help="导出雷达报告 Markdown")
    p.set_defaults(func=cmd_radar)

    p = sub.add_parser("batch2", help="第二批 8 条情报：定价校准 + FDE 方向")
    p.add_argument("--export", action="store_true", help="导出四份 Markdown")
    p.set_defaults(func=cmd_batch2)

    p = sub.add_parser("skill", help="技能打包：岗位说明书 → 可上架的 Skill 包")
    p.add_argument("only", nargs="?", help="只打包某个岗位，如 A10")
    p.add_argument("--list", action="store_true", help="只看清单和定价")
    p.set_defaults(func=cmd_skill)

    p = sub.add_parser("matrix", help="矩阵账号体检：5 个平台的冲突、违规与整改清单")
    p.add_argument("--export", action="store_true", help="导出三份 Markdown")
    p.set_defaults(func=cmd_matrix)

    p = sub.add_parser("taobao", help="淘宝视觉官：主图方案 + 详情页脚本 + 标题")
    p.add_argument("--line", default="ai_efficiency",
                   choices=["ai_efficiency", "robot"], help="产品线")
    p.add_argument("--product", default="", help="产品名")
    p.add_argument("--export", action="store_true", help="导出三份 Markdown")
    p.add_argument("--check", help="检查一段文案是否踩淘宝/广告法红线")
    p.set_defaults(func=cmd_taobao)

    p = sub.add_parser("config", help="查看/修改配置")
    p.add_argument("--set", nargs="*", help="key=value 形式")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("cost", help="费用清单：每个岗位的 token 与花费")
    p.add_argument("--peak", action="store_true", help="按高峰时段单价算（默认空闲时段）")
    p.set_defaults(func=cmd_cost)

    p = sub.add_parser("cost-export", help="导出费用清单 CSV + Markdown")
    p.add_argument("--peak", action="store_true", help="按高峰时段单价算")
    p.set_defaults(func=cmd_export_cost)

    p = sub.add_parser("usage", help="查看真实消耗台账")
    p.set_defaults(func=cmd_usage)

    p = sub.add_parser("wechat", help="A26 微信情报官：微信聊天记录 → 客户/供应商/内部三线看板")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "sync", "detail", "export"],
                   help="status=看状态；sync=增量同步；detail=看某个会话；export=导出 Markdown")
    p.add_argument("--session", default="", help="detail 用的会话 username")
    p.add_argument("--limit", type=int, default=60, help="detail 拉多少条消息")
    p.add_argument("--out", default="", help="export 的输出路径")
    p.set_defaults(func=cmd_wechat)

    p = sub.add_parser("daily-sync", help="每日自动同步任务（当前：微信情报）")
    p.add_argument("--force", action="store_true", help="强制重算，不看缓存")
    p.set_defaults(func=cmd_daily_sync)

    p = sub.add_parser("credit", help="A27 积分管家：WorkBuddy 每日签到 + 积分 + 缓存命中率")
    p.add_argument("action", nargs="?", default="status",
                   choices=["status", "checkin", "report", "cache"],
                   help="status=看账号与签到状态；checkin=领取今日积分；"
                        "report=完整报告；cache=缓存命中率统计")
    p.add_argument("--force", action="store_true", help="强制领取（已签到也再调一次）")
    p.add_argument("--dry-run", action="store_true", help="只查状态不领取")
    p.add_argument("--probe", action="store_true", help="cache 动作：实测缓存命中率")
    p.add_argument("--rounds", type=int, default=3, help="实测轮次（默认 3）")
    p.add_argument("--model", default="glm-5.3-flash", help="实测用的模型")
    p.set_defaults(func=cmd_credit)

    p = sub.add_parser("shots", help="A28 商品视觉官：淘宝/咸鱼主图 + 详情页 + 上架文案")
    p.add_argument("action", nargs="?", default="build",
                   choices=["build", "copy", "check"],
                   help="build=出图+出文案；copy=只出文案；check=只做合规体检")
    p.add_argument("--platform", default="淘宝", choices=["淘宝", "咸鱼", "全部"],
                   help="按平台出（默认淘宝）")
    p.set_defaults(func=cmd_shots)

    p = sub.add_parser("vclone",
                       help="A29 视频工作流克隆器：按词锚定 + 一套配方出 N 个变体")
    p.add_argument("action", nargs="?", default="demo",
                   choices=["demo", "build", "variants", "list"],
                   help="demo=跑通示例；build=稿子转配方；"
                        "variants=按配方出变体；list=列出已存配方")
    p.add_argument("--src", default="", help="稿子文件（markdown/txt）")
    p.add_argument("--slots", default="钩子,痛点,解法,证据,行动",
                   help="槽位名，逗号分隔")
    p.add_argument("--variants", default="", help="变体 JSON 文件")
    p.add_argument("--workflow", default="", help="配方 JSON 文件")
    p.add_argument("--out", default="", help="输出目录")
    p.add_argument("--title", default="", help="配方标题")
    p.add_argument("--video", action="store_true", help="同时合成 MP4")
    p.set_defaults(func=cmd_vclone)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except llm.LLMError as e:
        print(f"\n❌ 调用失败：{e.message}")
        if e.hint:
            print(f"   建议：{e.hint}")
        return 2
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())

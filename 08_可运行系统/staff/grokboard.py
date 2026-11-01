"""多智能体监控大盘（GROKBOT 风格）。

参考视频 AfgDyf9m6N 里的 GROKBOT MULTI-AGENT CONTROL 面板：
  - 顶部 6 条指标（TASK ENGINE / SCAN SCORE / COMPUTE / VERIFY / RELAY / SYNC）
  - 中央大号金额卡 + 金额变化曲线
  - 中心辐射式 Agent 圆形矩阵（中央是中枢枢纽）
  - 每个 Agent 一张卡：完成度 %、进度条、当前任务、金额
  - 底部 CONTINUOUS FEED 实时执行日志流

和 board.py 的区别：
  board.py  面向「人」，回答「谁在偷懒、谁没干活」
  本模块   面向「客户/炫酷」，回答「整个系统在跑得怎么样、烧了多少」

数据全部来自已有的 telemetry 台账 + live 事件总线，
**不额外调模型、不花钱**（纯读本地数据）。
"""

import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta

from . import board as boardmod
from . import roles as rolesmod
from . import telemetry as telmod


# ---- 六个指标的定义 -------------------------------------------------
#
# 每个指标是一个 (英文名, 中文名, 取值函数) 三元组。
# 取值函数返回 0~100 的分数，前端画成百分比 + 波形。
#
# 为什么要这么设计：
#   GROKBOT 那块面板的六条指标不是随便放的，是「引擎六个环节的健康度」。
#   这里按我们自己的链路映射成六个环节，客户看一眼就知道系统在哪一环卡住了。

METRIC_DEFS = [
    ("TASK ENGINE", "任务引擎", "今日派活量", "今天派了多少活"),
    ("SCAN SCORE",  "扫描质量", "情报/素材就绪度", "输入层素材备得够不够"),
    ("COMPUTE",     "算力消耗", "Token 预算占用", "今天烧了多少预算"),
    ("VERIFY",      "质检通过", "一次过检率", "质检官一次过没返工"),
    ("RELAY",       "链路接力", "每日链路完成度", "流水线跑到第几棒"),
    ("SYNC",        "同步健康", "无故障岗位占比", "有多少岗位在正常待命"),
]

# 中心枢纽周围的 6 个「辐射位」，对应每日链路的六个阶段。
# 用阶段名而不是岗位名，因为客户看的是「流水线走到哪一步」。
RADIAL_STAGES = [
    ("SCAN",    "扫描",   ["A03", "A04"]),
    ("ANALYZE", "分析",   ["A05", "A06"]),
    ("CREATE",  "创作",   ["A07", "A17", "A20"]),
    ("EXECUTE", "执行",   ["A08", "A11"]),
    ("VERIFY",  "校验",   ["A10"]),
    ("RECORD",  "记录",   ["A09", "A14"]),
]

# 每个岗位的图标（按职能猜的，纯装饰，不影响逻辑）
_ROLE_EMOJI = {
    "A01": "🛡️", "A02": "📜", "A03": "🔭", "A04": "🗂️", "A05": "💡",
    "A06": "🧩", "A07": "✍️", "A08": "🔁", "A09": "🧼", "A10": "🔍",
    "A11": "🚀", "A12": "📈", "A13": "🛍️", "A14": "📊", "A15": "🎧",
    "A16": "🔎", "A17": "🎬", "A18": "💰", "A19": "🏷️", "A20": "🎭",
    "A21": "📦", "A22": "🖼️", "A23": "🗺️", "A24": "🧑", "A25": "📚",
    "A26": "💬",
}


def _short_task(text, n=18):
    """把任务名压短，用于卡片显示。"""
    if not text:
        return "待命"
    t = str(text).replace("\n", " ").strip()
    return t if len(t) <= n else t[:n] + "…"


def _today_key():
    return datetime.now().strftime("%Y-%m-%d")


def _recent_days(records, days=14):
    """按天切分记录，返回 {日期: [记录]}（只保留有记录的日期，按时间正序）。"""
    buckets = OrderedDict()
    for r in records:
        ts = str(r.get("ts") or "")
        if len(ts) < 10:
            continue
        day = ts[:10]
        buckets.setdefault(day, []).append(r)
    keys = list(buckets.keys())[-days:]
    return OrderedDict((k, buckets[k]) for k in keys)


def _wave(records, buckets=24):
    """把一批记录压成波形序列（0~1 的小数），给前端画迷你折线。

    做法：把记录按时间切成 buckets 段，每段的 token 量归一化。
    没有数据就给一条平线（前端画成静止波形，避免"坏了"的错觉）。
    """
    if not records:
        return [0.18] * buckets
    vals = [float(r.get("tokens") or 0) for r in records]
    hi = max(vals) or 1.0
    if len(vals) >= buckets:
        # 取最后 buckets 条，按等分聚合
        vals = vals[-buckets:]
        out = []
        step = max(1, len(vals) // buckets)
        for i in range(0, len(vals), step):
            chunk = vals[i:i + step]
            out.append(sum(chunk) / hi)
    else:
        out = [v / hi for v in vals]
        out += [0.18] * (buckets - len(out))
    # 归一到 0.15~1.0，别贴地也别爆顶
    return [round(0.15 + 0.85 * v, 4) for v in out[-buckets:]]


def build(cfg):
    """构建大盘数据。返回可直接 JSON 化的 dict。"""
    logs_dir = cfg["paths"]["logs"]
    records = telmod.read_all(logs_dir)
    today = _today_key()
    today_recs = [r for r in records if str(r.get("ts") or "")[:10] == today]

    # ---- 岗位维度 ----
    registry = rolesmod.RoleRegistry(cfg["paths"]["roles"])
    rows_by_code = {}
    for row in boardmod.Board(cfg).build().get("rows", []):
        rows_by_code[row["code"]] = row

    agents = []
    for role in registry.all():
        code = role.code
        row = rows_by_code.get(code, {})
        recs = [r for r in records if r.get("role") == code]
        today_r = [r for r in today_recs if r.get("role") == code]

        tokens = int(row.get("tokens") or 0)
        cost = float(row.get("cost_yuan") or 0.0)
        calls = int(row.get("calls") or 0)
        out_chars = int(row.get("output_chars") or 0)
        errors = len([r for r in recs if r.get("status") == "error"])

        # ---- 完成度 % ----
        #
        # 怎么算的（客户看得懂最重要）：
        #   每日链路岗位：产出达标 → 100；产出不够 → 按比例给分
        #   按需岗位    ：干过活就算完成（干过但产出太少 → 打折）
        floor = boardmod.MIN_OUTPUT.get(code, 200)
        if code in boardmod.DAILY_EXPECT:
            if out_chars >= floor:
                pct = 100.0
            elif out_chars > 0:
                pct = max(8.0, round(out_chars / floor * 100, 1))
            elif calls:
                pct = 5.0
            else:
                pct = 0.0
        else:
            if calls == 0:
                pct = 0.0
            elif out_chars >= floor:
                pct = 100.0
            else:
                pct = max(10.0, round(out_chars / floor * 100, 1))
        # 最近一次失败 → 扣一半（系统自己知道刚才没干成）
        if errors and recs and recs[-1].get("status") == "error":
            pct = round(pct * 0.5, 1)

        last = recs[-1] if recs else None
        agents.append({
            "code": code,
            "name": role.name,
            "emoji": _ROLE_EMOJI.get(code, "🤖"),
            "layer": role.layer,
            "automation": role.automation,
            "state": row.get("state", "未开工"),
            "pct": pct,
            "calls": calls,
            "today_calls": len(today_r),
            "tokens": tokens,
            "today_tokens": sum(int(r.get("tokens") or 0) for r in today_r),
            "cost_yuan": round(cost, 6),
            "today_cost": round(sum(float(r.get("cost_yuan") or 0.0) for r in today_r), 6),
            "output_chars": out_chars,
            "errors": errors,
            "slacking": int(row.get("slacking") or 0),
            "task": _short_task((last or {}).get("task") or ""),
            "last_ts": (last or {}).get("ts", "—"),
            "duration_s": round(float((last or {}).get("duration_s") or 0), 2),
            "wave": _wave(recs),
        })

    # ---- 顶部六指标 ----
    total_tokens = sum(a["tokens"] for a in agents)
    total_cost = sum(a["cost_yuan"] for a in agents)
    total_calls = sum(a["calls"] for a in agents)
    today_calls = sum(a["today_calls"] for a in agents)
    today_tokens = sum(a["today_tokens"] for a in agents)
    today_cost = sum(a["today_cost"] for a in agents)

    budget = int(cfg.get("daily_token_budget") or 200000)

    # 1) TASK ENGINE  今日派活量：今天跑够 30 次算满分
    task_engine = min(100.0, round(today_calls / 30.0 * 100, 1))
    # 2) SCAN SCORE   输入层就绪度：A03/A04 产出字数 / 预期
    scan_chars = sum(a["output_chars"] for a in agents if a["code"] in ("A03", "A04"))
    scan_floor = (boardmod.MIN_OUTPUT.get("A03", 200) + boardmod.MIN_OUTPUT.get("A04", 300))
    scan_score = min(100.0, round(scan_chars / scan_floor * 100, 1)) if scan_floor else 0.0
    # 3) COMPUTE      算力：今日 token 占预算的比例（越低越省，但要够用）
    compute = min(100.0, round(today_tokens / budget * 100, 1)) if budget else 0.0
    # 4) VERIFY       质检通过率：A10 成功次数 / 总次数
    a10 = [a for a in agents if a["code"] == "A10"]
    verify = 0.0
    if a10:
        a10_recs = [r for r in records if r.get("role") == "A10"]
        ok = len([r for r in a10_recs if r.get("status") == "ok"])
        verify = round(ok / len(a10_recs) * 100, 1) if a10_recs else 0.0
    # 5) RELAY        每日链路接力：最近一次链路跑完的步骤比例
    relay = 0.0
    runs = {}
    for r in records:
        rid = r.get("run_id") or ""
        if rid:
            runs.setdefault(rid, []).append(r)
    if runs:
        last_rid = list(runs.keys())[-1]
        steps = runs[last_rid]
        done = {s.get("role") for s in steps if s.get("status") == "ok"}
        total_steps = len(boardmod.DAILY_EXPECT)
        relay = round(len(done & set(boardmod.DAILY_EXPECT)) / total_steps * 100, 1)
    # 6) SYNC         同步健康：没有出错的岗位占比
    sync = 0.0
    if agents:
        healthy = len([a for a in agents if a["errors"] == 0])
        sync = round(healthy / len(agents) * 100, 1)

    metrics = [
        {
            "key": key, "en": en, "cn": cn, "hint": hint,
            "value": val,
            "wave": _wave(records) if key == "TASK ENGINE" else _wave(
                [r for r in today_recs] if key in ("COMPUTE", "SCAN SCORE") else records),
        }
        for (key, en, cn, hint), val in zip(METRIC_DEFS, [
            task_engine, scan_score, compute, verify, relay, sync])
    ]

    # ---- 辐射矩阵（6 个阶段，围绕中枢）----
    radial = []
    for en, cn, codes in RADIAL_STAGES:
        members = [a for a in agents if a["code"] in codes]
        if not members:
            radial.append({"en": en, "cn": cn, "pct": 0.0, "cost": 0.0,
                           "tokens": 0, "members": []})
            continue
        # 阶段完成度取成员平均
        pct = round(sum(m["pct"] for m in members) / len(members), 1)
        radial.append({
            "en": en,
            "cn": cn,
            "pct": pct,
            "cost": round(sum(m["cost_yuan"] for m in members), 6),
            "tokens": sum(m["tokens"] for m in members),
            "members": [{"code": m["code"], "name": m["name"],
                         "emoji": m["emoji"], "pct": m["pct"]} for m in members],
        })

    # ---- 金额曲线（近 14 天）----
    by_day = _recent_days(records, 14)
    curve = []
    for day, recs in by_day.items():
        curve.append({
            "day": day,
            "cost": round(sum(float(r.get("cost_yuan") or 0.0) for r in recs), 4),
            "tokens": sum(int(r.get("tokens") or 0) for r in recs),
            "calls": len(recs),
        })

    # ---- 中枢 ----
    hub = {
        "name": "OPC 中枢",
        "en": "GRID CORE",
        "cost_today": round(today_cost, 4),
        "cost_total": round(total_cost, 4),
        "tokens_today": today_tokens,
        "tokens_total": total_tokens,
        "calls_today": today_calls,
        "calls_total": total_calls,
        "budget": budget,
        "budget_pct": round(today_tokens / budget * 100, 1) if budget else 0.0,
        "agents": len(agents),
        "active": len([a for a in agents if a["today_calls"] > 0]),
    }

    return {
        "ok": True,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "demo_mode": bool(cfg.get("demo_mode")),
        "hub": hub,
        "metrics": metrics,
        "radial": radial,
        "agents": agents,
        "curve": curve,
        "totals": {
            "tokens": total_tokens,
            "cost_yuan": round(total_cost, 4),
            "calls": total_calls,
            "output_chars": sum(a["output_chars"] for a in agents),
            "agents": len(agents),
        },
    }

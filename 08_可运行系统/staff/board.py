"""AI 员工状态墙。

回答四个问题，全部基于真实台账，不猜：
    1. 在不在干活   —— 最近一次调用距今多久、这次跑了多久
    2. 有没有偷懒   —— 输出是否过短、是否把活推回给人、是否报错未完成
    3. 产出多少     —— 累计字数、产出文件数
    4. 完成没完成   —— 每日链路 8 步是否跑完、有没有卡在某一步

判定规则（可复算，规则写在代码里，不是黑箱）
------------------------------------
状态 state：
    🟢 干活中   最近 10 分钟内有调用
    🔵 刚完工   最近 24 小时内有调用，且无异常
    🟡 待命     超过 24 小时有调用，之后没动
    ⚪ 未开工   从来没被调用过
    🔴 异常     最近一次调用失败

偷懒度 slacking（0-100，100 = 明显偷懒）：
    只对「被叫过」的岗位计算。没被叫过 = 未开工，不算偷懒。
    基线 0
    + 平均产出 < 该岗位预期下限，+40
    + 产出含「待补/待填」标记（把活推回给人），+25
    + 疑似输出截断，+20
    + 调用失败，+40
    + 产出未落盘（可能空转），+20
    上限 100

产出判定 completion：
    每个岗位在每日链路里都有"该出的东西"（如 A07 出初稿、A10 出质检报告）。
    有产出 = 完成；没产出 = 未完成。
    产出文件以台账记录的 output_file 为准（文件名不一定含岗位号）。
"""

import os
import re
from datetime import datetime, timedelta

from . import roles as rolesmod
from . import telemetry

# 每日链路里每个岗位"该出的东西"，用于判定完成度
DAILY_EXPECT = {
    "A03": "情报采集", "A04": "素材整理", "A05": "选题生成",
    "A07": "撰写初稿", "A10": "合规质检", "A08": "按质检修改",
    "A09": "去AI味定稿", "A11": "多平台适配",
}

# 各岗位的合理产出下限（字），低于此判为偷懒
MIN_OUTPUT = {
    "A03": 200, "A04": 300, "A05": 300, "A07": 1000, "A10": 400,
    "A08": 800, "A09": 800, "A11": 600, "A13": 500, "A22": 400,
    "A16": 400, "A12": 300, "A17": 500, "A18": 400, "A21": 400,
    "A19": 300, "A14": 300, "A15": 200, "A06": 300, "A20": 800,
    "A01": 300, "A02": 300, "A09 ": 800,
}


def _parse_ts(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _files_by_role(outputs_dir):
    """统计每个岗位产出过多少文件。"""
    out = {}
    if not outputs_dir or not os.path.isdir(outputs_dir):
        return out
    for root, _dirs, files in os.walk(outputs_dir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            m = re.search(r"(A\d{2})", fn)
            if m:
                out.setdefault(m.group(1), []).append(os.path.join(root, fn))
    return out


class Board:
    """AI 员工状态墙。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.logs_dir = cfg["paths"]["logs"]
        self.outputs_dir = cfg["paths"]["outputs"]
        self.state_dir = cfg["paths"]["state"]

    # ---- 主入口 ----------------------------------------------------

    def build(self):
        records = telemetry.read_all(self.logs_dir)
        agg = telemetry.summarize(records)
        files = _files_by_role(self.outputs_dir)
        registry = rolesmod.RoleRegistry(self.cfg["paths"]["roles"])
        now = datetime.now()

        rows = []
        for role in registry.all():
            code = role.code
            a = agg.get(code)
            recs = [r for r in records if r.get("role") == code]
            last = recs[-1] if recs else None
            last_dt = _parse_ts((last or {}).get("ts", "")) if last else None

            # ---- 状态 ----
            if not recs:
                state = "未开工"
            elif last and last.get("status") == "error":
                state = "异常"
            elif last_dt and (now - last_dt) <= timedelta(minutes=10):
                state = "干活中"
            elif last_dt and (now - last_dt) <= timedelta(hours=24):
                state = "刚完工"
            else:
                state = "待命"

            # ---- 偷懒度 ----
            # 看的是「最近一次干得怎么样」，不是历史累加。
            # 同一个问题重复 10 次不代表现在偷懒 10 倍。
            # 注意：「未开工」不是偷懒，是还没派活。
            slack = 0
            reasons = []
            if recs:
                last_ok = None
                for r in reversed(recs):
                    if r.get("status") == "ok":
                        last_ok = r
                        break

                if last_ok:
                    chars = int(last_ok.get("output_chars") or 0)
                    floor = MIN_OUTPUT.get(code, 200)
                    if chars < floor:
                        slack += 40
                        reasons.append(f"最近一次产出 {chars} 字 < 预期 {floor}")

                last_issues = list(last.get("issues") or []) if last else []
                if any(("待补" in i or "待填" in i) for i in last_issues):
                    slack += 25
                    reasons.append("最近一次产出含待补标记（把活推回给你）")
                if any("截断" in i for i in last_issues):
                    slack += 25
                    reasons.append("最近一次输出疑似被截断")
                if last and last.get("status") == "error":
                    slack += 50
                    reasons.append("最近一次调用失败")

                # 连续失败 2 次以上 = 硬故障
                errs = len([r for r in recs[-3:] if r.get("status") == "error"])
                if errs >= 2:
                    slack += 30
                    reasons.append(f"最近 3 次里有 {errs} 次失败")

                # 干过活但产出没落盘 → 疑似空转
                if last_ok and not last_ok.get("output_file"):
                    slack += 20
                    reasons.append("产出未落盘（可能空转）")
            slack = min(slack, 100)

            # ---- 产出 ----
            # 产出文件以台账记录的 output_file 为准（文件名可能不含岗位号）
            out_files = [r.get("output_file") for r in recs if r.get("output_file")]
            out_files += files.get(code, [])
            out_files = list(dict.fromkeys(out_files))
            out_chars = sum(int(r.get("output_chars") or 0) for r in recs)

            # ---- 完成度 ----
            if out_files:
                completion = "已完成"
            elif recs and last and last.get("status") == "error":
                completion = "执行失败"
            elif recs:
                completion = "未完成"
            elif code in DAILY_EXPECT:
                completion = "未开工"
            else:
                completion = "按需触发"

            # ---- 问题 ----
            problems = []
            for r in recs[-5:]:
                if r.get("error"):
                    problems.append(r["error"][:80])
                for i in (r.get("issues") or []):
                    if i not in problems:
                        problems.append(i)

            rows.append({
                "code": code,
                "name": role.name,
                "layer": role.layer,
                "automation": role.automation,
                "state": state,
                "slacking": slack,
                "slack_reasons": reasons,
                "calls": len(recs),
                "tokens": sum(int(r.get("tokens") or 0) for r in recs),
                "cost_yuan": round(sum(float(r.get("cost_yuan") or 0.0) for r in recs), 6),
                "output_chars": out_chars,
                "output_files": len(out_files),
                "last_ts": (last or {}).get("ts", "—"),
                "last_task": (last or {}).get("task", "—"),
                "duration_s": (last or {}).get("duration_s", 0),
                "completion": completion,
                "problems": problems[:4],
                "expect": DAILY_EXPECT.get(code, ""),
            })

        # ---- 全局 ----
        total_tokens = sum(r["tokens"] for r in rows)
        total_cost = sum(r["cost_yuan"] for r in rows)
        total_calls = sum(r["calls"] for r in rows)
        total_out = sum(r["output_chars"] for r in rows)

        # 最近一次链路跑完没有
        runs = {}
        for r in records:
            rid = r.get("run_id") or ""
            if not rid:
                continue
            runs.setdefault(rid, []).append(r)
        last_run = None
        if runs:
            last_rid = list(runs.keys())[-1]
            steps = runs[last_rid]
            done_steps = {s.get("role") for s in steps if s.get("status") == "ok"}
            last_run = {
                "run_id": last_rid,
                "steps_ok": len(done_steps),
                "steps_total": len(DAILY_EXPECT),
                "roles_done": sorted(done_steps),
                "roles_missing": [c for c in DAILY_EXPECT if c not in done_steps],
                "ts": steps[-1].get("ts", ""),
                "complete": len(done_steps) >= len(DAILY_EXPECT),
            }

        return {
            "rows": rows,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "totals": {
                "employees": len(rows),
                "calls": total_calls,
                "tokens": total_tokens,
                "cost_yuan": round(total_cost, 6),
                "output_chars": total_out,
                "active": len([r for r in rows if r["state"] in ("干活中", "刚完工")]),
                "idle": len([r for r in rows if r["state"] == "待命"]),
                "never": len([r for r in rows if r["state"] == "未开工"]),
                "error": len([r for r in rows if r["state"] == "异常"]),
                "slacking": len([r for r in rows if r["slacking"] >= 50]),
            },
            "last_run": last_run,
            "has_data": len(records) > 0,
        }

    # ---- 文本输出 --------------------------------------------------

    def render_text(self, data):
        L = []
        t = data["totals"]
        L.append(f"  生成时间：{data['generated_at']}")
        L.append(f"  在岗 {t['active']} / 共 {t['employees']} 人"
                 f"　待命 {t['idle']}　未开工 {t['never']}　异常 {t['error']}")
        L.append(f"  累计调用 {t['calls']} 次　token {t['tokens']:,}"
                 f"　花费 ¥{t['cost_yuan']:.4f}　产出 {t['output_chars']:,} 字")
        if t["slacking"]:
            L.append(f"  ⚠️ 偷懒指数 ≥50 的有 {t['slacking']} 个岗位")
        L.append("")

        if not data["has_data"]:
            L.append("  ⚠️ 还没有工作台账数据。")
            L.append("     跑一次 `python run.py today` 或 `python run.py ask A07 \"...\"`")
            L.append("     之后这里就会有每个员工的真实状态。")
            L.append("")
            return "\n".join(L)

        icon = {"干活中": "🟢", "刚完工": "🔵", "待命": "🟡",
                "未开工": "⚪", "异常": "🔴"}

        def _w(s):
            """显示宽度：中文算 2 列。"""
            return sum(1 if ord(ch) < 128 else 2 for ch in s)

        def _pad(s, n):
            return s + " " * max(0, n - _w(s))

        L.append("  " + _pad("岗位", 16) + _pad("状态", 10)
                 + _pad("偷懒", 7) + _pad("调用", 7)
                 + _pad("token", 11) + _pad("产出字", 10) + "完成")
        L.append("  " + "-" * 74)

        for r in sorted(data["rows"], key=lambda x: (x["state"], -x["calls"])):
            label = f"{r['code']} {r['name']}"
            # 状态列用纯中文标签，不用 emoji —— Windows 终端里 emoji 宽度不稳定，
            # 会把整张表挤歪。颜色信息在网页版里有，这里保证对齐可读。
            state_text = r['state']
            L.append("  " + _pad(label, 16)
                     + _pad(state_text, 10)
                     + _pad(str(r["slacking"]), 7)
                     + _pad(str(r["calls"]), 7)
                     + _pad(f"{r['tokens']:,}", 11)
                     + _pad(f"{r['output_chars']:,}", 10)
                     + r["completion"])
        L.append("")

        # 需要关注的
        attention = [r for r in data["rows"]
                     if r["slacking"] >= 50 or r["state"] == "异常"
                     or r["problems"]]
        if attention:
            L.append("  【需要你关注】")
            L.append("  " + "-" * 70)
            for r in attention:
                L.append(f"  {r['code']} {r['name']}　偷懒指数 {r['slacking']}")
                for reason in r["slack_reasons"][:3]:
                    L.append(f"      · {reason}")
                for p in r["problems"][:2]:
                    L.append(f"      · {p}")
            L.append("")

        lr = data["last_run"]
        if lr:
            L.append("  【最近一次链路】")
            L.append(f"    {lr['run_id']}　完成 {lr['steps_ok']}/{lr['steps_total']} 步"
                     f"　{'✅ 跑完' if lr['complete'] else '⚠️ 没跑完'}")
            if lr["roles_missing"]:
                L.append(f"    缺：{', '.join(lr['roles_missing'])}")
            L.append("")
        return "\n".join(L)

    # ---- HTML 主页 -------------------------------------------------

    def render_html(self, data):
        """生成一个自包含的 HTML 主页（双击就能看）。"""
        t = data["totals"]
        state_color = {"干活中": "#16a34a", "刚完工": "#2563eb",
                       "待命": "#d97706", "未开工": "#94a3b8", "异常": "#dc2626"}

        cards = []
        for r in data["rows"]:
            c = state_color.get(r["state"], "#64748b")
            slack_color = ("#dc2626" if r["slacking"] >= 50 else
                           "#d97706" if r["slacking"] >= 25 else "#16a34a")
            problems = "".join(
                f'<li>{p}</li>' for p in r["problems"]) or "<li class=ok>无</li>"
            reasons = "".join(
                f'<li>{x}</li>' for x in r["slack_reasons"]) or "<li class=ok>无</li>"
            last = r["last_ts"]
            cards.append(f"""
<div class="card">
  <div class="cardtop">
    <div class="code">{r['code']}</div>
    <div class="namebox">
      <div class="name">{r['name']}</div>
      <div class="layer">{r['layer']} · 自动化 {r['automation']}</div>
    </div>
    <div class="badge" style="background:{c}">{r['state']}</div>
  </div>
  <div class="metrics">
    <div class="m"><span class="mv">{r['calls']}</span><span class="ml">调用</span></div>
    <div class="m"><span class="mv">{r['tokens']:,}</span><span class="ml">token</span></div>
    <div class="m"><span class="mv">¥{r['cost_yuan']:.4f}</span><span class="ml">花费</span></div>
    <div class="m"><span class="mv">{r['output_chars']:,}</span><span class="ml">产出字</span></div>
    <div class="m"><span class="mv">{r['output_files']}</span><span class="ml">文件</span></div>
    <div class="m"><span class="mv" style="color:{slack_color}">{r['slacking']}</span><span class="ml">偷懒指数</span></div>
  </div>
  <div class="sec">
    <div class="lab">最近工作</div>
    <div class="txt">{r['last_task']}</div>
    <div class="txt dim">{last}　耗时 {r['duration_s']}s</div>
  </div>
  <div class="sec">
    <div class="lab">完成情况</div>
    <div class="txt">{r['completion']}{'　应产出：' + r['expect'] if r['expect'] else ''}</div>
  </div>
  <div class="sec">
    <div class="lab">偷懒原因</div>
    <ul class="ul">{reasons}</ul>
  </div>
  <div class="sec">
    <div class="lab">遇到的问题</div>
    <ul class="ul">{problems}</ul>
  </div>
</div>""")

        if data["has_data"]:
            body = f"""
<div class="summary">
  <div class="stat"><span class="sv">{t['active']}</span><span class="sl">在岗</span></div>
  <div class="stat"><span class="sv">{t['employees']}</span><span class="sl">总员工</span></div>
  <div class="stat"><span class="sv">{t['calls']}</span><span class="sl">累计调用</span></div>
  <div class="stat"><span class="sv">{t['tokens']:,}</span><span class="sl">总 token</span></div>
  <div class="stat"><span class="sv">¥{t['cost_yuan']:.4f}</span><span class="sl">总花费</span></div>
  <div class="stat"><span class="sv">{t['output_chars']:,}</span><span class="sl">产出字数</span></div>
  <div class="stat warn"><span class="sv">{t['never']}</span><span class="sl">未开工</span></div>
  <div class="stat {'bad' if t['slacking'] else ''}"><span class="sv">{t['slacking']}</span><span class="sl">偷懒≥50</span></div>
</div>"""

        lr = data["last_run"]
        if lr:
            body += f"""
<div class="runbox">
  <div class="runhead">最近一次链路运行</div>
  <div class="runbody">{lr['run_id']}　完成 <b>{lr['steps_ok']}/{lr['steps_total']}</b> 步
  {'<span class="good">✅ 全部跑完</span>' if lr['complete'] else '<span class="bad">⚠️ 没跑完</span>'}
  {('　缺：' + ', '.join(lr['roles_missing'])) if lr['roles_missing'] else ''}</div>
</div>"""
        else:
            body += """
<div class="runbox">
  <div class="runhead">最近一次链路运行</div>
  <div class="runbody dim">还没有链路运行记录。跑一次 <code>python run.py today</code> 就会出现。</div>
</div>"""

        body += '<div class="grid">' + "".join(cards) + "</div>"

        if not data["has_data"]:
            body = """
<div class="empty">
  <h2>还没有工作台账数据</h2>
  <p>面板的所有数字都来自真实调用记录，不填假数据。</p>
  <p>跑一条命令就会有：</p>
  <pre>python run.py today --topic "你的选题"
python run.py ask A07 "帮我写一篇关于选题库的文章"</pre>
  <p>然后再跑 <code>python run.py board --html</code>，面板就会活起来。</p>
</div>""" + body

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 员工状态墙</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
background:#f1f5f9;color:#0f172a;line-height:1.6;padding:24px}}
.wrap{{max-width:1400px;margin:0 auto}}
h1{{font-size:24px;margin-bottom:4px}}
.sub{{color:#64748b;font-size:13px;margin-bottom:20px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:18px}}
.stat{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center}}
.stat.warn{{border-color:#fbbf24;background:#fffbeb}}
.stat.bad{{border-color:#fca5a5;background:#fef2f2}}
.sv{{display:block;font-size:22px;font-weight:700;color:#0f172a}}
.sl{{display:block;font-size:12px;color:#64748b;margin-top:2px}}
.runbox{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 18px;margin-bottom:18px}}
.runhead{{font-weight:600;font-size:14px;margin-bottom:4px}}
.runbody{{font-size:13px;color:#475569}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px;
box-shadow:0 1px 3px rgba(15,23,42,.06)}}
.cardtop{{display:flex;align-items:center;gap:12px;padding-bottom:12px;border-bottom:1px solid #f1f5f9}}
.code{{background:#0f172a;color:#fff;border-radius:8px;padding:6px 9px;font-weight:700;font-size:13px}}
.namebox{{flex:1;min-width:0}}
.name{{font-weight:600;font-size:15px}}
.layer{{font-size:12px;color:#94a3b8}}
.badge{{color:#fff;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:600;white-space:nowrap}}
.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:12px 0;
border-bottom:1px solid #f1f5f9}}
.m{{text-align:center}}
.mv{{display:block;font-size:16px;font-weight:600}}
.ml{{display:block;font-size:11px;color:#94a3b8}}
.sec{{padding-top:10px}}
.lab{{font-size:11px;color:#94a3b8;letter-spacing:.5px;margin-bottom:3px}}
.txt{{font-size:13px;color:#334155;word-break:break-all}}
.dim{{color:#94a3b8;font-size:12px}}
.ul{{margin-left:16px;font-size:12px;color:#b91c1c}}
.ul li.ok{{color:#16a34a}}
.good{{color:#16a34a;font-weight:600}}
.bad{{color:#dc2626;font-weight:600}}
.empty{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:28px;margin-bottom:18px}}
.empty h2{{font-size:18px;margin-bottom:8px}}
.empty p{{color:#475569;font-size:14px;margin-bottom:8px}}
pre{{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:8px;font-size:13px;
overflow-x:auto;margin:10px 0}}
code{{background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:13px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI 员工状态墙</h1>
  <div class="sub">数据来自 data/logs/telemetry.jsonl 真实调用台账 · 生成于 {data['generated_at']}</div>
  {body}
</div>
</body>
</html>"""

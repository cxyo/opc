"""AI 员工控制台（网页版）。

回答「我怎么给 AI 员工下达指令」——不用记命令，打开网页点选就行。

三个页面合一（单文件，双击即开）：
    1. 状态墙   —— 22 个员工的实时状态、token、产出、偷懒指数
    2. 派活台   —— 选岗位 + 写指令 + 一键下发
    3. 台账     —— 每一次调用的明细

数据来源：
    状态与台账 → data/logs/telemetry.jsonl
    产出文件   → data/outputs/
    岗位清单   → 07_AI员工/01_Bot提示词/

派活按钮怎么工作：
    控制台本身不能调 API（浏览器有跨域限制），所以它把指令写进
    data/state/orders.jsonl，然后由 `python run.py console` 轮询执行。
    这也符合项目的安全原则：所有真实调用都发生在 Python 侧，
    浏览器只是指挥台。
"""

import json
import os
from datetime import datetime
from typing import Any, Dict

from . import board as boardmod
from . import roles as rolesmod
from . import telemetry

# 派活队列文件
ORDERS_FILE = "orders.jsonl"
DONE_FILE = "orders_done.jsonl"


def orders_path(state_dir):
    return os.path.join(state_dir, ORDERS_FILE)


def done_path(state_dir):
    return os.path.join(state_dir, DONE_FILE)


def submit_order(state_dir, code, task, tier="create", use_kb=True,
                 save_as="", temp=0.7, extra=None):
    """把一条指令写进派活队列。返回订单 id。

    extra：附加字段（如返工单的 revise_from / instruction），
    会一起写进队列文件，worker 才能知道这单是干啥的。
    """
    oid = f"ord_{datetime.now().strftime('%H%M%S')}_{os.getpid() % 1000:03d}"
    rec = {
        "id": oid,
        "code": code,
        "task": task,
        "tier": tier,
        "use_kb": bool(use_kb),
        "save_as": save_as,
        "temp": temp,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",
    }
    if extra:
        rec.update(extra)
    os.makedirs(state_dir, exist_ok=True)
    with open(orders_path(state_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_orders(state_dir):
    """读全部订单（未执行的在前）。"""
    out = []
    for fn, key in ((ORDERS_FILE, "orders"), (DONE_FILE, "done")):
        p = os.path.join(state_dir, fn)
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    return out


def pending_orders(state_dir):
    return [o for o in read_orders(state_dir) if o.get("status") == "pending"]


def mark_done(state_dir, order, result_text, cost_yuan=0.0, output_file=""):
    """订单执行完，挪到已处理文件。"""
    rec = dict(order)
    rec["status"] = "done"
    rec["done_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec["result_preview"] = (result_text or "")[:2000]
    rec["result_chars"] = len(result_text or "")
    rec["output_file"] = output_file
    rec["cost_yuan"] = round(float(cost_yuan or 0.0), 6)
    with open(done_path(state_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 从待办里移除
    p = orders_path(state_dir)
    if os.path.exists(p):
        lines = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("id") != order.get("id"):
                    lines.append(line)
        with open(p, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")


def render_console(cfg, board_data=None):
    """生成控制台 HTML。"""
    registry = rolesmod.RoleRegistry(cfg["paths"]["roles"])
    roles = []
    # 行业过滤：切换行业后，派活台只显示该行业相关的岗位。
    # 「通用办公提效」是默认行业，不过滤（什么都得能做）。
    try:
        from . import industry as indmod
        _ind = indmod.current_pack(cfg)
        _allow = _ind.get("roles") or []
        _filtered = _ind.get("key") != "general" and bool(_allow)
        # 行业包可以给岗位重新起名（头盔厂的 A13 叫「设备档案员」，
        # 而不是内容体系里的「商品官」），现场的人才看得懂。
        _rn = indmod.role_names(cfg)
    except Exception:
        _ind = {}
        _allow = []
        _filtered = False
        _rn = {}

    for r in registry.all():
        if _filtered and r.code not in _allow:
            continue
        roles.append({
            "code": r.code,
            "name": _rn.get(r.code, r.name),
            "layer": r.layer,
            "automation": r.automation,
            "sample": r.quick_questions[0] if r.quick_questions else "",
        })

    if board_data is None:
        b = boardmod.Board(cfg)
        board_data = b.build()

    # 行业过滤只作用于「人」和「岗位」，**不切「钱」和「量」**。
    #
    # 为什么：累计调用 / token / 花费是整个项目的经营数据。
    # 如果跟着行业切掉，切到头盔厂后页面只剩「3 次调用、¥0.004」，
    # 客户会以为整个 AI 员工体系只花了 4 毛钱 —— 跟真实的 ¥1.24 差 300 倍。
    # （这是实测踩过的坑：DeepSeek 平台账单显示 ¥0.61，
    #   控制台却显示 ¥0.0043，一查才知道是统计被行业过滤误伤了。）
    #
    # 所以做法是：状态墙卡片只列本行业岗位（这部分确实是「这个行业的员工」），
    # 统计数字保持全量，另外单独算一份「本行业小计」给客户看对比。
    _ind_totals = None
    if _filtered:
        _allow_set = set(_allow)
        _rows_all = board_data.get("rows", [])
        _ind_rows = [r for r in _rows_all if r.get("code") in _allow_set]
        board_data = dict(board_data)
        board_data["rows"] = _ind_rows
        _ind_totals = {
            "employees": len(_ind_rows),
            "calls": sum(r.get("calls", 0) for r in _ind_rows),
            "tokens": sum(r.get("tokens", 0) for r in _ind_rows),
            "cost_yuan": round(sum(r.get("cost_yuan", 0.0) for r in _ind_rows), 6),
            "output_chars": sum(r.get("output_chars", 0) for r in _ind_rows),
        }
        _lr = board_data.get("last_run")
        if _lr:
            _lr = dict(_lr)
            _lr["roles_done"] = [c for c in _lr.get("roles_done", []) if c in _allow_set]
            _lr["roles_missing"] = [c for c in _lr.get("roles_missing", []) if c in _allow_set]
            _lr["steps_ok"] = len(_lr["roles_done"])
            board_data["last_run"] = _lr

    # 状态墙卡片也用行业化岗位名（跟派活台保持一致）
    if _rn:
        for _r in board_data.get("rows", []):
            if _r.get("code") in _rn:
                _r["name"] = _rn[_r["code"]]

    state_color = {"干活中": "#16a34a", "刚完工": "#2563eb",
                   "待命": "#d97706", "未开工": "#94a3b8", "异常": "#dc2626"}
    cards = []
    for r in board_data["rows"]:
        c = state_color.get(r["state"], "#64748b")
        sc = ("#dc2626" if r["slacking"] >= 50 else
              "#d97706" if r["slacking"] >= 25 else "#16a34a")
        rcode = r["code"]
        rname = r["name"]
        # 有产出的岗位给一个直达按钮
        out_btn = ""
        if r.get("output_chars", 0) > 0:
            out_btn = (f'<button class="mini" onclick="event.stopPropagation();'
                       f'openByRole(\'{rcode}\')">看产出</button>')
        cards.append(f"""
<div class="card" data-code="{rcode}" onclick="pick('{rcode}','{rname}')">
  <div class="ct">
    <div class="cd">{rcode}</div>
    <div class="cn"><div class="nm">{rname}</div><div class="ly">{r['layer']}</div></div>
    <div class="bd" style="background:{c}">{r['state']}</div>
  </div>
  <div class="mx">
    <span>{r['calls']}次</span><span>{r['tokens']:,}tok</span>
    <span>¥{r['cost_yuan']:.4f}</span><span>{r['output_chars']:,}字</span>
    <span style="color:{sc}">偷懒{r['slacking']}</span>
    {out_btn}
  </div>
</div>""")

    opts = "\n".join(
        f'<option value="{r["code"]}">{r["code"]} · {r["name"]}（{r["layer"]}）</option>'
        for r in roles)
    role_json = json.dumps(roles, ensure_ascii=False)

    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "console_template.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    # 实时看板的步骤链：跟着行业走。
    # 内容类行业用默认的内容生产 10 步，
    # 现场服务类行业（机器人、头盔厂）用 pack.json 里定义的岗位链。
    try:
        from . import industry as indmod
        _chain = indmod.chain(cfg)
    except Exception:
        _chain = []
    role_names = {r["code"]: r["name"] for r in roles}

    # 按天拆分：跟模型平台账单对账时用得上。
    # 平台账单是「按天 + 按 API Key」，本地台账也是按天，
    # 两边一对就知道差在哪天、差多少次。
    try:
        from . import telemetry as telmod
        _recs = telmod.read_all(cfg["paths"]["logs"])
    except Exception:
        _recs = []
    _byday: Dict[str, Dict[str, Any]] = {}
    for _r in _recs:
        _d = (_r.get("ts") or "")[:10] or "未知"
        _b = _byday.setdefault(_d, {"calls": 0, "tokens": 0,
                                   "cost_yuan": 0.0, "chars": 0})
        _b["calls"] += 1
        _b["tokens"] += int(_r.get("tokens") or 0)
        _b["cost_yuan"] += float(_r.get("cost_yuan") or 0)
        _b["chars"] += int(_r.get("output_chars") or 0)
    _days = [{"date": k, "calls": v["calls"], "tokens": v["tokens"],
              "cost": round(v["cost_yuan"], 6), "chars": v["chars"]}
             for k, v in sorted(_byday.items())]

    total = board_data["totals"]
    repl = {
        "%%OPTS%%": opts,
        "%%ROLE_JSON%%": role_json,
        "%%CARDS%%": "".join(cards),
        "%%T_ACTIVE%%": str(total["active"]),
        "%%T_EMPLOYEES%%": str(total["employees"]),
        "%%T_CALLS%%": str(total["calls"]),
        "%%T_TOKENS%%": f"{total['tokens']:,}",
        "%%T_COST%%": f"{total['cost_yuan']:.4f}",
        "%%T_OUTCHARS%%": f"{total['output_chars']:,}",
        "%%T_NEVER%%": str(total["never"]),
        "%%T_ERROR%%": str(total["error"]),
        "%%T_GENERATED%%": str(board_data["generated_at"]),
        "%%INDUSTRY%%": f"{_ind.get('icon', '📦')} {_ind.get('name', '通用办公提效')}",
        "%%IND_COUNT%%": str(len(roles)),
        "%%LV_CHAIN%%": json.dumps(_chain, ensure_ascii=False),
        "%%LV_ROLES%%": json.dumps(role_names, ensure_ascii=False),
        "%%IND_CALLS%%": (str(_ind_totals["calls"]) if _ind_totals else "—"),
        "%%IND_TOKENS%%": (f"{_ind_totals['tokens']:,}" if _ind_totals else "—"),
        "%%IND_COST%%": (f"{_ind_totals['cost_yuan']:.4f}" if _ind_totals else "—"),
        "%%IND_CHARS%%": (f"{_ind_totals['output_chars']:,}" if _ind_totals else "—"),
        "%%LEDGER_DAYS%%": json.dumps(_days, ensure_ascii=False),
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


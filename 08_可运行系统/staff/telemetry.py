"""员工工作台账（埋点层）。

为什么需要这个文件
------------------
以前系统只记 token（`data/logs/usage_*.csv`），记的是"花了多少钱"，
但没有记"谁干的、干得怎么样、出了什么问题"。
所以面板上只能显示钱，显示不出状态。

这里补上第二本账：`data/logs/telemetry.jsonl`
每一行 = 一次岗位调用的一笔完整记录：

    时间 | 岗位 | 干了什么 | 用了多少 token | 花了多少钱
    | 耗时多久 | 产出多少字 | 产出在哪个文件 | 遇到什么问题

怎么用
------
    from . import telemetry
    tr = telemetry.Recorder()
    tr.begin("A07", "撰写初稿", tier="create")
    ...干活...
    tr.end(status="ok", output_chars=1800, output_file="xxx.md", issues=[])

面板（`run.py board`）读这份台账，判断每个岗位：
    - 在不在干活（最近一次记录距今多久）
    - 有没有偷懒（输出过短、推回待补、报错）
    - 产出多少（字数 + 文件数）
    - 完成没完成（链路里有没有跑到位）

只记事实，不做评价。评价留给 board.py。
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime

# 一次运行一个 run_id，用来把 8 步链路归成一组
_CURRENT_RUN = None


def new_run_id(prefix="run"):
    """生成一个运行编号。同一次流水线共用一个。"""
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"


def set_run(run_id):
    """设定当前运行编号（供 pipeline 在链路开始时调用）。"""
    global _CURRENT_RUN
    _CURRENT_RUN = run_id
    return run_id


def current_run():
    return _CURRENT_RUN


class Recorder:
    """工作台账记录器。一次调用一个实例。"""

    def __init__(self, logs_dir):
        self.logs_dir = logs_dir
        self.path = os.path.join(logs_dir, "telemetry.jsonl")
        self._lock = threading.Lock()
        self._started = None
        self._meta = {}

    # ---- 生命周期 --------------------------------------------------

    def begin(self, role, task, tier="", model="", run_id=None):
        """标记一次调用开始。返回 self 以便链式调用。"""
        self._started = time.time()
        self._meta = {
            "role": role,
            "task": task,
            "tier": tier,
            "model": model,
            "run_id": run_id or _CURRENT_RUN or "",
        }
        return self

    def end(self, status="ok", tokens=0, prompt_tokens=0, completion_tokens=0,
            cost_yuan=0.0, output_chars=0, output_file="", issues=None,
            error=""):
        """标记一次调用结束并落盘。返回落盘后的记录 dict。"""
        dur = round(time.time() - self._started, 2) if self._started else 0.0
        rec = dict(self._meta)
        rec.update({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "duration_s": dur,
            "tokens": int(tokens or 0),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "cost_yuan": round(float(cost_yuan or 0.0), 6),
            "output_chars": int(output_chars or 0),
            "output_file": output_file,
            "issues": list(issues or []),
            "error": error,
        })
        self._write(rec)
        self._started = None
        self._meta = {}
        return rec

    def fail(self, error, issues=None):
        """快捷失败记录。"""
        return self.end(status="error", error=str(error)[:500],
                        issues=issues or ["执行异常"])

    # ---- 落盘 ------------------------------------------------------

    def _write(self, rec):
        try:
            os.makedirs(self.logs_dir, exist_ok=True)
            line = json.dumps(rec, ensure_ascii=False)
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            # 台账写不进去不能影响主流程
            pass


# ---- 读取 ---------------------------------------------------------

def read_all(logs_dir, limit=None):
    """读全部台账记录。返回 list[dict]，按时间正序。"""
    p = os.path.join(logs_dir, "telemetry.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    if limit:
        out = out[-limit:]
    return out


def detect_issues(content):
    """从产出内容里找出客观问题信号。

    只识别有明确文本证据的问题，不做主观评价：
    - 空产出
    - 待补标记（把活推回给人）
    - 截断迹象（以省略号或未闭合的代码块结尾）
    - 模型自述失败
    """
    issues = []
    if not content or not content.strip():
        return ["空产出"]
    text = content.strip()

    pending = text.count("【待补") + text.count("【待填") + text.count("[待补") + text.count("TODO")
    if pending:
        issues.append(f"{pending} 处待补/待填标记")

    if len(text) < 50:
        issues.append(f"产出过短（{len(text)} 字）")

    tail = text[-40:]
    if "```" in text and tail.count("```") % 2 == 1:
        issues.append("疑似输出被截断（代码块未闭合）")
    if tail.endswith("...") or tail.endswith("…"):
        issues.append("疑似输出被截断")

    low = text.lower()
    for marker in ("作为一个ai", "作为语言模型", "我无法完成", "抱歉，我不能"):
        if marker in low:
            issues.append(f"模型自述未完成（{marker}）")
            break

    return issues


def summarize(records):
    """把台账压成按岗位聚合的统计。"""
    by_role = {}
    for r in records:
        code = r.get("role") or "-"
        a = by_role.setdefault(code, {
            "role": code,
            "calls": 0,
            "errors": 0,
            "tokens": 0,
            "cost_yuan": 0.0,
            "output_chars": 0,
            "issues": [],
            "last_ts": "",
            "tasks": [],
        })
        a["calls"] += 1
        if r.get("status") == "error":
            a["errors"] += 1
        a["tokens"] += int(r.get("tokens") or 0)
        a["cost_yuan"] += float(r.get("cost_yuan") or 0.0)
        a["output_chars"] += int(r.get("output_chars") or 0)
        for i in r.get("issues") or []:
            if i not in a["issues"]:
                a["issues"].append(i)
        ts = r.get("ts") or ""
        if ts > a["last_ts"]:
            a["last_ts"] = ts
        task = r.get("task") or ""
        if task and task not in a["tasks"]:
            a["tasks"].append(task)
    for a in by_role.values():
        a["cost_yuan"] = round(a["cost_yuan"], 6)
    return by_role

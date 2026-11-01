# -*- coding: utf-8 -*-
"""实时事件总线。

给「实时看板」供数据：网页通过 SSE 订阅 `/api/live/stream`，
后端任何 AI 调用、派活、工作流步骤、重试、失败、产出，
都往这里发一条事件，前端立刻收到并渲染。

为什么单独一个模块：
  事件来源分散在 llm / pipeline / workflow / console 四处，
  如果每处都直接写 SSE 通道，代码会碎掉。
  这里做一层「广播中心」：谁都能 publish，页面按订阅收。

事件格式（统一）：
    {
      "id":    自增序号,
      "ts":    时间戳,
      "type":  事件类型,
      "level": info | good | warn | bad,
      "stage": 阶段名（工作流步骤 key），
      "role":  岗位编号（A01…）,
      "msg":   人话描述,
      "data":  附加数据 {},
      "elapsed": 毫秒耗时（可选）
    }

没有第三方依赖，纯标准库（queue + threading）。
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, List, Optional


class EventBus:
    """线程安全的事件广播中心。"""

    def __init__(self, history: int = 400):
        self._lock = threading.Lock()
        self._subscribers: List["queue.Queue"] = []
        self._history: List[Dict[str, Any]] = []
        self._history_max = history
        self._seq = 0

    # ---------------- 发布 ----------------

    def publish(self, type_: str, msg: str = "", level: str = "info",
                stage: str = "", role: str = "",
                data: Optional[Dict[str, Any]] = None,
                elapsed: Optional[int] = None) -> Dict[str, Any]:
        """广播一条事件。返回事件本体（调用方可用它拿 id）。"""
        with self._lock:
            self._seq += 1
            ev = {
                "id": self._seq,
                "ts": time.time(),
                "time": time.strftime("%H:%M:%S"),
                "type": type_,
                "level": level,
                "stage": stage,
                "role": role,
                "msg": msg,
                "data": data or {},
            }
            if elapsed is not None:
                ev["elapsed"] = int(elapsed)
            self._history.append(ev)
            if len(self._history) > self._history_max:
                del self._history[: len(self._history) - self._history_max]
            targets = list(self._subscribers)

        for q in targets:
            try:
                q.put_nowait(ev)
            except queue.Full:
                # 订阅者太慢，丢弃这一条，不能阻塞发布方
                pass
        return ev

    # ---------------- 订阅 ----------------

    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def recent(self, limit: int = 80) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history[-limit:])

    def clear(self) -> None:
        with self._lock:
            self._history.clear()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


# 全局单例。整个进程共用。
BUS = EventBus()


# ---------------- 便捷封装 ----------------
# 业务代码只要 bus.emit("ai_call", "正在调用 A03 情报官", stage="intel")
# 不需要关心 level / data 这些细节。

# ---------------- 岗位显示名 ----------------
# 埋点发生在各处（pipeline / workflow / console_server），
# 它们只知道岗位编号（A01…）。换行业后，同一个编号在不同行业
# 代表不同的活：内容体系里 A13 是「商品官」，
# 头盔厂里 A13 是「设备档案员」。
# 统一在这里做一次名字替换，事件里的岗位名就跟着行业变。
_ROLE_NAMES: Dict[str, str] = {}


def set_role_names(mapping: Optional[Dict[str, str]]) -> None:
    """设置当前行业的岗位显示名。切换行业时调用。"""
    global _ROLE_NAMES
    _ROLE_NAMES = dict(mapping or {})


def role_name(code: str, fallback: str = "") -> str:
    """取岗位的当前显示名。"""
    return _ROLE_NAMES.get(code) or fallback or code


def emit(type_: str, msg: str = "", level: str = "info", **kw) -> Dict[str, Any]:
    return BUS.publish(type_, msg, level, **kw)


def emit_step(stage: str, name: str, status: str = "running",
              msg: str = "", data: Optional[Dict[str, Any]] = None,
              **kw) -> Dict[str, Any]:
    """工作流步骤事件。status: running | ok | fail | retry"""
    level = {"running": "info", "ok": "good", "fail": "bad",
             "retry": "warn"}.get(status, "info")
    default = {
        "running": f"▶ {name} 执行中",
        "ok": f"✔ {name} 完成",
        "fail": f"✘ {name} 失败",
        "retry": f"↻ {name} 重试中",
    }.get(status, name)
    payload = {"status": status, "name": name}
    if data:
        payload.update(data)
    return BUS.publish("step", msg or default, level,
                       stage=stage, data=payload, **kw)


def emit_ai(role: str, name: str, status: str = "running",
             msg: str = "", data: Optional[Dict[str, Any]] = None,
             **kw) -> Dict[str, Any]:
    """单次 AI 调用事件。"""
    level = {"running": "info", "ok": "good", "fail": "bad",
             "retry": "warn"}.get(status, "info")
    # 岗位名按当前行业显示（切换行业后自动跟着变）
    name = _ROLE_NAMES.get(role) or name
    default = {
        "running": f"调用 {role} {name}",
        "ok": f"{role} {name} 返回",
        "fail": f"{role} {name} 失败",
        "retry": f"{role} {name} 重试",
    }.get(status, f"{role} {name}")
    payload = {"status": status, "name": name}
    if data:
        payload.update(data)
    return BUS.publish("ai_call", msg or default, level,
                       role=role, data=payload, **kw)


def emit_order(order_id: str, code: str, status: str = "running",
               msg: str = "", data: Optional[Dict[str, Any]] = None,
               **kw) -> Dict[str, Any]:
    """派活事件。

    data 参数会被合并进事件数据（不是覆盖），
    否则调用方传 data= 就会和内部的 data= 撞车报
    "got multiple values for keyword argument 'data'"。
    """
    level = {"running": "info", "ok": "good", "fail": "bad",
             "retry": "warn"}.get(status, "info")
    payload = {"status": status, "order_id": order_id}
    if data:
        payload.update(data)
    return BUS.publish("order", msg, level, role=code, data=payload, **kw)

# ---------------- 自检 ----------------

# 埋点失败记录：一旦有埋点抛异常，这里会留下痕迹。
# 为什么要留痕：
#   埋点全部包在 try/except 里（不能让埋点搞挂主流程），
#   但这会让「埋点坏了」变成一件看不见的事。
#   所以把失败原因记下来，概览页能显示，排查时一眼看到。
_EMIT_ERRORS: List[str] = []


def note_error(msg: str) -> None:
    """记一条埋点失败原因。"""
    _EMIT_ERRORS.append(msg)
    if len(_EMIT_ERRORS) > 20:
        del _EMIT_ERRORS[:-20]


def safe(fn, *args, **kwargs) -> Optional[Dict[str, Any]]:
    """安全地发一条事件。失败不影响主流程，但会留下痕迹。

    这是所有埋点调用的统一入口。业务代码只需要
        live.safe(live.emit_ai, code, name, "running", msg="...")
    不用自己写 try/except，也不会出现「静默失效」。
    """
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        note_error(f"{getattr(fn, '__name__', fn)}: {type(e).__name__}: {e}")
        return None


def emit_errors() -> List[str]:
    """最近的埋点失败原因（给概览页/排障用）。"""
    return list(_EMIT_ERRORS)


def self_check() -> Dict[str, Any]:
    """启动时自检：确认埋点链路是通的。

    为什么需要：
      埋点代码里到处是 `except Exception: pass`（防止埋点把主流程搞挂），
      但这会导致「埋点静默失效」—— 页面看着一切正常，就是没有事件。
      所以启动时主动打一发测试弹，确认总线能收到。
    """
    result = {"ok": False, "detail": ""}
    try:
        BUS.publish("selfcheck", "实时看板自检通过", "good", data={"probe": True})
        time.sleep(0.05)
        evs = BUS.recent()
        if evs and evs[-1].get("data", {}).get("probe"):
            result["ok"] = True
            result["detail"] = f"事件总线正常（缓冲 {len(evs)} 条）"
        else:
            result["detail"] = "事件写入后读不到"
    except Exception as e:
        result["detail"] = f"{type(e).__name__}: {e}"
    return result

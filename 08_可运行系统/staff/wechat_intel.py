# -*- coding: utf-8 -*-
"""A26 微信情报官 —— 服务层。

把数据层、关系识别、分析抽取串成一条流水线，供控制台 API 调用。

设计要点
--------
1. **缓存**：全量分析要读 1144 个会话、9 万条消息，太慢。
   结果落盘到 data/wechat_intel/情报缓存.json，
   同一个微信数据库（按 mtime + 大小判定）不重复算。
2. **增量同步**：微信在跑，库会变。
   sync() 检查源库 mtime，有变化就重算受影响会话。
3. **降级**：数据库没解密 / 微信没登录 → 明确报「读不到数据」，不返回空结果假装没事。

合规
----
    · 只读本机自己微信账号的数据
    · 原始记录不上传，密钥不出本机
    · 私人关系自动排除，不进业务视图
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

from . import wechat_analytics as wa
from . import wechat_data as wd
from . import wechat_relations as wr

CACHE_DIR = os.path.join("data", "wechat_intel")
CACHE_FILE = "情报缓存.json"
SNAPSHOT_FILE = "库指纹.json"

# 每个会话取多少条消息参与分析
MSG_LIMIT = 40

# 判定"久未联系"的天数
STALE_DAYS = 30


def _project_root() -> str:
    try:
        from . import config as cfgmod
        proj = getattr(cfgmod, "PROJECT", "")
        if proj and os.path.isdir(os.path.join(proj, "08_可运行系统")):
            return proj
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _cache_dir() -> str:
    d = os.path.join(_project_root(), "08_可运行系统", CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ 状态


# 分析逻辑版本号 —— 改了分类规则/词表/阶段判定就 +1。
#
# 为什么需要：源库指纹只反映"数据变没变"，
# 但**代码变了**结果也会变 —— 改完规则后如果只比源库指纹，
# 系统会拿旧缓存糊弄过去，老板看到的还是老结论。
# 这个版本号进指纹里，代码一改就自动重算。
LOGIC_VERSION = 4


def source_fingerprint() -> Dict[str, Any]:
    """源数据库指纹（路径 → 大小 + mtime）+ 逻辑版本。用于判断是否需要重算。"""
    fp: Dict[str, Any] = {"__logic__": LOGIC_VERSION}
    base = wd.plain_dir()
    if not os.path.isdir(base):
        return fp
    for root, _d, files in os.walk(base):
        for fn in files:
            if not fn.endswith(".db"):
                continue
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, base).replace("\\", "/")
            fp[rel] = [st.st_size, int(st.st_mtime)]
    return fp


def status() -> Dict[str, Any]:
    """当前状态：数据是否就绪、缓存是否新鲜。

    消息总数统计要扫 9 个库、上百张消息表（约 3 秒），
    而控制台每次打开页签都会调这里 —— 所以按库指纹做一层轻量缓存：
    源库没变就直接复用上次的统计结果。
    """
    ready = wd.is_ready()
    fp = source_fingerprint()
    snap_path = os.path.join(_cache_dir(), SNAPSHOT_FILE)
    snap = {}
    if os.path.isfile(snap_path):
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                snap = json.load(f)
        except Exception:
            snap = {}
    fresh = bool(snap) and snap == fp

    st: Dict[str, Any] = {}
    if ready:
        st = _stats_cached(fp)
    return {
        "就绪": ready,
        "数据目录": wd.plain_dir(),
        "缓存新鲜": fresh,
        "会话数": st.get("会话数", 0),
        "消息总数": st.get("消息总数", 0),
        "消息表数": st.get("消息表数", 0),
        "按库": st.get("按库", {}),
    }


_STATS_CACHE: Optional[Dict[str, Any]] = None


def _stats_cached(fp: Dict[str, Any]) -> Dict[str, Any]:
    """消息统计的进程内缓存（按源库指纹判定）。"""
    global _STATS_CACHE
    if _STATS_CACHE and _STATS_CACHE.get("_fp") == fp:
        return _STATS_CACHE["st"]
    st = wd.message_stats()
    _STATS_CACHE = {"_fp": fp, "st": st}
    return st


# ------------------------------------------------------------------ 主流程


def build(verbose: bool = True) -> Dict[str, Any]:
    """全量构建情报。"""
    t0 = time.time()
    if not wd.is_ready():
        return {"ok": False, "错误": "微信数据库尚未解密，请先运行解密流程",
                "数据目录": wd.plain_dir()}

    sessions = wd.load_session_index()
    contacts = wd.load_contacts()
    rooms = wd.load_rooms()
    members = wd.load_room_members()

    if verbose:
        print("会话 %d / 联系人 %d / 群 %d" % (len(sessions), len(contacts),
                                             len(rooms)))
    msgs = wd.iter_all_messages(limit_per_session=MSG_LIMIT)
    if verbose:
        print("消息 %d 条（%d 个会话）" % (sum(len(v) for v in msgs.values()),
                                          len(msgs)))

    classified = wr.classify_all(sessions, msgs, contacts, rooms, members)
    dash = wa.dashboard(classified, msgs, contacts)

    # 分类分布
    dist: Dict[str, int] = {}
    for r in classified:
        dist[r["关系"]] = dist.get(r["关系"], 0) + 1

    # 久未联系（有消息但超过 STALE_DAYS）
    now = time.time()
    stale = []
    for c in dash["客户分析"]:
        if c["消息数"] < 3:
            continue
        # 从溯源里取最后时间
        last = 0
        for e in c.get("溯源", []):
            try:
                ts = time.mktime(time.strptime(e["时间"], "%Y-%m-%d %H:%M"))
                last = max(last, ts)
            except Exception:
                pass
        if last and (now - last) > STALE_DAYS * 86400:
            stale.append({"名称": c["名称"], "阶段": c["进展阶段"],
                          "最后消息": c["溯源"][0]["时间"] if c["溯源"] else ""})
    dash["久未联系客户"] = stale

    # 待人工确认清单 —— 分类拿不准的会话集中列出来，
    # 让老板一眼看到"哪些需要我拍个板"，而不是被埋在 1144 个会话里。
    pending = []
    for r in classified:
        if r.get("关系") != "待人工确认":
            continue
        pending.append({
            "名称": r.get("名称", ""),
            "会话": r.get("username", ""),
            "置信度": r.get("置信度", 0),
            "消息数": len(msgs.get(r.get("username", ""), [])),
            "原因": (r.get("证据") or [""])[0],
            "证据": (r.get("证据") or [])[:3],
        })
    pending.sort(key=lambda x: -x["消息数"])

    out = {
        "ok": True,
        "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "耗时秒": round(time.time() - t0, 1),
        "覆盖": {
            "会话数": len(sessions),
            "有消息会话": len(msgs),
            "消息条数": sum(len(v) for v in msgs.values()),
            "联系人": len(contacts),
            "群": len(rooms),
        },
        "分类分布": dist,
        "客户数": dash["客户数"],
        "供应商数": dash["供应商数"],
        "内部人数": dash["内部人数"],
        "活跃客户数": dash["活跃客户数"],
        "有卡点的客户数": dash["有卡点的客户数"],
        "卡点汇总": dash["卡点汇总"],
        "客户分析": dash["客户分析"][:60],
        "供应商分析": dash["供应商分析"][:30],
        "员工分析": dash["员工分析"][:40],
        "久未联系客户": stale[:20],
        "待人工确认": pending[:40],
        "待人工确认数": len(pending),
        "置信度分布": _conf_dist(classified),
    }
    return out


def _conf_dist(classified: List[Dict[str, Any]]) -> Dict[str, int]:
    d = {"高(≥0.7)": 0, "中(0.4~0.7)": 0, "低(<0.4)": 0}
    for r in classified:
        c = r.get("置信度", 0)
        if c >= 0.7:
            d["高(≥0.7)"] += 1
        elif c >= 0.4:
            d["中(0.4~0.7)"] += 1
        else:
            d["低(<0.4)"] += 1
    return d


def save_cache(data: Dict[str, Any]) -> None:
    d = _cache_dir()
    with open(os.path.join(d, CACHE_FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(os.path.join(d, SNAPSHOT_FILE), "w", encoding="utf-8") as f:
        json.dump(source_fingerprint(), f, ensure_ascii=False, indent=1)


def load_cache() -> Optional[Dict[str, Any]]:
    p = os.path.join(_cache_dir(), CACHE_FILE)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_intel(force: bool = False, verbose: bool = False) -> Dict[str, Any]:
    """取情报。缓存新鲜就直接用，否则重建。"""
    if not force:
        c = load_cache()
        if c and c.get("ok"):
            snap_path = os.path.join(_cache_dir(), SNAPSHOT_FILE)
            try:
                with open(snap_path, "r", encoding="utf-8") as f:
                    snap = json.load(f)
            except Exception:
                snap = None
            if snap == source_fingerprint():
                c["缓存命中"] = True
                return c
    data = build(verbose=verbose)
    if data.get("ok"):
        save_cache(data)
    data["缓存命中"] = False
    return data


def sync(verbose: bool = True) -> Dict[str, Any]:
    """增量同步：微信数据变了就重算，否则直接返回缓存。"""
    before = load_cache()
    fp = source_fingerprint()
    snap_path = os.path.join(_cache_dir(), SNAPSHOT_FILE)
    old = {}
    if os.path.isfile(snap_path):
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = {}
    changed = []
    for k, v in fp.items():
        if k == "__logic__":
            continue
        if old.get(k) != v:
            changed.append(k)
    for k in old:
        if k == "__logic__":
            continue
        if k not in fp:
            changed.append(k + "(已删除)")
    # 分析逻辑版本变了 → 必须重算，哪怕源库一个字节没动
    logic_changed = old.get("__logic__") != LOGIC_VERSION
    if logic_changed:
        changed.insert(0, "分析规则升级(v%d→v%d)"
                       % (old.get("__logic__") or 0, LOGIC_VERSION))
    if not changed and before and before.get("ok"):
        if verbose:
            print("数据无变化，直接用缓存")
        before["缓存命中"] = True
        before["本次变化"] = []
        return before
    if verbose:
        if logic_changed:
            print("分析规则有更新，重算…")
        else:
            print("检测到 %d 个库有变化，重算…" % len(changed))
    data = build(verbose=verbose)
    if data.get("ok"):
        data["本次变化"] = changed
        save_cache(data)
    else:
        data["本次变化"] = changed
    data["缓存命中"] = False
    return data


# ------------------------------------------------------------------ 明细


def session_detail(username: str, limit: int = 60) -> Dict[str, Any]:
    """单个会话的明细：分类依据 + 命中消息。

    分类口径必须与 build() 完全一致（同一份 MSG_LIMIT 样本），
    否则会出现"列表里标着客户，点进去却显示待人工确认"的矛盾，
    老板会以为系统不可信。这里强制用 MSG_LIMIT 条参与分类，
    limit 只控制**返回多少条消息**用于查看。
    """
    sessions = wd.load_session_index()
    contacts = wd.load_contacts()
    rooms = wd.load_rooms()
    members = wd.load_room_members()
    s = next((x for x in sessions if x.get("username") == username), None)
    if not s:
        return {"ok": False, "错误": "会话不存在"}
    # 取消息：分类与展示都用同一份数据，保证口径一致。
    # 注意 load_messages 会遍历所有分片库，同一会话可能返回远多于 limit 条
    # （实测 120 条），所以这里不做切片 —— 分类信号必须用全量，
    # 否则会出现"列表里是客户、点进去变待人工确认"的矛盾。
    msgs_all = wd.load_messages(username, limit=limit)
    r = wr.classify_session(s, wd.display_name(username, contacts, rooms),
                             (rooms.get(username) or {}).get("name", ""),
                             msgs_all, contacts, members.get(username, []))
    r["username"] = username
    r["名称"] = wd.display_name(username, contacts, rooms)
    r["摘要"] = s.get("summary", "")
    r["最后活跃"] = s.get("last_ts", 0)
    r["消息"] = msgs_all[-limit:] if limit and limit < len(msgs_all) else msgs_all
    r["消息总数"] = len(msgs_all)
    if r.get("关系") == "客户":
        r["分析"] = wa.analyze_customer(r, msgs_all)
    elif r.get("关系") == "供应商":
        r["分析"] = wa.analyze_supplier(r, msgs_all)
    return r

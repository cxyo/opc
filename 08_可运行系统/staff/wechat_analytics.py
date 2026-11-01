# -*- coding: utf-8 -*-
"""A26 微信情报官 —— 事实抽取与分析。

从聊天记录里抽出**可核查的事实**，而不是泛泛的总结。

三类产出
--------
    客户线：对接了谁 / 进展到哪 / 卡在哪 / 为什么卡
    供应商线：货到哪一步 / 款到哪一步
    内部线：每人业务动向（挖掘客户数 / 拜访数 / 跟进事项）

铁律
----
    1. **每条结论必须有出处**（原文片段 + 时间 + 会话）
    2. **只用消息里真实出现的数字**，不推算、不虚构
    3. 抽不出来就写「未在聊天中提及」，**不猜**
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------------ 金额

AMOUNT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(万元|万|元|块|rmb|RMB|¥|￥)")
AMOUNT_BARE_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)")
NUM_UNIT_RE = re.compile(r"(\d+)\s*(台|套|个|件|人|次|条|批)")


def extract_amounts(text: str) -> List[Dict[str, Any]]:
    """抽金额。返回 [{值, 单位, 原文, 折合元}]"""
    out = []
    for m in AMOUNT_RE.finditer(text):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        unit = m.group(2)
        yuan = val * 10000 if unit in ("万元", "万") else val
        out.append({"值": val, "单位": unit, "折合元": yuan,
                    "原文": m.group(0)})
    for m in AMOUNT_BARE_RE.finditer(text):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        out.append({"值": val, "单位": "元", "折合元": val,
                    "原文": m.group(0)})
    return out


def extract_quantities(text: str) -> List[Dict[str, Any]]:
    """抽数量（几台设备、多少人）。"""
    out = []
    for m in NUM_UNIT_RE.finditer(text):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        out.append({"数": n, "单位": m.group(2), "原文": m.group(0)})
    return out


# ------------------------------------------------------------------ 状态

# 供应商/订单推进阶段 —— 按顺序判定，命中越靠后阶段越高
SUPPLIER_STAGES = [
    ("已交付", ["已到货", "已收货", "签收", "已送货", "到货了", "已到",
               "货到了", "入库", "已发货", "发出去了", "已寄出", "快递已发",
               "已经收到", "货到了这边", "物流显示已签收", "已签收",
               "到货签收", "货已到", "收到货了"]),
    ("安装调试中", ["正在安装", "安装中", "调试中", "正在调试", "现场安装",
                  "正在装", "在装", "安装师傅", "装好了在调", "调机",
                  "现场调试", "安装进度", "去安装"]),
    ("生产中", ["正在生产", "在生产", "生产中", "正在做", "开工",
               "赶制", "在做", "正在加工", "排产中", "在做货", "备货中",
               "正在备货", "在做样机", "生产进度"]),
    ("已下单", ["已下单", "已订货", "已采购", "订单已下", "已付定金",
               "已签合同", "下单了", "已经下单", "已下单了", "走采购流程",
               "已走采购", "下单成功", "已经订货", "合同已签",
               "已签合同了", "走完流程"]),
    ("已打样", ["样件已出", "样件做好了", "已打样", "样品已寄", "打样完成",
               "样机已到", "样机到了", "样品寄出", "样件确认", "打样确认",
               "样机做好", "样件寄出"]),
    ("打样中", ["正在打样", "打样中", "在打样", "开始打样", "打样单",
               "样机制作", "样件制作"]),
    ("询价中", ["报价中", "正在报价", "询价", "核价", "算价", "报价单",
               "报个价", "价格是多少", "多少钱一个", "询个价", "询价单",
               "担心报价", "单价是", "多少钱", "什么价", "价格怎么",
               "批量采购", "大量采购", "怎么拿货", "拿货价", "出厂价"]),
    ("洽谈中", ["能不能", "可不可以", "能不能做", "考虑一下", "请示一下",
               "问下领导", "回去商量", "先看看", "回头聊", "再议",
               "有想法吗", "能不能给我们"]),
]

# 款项阶段
PAYMENT_STAGES = [
    ("已结清", ["已结清", "已付清", "全款已付", "款已付清", "全部付清",
              "货款已付清", "已全额付款", "结尾款了"]),
    ("已付尾款", ["尾款已付", "已付尾款", "尾款已到", "付了尾款"]),
    ("已付定金", ["定金已付", "已付定金", "预付款已到", "已付预付款",
               "付了定金", "定金已经付", "订金已付", "先付了定金"]),
    ("已对账", ["对过账", "对账单已", "已对账", "月结对完", "账已核对"]),
    ("待付款", ["待付款", "还没付款", "款还没付", "待打款", "还没付",
              "未付款", "尾款没付", "钱还没打", "等付款"]),
]

# 客户推进阶段
CUSTOMER_STAGES = [
    ("已成交", ["已签合同", "合同已签", "已下单", "已成交", "已付定金",
               "合同签了"]),
    ("方案沟通", ["方案", "报价", "选型", "技术交流", "看样", "打样",
                "样机", "试机", "演示"]),
    ("初步接触", ["了解", "咨询", "问一下", "有需求", "需求"]),
]

# 卡点词 —— 为什么没成
BLOCKER_WORDS = [
    ("预算不足", ["预算不够", "预算不足", "价格太高", "太贵", "超预算",
                "预算有限"]),
    ("排期冲突", ["排不开", "档期", "排期", "没时间", "延后", "推迟",
                "往后放"]),
    ("技术问题", ["报错", "故障", "精度不够", "不稳定", "调试不通",
                "达不到", "有问题", "解决不了", "卡在"]),
    ("决策未定", ["再看看", "考虑考虑", "还没定", "内部讨论", "等领导",
                "定了再说", "再议"]),
    ("需求变更", ["改需求", "变更", "改成", "重新做", "改方案"]),
    ("付款问题", ["账期", "没付款", "催款", "发票问题", "对不上账"]),
    ("联系方式不畅", ["联系不上", "没回", "已读不回", "离职", "换人"]),
    ("产能不足", ["排不下", "产能", "做不完", "太忙", "人手不够"]),
]


def _match_stage(text: str, stages: List[Tuple[str, List[str]]]
                 ) -> List[Dict[str, Any]]:
    """按阶段顺序匹配，返回所有命中的阶段（按阶段先后）。"""
    out = []
    for stage, words in stages:
        hits = [w for w in words if w in text]
        if hits:
            out.append({"阶段": stage, "命中词": hits[:3]})
    return out


def _stage_by_time(messages: List[Dict[str, Any]],
                   stages: List[Tuple[str, List[str]]]
                   ) -> List[Dict[str, Any]]:
    """**按时间顺序**判定阶段推进。

    为什么不能直接用 latest_stage（取词表里最后一个命中的）：
      业务是**向前推进**的 —— 先询价、再下单、再打样、再到货。
      如果聊天里早期说"已下单"、后期说"已到货"，
      按词表顺序取会拿到靠前的"已下单"，等于告诉老板进度**倒退**了。

    做法：把消息按时间正序扫一遍，记录每个阶段**首次出现**的时间，
    最后返回按首次出现时间排序的阶段列表 → 最后一个就是当前阶段。
    """
    first_seen: Dict[str, Dict[str, Any]] = {}
    for m in messages:
        b = m.get("body") or ""
        if not b:
            continue
        for stage, words in stages:
            if stage in first_seen:
                continue
            hits = [w for w in words if w in b]
            if hits:
                first_seen[stage] = {"阶段": stage, "命中词": hits[:3],
                                     "时间": m.get("ts", 0)}
    return sorted(first_seen.values(), key=lambda x: x.get("时间", 0))


def latest_stage(stages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """取最靠后的阶段（列表已按阶段先后排列）。"""
    if not stages:
        return {}
    return stages[-1]


# ------------------------------------------------------------------ 抽取


def _fmt_ts(ts: int) -> str:
    import datetime
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def analyze_customer(session: Dict[str, Any],
                      messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """客户线分析：对接了谁、进展、卡点。"""
    texts = [(m.get("body") or "") for m in messages]
    joined = " ".join(texts)

    cust_stages = _stage_by_time(messages, CUSTOMER_STAGES)
    blockers = []
    for label, words in BLOCKER_WORDS:
        hits = [w for w in words if w in joined]
        if hits:
            blockers.append({"原因": label, "命中词": hits[:3]})

    amounts = extract_amounts(joined)
    quantities = extract_quantities(joined)

    # 找出卡点所在的原始消息（溯源）
    evidence = []
    for m in messages:
        b = m.get("body") or ""
        if not b:
            continue
        for label, words in BLOCKER_WORDS:
            if any(w in b for w in words):
                evidence.append({"时间": _fmt_ts(m["ts"]), "片段": b[:120],
                                 "原因": label})
                break
        if len(evidence) >= 5:
            break

    # 最新阶段也要溯源
    stage_ev = []
    if cust_stages:
        want = latest_stage(cust_stages)["阶段"]
        for m in reversed(messages):
            b = m.get("body") or ""
            for stage, words in CUSTOMER_STAGES:
                if stage == want and any(w in b for w in words):
                    stage_ev = [{"时间": _fmt_ts(m["ts"]), "片段": b[:120],
                                 "阶段": want}]
                    break
            if stage_ev:
                break

    return {
        "名称": session.get("名称", ""),
        "会话": session.get("username", ""),
        "关系": "客户",
        "置信度": session.get("置信度", 0),
        "进展阶段": latest_stage(cust_stages).get("阶段", "未明确"),
        "全部阶段": [s["阶段"] for s in cust_stages],
        "卡点": blockers,
        "涉及金额": amounts[:8],
        "涉及数量": quantities[:8],
        "消息数": len(messages),
        "最后活跃": _fmt_ts(session.get("最后活跃") or session.get("last_ts") or 0),
        "溯源": (stage_ev + evidence)[:8],
    }


def analyze_supplier(session: Dict[str, Any],
                     messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """供应商线分析：货到哪一步、款到哪一步。"""
    joined = " ".join((m.get("body") or "") for m in messages)

    goods = _stage_by_time(messages, SUPPLIER_STAGES)
    pays = _stage_by_time(messages, PAYMENT_STAGES)

    evidence = []
    for m in messages:
        b = m.get("body") or ""
        if any(any(w in b for w in ws) for _s, ws in SUPPLIER_STAGES) or \
           any(any(w in b for w in ws) for _s, ws in PAYMENT_STAGES):
            evidence.append({"时间": _fmt_ts(m["ts"]), "片段": b[:120]})
        if len(evidence) >= 6:
            break

    return {
        "名称": session.get("名称", ""),
        "会话": session.get("username", ""),
        "关系": "供应商",
        "置信度": session.get("置信度", 0),
        "货物阶段": latest_stage(goods).get("阶段", "未明确"),
        "货物全部阶段": [s["阶段"] for s in goods],
        "款项阶段": latest_stage(pays).get("阶段", "未明确"),
        "款项全部阶段": [s["阶段"] for s in pays],
        "涉及金额": extract_amounts(joined)[:8],
        "消息数": len(messages),
        "最后活跃": _fmt_ts(session.get("最后活跃") or session.get("last_ts") or 0),
        "溯源": evidence[:6],
    }


# 内部员工动态
EMP_ACTIONS = [
    ("开发客户", ["开发", "挖掘", "找客户", "新客户", "潜在客户", "意向客户",
                "对接客户", "拓客"]),
    ("拜访客户", ["拜访", "到厂", "去现场", "参观", "考察", "出差", "上门",
                "面谈", "接待"]),
    ("报价跟进", ["报价", "价格", "方案", "选型", "比价"]),
    ("跟进订单", ["订单", "合同", "定金", "发货", "交付", "验收"]),
    ("售后处理", ["售后", "维修", "故障", "调试", "返修", "投诉"]),
    ("内部协同", ["排产", "交接", "开会", "讨论", "对齐", "同步"]),
]


def analyze_employee(name: str, sessions: List[Dict[str, Any]],
                     all_msgs: Dict[str, List[Dict[str, Any]]],
                     cust_touch: Optional[List[str]] = None,
                     extra_msgs: Optional[List[Dict[str, Any]]] = None
                     ) -> Dict[str, Any]:
    """内部员工线：业务动向统计。

    「挖掘几个客户」= 在**客户会话里发过消息**的客户数量
    「拜访几个」= 消息里出现拜访类动词的次数

    注意 cust_touch / extra_msgs 来自客户会话 ——
    同事在客户群/私聊里的业务动作才是真的"在对接客户"，
    只看内部同事之间的聊天会漏掉全部对接行为。
    """
    cust_touch = cust_touch or []
    extra_msgs = extra_msgs or []
    visited = 0
    actions = {}
    ev = []
    # 动作统计要覆盖两处：这位同事自己的内部会话 + 他在客户会话里的发言
    own_msgs: List[Dict[str, Any]] = []
    for s in sessions:
        own_msgs.extend(all_msgs.get(s.get("username"), []))
    for m in own_msgs + extra_msgs:
        b = m.get("body") or ""
        for label, words in EMP_ACTIONS:
            if any(w in b for w in words):
                actions[label] = actions.get(label, 0) + 1
                if label == "拜访客户":
                    visited += 1
                    if len(ev) < 6:
                        ev.append({"时间": _fmt_ts(m["ts"]),
                                   "片段": b[:110], "动作": label})
    own = len(own_msgs)
    return {
        "姓名": name,
        "会话数": len(sessions),
        "对接客户数": len(set(cust_touch)),
        "对接客户": sorted(set(cust_touch))[:12],
        "拜访次数": visited,
        "动作分布": actions,
        "消息总量": own + len(extra_msgs),
        "客户侧消息数": len(extra_msgs),
        "溯源": ev,
    }


def summarize_employees(classified: List[Dict[str, Any]],
                        msgs: Dict[str, List[Dict[str, Any]]],
                        contacts: Optional[Dict[str, Dict[str, Any]]] = None
                        ) -> List[Dict[str, Any]]:
    """把内部会话按「会话对方」聚合成员工档案。

    关键：**跨会话关联**。
    员工档案来自「内部」私聊会话（私聊的 username 就是同事本人），
    但他对接的客户出现在「客户」会话里 —— 那边的消息发送者就是这位同事。
    所以要建一张 uid → 员工名 的表，再去客户会话里反查。

    员工 uid 的来源有两条，缺一不可：
      ① 「内部」私聊会话的 username —— 覆盖有单独私聊的同事
      ② 联系人表里备注/昵称带公司标识的 —— 覆盖只在客户群里出现的同事
         （这些人没有内部私聊会话，光靠 ① 会漏掉一大半）
    """
    contacts = contacts or {}
    by_person: Dict[str, List[Dict[str, Any]]] = {}
    emp_by_uid: Dict[str, str] = {}

    # ---- 来源 ①：内部私聊会话 ----
    for r in classified:
        if r.get("关系") != "内部":
            continue
        uid = r.get("username", "") or ""
        name = r.get("名称") or uid
        by_person.setdefault(name, []).append(r)
        # 只有私聊会话的 username 才是同事本人；群会话的 username 是群号
        if uid and not uid.endswith("@chatroom"):
            emp_by_uid[uid] = name

    # ---- 来源 ②：联系人表里带公司标识的同事 ----
    for uid, c in contacts.items():
        if not uid or uid.endswith("@chatroom"):
            continue
        remark = (c.get("remark") or "").strip()
        nick = (c.get("nick_name") or "").strip()
        label = "%s %s" % (remark, nick)
        if not label.strip():
            continue
        # 注意：「力生客户XXX」是**客户**标签不是同事 —— 客户自己的备注
        # 也常带公司名（说明是力生设备的客户），不能当成内部员工。
        if "客户" in remark:
            continue
        if any(tok in label for tok in ("力生", "卡诺普", "本公司")):
            # 名字统一取备注（用户自己打的标签最准）
            nm = (remark or nick or uid).strip()
            emp_by_uid.setdefault(uid, nm)
            by_person.setdefault(nm, [])

    # ---- 扫客户/供应商会话：谁在说话，就是哪位同事在对接 ----
    cust_touch: Dict[str, List[str]] = {}
    extra: Dict[str, List[Dict[str, Any]]] = {}
    for r in classified:
        rel = r.get("关系")
        if rel not in ("客户", "供应商"):
            continue
        su = r.get("username", "") or ""
        peer = r.get("名称") or su
        for m in msgs.get(su, []):
            sn = m.get("sender", "") or ""
            if not sn or sn == su:
                continue
            emp = emp_by_uid.get(sn)
            if not emp:
                continue
            cust_touch.setdefault(emp, []).append("%s(%s)" % (peer, rel))
            extra.setdefault(emp, []).append(m)

    out = []
    for name, sess in by_person.items():
        out.append(analyze_employee(name, sess, msgs,
                                    cust_touch.get(name, []),
                                    extra.get(name, [])))
    out.sort(key=lambda x: (-x["对接客户数"], -x["消息总量"]))
    return out


def dashboard(classified: List[Dict[str, Any]],
              msgs: Dict[str, List[Dict[str, Any]]],
              contacts: Optional[Dict[str, Dict[str, Any]]] = None
              ) -> Dict[str, Any]:
    """老板视角总览。"""
    cust = [r for r in classified if r.get("关系") == "客户"]
    sup = [r for r in classified if r.get("关系") == "供应商"]
    inn = [r for r in classified if r.get("关系") == "内部"]

    cust_an = [analyze_customer(r, msgs.get(r.get("username", ""), []))
               for r in cust]
    sup_an = [analyze_supplier(r, msgs.get(r.get("username", ""), []))
              for r in sup]
    emp_an = summarize_employees(classified, msgs, contacts)

    # 卡点汇总
    blocker_cnt: Dict[str, int] = {}
    for c in cust_an:
        for b in c.get("卡点", []):
            blocker_cnt[b["原因"]] = blocker_cnt.get(b["原因"], 0) + 1

    return {
        "客户数": len(cust),
        "供应商数": len(sup),
        "内部人数": len(inn),
        "客户分析": sorted(cust_an, key=lambda x: -x["消息数"]),
        "供应商分析": sorted(sup_an, key=lambda x: -x["消息数"]),
        "员工分析": emp_an,
        "卡点汇总": sorted(blocker_cnt.items(), key=lambda kv: -kv[1]),
        "有卡点的客户数": sum(1 for c in cust_an if c.get("卡点")),
        "活跃客户数": sum(1 for c in cust_an if c.get("消息数", 0) >= 10),
    }

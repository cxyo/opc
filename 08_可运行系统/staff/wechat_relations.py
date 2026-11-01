# -*- coding: utf-8 -*-
"""A26 微信情报官 —— 关系识别引擎。

把 308 个会话自动分成三类：**客户 / 供应商 / 公司内部**。

判定依据（全部来自真实数据，可溯源）
------------------------------------
信号 1：**备注标签** —— 最强信号
        用户自己打的备注就是业务标签，实测见过「力生客户」「力生马轶」
        「兄弟连李伟杰」这类，直接给出关系类型。

信号 2：**群名/昵称关键词** —— 强信号
        「售后群」「项目群」「生产运营」→ 内部/客户项目
        「采购」「供应商」「货源」→ 供应商

信号 3：**消息内容关键词** —— 中等信号，按会话累计
        报价/合同/打款 → 客户；进货/发货/到货/交期 → 供应商；
        排产/工位/上班/打卡 → 内部

信号 4：**群成员与本人的重叠** —— 辅助
        群成员里有多个带「力生」前缀的同事 → 内部群

信号 5：**消息方向与频次** —— 辅助
        对方主动找上门（我方回复少）→ 客户/供应商；同事日常 → 内部

打分为加权求和，输出 0~1 的置信度 + 命中的证据（可溯源）。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------------ 词典

# 客户信号
CUSTOMER_WORDS = [
    # 交易类
    "报价", "询价", "多少钱", "价格", "预算", "费用", "方案", "报价单",
    "合同", "签合同", "签约", "订单", "下单", "采购", "购买", "定制",
    "打款", "付款", "尾款", "定金", "预付款", "开票", "发票", "验收",
    "样机", "样品", "打样", "试制", "试机", "演示", "参观", "考察",
    "售后", "维修", "质保", "保修", "返修", "安装", "调试", "验收报告",
    "工期", "交期", "工期要求", "多少台", "几台", "台设备", "需求量",
    "合作", "意向", "需求", "需求分析", "招标", "比价", "付款方式",
    "模具", "夹具", "工装", "治具", "上下料", "打磨", "抛光", "机器人",
    "工作站", "产线", "自动化", "非标",
]
CUSTOMER_STRONG = ["客户", "甲方", "买家", "终端", "采购部", "询盘"]

# 供应商信号
#
# 分两档，原因是实测踩过的坑：
# 「PLC、传感器、机器人、机械臂」这类是**品类词**，
# 力生自己的技术同事在群里聊这些也会命中 → 把同事误判成供应商。
# 真正说明"对方是供应商"的，是**交易型信号**：
# 进货/货期/对账/月结/送货单 —— 出现这些才说明对方在卖货给我们。
SUPPLIER_DEAL_WORDS = [
    "进货", "采购", "采购部", "供应商", "货源", "厂家", "厂商", "货源",
    "货期", "交期", "备货", "库存", "现货", "打样费", "开模具", "模费",
    "治具费", "外协", "外发", "委外", "代加工", "贴牌",
    "对账单", "月结", "账期", "结算", "送货单", "物流单", "运单",
    "发货", "到货", "已到货", "已收货", "签收", "入库", "样品已寄",
    "厂价", "出厂价", "含税", "不含税", "开票", "发票",
]

# 品类词 —— 只作辅助（权重低），且必须配合交易型信号才算供应商
SUPPLIER_CATEGORY_WORDS = [
    "工厂", "车间", "产线", "打样", "配件", "耗材", "材料", "钢材",
    "铝材", "铸件", "标准件", "丝杠", "导轨", "减速机", "伺服", "电机",
    "PLC", "传感器", "气缸", "电缸", "夹爪", "打磨头", "砂轮", "布轮",
    "耗材订单", "机械臂", "机器人", "本体的", "六轴", "四轴", "示教器",
    "示教", "离线编程", "抓取", "夹持", "打磨机", "抛光机", "去毛刺",
    "毛刺", "安装调试", "现场安装", "外安装", "外包安装", "调试完成",
    "试运行", "电气", "线缆", "线束", "接插件", "拖链", "减速比", "负载",
    "臂展", "润滑油", "备件", "易损件", "保养", "换刀", "换型", "产能",
    "排产",
]
SUPPLIER_WORDS = SUPPLIER_DEAL_WORDS + SUPPLIER_CATEGORY_WORDS
# 「工厂」「车间」这类词太泛 —— 客户自己的工厂、酒店的厂区都会命中，
# 只有在和「供应商/厂商/货源」一起出现时才算供应商特征。
SUPPLIER_STRONG = ["供应商", "厂商", "货源", "供应商群"]

# 内部信号
INTERNAL_WORDS = [
    "排产", "工位", "上料", "换型", "换线", "点检", "保养", "维修单",
    "上班", "下班", "打卡", "考勤", "排班", "请假", "加班", "入职",
    "离职", "工资", "提成", "绩效", "奖金", "报销", "出差", "培训",
    "早会", "夕会", "周会", "月会", "日报", "周报", "月报", "KPI",
    "OKR", "复盘", "交接", "岗位", "招聘", "面试", "转正",
    "装配", "接线", "调试完成", "发货前", "装机", "试产",
    "内部", "厂里", "车间", "同事",
]
INTERNAL_STRONG = ["内部", "同事", "本公司", "我司", "我们公司"]

# 售后/项目群 —— 属于「客户侧」，但要看成员
SERVICE_GROUP_WORDS = ["售后", "服务群", "运维", "驻场", "保修"]

# 强业务词 —— 命中 1 个即可定性为业务关系（其余词需命中 ≥3 个）
STRONG_BUSINESS_WORDS = [
    # 客户侧
    "报价单", "签合同", "合同", "订单", "定金", "尾款", "预付款", "开票",
    "发票", "验收报告", "打样", "样机", "试制", "打样费", "模具费", "治具费",
    "招标", "比价", "询价单", "采购合同", "项目验收", "工期要求",
    # 供应商侧
    "供应商", "厂家直销", "发货单", "送货单", "对账单", "月结", "账期",
    "外协", "委外加工", "代加工", "打样费", "开模具",
    # 内部侧
    "排产", "工位", "点检", "交接单", "工时", "考勤", "排班", "绩效",
]

# 排除：明显无关的会话类型
EXCLUDE_NAMES = ["文件传输助手", "微信团队", "微信支付", "微信运动",
                 "微信开发者", "订阅号消息", "服务通知", "微信团队助手",
                 "腾讯", "微信收款助手", "微信支付凭证", "微信红包",
                 "广告", "小程序", "订阅号", "看一看", "拍一拍", "摇一摇"]

# 公司内部人名前缀（实测：力生智能）
COMPANY_TOKENS = ["力生", "本公司", "我们厂", "公司内部"]

# 内部同事的**昵称**特征（比备注更可靠 —— 用户打备注时可能只写「韩总」，
# 但微信昵称往往带完整部门和职位，如「韩磊（力生激光切割打磨机器人）」）
INTERNAL_NICK_TOKENS = ["力生", "卡诺普", "本公司"]

# ------------------------------------------------------------------ 排除

# 私人关系 —— 不是业务关系，必须剔除
PRIVATE_TAGS = ["爸爸", "妈妈", "老婆", "老公", "哥哥", "姐姐", "弟弟", "妹妹",
                "儿子", "女儿", "爷爷", "奶奶", "外公", "外婆", "舅舅", "姑姑",
                "叔叔", "阿姨", "伯父", "婶婶", "堂哥", "堂姐", "表哥", "表姐",
                "家人", "亲戚", "小学", "初中", "高中", "大学", "同学",
                "邻居", "房东", "业主群", "小区", "业主", "家族"]

# 纯社交/娱乐群 —— 不是业务关系
SOCIAL_TAGS = ["相亲相爱", "家族", "同学会", "婚礼", "生日会", "旅游", "亲子",
               "幼儿园", "宝宝", "萌宝", "广场舞", "钓鱼", "麻将", "棋牌",
               "养生", "减肥", "美食", "闲聊", "吐槽", "表情包", "段子"]

# 纯学习/工具类群 —— 不是业务关系（但也算「无关」而非「未识别」）
LEARNING_TAGS = ["资料库", "工具库", "资源", "课程", "学习", "训练营", "陪跑",
                  "答疑", "公开课", "读书", "书单", "模板", "素材", "教程",
                  "网盘", "自取", "领取", "福利", "抽奖", "秒杀",
                  # 实测误判修正：这些被当成客户，实为课程/粉丝群
                  "弟子班", "助教", "学员", "粉丝团", "实战分享", "实操落地",
                  "英荟", "班主任", "打卡群", "抄作业", "作业", "考试",
                  "证书", "结课", "开课", "报名", "作业本", "真题",
                  # 实测误判修正（第二轮）：这些是**服务方/培训方**的身份，
                  # 不是买方。聊天里聊到"订单""方案""自动化"就被当成客户，
                  # 实际是在给人家做客服、做培训、做职业规划。
                  "职业规划", "规划师", "客服", "培训", "讲师", "老师",
                  "导师", "教练", "助教", "班主任", "招生", "课程顾问",
                  "客诉", "售后部", "服务台"]

# 服务方/培训方身份 —— 命中即排除（不是客户）
SERVICE_PROVIDER_TAGS = ["职业规划", "规划师", "客服", "培训", "讲师",
                        "老师", "导师", "教练", "班主任", "招生",
                        "课程顾问", "服务台", "售后部"]

# 官方/系统会话
SYSTEM_TAGS = ["微信团队", "微信支付", "微信运动", "微信开发者", "订阅号",
               "服务通知", "微信收款助手", "微信红包", "腾讯", "小程序",
               "看一看", "拍一拍", "摇一摇", "广告", "视频号", "小程序助手",
               "WeChat", "微信客服", "文件传输助手", "微信团队助手"]


def _is_private(name: str, room_name: str) -> bool:
    t = (name or "") + " " + (room_name or "")
    return any(w in t for w in PRIVATE_TAGS)


def _is_social(name: str, room_name: str) -> bool:
    t = (name or "") + " " + (room_name or "")
    return any(w in t for w in SOCIAL_TAGS)


def _is_learning(name: str, room_name: str) -> bool:
    t = (name or "") + " " + (room_name or "")
    return any(w in t for w in LEARNING_TAGS)


def _is_system(name: str, room_name: str) -> bool:
    t = (name or "") + " " + (room_name or "")
    return any(w in t for w in SYSTEM_TAGS)


def _is_service_provider(name: str, room_name: str) -> bool:
    """服务方/培训方 —— 他们是**卖服务给别人的**，不是我们的客户。

    实测踩过的坑：「客服班班安安-技成培训」「路飞｜资深职业规划师~贺老师」
    聊天里聊到"订单""方案""自动化""合同"，被当成客户。
    但对方身份是给我方提供客服/培训/规划服务的 —— 属于业务往来的另一头，
    混进客户线会让老板误判"我们有这么多客户"。
    """
    t = (name or "") + " " + (room_name or "")
    return any(w in t for w in SERVICE_PROVIDER_TAGS)


def _count(text: str, words: List[str]) -> List[str]:
    """返回命中的词列表（用于溯源）。"""
    if not text:
        return []
    out = []
    for w in words:
        if w in text:
            out.append(w)
    return out


def _score(text: str, words: List[str], weight: float) -> Tuple[float, List[str]]:
    hits = _count(text, words)
    if not hits:
        return 0.0, []
    # 命中越多分越高，但收益递减，避免靠堆词刷分
    return weight * (1 + 0.35 * (len(hits) - 1)), hits


# ------------------------------------------------------------------ 识别


def classify_session(session: Dict[str, Any],
                     display: str,
                     room_name: str,
                     messages: List[Dict[str, Any]],
                     contacts: Dict[str, Dict[str, Any]],
                     members: List[str],
                     self_id: str = "") -> Dict[str, Any]:
    """判定单个会话的关系类型。

    返回：{关系, 置信度, 得分明细, 证据}
    """
    text_parts: List[str] = []
    ev: List[str] = []

    # ---- 信号 0：排除（私人 / 社交 / 学习 / 系统）
    if _is_system(display, room_name):
        return {"关系": "无关", "置信度": 0.0, "证据": ["系统/官方会话"],
                "得分": {}}
    if _is_private(display, room_name):
        return {"关系": "私人", "置信度": 0.0, "证据": ["私人关系，不计入业务"],
                "得分": {}}
    if _is_social(display, room_name):
        return {"关系": "社交", "置信度": 0.0, "证据": ["社交群，不计入业务"],
                "得分": {}}
    if _is_learning(display, room_name):
        return {"关系": "无关", "置信度": 0.0, "证据": ["学习/资料群，不计入业务"],
                "得分": {}}
    if _is_service_provider(display, room_name):
        return {"关系": "无关", "置信度": 0.0,
                "证据": ["服务方/培训方，不是客户"], "得分": {}}

    # ---- 信号 1：备注标签（最强）
    tag_scores = {"客户": 0.0, "供应商": 0.0, "内部": 0.0}
    if session.get("username") in contacts:
        c = contacts[session["username"]]
        remark = (c.get("remark") or "")
        nick = (c.get("nick_name") or "")
        if remark:
            for w in CUSTOMER_STRONG:
                if w in remark:
                    tag_scores["客户"] += 3.0
                    ev.append("备注含「%s」→ 客户" % w)
            for w in SUPPLIER_STRONG:
                if w in remark:
                    tag_scores["供应商"] += 3.0
                    ev.append("备注含「%s」→ 供应商" % w)
            for w in INTERNAL_STRONG:
                if w in remark:
                    tag_scores["内部"] += 3.0
                    ev.append("备注含「%s」→ 内部" % w)
            for tok in COMPANY_TOKENS:
                if tok in remark and "客户" not in remark:
                    tag_scores["内部"] += 1.5
                    ev.append("备注含公司标识「%s」" % tok)
                elif tok in remark and "客户" in remark:
                    tag_scores["客户"] += 1.0
                    ev.append("备注含公司标识「%s」" % tok)
        # 昵称带公司/部门标识 → 内部同事。
        # 备注可能只写「韩总」看不出身份，但昵称是「韩磊（力生激光切割打磨机器人）」——
        # 这种必须判内部，否则同一个人会同时出现在客户线和内部线，老板会懵。
        # 「力生客户XXX」是客户标签，昵称里带"客户"字样时不算内部。
        if nick and "客户" not in remark:
            for tok in INTERNAL_NICK_TOKENS:
                if tok in nick:
                    tag_scores["内部"] += 2.0
                    ev.append("昵称含「%s」→ 内部同事" % tok)
                    break

    # ---- 信号 2：名称关键词
    name_text = (room_name or "") + " " + (display or "")
    if name_text:
        text_parts.append(name_text)
        if any(w in name_text for w in EXCLUDE_NAMES):
            return {"关系": "无关", "置信度": 0.0, "证据": ["官方/工具会话"],
                    "得分": {}}
        for w in CUSTOMER_STRONG:
            if w in name_text:
                tag_scores["客户"] += 2.5
                ev.append("名称含「%s」" % w)
        for w in SUPPLIER_STRONG:
            if w in name_text:
                tag_scores["供应商"] += 2.5
                ev.append("名称含「%s」" % w)
        # 群名里的外包/代加工特征 → 供应商（实测「科世科外包安装沟通群」
        # 只在群名里有供应商特征，消息里全是技术讨论）
        for w in ("外包", "外协", "外发", "委外", "代加工", "供应商",
                  "厂商", "配件供应", "材料供应"):
            if w in name_text:
                tag_scores["供应商"] += 2.0
                ev.append("群名含「%s」→ 供应商" % w)
                break
        for w in SERVICE_GROUP_WORDS:
            if w in name_text:
                tag_scores["客户"] += 1.5
                ev.append("名称含「%s」→ 客户侧服务" % w)
        # 群名含公司标识 → 内部（实测「力生异形磨光群」既是内部协作群，
        # 也可能是客户群；但「力生XX群」里绝大多数是公司自己的项目群）
        # 注意：这是**弱信号**（权重 1.2），不能盖过消息里的客户/供应商信号
        for tok in COMPANY_TOKENS:
            if tok in name_text:
                tag_scores["内部"] += 1.2
                ev.append("群名含公司标识「%s」" % tok)

    # ---- 信号 3：消息内容
    body_all = []
    for m in messages:
        body_all.append(m.get("body") or "")
    body = " ".join(body_all)
    if body:
        text_parts.append(body)
        # **密度修正**：光靠 1~2 个泛词（如"多少钱"）不足以判为业务关系。
        # 要求命中词数 ≥ 3，或者命中了「强业务词」。
        for cat, words, w in (
                ("客户", CUSTOMER_WORDS, 1.0),
                ("供应商", SUPPLIER_WORDS, 1.0),
                ("内部", INTERNAL_WORDS, 1.0)):
            sc, hits = _score(body, words, w)
            if not hits:
                continue
            strong_hit = any(h in hits for h in STRONG_BUSINESS_WORDS)
            if len(hits) < 3 and not strong_hit:
                continue
            # 命中密度越高，分数越高
            dens = min(3.0, len(hits) / 4.0)
            tag_scores[cat] += sc * (0.5 + 0.5 * dens)
            ev.append("消息命中%s词 %d 个（%s…）"
                      % (cat, len(hits), "、".join(hits[:3])))

        # 供应商再细分：交易型信号（进货/货期/对账）权重高，
        # 品类型信号（PLC/机器人/电机）权重低 ——
        # 否则自家技术同事聊 PLC 就会被判成供应商。
        deal_sc, deal_hits = _score(body, SUPPLIER_DEAL_WORDS, 1.0)
        cat_sc, cat_hits = _score(body, SUPPLIER_CATEGORY_WORDS, 1.0)
        # 交易信号要**成组**才算数：实测单个「现货」「发货」「开票」
        # 在客户闲聊里也会出现，1 个就定性会把客户判成供应商。
        if len(deal_hits) >= 2:
            tag_scores["供应商"] += deal_sc * (0.5 + 0.5 * min(3.0, len(deal_hits) / 2.0))
            ev.append("命中交易型供应商信号 %d 个（%s…）"
                      % (len(deal_hits), "、".join(deal_hits[:3])))
        elif len(deal_hits) == 1:
            # 单个交易信号只记 0.3 分，作为"可能"而非"确定"
            tag_scores["供应商"] += deal_sc * 0.3
            ev.append("命中交易型供应商信号 1 个（%s…，证据偏弱）"
                      % "、".join(deal_hits[:3]))
        if cat_hits:
            tag_scores["供应商"] += cat_sc * 0.25 * min(3.0, len(cat_hits) / 4.0)
            ev.append("命中品类型供应商信号 %d 个（%s…）"
                      % (len(cat_hits), "、".join(cat_hits[:3])))
        # 只有品类信号、没有任何交易信号 → 供应商判定不成立，扣回去
        if cat_hits and not deal_hits:
            tag_scores["供应商"] = max(0.0, tag_scores["供应商"] - cat_sc * 0.9)

    # ---- 信号 4：群成员里的内部人
    if members:
        internal_cnt = 0
        for mid in members:
            mc = contacts.get(mid) or {}
            label = (mc.get("remark") or "") + " " + (mc.get("nick_name") or "")
            if any(t in label for t in COMPANY_TOKENS):
                internal_cnt += 1
        if internal_cnt >= 2:
            tag_scores["内部"] += 1.0 + 0.4 * internal_cnt
            ev.append("群内有 %d 位带公司标识的成员" % internal_cnt)

    # ---- 归一化
    total = sum(tag_scores.values())
    if total <= 0:
        # 没命中任何业务信号 —— 若会话本身有大量往来，说明是**业务但特征弱**
        # （如纯技术讨论），单独标出来，别混进「未识别」
        if len(messages) >= 8:
            return {"关系": "待人工确认", "置信度": 0.25,
                    "证据": ["有 %d 条往来但无明显业务特征词" % len(messages)],
                    "得分": {}}
        return {"关系": "未识别", "置信度": 0.0, "证据": [], "得分": tag_scores}
    best = max(tag_scores, key=lambda k: tag_scores[k])
    # **内部优先**：已确认是公司同事的（备注/昵称带公司标识），
    # 哪怕聊天里全是采购话题，也应归内部 —— 他是在替公司采购，
    # 不是公司的供应商。实测「力生陈影辉」就属于这种。
    if tag_scores["内部"] >= 2.0 and "内部" in tag_scores:
        if any("备注含公司标识" in e or "昵称含" in e or "群名含公司标识" in e
               for e in ev):
            best = "内部"
    # 置信度 = 最高分 / 总分，同时考虑绝对强度
    share = tag_scores[best] / total
    strength = min(1.0, total / 6.0)
    conf = round(0.55 * share + 0.45 * strength, 3)

    # 内部同事的私聊默认偏内部（同事之间不会聊生意）
    if best == "内部" and conf < 0.5:
        conf = round(conf + 0.15, 3)

    # 客户线同样要设门槛 —— 置信度太低说明只是"聊得像生意"，
    # 不是真的客户。混进客户线会让老板高估客户数。
    # 但也不能砍太狠：0.5 以上的证据链够扎实（实测真客户多在 0.6+）。
    if best == "客户" and conf < 0.55:
        return {"关系": "待人工确认", "置信度": conf,
                "证据": ["业务特征偏弱，需人工确认是否客户"] + ev[:5],
                "得分": {k: round(v, 2) for k, v in tag_scores.items()}}

    # 供应商是三线里**最不能错**的一条 —— 判错会让老板追错进度。
    # 硬门槛：必须命中强特征 —— 名称/备注里有「供应商/厂商/货源」字样，
    # 或群名含外包类特征，或有 ≥2 个交易型信号。
    # 否则降级为「待人工确认」，宁可让老板补一次，也别给假结论。
    if best == "供应商":
        has_strong = False
        for e in ev:
            # 弱证据（单个交易信号）直接排除
            if "证据偏弱" in e:
                continue
            # "命中交易型供应商信号 N 个" —— N>=2 才算强
            if "交易型供应商信号" in e:
                import re as _re
                mm = _re.search(r"信号\s*(\d+)\s*个", e)
                if mm and int(mm.group(1)) >= 2:
                    has_strong = True
                    break
                continue
            # 其余证据：只有"名称/备注/群名里出现供应商字样"才算强
            if ("供应商" in e or "厂商" in e or "货源" in e) and (
                    "名称含" in e or "备注含" in e or "群名含" in e):
                has_strong = True
                break
        if not has_strong:
            return {"关系": "待人工确认", "置信度": 0.3,
                    "证据": ["仅命中弱供应商特征，需人工确认"] + ev[:5],
                    "得分": {k: round(v, 2) for k, v in tag_scores.items()}}

    return {
        "关系": best,
        "置信度": conf,
        "得分": {k: round(v, 2) for k, v in tag_scores.items()},
        "证据": ev[:8],
    }


def classify_all(sessions: List[Dict[str, Any]],
                 msgs_by_session: Dict[str, List[Dict[str, Any]]],
                 contacts: Dict[str, Dict[str, Any]],
                 rooms: Dict[str, Dict[str, Any]],
                 members_by_room: Dict[str, List[str]],
                 self_id: str = "") -> List[Dict[str, Any]]:
    """批量分类所有会话。"""
    from . import wechat_data as wd
    out = []
    for s in sessions:
        u = s.get("username", "")
        if not u or u in ("filehelper",):
            continue
        disp = wd.display_name(u, contacts, rooms)
        room_name = (rooms.get(u) or {}).get("name", "")
        msgs = msgs_by_session.get(u, [])
        if not msgs:
            # 没消息也看名称
            msgs = []
        r = classify_session(s, disp, room_name, msgs, contacts,
                             members_by_room.get(u, []), self_id)
        r["username"] = u
        r["名称"] = disp
        r["群名"] = room_name
        r["消息数"] = len(msgs)
        r["最后活跃"] = s.get("last_ts", 0)
        r["摘要"] = s.get("summary", "")
        out.append(r)
    return out

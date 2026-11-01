"""成交漏斗体检：找出「有流量没成交」断在哪一环。

背景
----
陈灏讲 AI 变现三段式（交付 / 流量 / 成交）时，反复强调一个问题：
大多数人的瓶颈不是流量不够，是**成交环节没有设计**——
用户看到了内容，却不知道下一步该干什么，于是"热闹一阵就过去了"。

他的原话（销售一节）：「只有曝光，没有产品，热闹一阵就过去了。
只有产品，没有持续内容，别人连为什么要买都不知道。」

所以本模块做一件事：把系统已有的真实数据（发布日志、SKU 库存、禁区命中、
知识库规模、账号定位），映射成一条**五段成交漏斗**，逐段判断：
    这一段有没有做？做到什么程度？下一段该做什么？

五段漏斗
--------
    ① 货   —— 有没有能卖的东西（SKU 是否够、护城河是否写清）
    ② 引流 —— 有没有持续产出（发布频率、选题储备）
    ③ 试   —— 有没有让人白嫖到手的入口（试问、样例、免费额度）
    ④ 成交 —— 有没有明确的付款动作（收银台、报价、CTA）
    ⑤ 留   —— 有没有复购和转介绍（会员、复购路径）

每段给三个数：
    status  完成度 0-100
    gap     最大缺口的一句话说明
    action  下一步具体做什么（能直接执行，不是原则）

判定原则
--------
- **不编数据**：只读系统里真实存在的文件，读不到就标"未开始"，不假装有。
- **不看粉丝数**：粉丝多不等于能成交。判断依据是"每一步有没有对应的动作"。
- **动作要具体**：必须带得上命令或文件名，用户看完就知道今天干什么。

用法
----
    from . import funnel
    f = funnel.Funnel(cfg)
    report = f.inspect()
    print(report["markdown"])
"""

import csv
import os
import re
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 五段漏斗的定义
# ---------------------------------------------------------------------------
# key        段名
# title      这一段在解决什么问题
# check      这一段"做到位"的标志
# action     断了以后具体做什么（可直接执行）

STAGES = [
    {
        "key": "goods",
        "name": "货",
        "title": "有没有能卖的东西",
        "done": "SKU 数量够、护城河写清、定价有锚",
        "action": "python run.py shop --export 导出货品清单，"
                  "挑 1 款写成完整商品卡（含定价、交付物、护城河）",
    },
    {
        "key": "traffic",
        "name": "引流",
        "title": "有没有持续产出",
        "done": "有稳定的发布节奏，选题储备够用",
        "action": "python run.py today --topic \"你的选题\" 生成一篇，"
                  "手动发布到 1 个平台，把 publish_log 的阅读/涨粉填回去",
    },
    {
        "key": "trial",
        "name": "试",
        "title": "有没有让人白嫖到手的入口",
        "done": "客户不花钱也能拿到真东西，尝到甜头",
        "action": "python run.py demo 生成一个 5 分钟可演示的样例，"
                  "挂到货架页引流区，让客户先拿到结果再谈钱",
    },
    {
        "key": "deal",
        "name": "成交",
        "title": "有没有明确的付款动作",
        "done": "看到货之后，路径上就有一个明确的下一步",
        "action": "在货架页把「联系方式」改成带二维码的付款入口，"
                  "并写清楚「怎么买、多久交付、不满意怎么办」",
    },
    {
        "key": "keep",
        "name": "留",
        "title": "有没有复购和转介绍",
        "done": "第一次成交的人有第二次的理由",
        "action": "把「代做服务」改写成「按月不限次」，"
                  "用服务次数做复购钩子，一次成交变月费",
    },
]


class Funnel:
    """成交漏斗体检器。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.paths = cfg["paths"]
        self.notes = []      # 数据读取说明（哪些是真实数据，哪些缺失）
        self.stages = []
        self.radar_actions = []   # 情报雷达给出的三件待办

    # ---------- 读取真实数据 ----------

    def _read_publish_log(self):
        """发布日志 → (条数, 已填阅读的条数, 成交条数, 最近7天条数)"""
        p = os.path.join(self.paths["state"], "publish_log.csv")
        if not os.path.exists(p):
            self.notes.append("publish_log.csv 不存在 → 引流段按 0 计")
            return 0, 0, 0, 0
        try:
            with open(p, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            self.notes.append(f"publish_log.csv 读取失败：{e}")
            return 0, 0, 0, 0

        total = len(rows)
        filled = sum(1 for r in rows if (r.get("阅读") or "").strip())
        deal = sum(1 for r in rows if (r.get("成交") or "").strip()
                   and str(r.get("成交")).strip() not in ("0", "0.0", "0个"))
        recent = 0
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        for r in rows:
            d = (r.get("日期") or "").strip()
            if d and d >= cutoff:
                recent += 1
        return total, filled, deal, recent

    def _read_topic_pool(self):
        """选题池条数。

        两个来源都读：
        1. data/state/选题池.csv（跑过选题流程后自动累积）
        2. 02_知识库种子/选题池.md（人工维护的原始选题表，表格行数）
        只读前一个会漏掉大部分选题，导致漏斗误报「选题池 0 条」。
        """
        n = 0
        csvp = os.path.join(self.paths["state"], "选题池.csv")
        if os.path.exists(csvp):
            try:
                with open(csvp, "r", encoding="utf-8") as f:
                    n += len(list(csv.DictReader(f)))
            except Exception:
                pass

        mdp = os.path.join(
            os.path.dirname(self.paths["state"]),
            "..", "..", "02_知识库种子", "选题池.md")
        mdp = os.path.abspath(mdp)
        if os.path.exists(mdp):
            try:
                with open(mdp, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip()
                        # 只数选题表格行：| 序号 | 标题 | ... | 状态 |
                        if s.startswith("|") and s.count("|") >= 4:
                            cells = [c.strip() for c in s.strip("|").split("|")]
                            if cells and cells[0].isdigit():
                                n += 1
            except Exception:
                pass
        return n

    def _count_skus(self):
        """可售 SKU 数量 —— 复用 shop 模块，不重复实现。"""
        try:
            from . import shop as shopmod
            return len(shopmod.list_skus())
        except Exception:
            return 0

    def _count_forbidden(self):
        """禁区条数——复用 kb 的真实索引，不数文件个数。"""
        p = self.paths.get("forbidden")
        if not p or not os.path.isdir(p):
            return 0
        try:
            from . import kb as kbmod
            idx = kbmod.ForbiddenIndex.from_dir(p)
            return len(idx.entries)
        except Exception:
            n = 0
            for root, _dirs, files in os.walk(p):
                n += len([f for f in files if f.endswith(".md")])
            return n

    def _has_position_card(self):
        """账号定位卡是否填了关键字段。"""
        p = os.path.join(self.paths["seeds"], "账号定位卡.md")
        if not os.path.exists(p):
            return False
        try:
            with open(p, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            return False
        # 有实质内容 = 有非空的定位描述行
        return len([l for l in txt.split("\n")
                    if l.strip() and "待填" not in l
                    and not set(l.strip()) <= set("-=*# |")]) > 12

    def _count_outputs(self):
        """已生成的内容产出文件数。"""
        p = self.paths.get("outputs")
        if not p or not os.path.isdir(p):
            return 0
        n = 0
        for root, _dirs, files in os.walk(p):
            n += len([f for f in files if f.endswith(".md")])
        return n

    def _check_paywall(self):
        """货架页里有没有明确的付款入口。"""
        from . import config as cfgmod
        p = os.path.join(cfgmod.PROJECT, "09_AI超市模式", "货架页.md")
        if not os.path.exists(p):
            return False, "货架页.md 不存在（在 09_AI超市模式/ 下）"
        try:
            with open(p, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            return False, "货架页读取失败"
        if "待填" in txt:
            return False, "货架页还有未填的【待填】项"
        has_price = bool(re.search(r"¥\s*\d", txt))
        has_cta = any(k in txt for k in ("微信", "二维码", "闲鱼", "面包多", "下单"))
        return (has_price and has_cta), ""

    # ---------- 判定五段 ----------

    def _judge(self):
        total_pub, filled, deal, recent = self._read_publish_log()
        topics = self._read_topic_pool()
        skus = self._count_skus()
        forbid = self._count_forbidden()
        outputs = self._count_outputs()
        positioned = self._has_position_card()
        paywall_ok, paywall_msg = self._check_paywall()

        s = {}

        # ① 货
        score = 0
        if skus >= 8:
            score += 60
        elif skus >= 4:
            score += 40
        elif skus >= 1:
            score += 20
        if forbid >= 20:
            score += 25
        if outputs >= 3:
            score += 15
        s["goods"] = {
            "status": min(score, 100),
            "gap": (f"可售 SKU {skus} 款，禁区 {forbid} 条，产出 {outputs} 份"
                    if skus else "还没有可售的 SKU —— 货为零，别的环节都无从谈起"),
            "evidence": f"SKU={skus}｜禁区={forbid}｜产出={outputs}",
        }

        # ② 引流
        score = 0
        if topics >= 20:
            score += 40
        elif topics >= 10:
            score += 25
        elif topics >= 1:
            score += 10
        if recent >= 3:
            score += 40
        elif recent >= 1:
            score += 20
        if positioned:
            score += 20
        s["traffic"] = {
            "status": min(score, 100),
            "gap": (f"近 7 天产出 {recent} 篇，选题池 {topics} 条，发布数据回填 {filled}/{total_pub}"
                    if recent else "近 7 天没有产出记录 —— 引流断档"),
            "evidence": f"近7天={recent}篇｜选题池={topics}｜回填={filled}/{total_pub}",
        }

        # ③ 试
        score = 0
        if outputs >= 1:
            score += 35
        if topics >= 10:
            score += 20
        if forbid >= 20:
            score += 25
        if positioned:
            score += 20
        s["trial"] = {
            "status": min(score, 100),
            "gap": ("还没有让客户零成本拿到手的样例 —— 他们没见过结果，就不会付钱"
                    if outputs == 0 else
                    f"有 {outputs} 份产出，但没做成「客户能直接拿走」的样例"),
            "evidence": f"产出={outputs}｜禁区={forbid}｜定位卡={'已填' if positioned else '待填'}",
        }

        # ④ 成交
        score = 0
        if paywall_ok:
            score += 50
        else:
            score += 0
        if skus >= 4:
            score += 25
        if total_pub and filled:
            score += 25
        s["deal"] = {
            "status": min(score, 100),
            "gap": (paywall_msg or "货架页缺明确的付款入口 —— 客户看到货，不知道往哪下单")
                    if not paywall_ok else
                    f"付款入口已就位，但发布数据回填只有 {filled}/{total_pub} 条，无法判断哪款在卖",
            "evidence": f"付款入口={'OK' if paywall_ok else '缺'}"
                        f"｜SKU={skus}｜回填={filled}/{total_pub}｜成交={deal}",
        }

        # ⑤ 留
        score = 0
        if deal >= 3:
            score += 50
        elif deal >= 1:
            score += 30
        if skus >= 6:
            score += 25
        if total_pub >= 5:
            score += 25
        s["keep"] = {
            "status": min(score, 100),
            "gap": (f"目前成交 {deal} 单，还看不出复购"
                    if deal else "还没有成交记录，复购路径无从谈起"),
            "evidence": f"成交={deal}单｜发布={total_pub}条｜SKU={skus}",
        }

        self.stages = s
        return s

    # ---------- 输出 ----------

    def inspect(self):
        """执行体检。返回 dict。"""
        self._judge()
        stages = []
        for st in STAGES:
            r = self.stages.get(st["key"], {})
            stages.append({
                "key": st["key"], "name": st["name"], "title": st["title"],
                "done": st["done"], "action": st["action"],
                "status": r.get("status", 0),
                "gap": r.get("gap", ""),
                "evidence": r.get("evidence", ""),
            })

        # 找到最短的那一板（瓶颈）
        bottleneck = min(stages, key=lambda x: x["status"]) if stages else None

        # 情报待办：外部视频情报里现在值得做的三件事
        self.radar_actions = self._radar_actions()

        return {
            "stages": stages,
            "bottleneck": bottleneck,
            "radar_actions": self.radar_actions,
            "overall": round(sum(x["status"] for x in stages) / max(len(stages), 1)),
            "notes": self.notes,
            "markdown": self._markdown(stages, bottleneck),
        }

    def _radar_actions(self):
        """从情报雷达里取「只做三件事」，让漏斗与情报闭环。

        雷达没有数据（比如还没抓过视频）时返回空列表，不报错。
        """
        try:
            from . import radar as radarmod
            rep = radarmod.Radar(self.cfg)
            out = []
            for key in radarmod.TOP3:
                cat = next((c for c in radarmod.CATEGORIES if c["key"] == key), None)
                if cat:
                    out.append({"name": cat["name"], "action": cat["action"]})
            return out
        except Exception:
            return []

    def _markdown(self, stages, bottleneck):
        L = []
        L.append("# 成交漏斗体检")
        L.append("")
        L.append(f"体检时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        L.append("")
        L.append("> 读的是系统里的真实数据，读不到就标「未开始」，不编。")
        L.append("")
        L.append("| 段 | 解决什么 | 完成度 | 最大缺口 |")
        L.append("|---|---|---|---|")
        for s in stages:
            bar = "█" * (s["status"] // 10) + "░" * (10 - s["status"] // 10)
            L.append(f"| **{s['name']}** | {s['title']} | `{bar}` {s['status']}% | {s['gap']} |")
        L.append("")
        if bottleneck:
            L.append(f"## 瓶颈：{bottleneck['name']}（{bottleneck['title']}）")
            L.append("")
            L.append(f"**做到位的标志**：{bottleneck['done']}")
            L.append("")
            L.append(f"**现在就做这件事**：")
            L.append("")
            L.append(f"```bash\n{bottleneck['action']}\n```")
            L.append("")
        L.append("## 五段全部动作清单")
        L.append("")
        for s in stages:
            L.append(f"### {s['name']}｜{s['title']}")
            L.append("")
            L.append(f"- 现状：{s['evidence']}")
            L.append(f"- 缺口：{s['gap']}")
            L.append(f"- 做到位：{s['done']}")
            L.append("")
            L.append(f"```bash\n{s['action']}\n```")
            L.append("")
        if self.radar_actions:
            L.append("## 外部情报里的三件待办")
            L.append("")
            L.append("> 来源：情报雷达（`python run.py radar`）")
            L.append("")
            for i, r in enumerate(self.radar_actions, 1):
                L.append(f"{i}. **{r['name']}**：{r['action']}")
            L.append("")
        if self.notes:
            L.append("## 数据说明")
            L.append("")
            for n in self.notes:
                L.append(f"- {n}")
            L.append("")
        return "\n".join(L)



def build_report(cfg):
    """便捷入口：直接返回体检结果。"""
    return Funnel(cfg).inspect()

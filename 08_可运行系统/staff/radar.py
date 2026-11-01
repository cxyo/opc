"""情报雷达：把外部视频/文章情报，翻译成"我该动哪个岗位"。

背景
----
2026-10-27 收到一批视频号链接（37 条去重），题材横跨：
WorkBuddy 获客体系、GEO 优化、一人公司案例、开源项目（数字人/声音克隆/AI 漫剧）。

单看每条视频都是"信息"，看完就忘。本模块做三件事：

    ① 归类    —— 按关键词把每条情报打上"类别标签"
    ② 判定    —— 每类给出「抄什么 / 不抄什么 / 落到哪个岗位」
    ③ 排序    —— 只输出"现在值得动的 3 件事"，其余归档观察

核心判据
--------
- **看岗位，不看热闹**：一条情报如果落不到你现有 21 个岗位上，就是"观察"，不是"行动"。
- **红线优先**：任何涉及群控/批量私信/站外导流/绝对化承诺的手法，
  一律标为「不抄」并指向禁区清单对应条目。
- **不猜数据**：情报原文里没有的数字一律不补，只标"待查"。

用法
----
    from . import radar
    r = radar.Radar(cfg)
    report = r.inspect()
    print(report["markdown"])
"""

import json
import os
import re

# ---------------------------------------------------------------------------
# 情报分类规则
#
# order 决定判定顺序（先匹配先生效），所以更具体的关键词要放前面。
# 每个类别带：对应的项目岗位、能抄的、不能抄的、下一步动作。

CATEGORIES = [
    {
        "key": "geo",
        "name": "GEO 优化",
        "keywords": ["GEO", "geo", "AI搜索", "ai搜索", "大模型收录", "搜索优化"],
        "roles": ["A16 GEO优化官", "A19 品牌实体官"],
        "copy": [
            "「什么算有效 GEO」的测量权可抄：北京日报开源 GEO 工具的思路是"
            "先定测量标准再谈优化——你 A16 现在缺的就是这个测量口径",
            "「新手也能操作、拒绝被高价割韭菜」的定位可抄："
            "说明 GEO 优化正在从专业服务变成可交付的标准化流程，"
            "这是你把它做成 SKU 的窗口期",
        ],
        "dont": [
            "「AI智软 / 精准GEO优化系统」这类带联系方式的账号不要去加盟或买课——"
            "你是要卖 GEO 服务，不是加入 GEO 推销链",
            "不要承诺「保证被大模型收录」——无法验证的绝对化承诺违反禁区红线",
        ],
        "action": "A16 已按市场重定价（免费诊断 / ¥999 起一次 / ¥1,999 起每月）。"
                  "接下来做 3 个免费诊断攒案例，并定出「什么算被收录」的测量口径——"
                  "市场基础档是 ¥3,000-8,000/月，案例到位后再往上提。"
                  "详见 11_变现模式/GEO市场定价对照.md。",
    },
    {
        "key": "content",
        "name": "内容形态与短剧",
        "keywords": ["数字人", "声音克隆", "语音合成", "TTS", "漫剧", "短剧",
                     "直播带货", "虚拟人", "虚拟主播", "配音"],
        "roles": ["A20 短剧官", "A17 短视频官"],
        "copy": [
            "Toonflow 的三层 Agent + 无限画布 + 本地记忆这套结构，"
            "和你 A20 短剧官的「编剧→分镜→角色→生成」是同一套骨架，可以照着补流程图",
            "「先给分镜表、标好试拍/提示词/成本，你点头它才动手」这个交互，"
            "正是你三道人工闸的同构设计——可以作为 A20 的产品化卖点",
        ],
        "dont": [
            "本轮不接入。数字人/声音克隆要显卡、要部署、要版权确认，是团队级投入",
            "声音克隆、名人音色一律不做——肖像权与声音权风险远大于收益",
        ],
        "action": "只写成 1 条内容讲「为什么我不做数字人直播」——"
                  "你有 21 个岗位和一套三道人工闸，这本身就是差异化观点。",
    },
    {
        "key": "acquire",
        "name": "获客与截流",
        "keywords": ["获客", "截流", "找客户", "线索", "意向度", "精准获客",
                     "实体获客", "短视频获客", "AI获客", "引流"],
        "roles": ["A12 流量官", "A18 转化官", "A03 情报官"],
        "copy": [
            "「意向度自动分级」这个思路可抄：客户线索按 A/B/C/D 分级，"
            "A 类 5 分钟内响应，B 类进跟进台账——这正是你 A18 转化官缺的输入结构",
            "「6 张表」的管理思路可抄：线索表/意向分级表/话术库表/跟进台账表，"
            "可以直接落成你知识库里的 4 张表，让 A18 有稳定输入",
        ],
        "dont": [
            "「批量私信 / 群控 / 自动化养号」一律不抄——"
            "禁区清单已明确列为封号红线（平台规避工具，违反破坏计算机信息系统罪）",
            "「站外导流」不抄——抖音 2026 规则：仅企业号可用官方留资组件，"
            "严禁任何形式的直接站外导流",
        ],
        "action": "三张表已建好（知识库/05_客户与商机/ 下的线索表、意向分级表、"
                  "跟进台账表）。接下来每次有人问价就往里记一条，"
                  "先攒 3 条真实线索，让 A18 转化官有真东西可转。",
    },
    {
        "key": "solo",
        "name": "一人公司与超级个体",
        "keywords": ["一人公司", "OPC", "超级个体", "虚拟组织", "自雇", "个人IP",
                     "最小生意模型", "好书推荐"],
        "roles": ["A21 交付官", "A13 商品官", "A05 选题官"],
        "copy": [
            "Enter.AI 那个案例（8 个机器人虚拟组织、一周 214 个验证客户、"
            "89 封个性化外联、收件箱清零）值得反复看——它证明"
            "「一个人 + 一支 AI 队伍」不是概念，是已经被跑出数字的模式",
            "「从执行者变成分配者」这个定位句可直接当你的账号人设方向",
            "两本 OPC 书的推荐话术可以抄结构：先给处境（不完全依赖平台），"
            "再给解法（手把手从 0 到 1）",
        ],
        "dont": [
            "周末加密货币交易净赚 13,100 美元——不要抄。数字来自一次行情，"
            "不可复制也不可承诺，属于典型的「看起来很厉害但学不了」的内容",
            "「24 小时搭出最小生意模型」的 24 小时是剪辑效果，"
            "不是真实时长。你的链路是 2-3 分钟，这个差距要如实讲",
        ],
        "action": "写一条内容：标题用「21 个 AI 员工里，我最后只敢让 3 个全自动」，"
                  "正文讲你为什么只放 3 个自动、另外 18 个为什么要人工闸。"
                  "这是你 7 张神通卡里「卡7 只做一件事」最好的素材。",
    },
    {
        "key": "workflow",
        "name": "工作流与技能",
        "keywords": ["工作流", "流水线", "工具串", "多Agent", "skill", "技能",
                     "Agent", "自媒体流水线"],
        "roles": ["A07 撰稿官", "A08 改稿官", "A21 交付官"],
        "copy": [
            "牛津那个「先让 AI 理解你的资料 → 不同 Agent 分工互相检查 → 人判断方向 → "
            "沉淀方法复用」的流程可抄，它和你现在的 8 步链路几乎一模一样，"
            "可以直接当你的方法论背书",
            "「把选题和素材递进去，脚本/配音/画面/字幕自己跑完」的产品化思路可抄："
            "这正是 A17 短视频官该有的输入输出边界",
            "WorkBuddy 技能可以上架售卖——你已经在做这套东西，等于现成分发渠道",
        ],
        "dont": [
            "别去追那 5 个工具的具体组合（HyperFrames/Remotion/Sound Pass/豆包TTS/"
            "faster-whisper）——它们解决的是视频生成，你现在的瓶颈不是这个",
            "「两个 skill 把电脑变成自媒体流水线」是演示效果，"
            "你复现时按自己 8 步链路的真实耗时讲，别照抄",
        ],
        "action": "在选题池里加一条：'21 个 AI 员工是怎么协作的'，"
                  "把牛津那个流程图翻译成你的 8 步链路图，做成一张图发出去。",
    },
    {
        "key": "opensource",
        "name": "开源项目与工具",
        "keywords": ["开源", "GitHub", "Star", "star", "平替", "免费"],
        "roles": ["A04 素材官"],
        "copy": [
            "这些项目本身就是素材库：多 Agent 分层（Toonflow 三层 Agent）、"
            "先给分镜表再渲染（59.4k Star 那条）、本地部署隐私（Duix 手机端离线）"
            "——这三条正好能写成 3 条干货内容",
        ],
        "dont": [
            "全部不接入。数字人、声音克隆、AI 漫剧三条线都需要显卡/部署/版权确认，"
            "是团队级投入，单人阶段做了就是分心",
        ],
        "action": "本轮只做一件事：把开源项目写成 3 条内容发出去，"
                  "不下载、不部署、不接入。",
    },
    {
        "key": "monetize",
        "name": "平台与变现基建",
        "keywords": ["上架", "售卖", "变现", "赚钱", "加盟", "代理", "扶持",
                     "SCRM", "企微", "月入过万", "Skill"],
        "roles": ["A13 商品官", "A21 交付官", "A22 淘宝视觉官"],
        "copy": [
            "WorkBuddy 技能/专家可上架售卖——你本来就在做这套东西，"
            "这条视频等于给了你现成的分发渠道，值得研究上架规则",
            "**鉴锋那条（WorkBuddy 打造月入过万的 Skill）与你完全同构**——"
            "别人已经跑通的路径，你货已经做好了只差上架",
            "**官方建议「初期设 5 元或 10 元探需求」**——"
            "买家对没听过的技能决策成本就是几块钱。你现在的 ¥29-99 "
            "在开放平台属于高价，得先降价测需求",
        ],
        "dont": [
            "「0 经验可加入、总部一站式扶持、产掘金 AI 蓝海」这类是招商话术，"
            "不是方法论。看完只留渠道信息，不留期望",
            "不要同时铺 3 个平台——一个人只能维护 1 个主渠道 + 1 个分发渠道",
            "不要把「月入过万」写进标题——平台严打这类承诺，"
            "你也没有数据支撑",
        ],
        "action": "去 WorkBuddy 开放平台（open.workbuddy.cn）开通收入变现："
                  "先免费传 3 个技能过审（这是收款的前置门槛），"
                  "再把 A22 挂 ¥9.9 一次性试水。淘宝店定价不动，两边分工。",
    },
]

# ---------------------------------------------------------------------------
# 显式作者映射
#
# 为什么需要：纯关键词会误伤。比如「北京日报开源GEO工具」里同时有
# "GEO"和"获客"，「江先生短视频获客系统」是流水线方法论而非获客打法。
# 所以对已确认的账号直接指定类别，关键词只作兜底。
#
# value: (类别 key, 备注)

AUTHOR_MAP = {
    # GEO 阵营
    "阿阳聊AI": ("geo", ""),
    "精准GEO优化系统": ("geo", ""),
    "品牌Geo优化大师": ("geo", ""),
    "大龙-实体AI获客": ("geo", "账号名带获客，主题是 GEO"),
    # 获客阵营
    "Kanyi聊运营": ("acquire", ""),
    "用户1747642623": ("acquire", ""),
    "蒋校长聊ai获客": ("acquire", ""),
    "小吉炖蘑菇啊": ("acquire", "竞品截流，单独拎出来讲"),
    # 内容形态
    "非哥黑科技": ("content", "小说转漫剧"),
    "AIFeed": ("content", "Toonflow 短剧"),
    "AI摸鱼ing": ("content", "Agnes AI 短剧"),
    "青青-wang": ("content", "虚拟人播报 / 创业项目库（两条内容不同，按 ID 区分）"),
    "AI慢聊": ("content", "LiveTalking 数字人"),
    "IT小圈": ("content", "数字人直播"),
    "逐光0001": ("content", "Duix 本地数字人"),
    "哒哒工具库": ("content", "VoxCPM2 语音合成"),
    "方格动态": ("content", "VibeVoice 语音"),
    "开源日记": ("content", "声音克隆"),
    # 一人公司
    "Enter.AI": ("solo", "Grok Bot 虚拟组织"),
    "大浪-高净值IP打造": ("solo", "最小生意模型"),
    "东南书社": ("solo", "OPC 书目"),
    "玄和师兄": ("solo", "OPC 书目"),
    "杭州-知了指挥官": ("solo", "一人公司团队"),
    # 工作流
    "江先生短视频获客系统": ("workflow", "五工具流水线"),
    "Ai实战获客小K": ("workflow", "接 4 个技能出片"),
    "慧运营AI获客-阳哥": ("workflow", "两个 skill 做流水线"),
    "牛津Kate朱朱": ("workflow", "AI 工作流拆解"),
    "AI实战大鑫": ("workflow", "ComfyUI 平替"),
    "辰土AI": ("workflow", "获客 skill / 上网赚钱 AI"),
    "爱科技AI": ("workflow", "59.4k Star 视频流水线"),
    # 变现基建
    "讯灵AI-GEO代理": ("monetize", "招商加盟话术"),
    "微盛Agent增长笔记": ("monetize", "技能上架售卖"),
    # 第二批（2026-10-28）
    "鉴锋Agent搭建笔记": ("monetize", "WorkBuddy Skill 月入过万"),
    "央视财经": ("solo", "一个老板八个AI虾兵，权威背书"),
    "陈灏AI商业观察": ("solo", "FDE 叙事框架"),
    "江哥AI落地实战": ("solo", "FDE 概念解释"),
    "Ai老板洞见": ("acquire", "无效获客诊断"),
}


# 第二批按视频 ID 精确映射（同一作者不同内容，主题不同）
ID_MAP = {
    "AkBc3pRqTQ": ("workflow", "近3000个创业项目数据库"),
    "AKH7V7ji4J": ("content", "大模型与智能体搭配清单"),
    "AoRgK78kBF": ("solo", "FDE 概念（同作者重复分发）"),
}


# 只做三件事的优先顺序（不是分类顺序，是"值得现在动"的顺序）
# 说明：内容形态类虽然条目多，但结论是"本轮不接入"，所以不进前三。
# 2026-10-28 调整：变现基建升到第一——WorkBuddy 开放平台收入变现已开放，
# 抽成 1%，而鉴锋那条（WorkBuddy Skill 月入过万）证明路径已跑通。
# GEO 已在第一批落地（定价对照文档已写），不再是"最该现在动"的事。
TOP3 = ["monetize", "acquire", "solo"]

# 不抄清单：命中即标记为红线风险
RED_LINE_KEYWORDS = [
    ("群控", "群控/批量操作", "平台规避工具，禁区清单列为封号红线"),
    ("批量私信", "批量私信", "封号，禁区清单：私信批量发广告"),
    ("养号", "矩阵养号", "平台规避工具，绝对禁止"),
    ("加微信", "站外导流", "抖音 2026：仅企业号可用官方留资组件"),
    ("私聊转账", "私下交易", "小红书禁一切站外交易"),
    ("保证收录", "绝对化承诺", "无法验证的承诺，违反禁区红线"),
    ("100%", "绝对化承诺", "违反广告法与平台内容规范"),
    ("最强", "绝对化承诺", "违反广告法与平台内容规范"),
]

# 值得单独拿出来的一条（竞品截流），它属于获客但不该混在批量归类里
COMPETITOR_KEYWORDS = ["竞品", "截流"]
COMPETITOR_NOTE = (
    "「搜竞品词做投放截流」这条：逻辑本身成立（搜品牌词的人意向更明确），"
    "但它要花钱买搜索流量，且你目前连一条投放链接都没有。"
    "结论：记下来，等有 ¥1000 以上的投放预算再看，现在不抄。"
)


class Radar:
    """外部情报雷达。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.paths = cfg.get("paths", {})
        self.meta_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "tools", "sph_meta.json")

    # -- 期数（栏目化编号）-----------------------------------------------
    # 为什么要期数：2026-10-31 实测，EQ0.98 的封面右上角标「009期」，
    # 观众看到期数会预期下一期 —— 这是**订阅钩子**，本批唯一一个有编号的内容。
    # 期数按「年 + 自然周」推算，周内重跑不重复计数。
    @staticmethod
    def _issue_no():
        """返回 (期号字符串, 年第几周)。周内重跑不递增。"""
        import datetime as _dt
        now = _dt.date.today()
        iso = now.isocalendar()
        year, week = iso[0], iso[1]
        # 起始周固定为 2026-01-05（2026 年第一个周一），往前推周数
        base = _dt.date(2026, 1, 5)
        weeks = (now - base).days // 7
        if weeks < 0:
            weeks = 0
        return "%03d期" % (weeks + 1), "第%d周" % week

    def _issue_state(self):
        """期号 + 上次生成时间，用于判断是否新增一期。"""
        state_path = os.path.join(
            self.cfg.get("paths", {}).get("outputs", "."),
            "文章_情报雷达", "_期号.json")
        issue, week = self._issue_no()
        prev = {}
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
            except Exception:
                prev = {}
        return issue, week, state_path, prev

    # -- 数据读取 ---------------------------------------------------------
    def _load(self):
        p = os.path.abspath(self.meta_path)
        if not os.path.exists(p):
            return []
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    # -- 分类 -------------------------------------------------------------
    @staticmethod
    def _classify(text, author="", vid=""):
        """优先用视频 ID 精确映射 → 作者映射 → 关键词兜底。"""
        by_id = ID_MAP.get(vid)
        if by_id:
            for cat in CATEGORIES:
                if cat["key"] == by_id[0]:
                    return cat
        for cat in CATEGORIES:
            if cat["key"] == AUTHOR_MAP.get(author, (None,))[0]:
                return cat
        low = (text or "").lower()
        for cat in CATEGORIES:
            for kw in cat["keywords"]:
                if kw.lower() in low:
                    return cat
        return None

    @staticmethod
    def _red_lines(text):
        hits = []
        low = (text or "").lower()
        for kw, label, why in RED_LINE_KEYWORDS:
            if kw.lower() in low:
                hits.append({"label": label, "why": why})
        return hits

    # -- 输出 -------------------------------------------------------------
    def inspect(self):
        items = [x for x in self._load() if x.get("ok")]
        buckets = {}
        red_flagged = []
        competitor = []
        unclassified = []

        for it in items:
            text = (it.get("title") or "") + " " + (it.get("author") or "")
            cat = self._classify(text, it.get("author") or "", it.get("id") or "")
            if cat:
                buckets.setdefault(cat["key"], {"cat": cat, "items": []})["items"].append(it)
            else:
                unclassified.append(it)

            rl = self._red_lines(it.get("title") or "")
            if rl:
                red_flagged.append({"item": it, "hits": rl})
            if any(k in (it.get("title") or "") for k in COMPETITOR_KEYWORDS):
                competitor.append(it)

        ordered = [c for c in CATEGORIES if c["key"] in buckets]

        md = []
        issue, week, _state_path, _prev = self._issue_state()
        md.append("=" * 62)
        md.append("  情报雷达 · %s（%s）" % (issue, week))
        md.append("=" * 62)
        md.append("")
        md.append(f"已解析情报：{len(items)} 条"
                  + (f"（另有 {len(unclassified)} 条未归类）" if unclassified else ""))
        md.append("")

        # 一、按类别
        md.append("【一】分类结果与落地判断")
        md.append("-" * 62)
        for c in ordered:
            b = buckets[c["key"]]
            md.append("")
            md.append(f"▌{c['name']}（{len(b['items'])} 条）")
            md.append(f"  对应岗位：{' / '.join(c['roles'])}")
            if c["key"] not in TOP3:
                md.append("  ⚑ 本轮结论：归档观察，不进「只做三件事」")
            md.append("")
            for it in b["items"]:
                first = (it.get("title") or "").split("\n")[0]
                if len(first) > 52:
                    first = first[:52] + "…"
                md.append(f"  · {it.get('date','?')}  {it.get('author','?')}")
                md.append(f"    {first}")
            md.append("")
            md.append("  ✓ 可抄：")
            for x in c["copy"]:
                md.append(f"    · {x}")
            md.append("  ✗ 不抄：")
            for x in c["dont"]:
                md.append(f"    · {x}")
            md.append(f"  → 现在就做：{c['action']}")
            md.append("")

        if competitor:
            md.append("【二】单独拎出来的一条")
            md.append("-" * 62)
            for it in competitor:
                md.append(f"  · {it.get('author','?')}  {it.get('date','?')}")
                md.append(f"    {(it.get('title') or '').split(chr(10))[0][:60]}")
            md.append("")
            md.append(f"  {COMPETITOR_NOTE}")
            md.append("")

        if red_flagged:
            md.append("【三】红线提醒（看到就跳过，别学）")
            md.append("-" * 62)
            for r in red_flagged:
                labels = "、".join(h["label"] for h in r["hits"])
                why = r["hits"][0]["why"]
                md.append(f"  · {r['item'].get('author','?')}  —— {labels}")
                md.append(f"    原因：{why}")
            md.append("")
            md.append("  详见 06_组织架构/02_禁区与合规/禁区清单_平台红线.md")
            md.append("")

        # 四、只给三件事
        md.append("【四】只做三件事（其余全部归档观察）")
        md.append("-" * 62)
        n = 0
        for key in TOP3:
            if key not in buckets:
                continue
            c = buckets[key]["cat"]
            n += 1
            md.append(f"  {n}. 【{c['name']}】{c['action']}")
        if n == 0:
            md.append("  （没有可执行情报，先抓取：python tools/fetch_sph.py --file tools/ids.txt）")
        md.append("")
        md.append("  其余类别的「现在就做」先不做——记到观察清单，等上面三件做完再说。")
        md.append("")

        return {
            "markdown": "\n".join(md),
            "total": len(items),
            "buckets": {k: len(v["items"]) for k, v in buckets.items()},
            "red_flagged": len(red_flagged),
            "unclassified": len(unclassified),
            "期号": issue,
            "周次": week,
        }

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
岗位说明书 → 可安装 Skill 包

背景
----
你的淘宝店（龙虾指令集）在卖「500+ 龙虾技能」，QClaw / OpenClaw 的技能市场
里一个技能就是一个**目录**，核心是 SKILL.md。

而 `07_AI员工/01_Bot提示词/` 下这 21 份岗位说明书，每个都已经包含：
    人设与回复逻辑（完整提示词）+ 职责边界 + 执行流程 + 输出规范
    + 降级行为 + 绝对禁止 + 输入变量
这正是 SKILL.md 需要的东西，只是格式是「给人看的文档」，
不是「给 Agent 装的目录」。

本脚本做一次转换，把 21 份说明书批量变成可以直接上架/发货的技能包：
    <slug>/
      SKILL.md      ← 必需：YAML frontmatter + 提示词正文
      clawhub.json  ← 必需：元数据 + 定价
      README.md     ← 可选：市场落地页（人看的）
      安装说明.md   ← 额外：给买家的一页纸
    └── <slug>.zip  ← 可直接挂淘宝自动发货

用法
----
    python skill_pack.py                  # 全部 21 个岗位
    python skill_pack.py A10 A07          # 只打包指定岗位
    python skill_pack.py --list           # 只看有什么
    python skill_pack.py --out D:\\xxx    # 换输出目录

注意
----
- 技能包里的 SKILL.md **不挂知识库**（ClawHub 技能没有知识库概念），
  所以凡是需要「禁区清单」「定位卡」才能工作的岗位，
  必须把关键约束**内联**进提示词，否则装上去是空壳。
  本脚本会自动做这件事：把「核心依赖」转成一段「必须自带的前提」说明。
"""
import os
import re
import json
import zipfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
# 一人AI公司/
PROJECT = os.path.abspath(os.path.join(HERE, "..", ".."))
ROLES_DIR = os.path.join(PROJECT, "07_AI员工", "01_Bot提示词")
OUT_DIR = os.path.join(PROJECT, "05_产品交付", "Skill包")

AUTHOR = "lobster-skill"
AUTHOR_NAME = "龙虾指令集"
VERSION = "1.0.0"
MIN_CLAW_VERSION = "1.2.0"

# ---------------------------------------------------------------------------
# 每个岗位的「一句话价值 / 适合谁 / 前提条件 / 定价」
# 这一层是产品信息，不是提示词信息，必须手工定——所以放在这里。
# 定价参考：ClawHub 生态里专业实用型技能 $15-50 折算（国内淘宝生态按 ¥ 计）。

PRICING = {
    "A01": (29,  "平台规则红线审计：给你新公告，它告诉你比现有禁区多了哪条"),
    "A02": (29,  "政策解读：给你政策原文，它分出禁止/注意/鼓励并转成选题"),
    "A03": (39,  "情报筛选：给它一批抓取内容，它只留下跟你定位相关的"),
    "A04": (29,  "资料归档：给它一堆散料，它分类成卡片并去重"),
    "A05": (49,  "选题生成：给它情报和素材，它出4-6个带四维评分的候选选题"),
    "A06": (39,  "爆款拆解：给它参考文章，它拆出可复用的结构骨架"),
    "A07": (69,  "撰稿：给它骨架和你的真实经历，它出1500-3000字初稿+10个标题"),
    "A08": (39,  "改稿：给它稿子和质检报告，它逐条改并给修改对照表"),
    "A09": (59,  "去AI味：按你的风格指纹改写，给出改写记录和自检评分"),
    "A10": (99,  "发布前三合一质检：违规/AI味/事实错误，一份报告带通过判定"),
    "A11": (49,  "多平台适配：一篇稿子改成公众号/小红书/口播稿三个版本"),
    "A12": (59,  "流量复盘：给它30天发布数据，它出最多5条今天就能做的调整"),
    "A13": (69,  "商品详情页：给它产品资料，出带「不适合谁」的详情页+合规检查"),
    "A14": (39,  "发布复盘：给它昨天数据，出逐篇表现和选题池表现分更新"),
    "A15": (39,  "客服回复：给它买家问题和FAQ，生成回复草稿"),
    "A16": (99,  "GEO优化：让AI搜索推荐你的品牌，产出问答式内容规格"),
    "A17": (59,  "短视频脚本：给它选题和平台，出前3秒钩子+分镜+AI标注位置"),
    "A18": (69,  "转化：识别客户意向等级，生成回复草稿和异议处理方案"),
    "A19": (79,  "品牌实体卡：统一全渠道口径，列出AI提及你的错误和幻觉"),
    "A20": (99,  "短剧季纲：给它真实痛点，出人物卡+第1集完整脚本"),
    "A21": (79,  "交付管理：给它订单和进度，出交付启动包+验收清单"),
    "A22": (99,  "淘宝视觉：给它商品资料，出5张主图方案+7屏详情页+标题候选"),
}

# 每个岗位的「适合谁 / 不适合谁」——这一段直接决定转化率，
# 因为买家判断"要不要买"主要靠这段。写具体场景，不写"所有做内容的人"。
AUDIENCE = {
    "A01": (["公众号/小红书/抖音号主，每周要盯平台公告",
             "同时管 3 个以上账号，怕漏一条红线就限流的团队",
             "做企业号运营，需要留一份可追溯的规则审计记录的人"],
            ["以为规则可以靠 AI 自动抓取的人（它不联网抓，得你喂）",
             "一个月都不发内容、暂时不需要合规的人"]),
    "A02": (["做政策解读、政务内容、需要判断文件性质的人",
             "需要从政策里找选题方向的时政/产业类账号",
             "合规岗、法务助理这类要读原文出结论的角色"],
            ["只想听结论不想给原文的人（它不猜，必须看原文）"]),
    "A03": (["一天要刷 50 条同行内容、筛不出哪些值得看的人",
             "靠信息差做内容的账号",
             "需要给团队做每日情报简报的人"],
            ["信息量不大、每天只看几条的人"]),
    "A04": (["收藏夹里堆了 200 篇文章、永远没整理过的人",
             "团队知识库乱、每次写东西都要重新找素材的人",
             "做方法论沉淀的个人博主"],
            ["资料量很少、随手就能翻到的人"]),
    "A05": (["每天不知道写什么、发文全靠临时起意的人",
             "手里有素材但不知道怎么变成选题的人",
             "需要按月排选题的内容团队"],
            ["有稳定选题来源、不缺选题的人"]),
    "A06": (["看到爆款想抄但抄不出结构的账号",
             "需要把别人的框架变成自己可填的模板的人",
             "做短剧/带货脚本、要拆解分镜结构的人"],
            ["纯原创型作者，不看同行的人"]),
    "A07": (["有骨架但写不出来、或写出来像 AI 的人",
             "每周要出 3-5 篇长文的内容团队",
             "做白皮书、案例复盘、需要长文的人"],
            ["要写 500 字短文案的人（这个技能偏长文）"]),
    "A08": (["收到质检意见不知道从哪下手的人",
             "改稿来回好几轮、效率上不去的团队",
             "需要一个可追溯的修改对照表做交付的人"],
            ["还不确定要改什么的人（先用质检官）"]),
    "A09": (["稿子一眼假、读者评论说太 AI 了的人",
             "要写公众号长文、平台开始降 AI 权重的人",
             "有自己语言习惯、但写出来总丢的人"],
            ["纯公文/法律文本（去 AI 味对这类无意义）"]),
    "A10": (["每天发内容、担心哪天被判违规限流的人",
             "团队多人发文、需要统一发布口径的人",
             "接代写单、怕出事赔钱的自由职业者"],
            ["要求保证合规的人（最终由平台判定，它不背这个承诺）"]),
    "A11": (["一篇内容要发公众号+小红书+抖音、每次手动改到烦的人",
             "内容团队多人协作、需要统一适配标准的人",
             "做矩阵号、需要一稿多版的人"],
            ["只发一个平台的人"]),
    "A12": (["发了 30 篇内容、说不清哪类选题有效的人",
             "想提升打开率但不知道从哪下手的人",
             "需要每月固定输出调整项的内容负责人"],
            ["发不到 10 篇、数据不足的人（它会明说可信度低）"]),
    "A13": (["淘宝/闲鱼/小红书店铺上架、详情页转化低的人",
             "不会写不适合谁、因此退款率高的卖家",
             "需要上架前合规自查的人"],
            ["已经有稳定模板、只需要填几个字段的人"]),
    "A14": (["发完就忘、月底复盘只剩感觉的人",
             "需要给选题池打表现分、按数据排下一批题的人",
             "带 2-5 人内容小团队、需要统一复盘口径的人"],
            ["只发了 1-2 篇就想看出规律的人"]),
    "A15": (["客服消息多、回复慢、买家重复问同样的问题的人",
             "一个人管店铺咨询、想沉淀 FAQ 的卖家",
             "需要把高频问题变成产品改进依据的人"],
            ["涉及退款、赔付、承诺的情况（这些强制转人工）"]),
    "A16": (["做内容做了一年、AI 搜自己搜不到的人",
             "想让 AI 主动推荐自己品牌的企业",
             "已经在做 GEO、但不知道什么算有效的人"],
            ["指望它保证排名的（做不到，也没人能保证）"]),
    "A17": (["要做口播视频、但写不出前 3 秒的人",
             "需要分镜和拍摄清单的兼职做号人",
             "同一选题要出视频版和图文版的人"],
            ["纯概念类选题（它会建议改图文，不硬转视频）"]),
    "A18": (["私信和评论多、不知道怎么判断优先级的卖家",
             "需要把对话变成可跟进记录的人",
             "想沉淀高频异议话术的人"],
            ["需要它自动谈价格的人（涉金额必须人工）"]),
    "A19": (["开了多个账号、名字/介绍写法不统一的人",
             "想知道 AI 现在怎么描述自己的品牌的人",
             "要做 AI 搜索可见度的企业"],
            ["只开一个账号、还没开始做内容的人"]),
    "A20": (["想做 AI 短剧、卡在剧本和分镜的人",
             "有真实行业痛点、想做成系列内容的人",
             "短剧团队里负责策划的人"],
            ["没有真实痛点的人（它会拒绝编故事）"]),
    "A21": (["接单交付、每次都从零开始梳理流程的人",
             "需要给客户一份验收清单的人",
             "一个人同时服务多个客户的自由职业者"],
            ["没有交付标准、还没想清楚卖什么的人"]),
    "A22": (["在淘宝卖虚拟商品（资料包/技能包）、主图总被驳回的人",
             "想搞懂淘宝主图5张图怎么分工的人",
             "担心AI生成主图违规的人"],
            ["卖实物商品的（本技能针对虚拟商品，场景摆拍那套不适用）"]),
}


# 需要内联的前提说明（否则装上去是空壳）
PREREQ = {
    "A01": "**必须自带**：你所在平台的现行规则清单（至少含最近一次公告的原文摘录）。\n没有规则清单，它无法比对，只能靠它自己的记忆——那样不可靠。",
    "A02": "**必须自带**：政策原文片段（保留发布日期与文号）。\n它不联网查政策，只基于你给的原文判断。",
    "A10": "**必须自带**：目标平台的现行红线条目。\n注意：平台规则每月都在变，本技能内置的规则**会过期**，请每季度核对一次。",
    "A09": "**必须自带**：你的风格指纹卡（句长习惯、口头禅、禁用词）。\n没有它，它只能按通用规则去AI味，效果打折。它会在缺料时明确告警。",
    "A07": "**建议自带**：你的真实经历（做过的事、翻过的车、具体的数字）。\n它会主动在缺真实素材时标【待补】，不会替你编。",
    "A13": "**必须自带**：你的产品资料（卖点、交付物、定价）。\n涉及广告法合规检查，它会拒绝输出可上架版本（只给问题清单）——这是设计如此。",
    "A19": "**必须自带**：你的账号定位卡。",
    "A20": "**必须自带**：真实痛点（你或客户的真实经历）。\n它拒绝为无法核实的痛点编故事。",
    "A21": "**必须自带**：你的交付SOP和验收标准。",
    "A22": "**必须自带**：你的商品交付清单（写进主图第2张的就是它，清单不实会被退回）。\n"
            "**注意**：淘宝规则每季度都在变，本技能内置的尺寸与红线**会过期**，请每季度核对一次。",
}


def extract_prompt(md_text):
    """从岗位说明书里抽出「一、人设与回复逻辑」代码块的内容。"""
    m = re.search(
        r"##\s*一、人设与回复逻辑.*?```[^\n]*\n(.*?)\n```",
        md_text, re.S)
    if m:
        return m.group(1).strip()
    # 兜底：取第一个代码块
    m2 = re.search(r"```[^\n]*\n(.*?)\n```", md_text, re.S)
    return m2.group(1).strip() if m2 else ""


def extract_sections(md_text):
    """抽出开场白、建议问题、输入变量。"""
    out = {}
    m = re.search(r"##\s*二、开场白.*?```[^\n]*\n(.*?)\n```", md_text, re.S)
    out["hello"] = m.group(1).strip() if m else ""

    m = re.search(r"##\s*三、建议问题.*?\n(.*?)(?=\n---|\n##\s*四)", md_text, re.S)
    asks = []
    if m:
        for line in m.group(1).split("\n"):
            s = line.strip()
            if re.match(r"^\d+[.、)]", s):
                asks.append(re.sub(r"^\d+[.、)]\s*", "", s))
    out["asks"] = asks

    m = re.search(r"##\s*五、输入变量.*?\n(.*?)(?=\n---|\n##\s*六)", md_text, re.S)
    out["vars"] = m.group(1).strip() if m else ""
    return out


def parse_meta(md_text):
    """从文件头部的引用行里取层级/自动化等级。

    文件头格式：
        > Quality Inspector | L3 检测层 | 自动化等级：L2 半自动 | 每日上限 5 次 | 模型：强模型（DeepSeek-R1 / 0.2）
        > 核心依赖：禁区清单（平台红线 + 国家政策）+ 风格指纹
    """
    out = {"en": "", "layer": "", "auto": "", "deps": ""}

    # 第一行引用：> 英文名 | 层级 | 自动化等级：X | ... | 模型：...
    first = re.search(r"^>\s*(.+)$", md_text, re.M)
    if first:
        line = first.group(1)
        parts = [x.strip() for x in line.split("|")]
        if parts:
            out["en"] = parts[0]
        for p in parts:
            if p.startswith("自动化等级："):
                out["auto"] = p.replace("自动化等级：", "").strip()
            elif "层" in p and len(p) <= 12 and not p.startswith("模型"):
                out["layer"] = p
            elif p.startswith("模型："):
                out["model"] = p.replace("模型：", "").strip()

    # 第二行引用：> 核心依赖：xxx
    dep = re.search(r"^>\s*核心依赖：(.+)$", md_text, re.M)
    if dep:
        out["deps"] = dep.group(1).strip()

    return out


def slugify(name):
    """岗位名 → skill slug。

    岗位名是中文，且 ClawHub 的 slug 必须是小写英文/数字/连字符。
    所以用「岗位编号 + 英文代号」，英文代号从文件头的英文名取，
    取不到就用编号本身（a10质检官 会被规范化成 a10）。
    """
    # 去掉中文岗位名里的下划线前缀
    cn = name.lstrip("_").strip()
    return f"{cn}"  # 保留中文名，稍后由 build_slug 处理


# 岗位编号 → 英文 slug 后缀（ClawHub 规范：小写英文 + 连字符）
EN_SLUG = {
    "A01": "safety-auditor", "A02": "policy-analyst", "A03": "intel-scout",
    "A04": "material-organizer", "A05": "topic-planner", "A06": "viral-deconstructor",
    "A07": "copywriter", "A08": "revision-editor", "A09": "humanizer",
    "A10": "quality-inspector", "A11": "multi-platform-publisher",
    "A12": "traffic-analyst", "A13": "product-page-writer", "A14": "post-mortem",
    "A15": "faq-support", "A16": "geo-optimizer", "A17": "short-video-script",
    "A18": "conversion-helper", "A19": "brand-entity-card", "A20": "short-drama-planner",
    "A21": "delivery-manager",
    "A22": "taobao-visual",
    "A23": "planner",
}


def build_slug(code, name):
    """生成最终 slug：a10-quality-inspector"""
    en = EN_SLUG.get(code, slugify(name.lstrip("_")))
    return f"{code.lower()}-{en}"


def build_skill_md(code, name, meta, prompt, sections):
    """生成 SKILL.md。"""
    # frontmatter description 必须说清「做什么 + 给谁用」，因为 Agent 靠它决定要不要加载
    desc_map = {
        "A10": "发布前三合一质检：同时检查平台违规、AI味、事实错误，输出一份带通过/打回判定的合并报告。",
        "A07": "按结构骨架撰写1500-3000字初稿并生成10个候选标题，缺真实素材处标【待补】不编造。",
        "A09": "把AI生成的规整文字改写成真人口吻，输出改写记录与自检评分。",
        "A16": "让AI搜索推荐你的品牌：把目标提问转成问答式内容规格，并做提及率实测。",
        "A13": "生成带「适合谁/不适合谁」的电商详情页，并做广告法合规检查。",
        "A18": "识别客户意向等级、生成回复草稿与异议处理方案，把对话沉淀进跟进台账。",
        "A03": "从一批抓取内容中筛出与账号定位相关的部分，生成标准情报卡并打分。",
        "A05": "汇总情报、素材与历史表现，产出4-6个候选选题并打四维评分。",
        "A12": "汇总近30天发布数据找出规律，输出最多5条今天就能执行的调整项。",
        "A22": "为虚拟商品出5张主图分工方案、7屏详情页脚本与标题候选，并做视觉合规体检。",
    }
    desc = desc_map.get(code, f"{name}：{meta['deps'][:60]}")

    fm = {
        "name": build_slug(code, name),
        "description": desc,
        "version": VERSION,
        "author": AUTHOR,
        "tags": ["content", "ai-workflow", "china", "marketing"],
        "license": "MIT-0",
    }
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for x in v:
                lines.append(f"  - {x}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {code} {name}")
    lines.append("")
    lines.append(f"> {meta['en']} · {meta['layer']} · 自动化等级 {meta['auto']}")
    lines.append("")

    prereq = PREREQ.get(code)
    if prereq:
        lines.append("## 使用前提")
        lines.append("")
        lines.append(prereq)
        lines.append("")

    lines.append("## 角色与执行方式")
    lines.append("")
    lines.append(prompt)
    lines.append("")

    if sections.get("vars"):
        lines.append("## 输入变量")
        lines.append("")
        lines.append(sections["vars"])
        lines.append("")

    if sections.get("asks"):
        lines.append("## 典型用法")
        lines.append("")
        for a in sections["asks"]:
            lines.append(f"- {a}")
        lines.append("")

    lines.append("## 已知限制")
    lines.append("")
    lines.append("- 平台规则类判断依赖你提供的规则清单，**不会自动联网更新**。")
    lines.append("- 需要真实经历才能写好的内容，它会标【待补】等你补，不会替你编。")
    lines.append("- 输出是草稿，对外发布前请自行确认。")
    lines.append("")
    return "\n".join(lines)


def build_clawhub_json(code, name, desc, price):
    return {
        "name": build_slug(code, name),
        "displayName": f"{code} {name}",
        "version": VERSION,
        "description": desc,
        "author": AUTHOR,
        "license": "MIT-0",
        "pricing": {"model": "one-time", "price": price * 100},
        "tags": ["content", "ai-workflow", "china", "marketing"],
        "minOpenClawVersion": MIN_CLAW_VERSION,
        "repository": "",
    }


def build_readme(code, name, desc, price, sections):
    L = []
    L.append(f"# {code} {name}")
    L.append("")
    L.append(f"**{desc}**")
    L.append("")
    L.append(f"售价：¥{price}　|　一次买断，永久使用，可升级")
    L.append("")
    L.append("## 装上之后你的工作流长什么样")
    L.append("")
    L.append("```")
    L.append("你 ──把稿子和平台给它──▶ 它 ──▶ 一份合并报告：通过 / 打回 / 需人工确认")
    L.append("```")
    L.append("")
    L.append("## 适合谁")
    L.append("")
    good, bad = AUDIENCE.get(code, (["靠平台流量吃饭的内容号、电商卖家",
                                    "一个人要顶一个团队的小微团队负责人",
                                    "不想自己调提示词、只想要能直接用的结果的人"],
                                   ["还没想清楚自己要做什么的人"]))
    for g in good:
        L.append(f"- ✅ {g}")
    L.append("")
    L.append("## 不适合谁")
    L.append("")
    for b in bad:
        L.append(f"- ❌ {b}")
    L.append("")
    L.append("## 怎么用（三步）")
    L.append("")
    L.append("1. 在技能中心搜索本技能，点「一键安装」")
    L.append("2. **重启客户端**（不重启 Agent 认不到新技能——这是最常见的坑）")
    L.append("3. 直接说人话，比如：")
    for a in sections.get("asks", [])[:3] or ["检查这篇能不能发"]:
        L.append(f"   - 「{a}」")
    L.append("")
    L.append("## 前置条件")
    L.append("")
    prereq = PREREQ.get(code)
    if prereq:
        L.append(prereq.replace("**必须自带**：", "需要准备：").replace("**建议自带**：", "建议准备："))
    else:
        L.append("无需额外准备，直接可用。")
    L.append("")
    L.append("## 常见问题")
    L.append("")
    L.append("**Q：装完不生效？**")
    L.append("A：99% 是没重启。装完必须重启客户端。")
    L.append("")
    L.append("**Q：平台规则变了怎么办？**")
    L.append("A：涉及平台红线的技能，内置规则会过期。本技能会明确告诉你「必须自带规则清单」，"
             "把你手上的现行规则喂给它，比用内置的旧版准。")
    L.append("")
    L.append("**Q：输出能直接发布吗？**")
    L.append("A：输出是草稿。它会标出该你确认的地方（涉金额、承诺、绝对化表述），确认后再用。")
    L.append("")
    L.append("## 售后")
    L.append("")
    L.append("- 安装问题：公众号 `LobsterSkill` 留言，24 小时内回")
    L.append("- 不好用：7 天内可退，不问理由")
    L.append("- 想要更多技能：店铺「龙虾指令集」持续更新")
    L.append("")
    return "\n".join(L)


def build_install_guide(code, name, sections):
    L = []
    L.append(f"# {code} {name} · 安装说明")
    L.append("")
    L.append("## 适用客户端")
    L.append("")
    L.append("- 腾讯 QClaw（小龙虾）")
    L.append("- OpenClaw")
    L.append("- 小龙虾 AI（Web / iOS）")
    L.append("")
    L.append("## 安装步骤")
    L.append("")
    L.append("1. **下载并解压**本技能包")
    L.append("2. **放进技能目录**：")
    L.append("")
    L.append("   ```")
    L.append(f"   skills/{build_slug(code, name)}/")
    L.append("   ```")
    L.append("")
    L.append("   > 也可以直接在客户端「技能中心」里安装压缩包，效果一样。")
    L.append("")
    L.append("3. **重启客户端**（关键，漏了这步技能不生效）")
    L.append("")
    L.append("4. 打开技能中心，确认能看到本技能")
    L.append("")
    L.append("## 验证是否装成功")
    L.append("")
    L.append("在对话里说：")
    L.append("")
    for a in sections.get("asks", [])[:3] or ["检查这篇能不能发"]:
        L.append(f"- 「{a}」")
    L.append("")
    L.append("正常会返回结构化输出。")
    L.append("")
    L.append("## 遇到问题")
    L.append("")
    L.append("| 现象 | 原因 | 解决 |")
    L.append("|---|---|---|")
    L.append("| 技能列表里找不到 | 目录层级错了 | 确认 `skills/` 下直接是技能文件夹 |")
    L.append("| 装完 Agent 不认 | 没重启 | 重启客户端 |")
    L.append("| 输出格式不对 | 模型没跟上 | 换更强的模型再试 |")
    L.append("| 说缺少前提 | 这是设计 | 把要求的规则清单/资料喂给它 |")
    L.append("")
    return "\n".join(L)


def list_roles():
    if not os.path.isdir(ROLES_DIR):
        print(f"找不到岗位目录：{ROLES_DIR}")
        return []
    files = []
    for fn in sorted(os.listdir(ROLES_DIR)):
        if fn.endswith(".md") and re.match(r"^A\d\d_", fn):
            files.append(os.path.join(ROLES_DIR, fn))
    return files


def main(argv):
    only = [a.upper() for a in argv if re.match(r"^A\d\d$", a.upper())]
    show_list = "--list" in argv
    out_dir = OUT_DIR
    for a in argv:
        if a.startswith("--out="):
            out_dir = a.split("=", 1)[1]

    files = list_roles()
    if only:
        files = [f for f in files
                 if os.path.basename(f)[:3].upper() in only]

    if show_list:
        print(f"共 {len(list_roles())} 个岗位可打包：\n")
        for f in list_roles():
            fn = os.path.basename(f)
            code = fn[:3]
            name = fn[3:].replace(".md", "").lstrip("_")
            price = PRICING.get(code, (49, ""))[0]
            print(f"  {code}  {name:<14} ¥{price}")
        return 0

    if not files:
        print("没有可打包的岗位")
        return 1

    os.makedirs(out_dir, exist_ok=True)
    built = []

    for path in files:
        fn = os.path.basename(path)
        code = fn[:3].upper()
        name = fn[3:].replace(".md", "").lstrip("_")
        with open(path, "r", encoding="utf-8") as f:
            md = f.read()

        meta = parse_meta(md)
        prompt = extract_prompt(md)
        sections = extract_sections(md)
        if not prompt:
            print(f"  跳过 {fn}：没抽到提示词")
            continue

        price, desc = PRICING.get(code, (49, f"{name}：内容生产流水线岗位技能"))
        slug = build_slug(code, name)
        pkg = os.path.join(out_dir, slug)
        os.makedirs(pkg, exist_ok=True)

        skill_md = build_skill_md(code, name, meta, prompt, sections)
        with open(os.path.join(pkg, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_md)

        cj = build_clawhub_json(code, name, desc, price)
        with open(os.path.join(pkg, "clawhub.json"), "w", encoding="utf-8") as f:
            json.dump(cj, f, ensure_ascii=False, indent=2)

        with open(os.path.join(pkg, "README.md"), "w", encoding="utf-8") as f:
            f.write(build_readme(code, name, desc, price, sections))

        with open(os.path.join(pkg, "安装说明.md"), "w", encoding="utf-8") as f:
            f.write(build_install_guide(code, name, sections))

        # 打包 zip（自动发货直接发这个）
        zp = os.path.join(out_dir, f"{slug}.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _d, fs in os.walk(pkg):
                for x in fs:
                    full = os.path.join(root, x)
                    z.write(full, os.path.relpath(full, out_dir))

        built.append((code, name, price, slug, len(skill_md)))

    print(f"\n打包完成：{len(built)} 个技能包")
    print(f"输出目录：{out_dir}\n")
    print(f"{'编号':<6}{'岗位':<16}{'定价':<8}{'SKILL.md':<12}目录名")
    print("-" * 72)
    for code, name, price, slug, size in built:
        print(f"{code:<7}{name:<17}¥{price:<7}{size:<12}{slug}")

    total = sum(p for _c, _n, p, _s, _z in built)
    print(f"\n{len(built)} 个技能全上 = ¥{total}")
    print("\n下一步：")
    print("  1. 打开每个包里的 README.md 检查一遍描述是否够具体（这一句决定卖不卖得动）")
    print("  2. 把 zip 挂到淘宝「龙虾指令集」店铺，标题带搜索词")
    print("  3. 选 3 个最好卖的做「主推款」，其余当「凑单款」")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

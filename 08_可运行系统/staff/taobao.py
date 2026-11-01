"""淘宝视觉官：主图 + 详情页 + 标题的完整设计与合规体检。

背景
----
用户的主力成交口是淘宝店（lxzlj.taobao.com），卖两类产品：
    1. AI 办公提效（Skill 包、提示词、流程包）
    2. 工业机器人资料包（编程、力控、PLC 联动）

这两类产品有一个共同点，也是本模块存在的理由：
**它们都是"看不见的商品"。** 买家看不到实物，收到的是电子文件或安装包。
所以主图不能拍实物，主图必须靠"信息设计"把抽象价值变成可点击的画面。

但淘宝 2026 年的规则把这件事卡得很死：
    - 主图第 1 张必须 1:1、≥800×800、文字面积 ≤10-20%
    - 禁促销标（秒杀/包邮/满减）、禁水印、禁二维码、禁极限词
    - 第 5 张强制纯白底 RGB(255,255,255)、无文字无阴影、主体占 70-80%
    - 详情页宽度 750px，首屏 750×600
    - **AI 生成主图必须显著标识**（《人工智能生成合成内容标识办法》
      2025-09-01 施行 + 淘宝 AI 假图治理，商家不标识 = 侵权知情权）
    - 虚拟商品必须选虚拟物品类目，详情页须写明"虚拟产品一经售出不退不换"
    - 站内禁止站外导流（微信/二维码）→ 所以主图与详情页里
      **不能放任何微信号、二维码**，成交必须留在淘宝站内

本模块做四件事：
    1. 主图方案   —— 5 张图的分工、文案、版式、尺寸，全部给到可直接执行
    2. 详情页脚本 —— 分屏结构 + 每屏文案 + 首屏钩子
    3. 标题生成   —— 搜索词前置、长度合规、不含极限词
    4. 合规体检   —— 逐条比对淘宝 + 广告法红线，不过就给出替代写法

设计原则
--------
- **虚拟商品的视觉靠"承诺 + 交付清单"，不靠"场景摆拍"。**
  场景图是给实物用的，资料包用场景图等于货不对板。
- **不用 AI 图冒充实物。** AI 只能用来做信息图/示意图/图标，且必须标识。
  凡是"看起来像实物"的一律不做——这是 20 万罚款线。
- **不放站外联系方式。** 卖家的唯一目标是把成交留在站内。
"""

import os
import re

# ---------------------------------------------------------------------------
# 一、淘宝视觉硬参数（2026 年现行，以千牛后台最新提示为准）
# ---------------------------------------------------------------------------

SPEC = {
    "main_1x1": {"size": "1200×1200", "min": "800×800", "ratio": "1:1"},
    "main_3x4": {"size": "750×1000 或 1200×1600", "ratio": "3:4"},
    "detail_width": 750,
    "detail_first_screen": "750×600",
    "video_main": {"duration": "9-30 秒", "size": "≤20MB", "ratio": "1:1 或 3:4",
                   "rule": "首帧必须与第 1 张主图一致"},
    "file": {"format": "JPG/PNG", "size": "≤3MB", "dpi": "≥72",
             "white_bg": "RGB(255,255,255)"},
    "limits": {
        "text_area": "主图文字面积 ≤10%-20%，移动端不超过 3 行",
        "subject_ratio": "主体占画面 70% 以上",
        "white_5th": "第 5 张强制纯白底、无文字/无阴影/无边框/无模特，主体占 70%-80%",
        "promo": "禁促销标（秒杀/包邮/满减）、禁自定义边框、禁水印、禁二维码",
        "extreme": "禁「最/第一/顶级/全网最低/100%/根治」",
        "outlink": "禁微信号/二维码/手机号/站外链接",
    },
}

# ---------------------------------------------------------------------------
# 二、5 张主图分工（虚拟商品通用打法）
# ---------------------------------------------------------------------------

MAIN_SLOTS = [
    {
        "no": 1,
        "role": "搜索封面",
        "job": "决定搜索结果里的点击率",
        "must": "1:1、≥800×800、主体占 70%+、浅色干净底、文字 ≤10%",
        "content": "一句话核心承诺 + 品类词 + 一个具体数字",
        "avoid": "促销标、二维码、极限词、超过 3 行字",
    },
    {
        "no": 2,
        "role": "交付清单",
        "job": "消除「买到手是什么」的疑虑",
        "must": "文字可读、不遮挡",
        "content": "包里到底有什么：目录截图或清单列表（3-6 项）",
        "avoid": "抽象图标堆砌、看不出实物内容",
    },
    {
        "no": 3,
        "role": "使用场景",
        "job": "让买家认出「这就是我需要的」",
        "must": "场景真实、贴近买家日常",
        "content": "打开就能用的样子：终端截图 / 流程图 / 界面局部",
        "avoid": "虚构的客户评价、伪造的对话截图",
    },
    {
        "no": 4,
        "role": "信任背书",
        "job": "降低「会不会是骗子」的疑虑",
        "must": "可验证、留余地",
        "content": "作者背景 + 交付方式 + 售后承诺 + 更新频率",
        "avoid": "伪造检测报告、盗用资质、编造用户数",
    },
    {
        "no": 5,
        "role": "白底图",
        "job": "被抓进「猜你喜欢」公域流量池",
        "must": "RGB(255,255,255)、无文字/无阴影/无边框/无模特、主体占 70%-80%",
        "content": "虚拟商品的等价物：产品名 + 规格标牌式排版（若类目强制无文字则只留图形）",
        "avoid": "任何文字、渐变阴影、边框、模特",
    },
]

# ---------------------------------------------------------------------------
# 三、详情页分屏脚本（750px 宽，首屏 750×600）
# ---------------------------------------------------------------------------

DETAIL_SLOTS = [
    {"no": 1, "screen": "首屏 750×600", "name": "钩子",
     "must": "3 秒内让人知道「这是给谁的、解决什么」",
     "template": "【给{人群}】{具体问题}——{具体结果}\n（下方配真实界面截图）"},
    {"no": 2, "screen": "第二屏", "name": "痛点具象化",
     "must": "用买家的原话，不用形容词",
     "template": "你是不是也遇到过：\n1. {痛点原话}\n2. {痛点原话}\n3. {痛点原话}"},
    {"no": 3, "screen": "第三屏", "name": "交付清单",
     "must": "逐项列出，含格式、页数/条数",
     "template": "包内含 {N} 项：\n① {名称}（{格式}，{数量}）\n② …"},
    {"no": 4, "screen": "第四屏", "name": "使用前后",
     "must": "只对比可验证的工作量，不承诺效果数字",
     "template": "用之前：{原流程与耗时}\n用之后：{新流程与耗时}\n（耗时按你自己的实测数据填，不要估）"},
    {"no": 5, "screen": "第五屏", "name": "适用与不适用",
     "must": "必须写不适用，这是降售后纠纷最快的方式",
     "template": "适合：{条件1}、{条件2}\n不适合：{条件1}、{条件2}"},
    {"no": 6, "screen": "第六屏", "name": "交付与售后",
     "must": "写清交付方式、时效、退款规则",
     "template": "交付：{方式}，下单后 {时长} 内可取\n更新：{更新频率与周期}\n退款：{规则}"},
    {"no": 7, "screen": "第七屏", "name": "虚拟商品声明 + AI 声明",
     "must": "法务必需，不可省",
     "template": "本商品为虚拟商品，交付形式为 {形式}，一经售出不退不换。\n"
                "本商品由作者原创整理，非官方产品，与任何品牌/厂商无隶属关系。\n"
                "页面配图含 AI 辅助生成的示意图，已按《人工智能生成合成内容标识办法》标注。"},
]

# ---------------------------------------------------------------------------
# 四、红线词库（广告法 + 淘宝 + 平台）
# 分三级：🔴 绝对禁止 / 🟡 高风险 / 🟢 需人工确认
# ---------------------------------------------------------------------------

RED = {
    "extreme": {
        "level": "🔴",
        "words": ["最强", "最好", "最佳", "最优", "最高级", "最低价", "全网最低",
                  "全网第一", "第一品牌", "行业第一", "销量第一", "NO.1", "TOP1",
                  "唯一", "独家", "仅此一家", "首个", "首发", "首款", "国家级",
                  "世界级", "顶级", "顶尖", "天花板", "yyds", "绝绝子", "封神",
                  "100%", "百分百", "绝对", "万能", "永久", "永不", "彻底",
                  "根治", "全部消除", "零失败", "无副作用", "秒杀", "全网最",
                  "销量冠军", "行业龙头", "唯一指定"],
        "replace": {
            "最强": "更适合{场景}的一套",
            "最好": "我这套最常用的",
            "全网最低": "¥{价格}，含 {N} 项交付",
            "第一": "较早做这一块的",
            "唯一": "少数同时做 {X} 和 {Y} 的",
            "100%": "按 {N} 个真实案例测过",
            "根治": "针对 {具体场景} 优化",
            "顶级": "行业里比较常用的做法",
            "天花板": "我目前做的水平",
            "独家": "我自己整理的",
        },
    },
    "false": {
        "level": "🔴",
        "words": ["保证学会", "包会", "永久有效", "终身", "无效退款", "假一赔十",
                  "全额返现", "必涨", "月入过万", "稳赚", "包过", "包就业",
                  "官方", "授权", "旗舰", "专卖", "专营", "官方出品", "正品行货",
                  "原厂", "正版授权"],
        "replace": {
            "保证学会": "含使用说明与 {N} 个示例",
            "包会": "附 {N} 条排错提示",
            "官方": "（若无授权则删掉这个词）",
            "旗舰": "（无旗舰店资质则删掉）",
            "正品行货": "个人原创整理，非第三方产品",
        },
    },
    "medical": {
        "level": "🔴",
        "words": ["治疗", "治愈", "根治", "疗效", "消炎", "杀菌", "降糖",
                  "降压", "抗癌", "防癌", "助眠", "增强免疫力", "零激素"],
        "replace": {"治疗": "解决", "助眠": "帮你把睡前流程固定下来"},
    },
    "outlink": {
        "level": "🔴",
        "words": ["微信", "加V", "vx", "威信", "二维码", "手机号", "QQ",
                  "私信我发", "主页有惊喜", "扫码", "淘宝口令", "闲鱼"],
        "replace": {"微信": "（改为：下单后通过淘宝消息联系）"},
    },
    "false_screenshot": {
        "level": "🔴",
        "words": ["客户真实反馈", "已售出 1000 份", "好评率 100%", "行业第一",
                  "亲测有效率 100%"],
        "replace": {"客户真实反馈": "（改成你自己实测的截图）"},
    },
    "ai_unmarked": {
        "level": "🔴",
        "words": ["（判定项，非关键词）AI 生成的主图未标注 AI 标识",
                  "AI 生成图与实物差异大导致货不对板"],
        "replace": {"AI 生成的主图未标注": "加「AI 辅助生成示意图」角标 + 详情页声明"},
    },
    "promise": {
        "level": "🟡",
        "words": ["立刻见效", "马上", "彻底解决", "无门槛", "零基础也能",
                  "任何人都能", "一键搞定全部", "自动赚钱", "被动收入"],
        "replace": {"零基础也能": "第一次用需要 {N} 分钟读一遍说明"},
    },
    "compare": {
        "level": "🟡",
        "words": ["碾压", "吊打", "完爆", "秒杀同类", "比某某牌好"],
        "replace": {"吊打": "和常见的做法差别在 {具体一点}"},
    },
    "urgency": {
        "level": "🟡",
        "words": ["最后一天", "随时涨价", "仅此一次", "随时结束", "错过再等一年"],
        "replace": {"最后一天": "活动到 {具体日期}"},
    },
    "ai_disclosure": {
        "level": "🟢",
        "words": ["（判定项）页面配图含 AI 生成的示意图是否已标注"],
        "replace": {"需人工确认": "在第 4 屏或末屏加 AI 声明，并给 AI 图加角标"},
    },
}

# ---------------------------------------------------------------------------
# 五、产品线模板
# ---------------------------------------------------------------------------

PRODUCT_LINES = {
    "ai_efficiency": {
        "name": "AI 办公提效",
        "title_tpl": "{关键词} {人群} {具体结果} | {交付形式} {数量}份",
        "keywords": ["AI提效", "办公提效", "提示词", "工作流", "自动化办公",
                     "AI技能包", "效率工具"],
        "main1_tpl": "{核心承诺}（{交付数量}）\n{品类词}",
        "pack_tpl": "① {技能名}（{用途}）\n② {技能名}（{用途}）\n③ {技能名}（{用途}）\n"
                    "④ 安装说明 + {N} 个示例",
        "scene_tpl": "打开就能用的样子：\n{真实界面截图位置}\n（用你自己截的图）",
        "trust_tpl": "作者：{你的身份标签}\n交付：下单后自动发货（网盘链接）\n"
                     "更新：{更新频率}\n售后：{响应方式}",
        "who_fit": "已经用 AI 但每天还在重复做同一件事的办公人群",
        "who_not": "完全没用过 AI、不想装任何工具的人",
        "price_ref": "¥29-99 / 技能包，¥199-499 / 流程包",
    },
    "robot": {
        "name": "工业机器人资料包",
        "title_tpl": "{品牌型号} {编程类型} {内容类型} | {数量}套 {格式}",
        "keywords": ["机器人编程", "示教器", "力控", "PLC联动", "打磨工作站",
                     "六轴", "轨迹规划", "IO信号", "卡诺普", "汇川"],
        "main1_tpl": "{具体能解决的问题}\n{品牌}{型号} {内容类型}",
        "pack_tpl": "① {内容}（{格式}）\n② {内容}（{格式}）\n③ {内容}（{格式}）\n"
                    "④ 现场调试记录 + 常见故障排查",
        "scene_tpl": "程序结构长这样：\n{代码/流程图位置}\n（用你自己写的脱敏截图）",
        "trust_tpl": "作者：{你的真实项目背景}\n来源：{真实项目经历}\n"
                     "交付：网盘链接，下单后 {时长} 内可取\n售后：{响应方式}",
        "who_fit": "正在做机器人集成/调试、缺可参考案例的工程师",
        "who_not": "零基础想转行、指望看完就会编程序的人",
        "price_ref": "¥49-199 / 资料包",
        "critical": [
            "**必须自己编写。** 不得收录任何厂商的示教器程序、原厂图纸、"
            "手册截图、RobotStudio/RoboGuide 工程文件。",
            "**必须脱敏。** 真实现场截图要抹掉客户名、图号、IP、报警代码。",
            "**不得使用厂商 Logo。** 标题与主图里不出现 FANUC/ABB/库卡等商标。",
            "**免责声明必写。** 「非官方教程，作者原创整理，与 {品牌} 无隶属关系，"
            "商标归其公司所有」。",
        ],
    },
}

# ---------------------------------------------------------------------------
# 六、审查与生成
# ---------------------------------------------------------------------------


def check_text(text, platform="淘宝"):
    """检查一段文案是否命中红线。返回 (hits, ok)。

    豁免机制（2026-10-31 新增）
    -------------------------
    裸词匹配会误伤两类**必须出现**的表述：

      1. 「非官方产品」「非官方教程」—— 这是平台强制要求的免责声明，
         删掉它反而违规。规则要认「非官方」是豁免语境。
      2. 否定句里的敏感词，如「不适合期待装上就能自动赚钱的人」
         —— 这里「自动赚钱」是被否定的对象，不是承诺。

    所以命中之后再看一眼上下文：命中词的前后若构成豁免语境，
    就不算违规。豁免规则集中放在 EXEMPT_PATTERNS，
    每条是一个 (前缀正则, 命中词) —— 前缀匹配即豁免。
    """
    hits = []
    low = (text or "").lower()
    for cat, spec in RED.items():
        if cat == "ai_unmarked":
            continue
        for w in spec["words"]:
            if w.lower() in low:
                if _is_exempt(text, w):
                    continue
                rep = spec.get("replace", {}).get(w, "")
                hits.append({
                    "level": spec["level"], "cat": cat, "word": w,
                    "replace": rep, "platform": platform,
                })
    return hits, len(hits) == 0


# 豁免上下文：命中词出现在这些前缀之后时不算违规。
# 每条是 (前缀正则, 命中词) —— 前缀用 re.search 在原文里找。
EXEMPT_PATTERNS = [
    # 「非官方产品 / 非官方教程」= 免责声明，平台强制要求写
    (r"非官方", "官方"),
    # 「官方账单 / 官方侧 / 官方接口」指平台自己的对账单，
    # 不是「官方授权 / 官方出品」的暗示 —— 这类词组本身就是豁免词组。
    (r"(官方账单|官方侧|官方接口|官方文档|官方用量|官方价格)", "官方"),
    # 否定语境：「不适合…自动赚钱」「不会…月入过万」
    (r"(不适合|不要指望|不承诺|不等于|不会|别指望|不要幻想|并非|不保证)", "自动赚钱"),
    (r"(不适合|不要指望|不承诺|不等于|不会|别指望|不要幻想|并非|不保证)", "月入过万"),
    (r"(不适合|不要指望|不承诺|不等于|不会|别指望|不要幻想|并非|不保证)", "稳赚"),
    (r"(不适合|不要指望|不承诺|不等于|不会|别指望|不要幻想|并非|不保证)", "被动收入"),
]


def _is_exempt(text: str, word: str) -> bool:
    """判断命中词是否处在豁免语境里。

    做法：找到豁免前缀，再看敏感词是否**紧跟其后**（中间允许插入
    若干个字，如「不适合：期待装上就能自动赚钱的人」——
    前缀「不适合」与敏感词之间隔了 9 个字，所以窗口不能只按词长算，
    而要给一个实际的语境距离上限）。
    """
    if not text or not word:
        return False
    for pattern, target in EXEMPT_PATTERNS:
        if target != word:
            continue
        for m in re.finditer(pattern, text):
            # 情形一：前缀本身就是一个完整豁免词组（如「官方账单」）
            if m.group(0) != target and word in m.group(0):
                return True
            # 情形二：敏感词紧跟前缀之后
            start = m.end()
            idx = text.find(word, start)
            if idx < 0:
                continue
            # 前缀与敏感词之间最多隔 24 个字，再远就不是同一个语境了
            if idx - start <= 24:
                return True
    return False


def check_title(title):
    """标题专项检查：长度、极限词、站外词。"""
    issues = []
    n = len(title or "")
    if n > 30:
        issues.append(f"标题 {n} 字，超过 30 字会在搜索结果里被截断，建议压到 30 字内")
    if n < 8:
        issues.append(f"标题仅 {n} 字，太短，搜不到")
    hits, _ = check_text(title)
    for h in hits:
        issues.append(f"[{h['level']}] 命中「{h['word']}」"
                      + (f" → 建议改为「{h['replace']}」" if h["replace"] else ""))
    # 搜索词前置检查
    if title:
        head = title[:4]
        core = [k for k in ("AI提效", "办公提效", "机器人编程", "示教器", "力控",
                            "PLC联动", "提示词", "工作流", "打磨")
                 if k in title]
        if core and not any(k[:2] in head for k in core):
            issues.append(f"核心词「{core[0]}」不在前 4 个字，搜索权重会低，建议前置")
    return issues


def build_main_images(line_key="ai_efficiency", product="", extra=""):
    """生成 5 张主图的完整方案（可直接交给设计或自己照着做）。"""
    line = PRODUCT_LINES.get(line_key, PRODUCT_LINES["ai_efficiency"])
    p = product or "（未填产品名）"

    imgs = []
    for s in MAIN_SLOTS:
        if s["no"] == 1:
            if line_key == "robot":
                text = (extra or "打磨工作站力控怎么调") + "\n" + \
                       (product or "工业机器人编程资料包")
            else:
                text = (extra or "把每天重复的那件事交给 AI") + "\n" + \
                       (product or "AI 办公提效技能包")
        elif s["no"] == 2:
            text = line["pack_tpl"].format(
                技能名="XX 技能", 用途="XX", 内容="XX", 格式="XX",
                N=3, 名称="XX")
        elif s["no"] == 3:
            if line_key == "robot":
                text = ("程序结构长这样：\n"
                        "（贴你自己写的脱敏代码或流程图）")
            else:
                text = ("打开就能用的样子：\n"
                        "（贴你自己截的真界面图）")
        elif s["no"] == 4:
            if line_key == "robot":
                text = ("作者：工业机器人编程工程师\n"
                        "来源：机器人打磨工作站、力控、PLC 联动真实项目\n"
                        "交付：网盘链接，下单后 10 分钟内可取\n"
                        "售后：淘宝消息，工作日 24 小时内\n"
                        "声明：非官方教程，与厂商无隶属关系")
            else:
                text = ("作者：工业机器人编程工程师 + AI 内容系统实践者\n"
                        "交付：下单后自动发货（网盘链接）\n"
                        "更新：每两周更新一次\n"
                        "售后：淘宝消息，工作日 24 小时内")
        else:
            text = "（无文字）"
        imgs.append({
            "no": s["no"], "role": s["role"], "job": s["job"],
            "must": s["must"], "avoid": s["avoid"], "text": text,
        })
    return {
        "line": line["name"], "product": p,
        "spec": SPEC, "images": imgs,
        "video": SPEC["video_main"],
    }


def build_detail(line_key="ai_efficiency"):
    """生成详情页分屏脚本。"""
    line = PRODUCT_LINES.get(line_key, PRODUCT_LINES["ai_efficiency"])
    is_robot = (line_key == "robot")

    # 一次性准备替换表，避免二次替换出错
    ctx = {
        "人群": line["who_fit"],
        "具体问题": ("打磨工作站的力控怎么调、程序怎么写才不撞"
                     if is_robot else
                     "写文案、做表格、查资料这些每天重复的事"),
        "具体结果": ("省下 2 天现场调试" if is_robot else "省下每天 1-2 小时"),
        "痛点原话": ("示教器点位录完一改工件就要重来" if is_robot
                     else "每天都在做，但每次都要重新想一遍怎么做"),
        "N": "3",
        "名称": "内容", "格式": "Markdown", "数量": "10 个示例",
        "原流程与耗时": ("自己试参数，一改就是一天" if is_robot
                        else "自己从零想 + 复制粘贴（每次 40 分钟）"),
        "新流程与耗时": ("照着模板改参数，改一次十分钟" if is_robot
                        else "装上就用（每次 3 分钟）"),
        "条件1": line["who_fit"],
        "条件2": "愿意花 10 分钟读一遍说明",
        "条件3": "需要固定流程的人",
        "方式": "网盘链接自动发货",
        "时长": "10 分钟",
        "更新频率": "每两周", "更新频率与周期": "每两周一次",
        "规则": "虚拟商品，一经售出不退不换；"
                "若文件损坏或无法打开，凭截图申请退",
        "形式": "电子文件/网盘链接",
        "品牌": "卡诺普",
    }

    screens = []
    for s in DETAIL_SLOTS:
        t = s["template"]
        # 单次遍历替换，长键优先，避免 {N} 被 {名称} 之类的短键抢先
        for k in sorted(ctx, key=len, reverse=True):
            t = t.replace("{" + k + "}", ctx[k])
        screens.append({
            "no": s["no"], "screen": s["screen"], "name": s["name"],
            "must": s["must"], "text": t,
        })
    return {"line": line["name"], "width": SPEC["detail_width"],
            "first_screen": SPEC["detail_first_screen"], "screens": screens}


def build_title(line_key="ai_efficiency", product="", keyword="", audience=""):
    """生成候选标题 + 关键词包。"""
    line = PRODUCT_LINES.get(line_key, PRODUCT_LINES["ai_efficiency"])
    kws = line["keywords"]

    if line_key == "robot":
        prod = product or "工业机器人编程资料包"
        cands = [
            f"{prod} {keyword or '示教器编程'} 力控调试 含案例",
            f"{keyword or '机器人编程'} {prod} {audience or '工程师'} 实测记录",
            f"工业机器人 {keyword or '打磨工作站'} 编程调试资料 含故障排查",
        ]
    else:
        prod = product or "AI办公提效技能包"
        cands = [
            f"{keyword or 'AI提效'} {prod} 21个技能 自动发货",
            f"{audience or '办公提效'} {keyword or '提示词包'} {prod} 实测可用",
            f"{prod} {keyword or '工作流'} 真实场景整理 含示例",
        ]

    out = []
    for c in cands:
        # 清掉占位残留（模板里用到的布尔技巧产生的空串）
        c = re.sub(r"\s{2,}", " ", c).strip()
        issues = check_title(c)
        out.append({"title": c, "len": len(c),
                    "issues": issues, "ok": not issues})
    return {"line": line["name"], "candidates": out,
            "keywords": kws, "price_ref": line["price_ref"]}


def full_check(texts):
    """对一整包文案（主图+详情+标题）做合规体检。返回 (report, pass)。"""
    rows = []
    for label, t in texts:
        hits, ok = check_text(t)
        rows.append({"label": label, "text": t, "hits": hits, "ok": ok})
    return rows


# ---------------------------------------------------------------------------
# 七、Markdown 渲染
# ---------------------------------------------------------------------------


def render_main_plan(m, spec=None):
    spec = spec or m["spec"]
    L = []
    L.append(f"# 淘宝主图方案 · {m['line']}")
    L.append("")
    L.append(f"产品：{m['product']}")
    L.append("")
    L.append("## 硬参数（照抄就行）")
    L.append("")
    L.append("| 项 | 要求 |")
    L.append("|---|---|")
    L.append(f"| 第 1 张尺寸 | {spec['main_1x1']['size']}（最低 {spec['main_1x1']['min']}，"
             f"比例 {spec['main_1x1']['ratio']}） |")
    L.append(f"| 3:4 竖版 | {spec['main_3x4']['size']} |")
    L.append(f"| 详情页宽度 | {spec['detail_width']}px，首屏 {spec['detail_first_screen']} |")
    L.append(f"| 文件 | {spec['file']['format']}，≤{spec['file']['size']}，"
             f"{spec['file']['dpi']}dpi |")
    L.append(f"| 白底 RGB | {spec['file']['white_bg']} |")
    L.append(f"| 视频主图 | {spec['video_main']['duration']}，{spec['video_main']['size']}，"
             f"{spec['video_main']['rule']} |")
    L.append("")
    L.append("## 5 张图分别干什么")
    L.append("")
    for im in m["images"]:
        L.append(f"### 第 {im['no']} 张 · {im['role']}")
        L.append("")
        L.append(f"- **作用**：{im['job']}")
        L.append(f"- **必须满足**：{im['must']}")
        L.append(f"- **文案草稿**：")
        L.append("")
        L.append("```")
        L.append(im["text"])
        L.append("```")
        L.append("")
        L.append(f"- **不要**：{im['avoid']}")
        L.append("")
    L.append("## 全局禁止项")
    L.append("")
    for k, v in spec["limits"].items():
        L.append(f"- **{k}**：{v}")
    L.append("")
    return "\n".join(L)


def render_detail(d):
    L = []
    L.append(f"# 淘宝详情页脚本 · {d['line']}")
    L.append("")
    L.append(f"宽度 {d['width']}px，首屏 {d['first_screen']}px。"
             "所有图统一 750px 宽，拼接时留 1-2px 透明缝。")
    L.append("")
    for s in d["screens"]:
        L.append(f"## 第 {s['no']} 屏 · {s['name']}")
        L.append("")
        L.append(f"- **要求**：{s['must']}")
        L.append("")
        L.append("```")
        L.append(s["text"])
        L.append("```")
        L.append("")
    return "\n".join(L)


def render_titles(t):
    L = []
    L.append(f"# 淘宝标题候选 · {t['line']}")
    L.append("")
    L.append(f"参考定价：{t['price_ref']}")
    L.append("")
    L.append("## 候选标题")
    L.append("")
    for c in t["candidates"]:
        mark = "✅" if c["ok"] else "⚠️"
        L.append(f"### {mark} {c['title']}")
        L.append("")
        L.append(f"长度 {c['len']} 字")
        if c["issues"]:
            for i in c["issues"]:
                L.append(f"- ⚠️ {i}")
        L.append("")
    L.append("## 关键词包（写标题和详情时都用得上）")
    L.append("")
    L.append("、".join(t["keywords"]))
    L.append("")
    L.append("**规则**：核心词放前 4 个字；标题里不放促销词（淘宝禁）；"
             "不堆砌关键词（堆砌会判重复铺货）。")
    L.append("")
    return "\n".join(L)


def render_check(rows, title="淘宝合规体检"):
    L = []
    L.append(f"# {title}")
    L.append("")
    total = sum(len(r["hits"]) for r in rows)
    bad = [r for r in rows if r["hits"]]
    L.append(f"检查 {len(rows)} 段文案，命中 **{total}** 条红线，"
             f"涉及 **{len(bad)}** 段。")
    L.append("")
    if not total:
        L.append("✅ 已知红线库未命中。注意：AI 标识与虚拟商品声明仍需人工确认。")
        L.append("")
        return "\n".join(L)
    for r in bad:
        L.append(f"## {r['label']}")
        L.append("")
        L.append("```")
        L.append(r["text"][:200])
        L.append("```")
        L.append("")
        for h in r["hits"]:
            rep = f" → 建议改为「{h['replace']}」" if h["replace"] else ""
            L.append(f"- {h['level']} **{h['word']}**{rep}")
        L.append("")
    L.append("## 人工确认项（系统判不了）")
    L.append("")
    L.append("- [ ] AI 生成的示意图是否已加「AI 辅助生成」角标")
    L.append("- [ ] 详情页是否已写明「虚拟商品一经售出不退不换」")
    L.append("- [ ] 是否声明「非官方产品，与品牌厂商无隶属关系」")
    L.append("- [ ] 主图/详情是否完全无微信/二维码/手机号")
    L.append("- [ ] 是否使用了自己拍/自己截的真实图（非 AI 伪造实物图）")
    L.append("")
    return "\n".join(L)


def export_plan(outdir, line_key="ai_efficiency", product="", extra=""):
    """导出完整的三份文件（按产品线分子目录，避免互相覆盖）。"""
    line = PRODUCT_LINES.get(line_key, PRODUCT_LINES["ai_efficiency"])
    sub = os.path.join(outdir, line["name"])
    os.makedirs(sub, exist_ok=True)
    m = build_main_images(line_key, product, extra)
    d = build_detail(line_key)
    t = build_title(line_key, product)
    files = []
    for name, content in (
        ("淘宝主图方案.md", render_main_plan(m)),
        ("淘宝详情页脚本.md", render_detail(d)),
        ("淘宝标题与关键词.md", render_titles(t)),
    ):
        p = os.path.join(sub, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        files.append(p)
    return files


if __name__ == "__main__":
    import sys
    line = sys.argv[1] if len(sys.argv) > 1 else "ai_efficiency"
    prod = sys.argv[2] if len(sys.argv) > 2 else ""
    out = sys.argv[3] if len(sys.argv) > 3 else "./淘宝视觉方案"
    for p in export_plan(out, line, prod):
        print("已生成:", p)

# -*- coding: utf-8 -*-
"""素材改写引擎 —— 把一条对标素材，改写成我们自己的文案 / 脚本。

放在 staff/ 而不是 tools/ 的原因（2026-10-31 实测踩过）：
    免安装版（PyInstaller）只打包 staff 包，不含 tools/ 目录。
    放 tools/ 时，控制台的「改写」按钮在 exe 版里直接报
    No module named 'rewrite_material' —— 源码版正常，exe 版失败。
    核心逻辑必须在 staff/ 里，tools/ 只留 CLI 薄封装。

用户 2026-10-31 的原话：
    "后期想做一期这样的视频，也有素材，可以根据这个自动改写成自己的素材，
     做成文案，脚本等发布。"

所以这条链是闭环的最后一环：
    原始素材 → 学到的公式 → 我们的真实数据 → 我们自己的文案

关键原则（跟整个项目一致）
--------------------------
1. **不抄内容，只借结构**。
   从素材里取的是「公式 / 封面版式 / 叙事结构」这些**方法**，
   不是它的观点、它的案例、它的原句。
   —— 这既是道德底线，也是法律底线：把别人的观点洗成自己的，
   平台会判搬运，法务也过不去。

2. **只用实测数字**。
   岗位数、行业包数从代码实时数；token 成本从 telemetry 读；
   部署耗时从实测结果读。拿不到就写「未获取」，绝不编一个。

3. **每条产出都标出处**。
   用了哪个公式、依据哪条视频、验证强度如何，全部写清楚。
   这样客户/同事看了知道这不是拍脑袋写的。

产出三件套：
    标题（3~5 条候选，各带公式出处）
    口播稿（按公式 F 的结构：先接住情绪，再给下一步）
    封面方案（按 T1~T4 选型 + 画面清单）
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))          # staff/
PKG = os.path.dirname(HERE)                               # 08_可运行系统/

from . import materials as M  # noqa: E402

# 公式表与封面模板表 —— 与 title_engine 同一份定义，保证两处口径一致。
#
# title_engine.py 原来放在 tools/，exe 里没有。2026-10-31 已把公式表
# 与封面模板表搬到 staff/title_rules.py（这里直接引用），两边共用一份。
from .title_rules import FORMULAS, COVERS, collect_facts  # noqa: E402


def _facts() -> Dict[str, Any]:
    """采集我们的真实数据。拿不到就返回空字典（下面各处会写「未获取」）。"""
    if collect_facts is None:
        return {}
    try:
        return collect_facts() or {}
    except Exception:
        return {}


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        return "{:,}".format(int(v))
    except Exception:
        return str(v)


# ---------------------------------------------------------------- 主入口


def rewrite(item_id: str) -> Dict[str, Any]:
    """给一条素材生成我们自己的文案三件套。

    返回：
      {"ok": True, "素材": {...}, "标题": [...], "口播稿": "...", "封面方案": {...}}
    """
    item = M.get(item_id)
    if not item:
        return {"ok": False, "error": "找不到素材 %s" % item_id}

    facts = _facts()
    titles = _titles(item, facts)
    script = _script(item, facts)
    cover = _cover(item, facts)

    return {
        "ok": True,
        "素材": item,
        "标题": titles,
        "口播稿": script,
        "封面方案": cover,
        "数据来源": _data_note(facts),
    }


# ---------------------------------------------------------------- 标题


def _titles(item: Dict[str, Any], facts: Dict[str, Any]) -> List[Dict[str, str]]:
    """生成候选标题。

    规则：**结构来自素材所属公式，内容永远用我们自己的数据**。
    对标视频的标题只用来判断「这条属于哪类结构」。
    """
    roles = _fmt(facts.get("岗位数"))
    packs = _fmt(facts.get("行业包数"))
    combos = _fmt(facts.get("岗位乘行业"))
    cost = facts.get("token成本") or {}
    tokens = _fmt(cost.get("累计token"))
    money = cost.get("累计花费")
    deploy = facts.get("部署耗时") or {}
    ds = deploy.get("秒")

    cat = (item.get("类目") or "")
    out: List[Dict[str, str]] = []

    def add(text: str, fkey: str, why: str):
        f = FORMULAS.get(fkey, {})
        out.append({
            "标题": text,
            "公式": "%s · %s" % (fkey, f.get("name", "")),
            "依据": f.get("src", "") or why,
            "验证强度": f.get("verified", "—"),
            "用法": f.get("use", ""),
        })

    # 规模数字 + 零门槛（公式 D，全批 63/63 条遵循）
    if combos:
        add("%s套AI员工团队，客户电脑双击即用" % combos, "D",
            "规模词来自实测岗位数×行业包数")
    elif roles and packs:
        add("%s个岗位 / %s个行业包，解压即用" % (roles, packs), "D",
            "规模词来自实测岗位数与行业包数")

    # 真实成本对比（公式 E）
    if tokens:
        add("跑完一次真实任务，%s token" % tokens, "E",
            "token 来自 telemetry 累计读数")
    if money:
        add("部署一整套AI员工，成本%s元" % money, "E",
            "花费来自 telemetry 累计读数")
    if ds:
        add("客户电脑解压双击，%s秒打开看板开始派活" % ds, "E",
            "耗时来自实测部署脚本（口径：到系统可用，不含模型调用）")

    # 先接住情绪，再给下一步（公式 F，转/赞最高那条的结构）
    if "C " in cat or "H " in cat:
        add("先接住老板对AI的怀疑，再给3条判断标准：这活到底能不能交给AI",
            "F", "对标素材属于观点/认知类，用方法论结构而非工具介绍")
    if combos:
        add("想找现成AI员工团队的，%s套今天就能用起来" % combos, "F",
            "先接住念头再给下一步")
    else:
        add("想找个现成AI工具的，这个项目今天就能用起来", "F",
            "先接住念头再给下一步")
    # 交付方 vs 卖课方（公式 G）
    add("同行卖AI课，我给工厂交付了一整套AI员工", "G",
        "强调可验收，不讲部署教学")

    # 反向许愿（公式 C）
    add("别再自己写方案了，这个岗位现在是AI员工", "C", "反向用法，零情绪词")

    # 碰到我的 X（公式 A，待验证）
    add("碰到我的AI员工团队，你的交付周期算是结束了", "A",
        "公式 A 只有 1 个数据点，标注为待验证")

    # ↓↓↓ 第三批新增：公式 I（接点式）/ J（私人推荐式）/ K（对比结构）↓↓↓
    # 这三条都是 2026-10-31 第三批 19 条实测验证的（转/赞 21.91 / 11.55 / 15.03）。
    #
    # 公式 I 是本批最强信号：单讲一个工具 → 转/赞 8~15；
    # 两个工具「接上了」→ 转/赞 19~22。转发的人在表达自己的进度和判断力。
    # 我们的独家接点是「WorkBuddy + 微信聊天记录」（A26 微信情报官）——
    # 全网目前没有第二个人做这个接点。
    add("WorkBuddy 接上微信聊天记录，3步自动出客户跟进表", "I",
        "接点式：我们的独家接点是「WorkBuddy + 微信」，全网无人占位")
    add("WorkBuddy 读懂你的微信，供应商货期和客户阶段自动分好", "I",
        "接点式第二版，强调「读懂」而非「读」")

    # 公式 J：私人推荐式。零数字、零震惊词，用条件句开头。
    # 和公式 D 分工：D 用于「我有数据」，J 用于「我有判断」。
    add("如果你想知道你的客户到底推进到哪一步了，那微信聊天记录真的值得你看一眼",
        "J", "私人推荐式：适用于方法论/选型类内容")
    add("如果你正在纠结要不要让AI接手业务对接，那这本书真的值得你认真读一读",
        "J", "私人推荐式：书/方法论推荐位")

    # 公式 K：对比结构。对比轴不是「功能有无」，是「同一件事的提问方式差在哪」。
    add("同样问WorkBuddy，小白和高手差在哪", "K",
        "对比结构：左右分屏，左边留白少元素，右边清单+勾选框")
    add("同样让它读客户聊天，小白和高手问法差在哪", "K",
        "对比结构：落到 A26 场景的具体对比轴")

    if not out:
        out.append({
            "标题": "（暂无可用模板：实测数据缺失）",
            "公式": "—", "依据": "岗位数/行业包数/token 读不到",
            "验证强度": "—", "用法": "先跑一次真实任务产生遥测数据",
        })
    return out


# ---------------------------------------------------------------- 口播稿


def _script_junction(item, facts, combos, roles, packs,
                     tokens, money, ds, kb, redline) -> str:
    """公式 I：接点式口播稿。

    为什么单独一套（2026-10-31 第三批实测）：
    单讲一个工具 → 转/赞 8~15；
    两个工具「接上了」→ 转/赞 19.43 / 21.91（本批最高两位）。
    转发的人在表达自己的进度和判断力，所以**成果必须具体**，
    不能停在「效率提高了」这种空话。
    """
    L = []
    A = L.append
    A("【口播稿 · 按公式 I 接点式结构】")
    A("")
    A("■ 第 1 段 · 抛出接点（0~6 秒）")
    A("")
    A("  「WorkBuddy 读微信聊天记录这事儿，我打通了。」")
    A("")
    A("  依据：公式 I「接点式：工具 A + 工具 B = 成果」；"
      "实测接点式转/赞 19.43 / 21.91，为第三批最高两位")
    A("")
    A("■ 第 2 段 · 说清接的是什么（6~15 秒）")
    A("")
    A("  「以前想知道哪个客户推进到哪一步，得自己翻聊天记录翻半天。")
    A("   现在它自己读、自己分、自己出表 ——")
    A("   客户线、供应商线、内部线，三张表分开。」")
    A("")
    A("  依据：公式 I 的成果词要具体（是「出三张表」，不是「效率高」）")
    A("")
    A("■ 第 3 段 · 给硬指标（15~40 秒）")
    A("")
    A("  「我这边是自己真跑出来的：")
    if roles:
        A("   整套系统 %s 个岗位、%s 个行业包，全部自己跑通；" % (roles, packs))
    A("   读的是真实微信库，消息全量读出来；")
    A("   客户、供应商、内部三线自动分开，每条结论都带原文出处；")
    A("   供应商的货到哪一步、款付到哪一步，分开两栏报。")
    if ds:
        A("   整套系统客户电脑解压双击，%s 秒打开看板开始派活；" % ds)
    if tokens:
        A("   跑完一次真实任务 %s token" % tokens +
          ("、%s 元。" % money if money else "。"))
    A("   这些数字都是我自己跑出来的，不是抄的。」")
    A("")
    A("  依据：公式 E「真实成本对比」；数字全部来自实测")
    A("")
    A("■ 第 4 段 · 给下一步（40~50 秒）")
    A("")
    A("  「想试试的话，评论区扣个『怎么读』，我把部署方式发你。」")
    A("")
    A("  依据：公式 F 结尾 —— 给一个立刻能做的下一步，不是给结论")
    A("")
    A("■ 素材溯源")
    A("")
    A("  本稿结构参考：%s（%s，%s）" % (
        item.get("作者") or "对标账号",
        item.get("日期") or "日期未获取",
        item.get("标题") or "标题未获取"))
    A("  链接：%s" % (item.get("链接") or "—"))
    A("  注意：只借结构，不抄观点与原句。")
    A("")
    return "\n".join(L)


def _script_compare(item, facts, combos, roles, packs,
                    tokens, money, ds, kb, redline) -> str:
    """公式 K：对比结构口播稿。

    为什么有效（2026-10-31 第三批实测）：
    对比轴不是「功能有无」，是「同一件事的提问方式差在哪」。
    观众看完会自省「我是不是也这样问的」—— 这才是转发的动机。
    """
    L = []
    A = L.append
    A("【口播稿 · 按公式 K 对比结构】")
    A("")
    A("■ 第 1 段 · 抛出对比（0~6 秒）")
    A("")
    A("  「同样问 WorkBuddy，小白和高手差在哪？」")
    A("")
    A("  依据：公式 K「对比结构」；实测转/赞 15.03，"
      "高于同量级功能介绍类内容")
    A("")
    A("■ 第 2 段 · 小白问法（6~20 秒）")
    A("")
    A("  「小白是这么问的：")
    A("   『帮我写篇文章。』")
    A("   『读一下我的微信。』")
    A("   『做条短视频。』")
    A("   —— 就这三句，AI 给的也就是这三句的答案。」")
    A("")
    A("■ 第 3 段 · 高手问法（20~40 秒）")
    A("")
    A("  「高手是这么问的：")
    A("   『以我已发布内容的语气写，主题是 X，先给我 3 个选题我选。』")
    A("   『读我微信里所有跟客户相关的会话，"
      "输出供应商货期和客户阶段两张表，每条带原文出处。』")
    A("   『用分镜表模板把这篇拆成 5 个镜头，每个标好时长。』")
    A("   —— 差别不在工具，在**问题里有没有给出约束和验收标准**。」")
    A("")
    A("  依据：公式 K 的核心 —— 对比轴是「提问方式」而非「功能有无」")
    A("")
    A("■ 第 4 段 · 给判断标准（40~52 秒）")
    A("")
    A("  「判断自己是不是高手，就看一件事：")
    A("   你给的指令里，有没有写清楚『做成什么样算成功』。」")
    A("")
    if ds:
        A("  「顺带说，我这套系统客户电脑解压双击，%s 秒就能开始派活。」" % ds)
        A("")
    A("■ 素材溯源")
    A("")
    A("  本稿结构参考：%s（%s，%s）" % (
        item.get("作者") or "对标账号",
        item.get("日期") or "日期未获取",
        item.get("标题") or "标题未获取"))
    A("  链接：%s" % (item.get("链接") or "—"))
    A("  注意：只借结构，不抄观点与原句。")
    A("")
    return "\n".join(L)


def _script(item: Dict[str, Any], facts: Dict[str, Any]) -> str:
    """生成口播稿。

    结构按公式 F：先接住情绪 → 给判断标准 → 给硬指标 → 给下一步。
    每段都标注依据，让后期改稿有据可依。

    第三批（2026-10-31）新增两套结构，按素材类型自动切换：
      · 公式 I 接点式 —— 两个工具接通了，成果是什么
      · 公式 K 对比结构 —— 小白问法 vs 高手问法
    """
    combos = _fmt(facts.get("岗位乘行业"))
    roles = _fmt(facts.get("岗位数"))
    packs = _fmt(facts.get("行业包数"))
    cost = facts.get("token成本") or {}
    tokens = _fmt(cost.get("累计token"))
    money = cost.get("累计花费")
    deploy = facts.get("部署耗时") or {}
    ds = deploy.get("秒")
    kb = _fmt(facts.get("知识库篇数"))
    redline = _fmt(facts.get("平台红线条数"))

    # 判断该用哪套结构（和封面模板用同一套判据，保持一致）
    hay = "%s %s %s" % (item.get("类目") or "", item.get("标题") or "",
                        item.get("值得借鉴") or "")
    is_junction = any(k in hay for k in ("接上", "打通了", "接点", "接了", "接点式"))
    is_compare = any(k in hay for k in ("小白", "高手", "差在哪", "对比结构"))

    if is_junction:
        return _script_junction(item, facts, combos, roles, packs,
                                tokens, money, ds, kb, redline)
    if is_compare:
        return _script_compare(item, facts, combos, roles, packs,
                               tokens, money, ds, kb, redline)

    L = []
    A = L.append
    A("【口播稿 · 按公式 F 结构】")
    A("")
    A("■ 第 1 段 · 先接住情绪（0~8 秒）")
    A("")
    A("  「你是不是也这样 —— 想用AI，但一上来就被术语劝退了？」")
    A("")
    A("  依据：公式 F「先接住情绪，再给下一步」，"
      "出处方格动态 2026-10-23（转/赞 25.36，全批最高）")
    A("")
    A("■ 第 2 段 · 给判断标准（8~30 秒）")
    A("")
    A("  「先给你三个判断标准：")
    if roles and packs:
        A("   第一，这活有没有固定步骤？有 —— 固定步骤才能做成AI员工。")
        A("   第二，做错一次的代价大不大？大 —— 那就先让它出方案，人来拍板。")
        A("   第三，客户资料能不能不出本机？能 —— 这是最容易被忽略的一条。」")
    else:
        A("   第一，这活有没有固定步骤？")
        A("   第二，做错一次的代价大不大？")
        A("   第三，客户资料能不能不出本机？")
    A("")
    A("  依据：缺口清单第 5 条「补判断框架（转/赞最高的内容形态）」")
    A("")
    A("■ 第 3 段 · 给硬指标（30~50 秒）")
    A("")
    A("  「我这边是现成的：")
    if combos:
        A("   %s 套AI员工团队（%s 个岗位 × %s 个行业包）；" % (combos, roles, packs))
    if ds:
        A("   客户电脑解压双击，%s 秒打开看板开始派活；" % ds)
    if tokens:
        A("   跑完一次真实任务 %s token" % tokens +
          ("、%s 元。" % money if money else "。"))
    if redline:
        A("   内置 %s 条平台红线，%s 篇知识库。" % (redline, kb))
    A("   这些数字都是我自己跑出来的，不是抄的。」")
    A("")
    A("  依据：公式 E「真实成本对比」+ 公式 D「规模数字」；"
      "数字全部来自实测（岗位/行业包实时统计，token 来自遥测，"
      "耗时来自部署实测脚本）")
    A("")
    A("■ 第 4 段 · 给下一步（50~60 秒）")
    A("")
    A("  「想试试的话，评论区扣个『怎么用』，我把部署方式发你。」")
    A("")
    A("  依据：公式 F 结尾 —— 给一个立刻能做的下一步，不是给结论")
    A("")
    A("■ 素材溯源")
    A("")
    A("  本稿结构参考：%s（%s，%s）" % (
        item.get("作者") or "对标账号",
        item.get("日期") or "日期未获取",
        item.get("标题") or "标题未获取"))
    A("  链接：%s" % (item.get("链接") or "—"))
    A("  注意：只借结构，不抄观点与原句。")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------- 封面


def _cover(item: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """生成封面方案。

    按素材所属类目选模板：盘点类用 T1，方法论类用 T2，
    交付实拍类用 T3，规模叙事类用 T4。
    接点类用 T6（第三批新增，转发最强），榜单类用 T5，实物推荐类用 T7。
    """
    cat = (item.get("类目") or "")
    combos = _fmt(facts.get("岗位乘行业"))
    roles = _fmt(facts.get("岗位数"))
    packs = _fmt(facts.get("行业包数"))
    cost = facts.get("token成本") or {}
    tokens = _fmt(cost.get("累计token"))
    deploy = facts.get("部署耗时") or {}
    ds = deploy.get("秒")

    # 类目优先；**视频素材没有「类目」字段**（只有文章类目库有），
    # 所以视频要靠标题 + 值得借鉴的内容来推断封面模板 ——
    # 否则永远落到默认 T4，第三批学到的 T5/T6/T7 就等于白加。
    hay = "%s %s %s" % (cat, item.get("标题") or "", item.get("值得借鉴") or "")
    if not cat:
        if any(k in hay for k in ("接上", "打通了", "接点", "接了")):
            cat = "接点"
        elif any(k in hay for k in ("热榜", "榜单", "周榜", "开源")):
            cat = "榜单"
        elif any(k in hay for k in ("书", "读一读", "值得")):
            cat = "书评"
        elif any(k in hay for k in ("小白", "高手", "差在哪")):
            cat = "对比"

    if cat.startswith("T3") or "交付" in cat:
        tkey = "T3"
    elif cat.startswith("A ") or "开源" in cat:
        tkey = "T1"
    elif cat.startswith("C ") or "H " in cat:
        tkey = "T2"
    elif "接点" in cat or "WorkBuddy" in cat:
        # 接点类：第三批实测转/赞 19.43 / 21.91，本批最强
        tkey = "T6"
    elif "榜单" in cat or "热榜" in cat:
        tkey = "T5"
    elif "书" in cat or "推荐" in cat or "读一读" in cat:
        tkey = "T7"
    elif "对比" in cat:
        tkey = "T6"
    else:
        tkey = "T4"

    t = COVERS.get(tkey, {})
    big = ""
    sub = ""
    if tkey == "T1":
        big = "AI员工系统真实成本表"
        sub = "%s 套团队 / %s 岗位 / %s 行业包" % (combos, roles, packs)
    elif tkey == "T2":
        big = "这活到底能不能交给AI？"
        sub = "先接住怀疑，再给3条判断标准"
    elif tkey == "T3":
        big = "作战看板 · 真实运行画面"
        sub = ("18 步 / %s token" % tokens) if tokens else "18 步真实任务，全程可见"
    elif tkey == "T5":
        # 榜单型：顶部周次角标是订阅钩子（观众知道下一期会来）
        big = "本周开源热榜"
        sub = "TOP3 + 星数，一句多余的话都没有"
    elif tkey == "T6":
        # 接点型：两个工具并置 + 明确成果词
        big = "WorkBuddy 读微信"
        sub = "3步自动出客户跟进表"
    elif tkey == "T7":
        # 实物实拍型：私人推荐，零数字零震惊
        big = "一人公司"
        sub = "这本书真的值得读一读"
    else:
        big = "%s 套AI员工团队" % (combos or "—")
        sub = "双击即用"

    shot_list = [
        "主体：作战看板终端录屏（真实界面，不用 AI 插画）",
        "大字：%s" % big,
        "副标题：%s" % sub,
        "底部一行硬指标：%s" % (("解压到能用 %s 秒" % ds) if ds
                               else "18 步 / %s token" % tokens),
    ]
    if tkey == "T5":
        shot_list = [
            "顶部绿色胶囊：第 N 周（**期数是订阅钩子**）",
            "大字：%s" % big,
            "项目 Logo + TOP3 每行一个图标",
            "每行右侧星数：★12.5k / ★8.7k / ★6.2k",
        ]
    elif tkey == "T6":
        shot_list = [
            "两个工具图标并置：WorkBuddy 绿色图标 + 微信图标",
            "中间：打通了（成果词，必须具体）",
            "大字：%s" % big,
            "副标题：%s" % sub,
        ]
    elif tkey == "T7":
        shot_list = [
            "主体：实物照片占满画面（书/产品摊在桌上）",
            "**不用 AI 生成元素** —— 实拍转/赞 11.55，打败全部 AI 封面",
            "大字：%s" % big,
            "副标题：%s" % sub,
        ]

    return {
        "模板": "%s · %s" % (tkey, t.get("name", "")),
        "版式": t.get("layout", ""),
        "出处": t.get("src", ""),
        "适合": t.get("fit", ""),
        "我们怎么用": t.get("for_us", ""),
        "封面大字": big,
        "副标题": sub,
        "底部一行": ("解压到能用 %s 秒" % ds) if ds else "",
        "画面清单": shot_list,
        "禁忌": "不要用纯色空白封面（摘星Ai谭鑫的反面教材：转/赞 14.97，"
                "但那是转载，自己做的东西不能没有信息）",
    }


# ---------------------------------------------------------------- 数据说明


def _data_note(facts: Dict[str, Any]) -> str:
    """说明数字从哪来 —— 避免把「未获取」当成「没查」。

    特别说明：**免安装版（客户拿到的那份）没有我们的运行日志**，
    所以 token 成本类指标取不到，标题会少 2 条。
    这是正确的 —— 不能把我们的运行数据硬塞给客户，
    客户装好、跑过自己的任务之后，这些数字自然就有了。
    """
    if not facts:
        return "未采集到实测数据，所有数字位置均标注「未获取」"
    parts = []
    for k in ("岗位数", "行业包数", "岗位乘行业", "知识库篇数", "平台红线条数"):
        if facts.get(k):
            parts.append("%s=%s" % (k, facts[k]))
    if facts.get("token成本"):
        parts.append("token/花费 来自 telemetry 累计读数")
    else:
        parts.append("token/花费 未获取（本机尚无运行记录，"
                     "跑过一次真实任务后自动产生）")
    if facts.get("部署耗时"):
        parts.append("部署耗时 来自实测脚本")
    return "；".join(parts) if parts else "未采集到实测数据"


# ---------------------------------------------------------------- CLI


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python tools/rewrite_material.py <素材ID>")
        print("      python tools/rewrite_material.py --list")
        return 1

    if sys.argv[1] == "--list":
        items = M.all_items()
        for it in items:
            if it.get("类型") != "video":
                continue
            print("%s  %s  %s" % (it.get("id"),
                                  (it.get("作者") or "")[:10],
                                  (it.get("标题") or "")[:40]))
        return 0

    res = rewrite(sys.argv[1])
    if not res.get("ok"):
        print("❌ %s" % res.get("error"))
        return 1

    m = res["素材"]
    print("=" * 68)
    print("  素材：%s" % (m.get("标题") or "")[:60])
    print("  作者：%s  日期：%s" % (m.get("作者"), m.get("日期")))
    print("  链接：%s" % m.get("链接"))
    print("=" * 68)
    print()
    print("【标题候选】")
    for i, t in enumerate(res["标题"], 1):
        print("  %d. %s" % (i, t["标题"]))
        print("     公式：%s" % t["公式"])
        print("     依据：%s" % t["依据"])
        print("     强度：%s ｜ 用途：%s" % (t["验证强度"], t["用法"]))
        print()
    print("【口播稿】")
    print(res["口播稿"])
    print("【封面方案】")
    c = res["封面方案"]
    print("  模板：%s" % c["模板"])
    print("  版式：%s" % c["版式"])
    print("  大字：%s" % c["封面大字"])
    print("  副标：%s" % c["副标题"])
    for line in c["画面清单"]:
        print("   - %s" % line)
    print()
    print("【数据来源】%s" % res["数据来源"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

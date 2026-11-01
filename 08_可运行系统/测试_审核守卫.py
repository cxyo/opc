#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""否定语境守卫 · 回归测试

为什么需要这个文件
------------------
禁区审核早先出过两个致命 bug：

  ① **占位符被当关键词** —— 禁区清单里"判定关键词"列写 `—`（表示本条
     没有自动判定关键词）的条目，被解析成了关键词 `—`。
     结果：SKILL.md 里任何一个破折号 `——` 都会命中所有这类条目，
     一个合格包被判 27 处红线、0 分、不能上架。

  ② **范畴错配** —— 拿"内容发布域"的条款（抖音标题党、淘宝极限词）
     去扫**产品文档**（SKILL.md）。结果"## 绝对禁止"里��"绝对"被判成
     标题党、"不写「100%」"里的"100%"被判成极限词。

修复后引入了三层机制：
  · clean_keywords()  过滤占位符
  · _scope_of()        按适用域分流（product / publish）
  · _is_negated_context() 否定语境守卫

**这套机制必须有人守着。** 审核一旦失灵，自动流水线就会把
违规包放进 _已上架，那是真金白银的封号和罚款风险。
所以这个文件是回归测试，不是可有可无的装饰。

跑法：
    python 08_可运行系统/测试_审核守卫.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "08_可运行系统"))

from staff import kb as kbmod          # noqa: E402
from staff import config as cfgmod     # noqa: E402


# ---- 用例集 -------------------------------------------------------------
# 每条 = (文本, 关键词, 期望是否否定语境)
# 期望 True  = 处在否定/引用语境，属于合规声明，**不算违规命中**
# 期望 False = 真实违规表达，**必须命中**

NEGATIVE_CASES = [
    ("5. 不群控、不批量私信、不刷量", "私信", True),
    ("5. 不群控、不批量私信、不刷量", "刷量", True),
    ("4. 不绕过付费墙、不破解、不代理他人账号", "破解", True),
    ("1. 不承诺效果（不写「保证」「100%」「必定」「稳赚」）", "保证", True),
    ("1. 不承诺效果（不写「保证」「100%」「必定」「稳赚」）", "100%", True),
    ("1. 不承诺效果（不写「保证」「100%」「必定」「稳赚」）", "稳赚", True),
    ("## 绝对禁止", "绝对", True),
    ("禁止使用群控、批量私信", "私信", True),
    ("严禁刷量", "刷量", True),
    ("不做洗稿搬运", "洗稿", True),
    ("不承诺，不保证任何效果", "保证", True),
    ("禁止在任何输出中使用「最佳」「第一」等极限词", "第一", True),
]

POSITIVE_CASES = [
    ("本工具可批量私信获客", "私信", False),
    ("帮你稳赚不赔", "稳赚", False),
    ("支持破解付费内容", "破解", False),
    ("提供洗稿能力", "洗稿", False),
    ("群控养号一体机", "群控", False),
    ("不承诺效果，这个工具能破解付费内容", "破解", False),
    ("不做A，能帮你批量私信", "私信", False),
    ("我们不接正常业务，只做群控", "群控", False),
    ("本工具提供群控、养号机、批量私信全套能力", "批量私信", False),
    ("功能列表：洗稿、搬运、对抗风控", "洗稿", False),
    ("支持稳赚、保本保息、代客理财", "稳赚", False),
    ("提供 AI 去检测服务，规避平台审核", "去检测", False),
]


def run():
    fails = []

    print("== ① 占位符清洗 ==")
    ph = kbmod.clean_keywords("—")
    if ph:
        fails.append("clean_keywords 未过滤占位符 —，得到 %r" % ph)
    else:
        print("   OK   占位符「—」已被过滤")
    ph2 = kbmod.clean_keywords("AI假图、货不对板、—、无")
    if "—" in ph2 or "无" in ph2:
        fails.append("clean_keywords 残留占位符：%r" % ph2)
    else:
        print("   OK   混合列表已剔除占位符：%r" % ph2)

    print("== ② 适用域分流 ==")
    idx = kbmod.ForbiddenIndex.from_dir(
        os.path.join(cfgmod.PROJECT, "03_知识库", "wiki", "10_禁区"))
    prod = idx.match("测试文本", scope="product")
    pub = idx.match("测试文本", scope="publish")
    n_prod = len([e for e in idx.entries if e.get("scope") == "product"])
    n_pub = len([e for e in idx.entries if e.get("scope") == "publish"])
    print("   索引 %d 条：product %d / publish %d" % (len(idx.entries), n_prod, n_pub))
    if n_prod < 8:
        fails.append("product 域条目只有 %d 条，产品红线没被索引进来" % n_prod)
    else:
        print("   OK   product 域条目充足（%d 条）" % n_prod)
    if n_pub < 40:
        fails.append("publish 域条目只有 %d 条，平台红线没被索引进来" % n_pub)
    else:
        print("   OK   publish 域条目正常（%d 条）" % n_pub)
    # 抽查：publish 域真能命中平台关键词
    if not idx.match("这标题太绝对了吧", scope="publish"):
        fails.append("publish 域无法命中「绝对」，分流逻辑异常")
    else:
        print("   OK   publish 域可正常命中平台关键词")

    print("== ③ 否定语境守卫 · 正例（应判否定）==")
    for txt, kw, _exp in NEGATIVE_CASES:
        got = kbmod._is_negated_context(txt, txt.find(kw), kw)
        if not got:
            fails.append("[否定] %r 中的 %r 未被识别为否定语境" % (txt[:30], kw))
            print("   FAIL %-8s %s" % (kw, txt[:34]))
        else:
            print("   OK   %-8s %s" % (kw, txt[:34]))

    print("== ④ 否定语境守卫 · 反例（应判命中）==")
    for txt, kw, _exp in POSITIVE_CASES:
        got = kbmod._is_negated_context(txt, txt.find(kw), kw)
        if got:
            fails.append("[命中] %r 中的 %r 被误判为否定语境（真实风险被放过）" % (txt[:30], kw))
            print("   FAIL %-8s %s" % (kw, txt[:34]))
        else:
            print("   OK   %-8s %s" % (kw, txt[:34]))

    print("== ⑤ 端到端：合格包必须通过审核 ==")
    # 样例包可能在 _待审/ 也可能在 _已上架/（上架后会被移走）
    sample = None
    for base in ("_待审", "_已上架"):
        d = os.path.join(cfgmod.PROJECT, "07_AI员工", "05_自动生成技能", base)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name, "SKILL.md")
            if os.path.exists(p):
                sample = p
                break
        if sample:
            break
    if sample:
        with open(sample, "r", encoding="utf-8") as f:
            txt = f.read()
        hits = idx.match(txt, scope="product", level_filter="红线")
        if hits:
            fails.append("合格包仍命中 product 域红线 %d 处：%r"
                         % (len(hits), [h["hit_keyword"] for h in hits]))
            print("   FAIL 命中 %r" % [h["hit_keyword"] for h in hits])
        else:
            print("   OK   合格包 0 处 product 域红线命中（%s）"
                  % os.path.basename(os.path.dirname(sample)))
    else:
        print("   SKIP 没有样例包，跳过")

    print()
    if fails:
        print("❌ 审核守卫回归测试失败，共 %d 项：" % len(fails))
        for f in fails:
            print("   · %s" % f)
        return 1
    print("✅ 审核守卫回归测试全部通过（%d 条用例）"
          % (len(NEGATIVE_CASES) + len(POSITIVE_CASES)))
    return 0


if __name__ == "__main__":
    sys.exit(run())

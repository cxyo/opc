# -*- coding: utf-8 -*-
"""标题公式 / 封面模板 / 实测数据采集 —— 系统级共用一份。

为什么从 tools/title_engine.py 拆出来（2026-10-31）
---------------------------------------------------
原来的公式表住在 `tools/title_engine.py`。但免安装版（PyInstaller）
**只打包 staff 包，不含 tools/ 目录**，于是：

  · 素材库页面的「改写」按钮在 exe 版直接报
    No module named 'rewrite_material' —— 源码版正常、exe 版失败
  · 公式表有两份的风险（title_engine 一份、rewrite 一份，改一处漏一处）

所以把**数据定义**（公式表、封面表、采集函数）提到 staff/ 里，
tools/title_engine.py 与 staff/rewrite.py 都从这里取。
一份定义，两个地方用，不会跑偏。

数据纪律（不变）
----------------
1. **只用实测数字**。岗位数、行业包数从代码实时数；
   token 成本从 telemetry 读。拿不到就写「未获取」，绝不编。
2. 每条标题都要标出**用了哪个公式、依据是哪条视频**。
3. 公式 A/B 只有 1 个数据点，标注保持"待验证"，不冒充已验证。
"""

import json
import os
import re
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

FORMULAS: Dict[str, Dict[str, Any]] = {
    "A": {
        "name": "碰到我的 X，你的 Y 算是 Z 了",
        "desc": "主语是「你」自动代入；「我的」与「你的」对冲制造悬念；"
                "「算是…了」暗示早就该这样",
        "src": "这很容易《碰到我的AI，你的钱包算是倒霉了》2026-09-02",
        "data": "点赞263 藏935 转3308（转/赞 12.6×）",
        "verified": "待验证",
        "use": "视频号口播开场",
    },
    "B": {
        "name": "今年的 X 把我变成了「Y」",
        "desc": "「今年的」给时代感；身份词夸张带落差；引号明示修辞，降低标题党抵触",
        "src": "同一条视频封面大字「今年的AI把我变成了皇帝」",
        "data": "点赞263 藏935 转3308",
        "verified": "待验证",
        "use": "封面大字（不走路播标题）",
    },
    "C": {
        "name": "反向许愿：别再 X 了，你的岗位现在是 Y",
        "desc": "公式 A 的机制反向使用 —— 不用「帮你省时间」这种零情绪词",
        "src": "由公式 A 推导",
        "data": "推导，无独立数据点",
        "verified": "推导",
        "use": "视频号口播开场、行业演示片头",
    },
    "D": {
        "name": "规模数字 + 零门槛部署",
        "desc": "数字负责让人点进来，门槛负责让人相信自己做得到。"
                "第二批 63 条**全部**同时用这两个元素。"
                "**两个方向都成立**（2026-10-31 补）："
                "大数字（144 个 AI 员工）显规模，"
                "小数字（4 个不要工资的员工）显可信 —— "
                "小数字反而更真实，'24 个' 听起来像吹牛，'4 个' 听起来像真话",
        "src": "哈工大AI工程师Peter 10-03 转/赞21.72；每日AI·replica-skill 10-26 转/赞23.09；"
               "江先生短视频获客系统 10-15「四个不要工资的员工 干完一个团队的活」"
               "转/赞10.10（**小数字变体**，藏赞比 2.04）",
        "data": "63/63 条遵循该双元素结构；小数字变体转/赞 10.10 同样跑得动",
        "verified": "已验证",
        "use": "封面大字 + 口播第一句",
    },
    "E": {
        "name": "真实成本对比",
        "desc": "倍数是全批最省力的可信度表达。8 条以上用星数/倍数/字节/时长这类硬指标。"
                "**并列结构变体（2026-10-31 补）**："
                "「N 条法则 + 每条配一个可验证的硬数字」。"
                "关键约束：**法则必须换成实词** —— "
                "「5 条黄金法则」是虚的，「24 个岗位怎么分工、谁先谁后」是实的",
        "src": "开发者Hailey 08-28 转/赞18.89「成本干到 Claude Code 的 1/70」；"
               "李尚龙说 10-12「AI时代创造财富的5条黄金法则」转/赞20.11（藏赞比 3.38）",
        "data": "全批 8 条以上只用硬指标不带情绪词；"
                "「N 条」并列结构转/赞 20.11，同样成立",
        "verified": "已验证",
        "use": "封面 + 口播第二句（必须用实测值）",
    },
    "F": {
        "name": "先接住情绪，再给下一步",
        "desc": "不是纯项目介绍。先说观众此刻的念头，再给能立刻做的下一步",
        "src": "方格动态 10-23 转/赞 25.36（第二批最高）",
        "data": "25.36 转/赞",
        "verified": "已验证",
        "use": "口播整体结构",
    },
    "G": {
        "name": "交付方 vs 卖课方（对比落差）",
        "desc": "同行在做什么，你做什么 —— 落差的另一边是「能验收」",
        "src": "陈灏 06-25 转/赞 9.34「同行卖课，你送智能体」",
        "data": "转/赞 9.34",
        "verified": "已验证",
        "use": "口播第三段、标题",
    },
    "H": {
        "name": "真实运行画面即封面",
        "desc": "第二批 63 条**没有一条**展示真实运行画面，全是插画或静态截图。"
                "真实终端录屏是当前最稀缺的视觉资产",
        "src": "每日AI 10-26 转/赞23.09；代码拾荒者 10-30 转/赞17.71；yoko.ai 10-26 藏/赞5.10",
        "data": "全批 0 条用真实运行画面",
        "verified": "已验证",
        "use": "封面主视觉",
    },
    # ↓↓↓ 第三批 19 条（2026-10-31）新增 ↓↓↓
    "I": {
        "name": "接点式：工具 A + 工具 B = 成果",
        "desc": "单讲一个工具怎么用，是自我消费型内容（转/赞 8~15）；"
                "讲两个工具**接通了**、原来要 3 小时现在 3 分钟，"
                "是可复述的成果，转发的人在表达自己的进度和判断力。"
                "关键在「接上/打通了」这三个字，和一个明确的成果词。",
        "src": "知行Agent实战日记 10-30「WorkBuddy 接上开拍，3步让 AI 帮你做视频」转/赞21.91；"
               "樱桃总的AI世界 10-20「AI自动剪辑打通了 Workbuddy+开拍」转/赞19.43",
        "data": "接点式 2 条转/赞 19.43 / 21.91，本批最高两位；"
                "同期单工具 WorkBuddy 主题 4 条，转/赞 3.67~15.03",
        "verified": "已验证",
        "use": "封面大字 + 标题（本系统的独家接点是「WorkBuddy + 微信聊天记录」）",
    },
    "J": {
        "name": "私人推荐式：如果你 X，那 Y 真的值得",
        "desc": "不用数字、不用震惊、不用必看。用条件句开头（如果你…），"
                "用「值得」收尾，全程是一个人向另一个人推荐。"
                "长尾内容拼的不是信息密度，是可信的私人推荐感。"
                "和公式 D（数字型）分工：D 用于「我有数据」，J 用于「我有判断」。",
        "src": "数理象限 10-07「如果你未来想一个人就开始做生意，那这本书真的值得你认真读一读」"
               "赞952 藏3839 转11000 —— 本批转发第一",
        "data": "转/赞 11.55，打败本批全部 AI 封面炫技内容",
        "verified": "已验证",
        "use": "口播第一句、公众号标题（方法论/选型类内容用它）",
    },
    "K": {
        "name": "对比结构：同样做 X，小白和高手差在哪",
        "desc": "左右分屏对比比功能罗列更有传播力。"
                "左边留白少元素，右边是清单+勾选框+结果箭头。"
                "对比轴不是「功能有无」，是「同一件事的提问方式差在哪」——"
                "这比讲工具能力更能引发自省和转发。",
        "src": "哪吒AI运营 10-30「同样问 WorkBuddy，小白和高手差在哪」转/赞15.03；"
               "封面左右分屏：小白区 1 个问号气泡，高手区 4 行勾选清单流向 3 个结果图标",
        "data": "转/赞 15.03，高于本批同量级功能介绍类内容",
        "verified": "已验证",
        "use": "封面主视觉 + 正文第一屏（小提示 vs 大提示对照）",
    },
}


# 封面模板表
COVERS: Dict[str, Dict[str, Any]] = {
    "T1": {
        "name": "清单 / 成本表型",
        "layout": "顶部标题条 + N 行「名称 + 一句话用途 + 价签」",
        "src": "一品Ai说 10-22；大A的AI训练营 10-26；清美AI研习社 10-14 藏/赞5.81",
        "fit": "收藏型内容 —— 成本表、岗位表用它",
        "for_us": "「AI 员工系统真实成本表」：左边列工具单价，右边列整套系统跑一次真实任务的开销",
    },
    "T2": {
        "name": "人物 + 大字幕型",
        "layout": "真人半身像 + 满屏大字（关键词高亮变色）",
        "src": "大浪-高净值IP打造 10-29；姜胡说 06-02",
        "fit": "转发型内容",
        "for_us": "「交付给 3 家工厂」这类里程碑时刻",
    },
    "T3": {
        "name": "真实界面实拍型",
        "layout": "真实软件界面 / 终端 / 文件树占 2/3 画面 + 底部一行结论",
        "src": "每日AI·replica-skill 10-26 转/赞23.09；代码拾荒者 10-30 转/赞17.71",
        "fit": "**可信度最高** —— 你的作战看板必须用它",
        "for_us": "作战看板终端录屏 + 底部「18 步 / ¥0.1406 / 73,936 token」",
    },
    "T4": {
        "name": "规模数字 + 图标墙型",
        "layout": "深色网格底 + 黄绿大字（数量词最大）+ 下方界面截图",
        "src": "无限引力GRAVITY 04-03「144个AI员工一键部署」；成也2077 05-13",
        "fit": "拉点击",
        "for_us": "「24 个岗位 × 6 个行业包 = 144 套」规模词做成画面主体",
    },
    # ↓↓↓ 第三批 19 条（2026-10-31）新增 ↓↓↓
    "T5": {
        "name": "榜单型（周次 + TOP3 + 星数）",
        "layout": "浅色底 + 顶部绿色胶囊写周次（如「七月第三周」）+ "
                  "深蓝大字榜单名（2 行）+ 项目 Logo + "
                  "TOP3 每行一个图标 + 星数（★12.5k / ★8.7k / ★6.2k）",
        "src": "Oraink灵砚 07-27「Github本周开源热榜」赞596 藏1672 转7064 转/赞11.85",
        "fit": "收藏型 + 栏目化内容",
        "for_us": "情报雷达的 GitHub 周榜、抖音/公众号周榜都能套这个模板；"
                  "**顶部周次角标是订阅钩子** —— 观众看到「009期」会知道下一期会来",
    },
    "T6": {
        "name": "接点型（工具 A + 工具 B）",
        "layout": "深色/夜景底 + 大字「[成果]打通了」+ 绿色工具名 + "
                  "「+ 第二个工具」；真人出镜时配手势（比 4、竖大拇指）",
        "src": "樱桃总的AI世界 10-20「AI自动剪辑打通了 / Workbuddy + 开拍」转9231 转/赞19.43；"
               "知行Agent实战日记 10-30「WorkBuddy 接上开拍」转/赞21.91",
        "fit": "**转发型，本批最高**",
        "for_us": "「WorkBuddy 读微信聊天记录，N 步自动出客户跟进表」——"
                  "独家接点，全网没有第二个人做",
    },
    "T7": {
        "name": "实物实拍型（书 / 产品实物）",
        "layout": "真实照片占满画面（书摊在桌上、桌面俯拍）+ 无 AI 生成元素",
        "src": "数理象限 10-07《一人公司》实拍封面 赞952 藏3839 转11000 —— 本批转发第一",
        "fit": "**私人推荐型内容**，转/赞 11.55 打败本批全部 AI 封面",
        "for_us": "「一人公司」类书评/选型内容；"
                  "手里有实物就拍实物，没有就别硬凑",
    },
    # ↓↓↓ 第四批 10 条视频（2026-10-31）新增 ↓↓↓
    "T8": {
        "name": "Skill 清单环绕型",
        "layout": "深色科技底 + 顶部三行大字（**数字口径用强调色放到最大字号**）"
                  "+ N 张发光小卡片环绕（编号 + 图标 + 四字动宾标题）"
                  "+ 真人出镜位（手托产品图标）",
        "src": "鉴锋Agent搭建笔记 10-10「WorkBuddy 做爆款视频必装 5 个 Skill」"
               "赞949 藏3463 转1.4万 —— **藏赞比 3.65**",
        "fit": "**收藏型**（藏 > 赞 = 用户当采购清单看，不是当内容消费）",
        "for_us": "「我的 24 个 AI 岗位，装一个网页控制台就够」——"
                  "本系统的岗位命名本来就是「编号 + 动宾四字」（A01 安全官），"
                  "可直接套用不用改词；"
                  "**注意**：真人出镜位需要实拍，AI 生成的人像撑不起「有人在用」",
    },
    "T9": {
        "name": "真实环境实拍型（人在画面里）",
        "layout": "真人在画面里（室内实景）+ **身后有亮着的屏幕** + "
                  "白色描边字幕（带身份/地域标签）+ 可选箭头或手指指示屏幕",
        "src": "Ai奶糖学姐 05-31「一键出数据拆解分析」赞994 藏3198 转1.0万 "
               "—— **藏赞比 3.22**，本批第二",
        "fit": "工具演示类内容的**可信度最高**选项",
        "for_us": "「作战看板真实录屏 + 人在画面里」——"
                  "本系统的实时看板真跑任务、真显示 token 与耗时，正是这个场景。"
                  "**与 T3 的区别**：T3 是屏幕占画面 2/3，"
                  "T9 是人在画面里、屏幕在背景里 —— 后者可信度更高，"
                  "因为「有人在用」比「有张截图」强",
    },
    "T10": {
        "name": "编号清单型",
        "layout": "顶部大字 + **标题里加筛选声明词**（真正/实测/整理）+ "
                  "N 行编号清单（每行：编号 + 彩色图标 + 名称 + **一行中文用途**）+ "
                  "手写便签情绪层 + 底部收藏标签",
        "src": "123茄紫Eallily 10-18「GitHub 上真正能搞钱的 8 个推荐库」"
               "赞45 藏203 转849 —— **藏赞比 4.51，本批第一**",
        "fit": "**收藏型**，藏赞比本批最高",
        "for_us": "「24 个 AI 岗位，一个网页控制台全装下」——"
                  "每行写：岗位代号 + 一句话职责。"
                  "**清单行规范：代号 + 一句中文用途，缺一不可** —— "
                  "只给名称的清单不会被用，这是本条最实用的细节。"
                  "**手写便签需要补书法字体或手写素材**，目前系统只有黑体",
    },
    "T11": {
        "name": "原理 + 真实终端型",
        "layout": "上半屏黑底标题式排版讲原理（2-3 行白字，"
                  "**不是大字封面而是讲清楚机制**）+ "
                  "下半屏真实终端截图（**手指/光标落点指示重点区域**）",
        "src": "manateelazycat 09-29「100 积分用出 5000 积分」"
               "赞256 藏1313 转3716 评论629 —— **藏赞比 5.13，本批第一**",
        "fit": "**收藏 + 转发双高**，技术机制类内容的最佳版式",
        "for_us": "「24 个岗位跑一篇文章，实测 0.21 元 / 73936 token」——"
                  "这类硬数字内容就该用这个版式："
                  "**先讲原理再给画面**，信息密度高，适合收藏。"
                  "手指落点 = 视线引导，比箭头更自然",
    },
}


# ---------------------------------------------------------------------------
# 传播指标判据（2026-10-31 第四批补）
# ---------------------------------------------------------------------------
#
# 为什么加「藏赞比」
# ----------------
# 原来只有「转/赞」一个判据（转发 ÷ 点赞），用它判断内容传播力。
# 但第四批 10 条视频暴露了它的盲区：
#
#   素材                     赞     藏     转     转/赞   藏赞比   实际表现
#   AkOaSjGVNF（积分缓存）    256   1313   3716   14.52   5.13    技术机制，收藏导向
#   AWlW8KBAJF（GitHub清单）    45    203    849   18.87   4.51    工具清单，采购导向
#   AwhGGQKHms（Skill合集）   949   3463  14000   14.75   3.65    工具清单，采购导向
#   AszxUybScB（5条法则）      94    318   1890   20.11   3.38    认知框架，长尾复用
#   AjHE3ql9zS（数据拆解）    994   3198  10000   10.06   3.22    工具演示，可信导向
#   AUziVHDfic（AI超市）        0      2      5      —      —     失败样本
#
# 两个发现：
#
#   1. **藏赞比 > 3 的内容，是「工具/清单/机制」类** ——
#      观众把它当**采购清单**或**技术资料**存下来，不是当内容消费。
#      这类内容的长尾复用价值远高于点赞数体现的。
#   2. **转发倾向 ≠ 分发量级**（2026-10-31 补，v_AuFpA8Sbhz 实测）——
#      那条转/赞 45.00（本批最高），但赞只有 1、转只有 45，
#      算法认可了却没进大流量池。
#      **不能拿转/赞 高就推断曝光大**，两者是两件事。
#
# 所以判据要分开用：
#   · 转/赞高 → 传播力（别人愿不愿意转给别人）
#   · 藏赞比高 → 留存力（别人愿不愿意存下来自己用）
#   · 绝对量（赞/藏） → 分发量级（平台给了多少流量）

def judge_reach(likes: Any, saves: Any, shares: Any) -> Dict[str, Any]:
    """判断一条内容的传播特征。

    返回三个维度 + 一条综合建议。
    """
    def _num(v):
        try:
            return float(str(v).replace(",", "")) or 0.0
        except Exception:
            return 0.0

    def _wan(v):
        """「1.4万」这类写法换算成数字。"""
        s = str(v or "").strip()
        try:
            if s.endswith("万"):
                return float(s[:-1]) * 10000
            return float(s.replace(",", "")) or 0.0
        except Exception:
            return 0.0

    L = _num(likes)
    S = _wan(saves)
    F = _wan(shares)

    # 点赞为 0 时，比值算不出来（不是 0，是「没数据」）
    f_over_l = round(F / L, 2) if L > 0 else None
    s_over_l = round(S / L, 2) if L > 0 else None

    # 传播力：转/赞
    if f_over_l is None:
        spread = "无"
    elif f_over_l >= 18:
        spread = "强"
    elif f_over_l >= 12:
        spread = "中"
    elif f_over_l > 0:
        spread = "弱"
    else:
        spread = "无"

    # 留存力：藏赞比
    if s_over_l is None:
        keep = "无"
    elif s_over_l >= 3:
        keep = "强"
    elif s_over_l >= 1.5:
        keep = "中"
    elif s_over_l > 0:
        keep = "弱"
    else:
        keep = "无"

    # 分发量级：绝对收藏数（收藏数比点赞数更接近真实曝光）
    if S >= 1000:
        reach = "大"
    elif S >= 100:
        reach = "中"
    elif S > 0:
        reach = "小"
    else:
        reach = "无"

    if keep == "强" and reach in ("大", "中"):
        advice = ("**值得做成系列** —— 藏赞比高说明有长尾复用价值，"
                  "绝对量也够。清单型 / 机制型内容的标准形态。")
    elif spread == "强" and reach == "小":
        advice = ("**转发倾向高但分发量级小** —— "
                  "可能是内容好但选题窄，或算法没推。"
                  "别拿转/赞 高就推断曝光大，这两件事要分开看。")
    elif spread == "强":
        advice = "传播型内容 —— 适合投放/转发，不适合当长期资产。"
    elif keep == "强":
        advice = "留存型内容 —— 适合沉淀为资料，配套做系列。"
    else:
        advice = "常规内容 —— 三项指标都不突出，参考价值有限。"

    return {
        "点赞": L, "收藏": S, "转发": F,
        "转赞比": f_over_l, "藏赞比": s_over_l,
        "传播力": spread, "留存力": keep, "分发量级": reach,
        "建议": advice,
    }


def cover_by_judgement(judgement: Dict[str, Any]) -> str:
    """按传播特征推荐封面模板。

    留存力强 → 清单/规格类模板（T8 / T10）
    传播力强 → 人物/大字类模板（T2 / T6）
    两者都强 → 原理 + 真实终端（T11）
    """
    keep = judgement.get("留存力")
    spread = judgement.get("传播力")
    if keep == "强" and spread == "强":
        return "T11"
    if keep == "强":
        return "T10"
    if spread == "强":
        return "T6"
    return "T3"


def _project_root() -> str:
    """项目根目录（含 03_知识库 / 07_AI员工 的那一层）。

    **必须用 config.PROJECT**，不能自己上溯 __file__：
    打包成 exe 后 __file__ 指向 PyInstaller 的临时解压目录，
    上溯出来的路径是错的 —— 实测表现为「岗位数取不到 → 标题少一半」
    （exe 版出 5 条、源码版出 9 条）。

    config._resolve_root() 已经处理了 exe 的目录布局判定。
    """
    try:
        from . import config as cfgmod
        proj = getattr(cfgmod, "PROJECT", "")
        if proj and os.path.isdir(os.path.join(proj, "07_AI员工")):
            return proj
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def collect_facts() -> Dict[str, Any]:
    """实时采集可用的硬指标。

    所有数字都来自代码实测或 telemetry 读数。
    拿不到就返回 None（上层写「未获取」），不猜。
    """
    root = _project_root()
    facts: Dict[str, Any] = {
        "岗位数": None,
        "行业包数": None,
        "岗位乘行业": None,
        "token成本": None,
        "部署耗时": None,
        "采集时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # ① 岗位数：直接数岗位提示词文件
    roles_dir = os.path.join(root, "07_AI员工", "01_Bot提示词")
    try:
        if os.path.isdir(roles_dir):
            codes = set()
            for fn in os.listdir(roles_dir):
                m = re.match(r"(A\d{2})_(.+)\.md$", fn)
                if m:
                    codes.add(m.group(1))
            facts["岗位数"] = len(codes) or None
    except Exception:
        pass

    # ② 行业包数：**必须用项目自己的接口**。
    #    磁盘上只有 3 个目录，但 industry.list_industries() 会把内置包
    #    （general / ecommerce / marketing）一起算进来，一共 6 个。
    #    只数目录会得到错的数字 —— 这坑踩过一次。
    try:
        sys.path.insert(0, os.path.join(root, "08_可运行系统"))
        from staff import industry as _ind
        from staff import config as _cfg
        _c = _cfg.load()
        _inds = _ind.list_industries(_c)
        if _inds:
            facts["行业包数"] = len(_inds)
            facts["行业包清单"] = [
                {"key": i.get("key"), "name": i.get("name"),
                 "source": i.get("source")} for i in _inds]
    except Exception:
        # 取不到就留 None，但**把原因记下来** ——
        # 静默 except 会让"数据没采到"变成"数据不存在"，很难查。
        facts.setdefault("_错误", []).append("行业包: %s" % traceback.format_exc(limit=1).strip())

    if facts["岗位数"] and facts["行业包数"]:
        facts["岗位乘行业"] = facts["岗位数"] * facts["行业包数"]

    # ③ token 成本 / 调用次数：从 telemetry 台账聚合
    #    台账是 JSONL（data/logs/telemetry.jsonl），不是 JSON —— 用项目自己的
    #    read_all + summarize，别自己解析，格式会变。
    try:
        from staff import telemetry as _tel
        from staff import config as _cfg2
        _c2 = _cfg2.load()
        _recs = _tel.read_all(_c2["paths"]["logs"])
        if _recs:
            _agg = _tel.summarize(_recs)
            tot_tok = sum(v.get("tokens", 0) or 0 for v in _agg.values())
            tot_cost = sum(v.get("cost_yuan", 0) or 0 for v in _agg.values())
            tot_calls = sum(v.get("calls", 0) or 0 for v in _agg.values())
            facts["token成本"] = {
                "累计token": tot_tok,
                "累计花费": round(tot_cost, 4),
                "累计调用": tot_calls,
                "岗位数_有调用": len(_agg),
                "来源": "data/logs/telemetry.jsonl",
            }
    except Exception:
        facts.setdefault("_错误", []).append("token台账: %s" % traceback.format_exc(limit=1).strip())

    # ④ 部署耗时：从实测脚本的结果文件读
    #    实测脚本落在 交付包/_实测计时_结果.json（不在 reports/ 下），
    #    里面是三轮明细，取成功轮次的中位数。
    #
    #    免安装版（exe）里没有「交付包/」这一层，所以要多找几个位置 ——
    #    找不到就写「未获取」，不猜。
    for rel in (os.path.join("交付包", "_实测计时_结果.json"),
                os.path.join("08_可运行系统", "data", "reports",
                             "_实测计时_结果.json"),
                os.path.join("08_可运行系统", "data", "reports",
                             "部署实测.json")):
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
            rows = d if isinstance(d, list) else d.get("轮次") or []
            ok = [r for r in rows if r.get("端到端总秒")]
            if ok:
                vals = sorted(r["端到端总秒"] for r in ok)
                facts["部署耗时"] = {
                    "秒": vals[len(vals) // 2],
                    "区间": "%s ~ %s" % (vals[0], vals[-1]),
                    "成功轮次": "%d/%d" % (len(ok), len(rows)),
                    "来源": rel.replace("\\", "/"),
                }
                break
        except Exception:
            facts.setdefault("_错误", []).append(
                "部署耗时(%s): %s" % (rel, traceback.format_exc(limit=1).strip()))

    return facts

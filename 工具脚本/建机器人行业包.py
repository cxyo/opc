# -*- coding: utf-8 -*-
"""把「尔必地-寮步-联技」半成品资料，转成可交付的行业包知识库。

为什么要做这一步：
    用户手里的机器人资料质量其实不低（README 521 行、排障手册 625 行、
    客户手册 251 行），但它是「给人读的资料」，不是「AI 能用的知识库」，
    也不是「客户能自己上手的交付包」。

这个脚本做四件事：
    1. 把散落在项目各处的核心资料，复制到行业包的 知识库/ 目录
    2. 把客户手册里已经是 FAQ 形态的部分，抽成结构化问答
    3. 写一份「大白话上岗手册」—— 面向完全没接触过 AI 的新员工
    4. 生成 pack.json，把岗位、知识库、交付物清单都定下来

跑法：
    python 工具脚本/建机器人行业包.py            # 干跑，只看要做什么
    python 工具脚本/建机器人行业包.py --apply    # 真执行
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys

SRC = r"C:\Users\micky\WorkBuddy\工业机器人\尔必地-寮步-联技"
PROJ = r"C:\Users\micky\WorkBuddy\一人AI公司"
PACK_DIR = os.path.join(PROJ, "03_知识库", "行业包", "robotics")
KB_DIR = os.path.join(PACK_DIR, "知识库")


def log(msg: str) -> None:
    print(msg)


def extract_faq(md: str) -> list:
    """从客户手册里抽 Q1/Q2/... 段落。"""
    out = []
    pat = re.compile(r"###\s*Q(\d+)[：:]\s*(.+?)\n(.*?)(?=\n###|\n##|\Z)", re.S)
    for m in pat.finditer(md):
        q = m.group(2).strip()
        body = m.group(3).strip()
        # 去掉代码块标记，保留里面的步骤文字
        body = re.sub(r"```text\s*\n", "", body)
        body = re.sub(r"\n```", "", body)
        body = re.sub(r"\*\*(.*?)\*\*", r"\1", body)
        body = re.sub(r"^>\s*", "", body, flags=re.M)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        out.append({"n": int(m.group(1)), "q": q, "a": body})
    return out


def extract_code_steps(md: str) -> list:
    """抽出所有 ```text 代码块（里面是操作步骤）。"""
    return [m.strip() for m in re.findall(r"```text\s*\n(.*?)```", md, re.S)]


def main() -> None:
    apply = "--apply" in sys.argv
    log("=" * 66)
    log("  建「工业机器人」行业包")
    log("=" * 66)
    log(f"  源资料：{SRC}")
    log(f"  目标  ：{PACK_DIR}")
    if not apply:
        log("  （干跑模式，加 --apply 才真正写入）")

    if not os.path.isdir(SRC):
        log(f"  ✘ 源资料目录不存在：{SRC}")
        return

    # ---------- 1. 挑要搬过去的资料 ----------
    picks = [
        ("01_设备档案.md", "设备档案.md", "机器人型号、轴参数、点位总表、IO 信号的底账"),
        ("02_点位总表.md", "点位总表.md", "各工位点位编号与命名规则"),
        ("03_IO与信号表.md", "IO信号表.md", "输入输出信号定义，排查报警必查"),
        ("06_调试与排障手册.md", "调试与排障手册.md", "五层定位法、故障速查、备份恢复、零位处理"),
        ("07_客户操作手册.md", "客户操作手册.md", "开机流程、常见问题、安全红线、点检表"),
        ("08_待确认清单.md", "待确认清单.md", "现场还没确认的项，避免客户踩坑"),
    ]
    ok = 0
    for src_name, dst_name, why in picks:
        sp = os.path.join(SRC, src_name)
        if not os.path.exists(sp):
            log(f"  · 跳过（不存在）：{src_name}")
            continue
        if apply:
            os.makedirs(KB_DIR, exist_ok=True)
            shutil.copy2(sp, os.path.join(KB_DIR, dst_name))
        ok += 1
        log(f"  ✓ {src_name} → 知识库/{dst_name}   （{why}）")
    log(f"  搬移 {ok}/{len(picks)} 份")

    # ---------- 2. 抽 FAQ ----------
    cust_path = os.path.join(SRC, "07_客户操作手册.md")
    faq = extract_faq(open(cust_path, encoding="utf-8").read()) \
        if os.path.exists(cust_path) else []
    steps = extract_code_steps(open(cust_path, encoding="utf-8").read()) \
        if os.path.exists(cust_path) else []
    log(f"  抽出问答 {len(faq)} 条、操作流程 {len(steps)} 套")

    # ---------- 3. 生成结构化问答文件 ----------
    # 放过程文档而不是知识库：原始客户手册里已经有同样的 Q1-Q6，
    # 进知识库会被检索两遍，AI 回答会重复。
    if faq:
        lines = [
            "# 常见问题与解决（结构化）",
            "",
            "> 这份是给 AI 员工看的「标准问答库」。",
            "> 每条都是「现象 → 原因 → 怎么办」，客户问什么都能对上号。",
            "",
        ]
        for it in faq:
            lines.append(f"## Q{it['n']}：{it['q']}")
            lines.append("")
            lines.append(it["a"])
            lines.append("")
        faq_text = "\n".join(lines)
        if apply:
            proc_dir = os.path.join(PACK_DIR, "过程文档")
            os.makedirs(proc_dir, exist_ok=True)
            with open(os.path.join(proc_dir, "常见问题与解决.md"), "w",
                      encoding="utf-8") as f:
                f.write(faq_text)
        log(f"  ✓ 生成 过程文档/常见问题与解决.md（{len(faq)} 条）")

    # ---------- 4. 生成大白话上岗手册 ----------
    guide = _build_plain_guide(faq, steps)
    if apply:
        with open(os.path.join(KB_DIR, "新员工上岗手册.md"), "w",
                  encoding="utf-8") as f:
            f.write(guide)
    log(f"  ✓ 生成 新员工上岗手册.md（{len(guide)} 字）")

    # ---------- 5. 生成操作流程文件 ----------
    # 注意：这两个文件【不进知识库】。它们的内容原始手册里已经有了，
    # 放进知识库会造成同一份内容被检索两遍，AI 回答会重复。
    # 它们的真正用途是给人看的中间稿，存到 过程文档/ 下。
    if steps:
        lines = ["# 操作流程（每套一张卡）", "",
                 "> 按顺序做，一步都不能跳。", ""]
        for i, st in enumerate(steps, 1):
            lines.append(f"## 流程 {i}")
            lines.append("")
            lines.append("```text")
            lines.append(st)
            lines.append("```")
            lines.append("")
        if apply:
            proc_dir = os.path.join(PACK_DIR, "过程文档")
            os.makedirs(proc_dir, exist_ok=True)
            with open(os.path.join(proc_dir, "操作流程卡.md"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(lines))
        log(f"  ✓ 生成 过程文档/操作流程卡.md（{len(steps)} 套，不进知识库）")

    # ---------- 6. pack.json ----------
    pack = {
        "key": "robotics",
        "name": "工业机器人",
        "icon": "🤖",
        "desc": "机器人工作站、程序调试、故障排查、点表维护、交付培训。"
                "以尔必地-寮步联技双磨头四工位打磨系统为样板。",
        "roles": ["A12", "A13", "A14", "A17", "A18", "A19", "A20", "A23"],
        "knowledge": ["设备档案", "点位总表", "IO信号表", "程序逻辑",
                      "故障案例", "操作SOP", "安全规范"],
        "deliverables": ["上岗手册", "操作卡", "故障速查表", "设备档案",
                         "点检表"],
        "theme": "tech",
        "tone": "严谨、可执行、每条都能照着做；先给结论，再给依据",
        "note": "以尔必地项目资料为样板，换客户时复制本目录改内容即可",
    }
    if apply:
        os.makedirs(PACK_DIR, exist_ok=True)
        with open(os.path.join(PACK_DIR, "pack.json"), "w",
                  encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False, indent=2)
    log(f"  ✓ 写 pack.json（{len(pack['roles'])} 个岗位）")

    log("=" * 66)
    if apply:
        log("  ✅ 完成。打开控制台 →「⚡ 实时看板」→ 行业方案 → 切到「🤖 工业机器人」")
        log("     然后点「🚀 现场演示 → 生成客户交付包」")
    else:
        log("  （干跑结束，加 --apply 执行）")
    log("=" * 66)


def _build_plain_guide(faq, steps) -> str:
    """写一份完全不用术语的上岗手册。"""
    L = [
        "# 新员工上岗手册（白话版）",
        "",
        "> **写给谁**：刚来这个车间、没碰过这台设备的人。",
        "> **怎么用**：照着做。看不懂就问 AI 员工，它只会照本资料回答。",
        "",
        "---",
        "",
        "## 一、这台设备是干什么的",
        "",
        "一台机器人带着两个磨头（一个粗磨、一个细磨），"
        "自动把放在四个工位上的金属件打磨干净。",
        "",
        "你不需要懂机器人原理，**你只需要记住三件事**：",
        "",
        "1. **开机按顺序来** —— 顺序错了会出问题",
        "2. **出事先急停** —— 任何异常，先按急停再想别的",
        "3. **不懂就叫人** —— 不要自己拆、不要自己改参数",
        "",
        "---",
        "",
        "## 二、你每天要做的三件事",
        "",
        "### 早上开机",
        "",
        "```text",
    ]
    if steps:
        L.append(steps[0])
    else:
        L.append("第1步  确认所有急停已复位\n"
                 "第2步  打开总电源\n"
                 "第3步  打开气源，确认压力表 0.8 MPa\n"
                 "第4步  等待控制器自检，示教器无报警\n"
                 "第5步  看三色灯，绿灯才能开工")
    L += [
        "```",
        "",
        "### 干活",
        "",
        "```text",
    ]
    if len(steps) > 1:
        L.append(steps[1])
    else:
        L.append("第1步  确认四工位工件都放好并夹紧\n"
                 "第2步  按【启动】\n"
                 "第3步  等四工位全部完成\n"
                 "第4步  取件、放入下一工位\n"
                 "第5步  循环")
    L += [
        "```",
        "",
        "### 下班关机",
        "",
        "```text",
    ]
    if len(steps) > 2:
        L.append(steps[2])
    else:
        L.append("第1步  等当前循环跑完\n"
                 "第2步  切到手动模式\n"
                 "第3步  关闭总电源\n"
                 "第4步  关闭气源\n"
                 "第5步  清理工作台")
    L += [
        "```",
        "",
        "---",
        "",
        "## 三、最常问的问题（背下来）",
        "",
    ]
    for it in faq[:6]:
        L.append(f"### {it['q']}")
        L.append("")
        L.append(it["a"])
        L.append("")
    L += [
        "---",
        "",
        "## 四、绝对不能做的事",
        "",
        "```text",
        "✗ 机器人运行时进入打磨区",
        "✗ 把手或工具伸进磨头能转的地方",
        "✗ 运行时打开安全围栏",
        "✗ 自己改程序、改参数",
        "✗ 用手动模式硬启动磨头",
        "",
        "✓ 长发扎起来，别被卷进去",
        "✓ 取工件戴好防护手套",
        "✓ 换砂纸时手离开夹纸机构",
        "✓ 任何异常：先急停，再排查",
        "```",
        "",
        "---",
        "",
        "## 五、什么时候必须叫维护人员",
        "",
        "| 情况 | 为什么 |\n|---|---|",
        "| 红灯报警、机器人不动 | 要查报警代码 |\n"
        "| 同一工件反复打磨位置都不对 | 点位可能被动过 |\n"
        "| 机器人姿态明显异常 | 可能零点异常，立即停机 |\n"
        "| 有异响、异味 | 机械故障征兆 |\n"
        "| 换砂纸反复失败 | 传感器或机构问题 |\n"
        "| 水磨不出水 | 水路问题 |\n"
        "| 力控失效（磨不进去或压太重） | 气路或参数问题 |",
        "",
        "---",
        "",
        "## 六、每班交接要填的点检表",
        "",
        "| # | 检查项 | 标准 |\n|---|---|---|",
        "| 1 | 急停是否正常 | 按下立即停 |",
        "| 2 | 气压 | 0.8 MPa |",
        "| 3 | 三色灯 | 绿灯 |",
        "| 4 | 砂纸仓余量 | 粗磨、细磨充足 |",
        "| 5 | 光纤检测位 | 无粉尘遮挡 |",
        "| 6 | 工作台 | 清洁，无残留工件 |",
        "| 7 | 工装夹紧 | 四工位夹紧到位 |",
        "| 8 | 水路 | 喷水正常 |",
        "| 9 | 异响异味 | 无 |",
        "| 10 | 示教器报警 | 无 |",
        "",
        "*本手册内容全部来自《客户操作手册》《调试与排障手册》，"
        "有疑问以原文档为准。*",
    ]
    return "\n".join(L)


if __name__ == "__main__":
    main()

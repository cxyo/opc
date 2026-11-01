#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""上架闸门 · 把待审包移进 _已上架/

为什么单独一个文件
------------------
自动流水线只能**产出候选**，不能上架。理由很实在：

    README 写得漂亮 ≠ 装得上、跑得通、对客户有用。

自动上架一次翻车，客户的信任就没了——而信任是这条变现路上
最贵、也最难得重建的东西。所以上架必须是**人按下的按钮**。

三重闸门（缺一不可）
--------------------
    ① 审核通过      —— 审核报告里"是否通过"为真
    ② 协议可商用    —— MIT / Apache / BSD / ISC 等宽松协议
    ③ 人工签字      —— 命令行必须显式传 --confirm

第③条是硬的：**没有 --confirm，脚本什么也不做**。
理由：闸门如果能被"顺手"跳过，那它就不叫闸门。

用法
----
    python publish_gate.py --list                     # 看待审清单和各自卡在哪
    python publish_gate.py --check gh-mouse-mcp       # 查某个包能不能上
    python publish_gate.py --publish gh-mouse-mcp --confirm   # 真正上架
    python publish_gate.py --publish-all --confirm    # 全部能上的都上

上架后做什么
------------
    · 包目录从 _待审/ 移到 _已上架/
    · clawhub.json 的 status 改成 "published"
    · 展示页自动重新生成（客户看到的商品列表会更新）
    · 记一条流水日志
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(PROJECT, "08_可运行系统"))
sys.path.insert(0, HERE)

SKILL_ROOT = os.path.join(PROJECT, "07_AI员工", "05_自动生成技能")
PENDING_DIR = os.path.join(SKILL_ROOT, "_待审")
PUBLISHED_DIR = os.path.join(SKILL_ROOT, "_已上架")
LOG_PATH = os.path.join(SKILL_ROOT, "流水线日志.jsonl")

# 可直接商用的宽松协议
OK_LICENSES = {"MIT", "MIT-0", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause",
               "ISC", "Unlicense", "MPL-2.0", "CC0-1.0"}


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _log(rec):
    rec = dict(rec)
    rec["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def check_one(slug):
    """检查单个包能否上架。返回 (可上架?, 原因列表, 包信息)。"""
    pkg = os.path.join(PENDING_DIR, slug)
    reasons = []
    info = {}

    if not os.path.isdir(pkg):
        return False, ["待审目录里没有这个包：%s" % slug], info

    audit = {}
    ap = os.path.join(pkg, "_审核报告.json")
    if os.path.exists(ap):
        try:
            audit = json.loads(_read(ap))
        except Exception:
            audit = {}
    hub = {}
    hp = os.path.join(pkg, "clawhub.json")
    if os.path.exists(hp):
        try:
            hub = json.loads(_read(hp))
        except Exception:
            hub = {}

    score = audit.get("总分")
    passed = audit.get("是否通过")
    lic = hub.get("license") or ""
    info = {
        "slug": slug,
        "score": score,
        "passed": passed,
        "license": lic,
        "name": hub.get("displayName") or slug,
        "problems": audit.get("阻断问题") or [],
        "warnings": audit.get("提醒") or [],
    }

    # 闸门① 审核通过
    if not passed:
        reasons.append("审核未通过（得分 %s）：%s"
                       % (score, "；".join(info["problems"]) or "有阻断问题"))
    # 闸门② 协议可商用
    if lic not in OK_LICENSES:
        reasons.append("协议是「%s」，不在可直接商用的宽松协议名单里，**必须人工确认授权**"
                       % (lic or "未标注"))
    # 附加检查：必备文件
    for f in ("SKILL.md", "clawhub.json", "README.md", "安装说明.md", "抖音脚本.md"):
        if not os.path.exists(os.path.join(pkg, f)):
            reasons.append("缺少文件：%s" % f)

    return (not reasons), reasons, info


def cmd_list():
    if not os.path.isdir(PENDING_DIR):
        print("没有待审目录")
        return 0
    slugs = sorted([d for d in os.listdir(PENDING_DIR)
                    if os.path.isdir(os.path.join(PENDING_DIR, d))])
    if not slugs:
        print("待审区是空的 —— 跑一次流水线："
              "\n  python 08_可运行系统/tools/auto_pipeline.py")
        return 0
    print("=" * 72)
    print("待审包清单（%d 个）" % len(slugs))
    print("=" * 72)
    for s in slugs:
        ok, reasons, info = check_one(s)
        mark = "✔ 可上架" if ok else "✖ 卡住"
        print("%-10s %-30s 评分 %-5s 协议 %s"
              % (mark, info.get("name", s)[:30], info.get("score", "-"),
                 info.get("license", "-")))
        for r in reasons:
            print("           · %s" % r)
    print()
    print("上架命令（注意 --confirm 是硬闸门）：")
    for s in slugs:
        ok, _, _i = check_one(s)
        if ok:
            print("  python 08_可运行系统/tools/publish_gate.py --publish %s --confirm" % s)
    return 0


def cmd_check(slug):
    ok, reasons, info = check_one(slug)
    print("包：%s" % info.get("name", slug))
    print("评分：%s ｜ 协议：%s" % (info.get("score"), info.get("license")))
    if ok:
        print("✔ 三重闸门全过，可以上架")
        print("  python 08_可运行系统/tools/publish_gate.py --publish %s --confirm" % slug)
    else:
        print("✖ 还差这些：")
        for r in reasons:
            print("   · %s" % r)
    return 0


def cmd_publish(slug, confirmed):
    ok, reasons, info = check_one(slug)
    if not confirmed:
        print("✖ 没传 --confirm，**什么都不做**。")
        print("  这是硬闸门：上架必须是人按下的按钮，脚本不能自己决定。")
        print("  确认无误后再执行：")
        print("  python 08_可运行系统/tools/publish_gate.py --publish %s --confirm" % slug)
        return 1
    if not ok:
        print("✖ 不能上架：")
        for r in reasons:
            print("   · %s" % r)
        return 1

    src = os.path.join(PENDING_DIR, slug)
    dst = os.path.join(PUBLISHED_DIR, slug)
    if os.path.exists(dst):
        print("✖ 已上架目录里已有同名包：%s" % slug)
        return 1
    os.makedirs(PUBLISHED_DIR, exist_ok=True)
    shutil.move(src, dst)

    # zip 跟着一起移
    zsrc = os.path.join(PENDING_DIR, slug + ".zip")
    if os.path.exists(zsrc):
        shutil.move(zsrc, os.path.join(PUBLISHED_DIR, slug + ".zip"))

    # 状态改 published
    hp = os.path.join(dst, "clawhub.json")
    if os.path.exists(hp):
        try:
            hub = json.loads(_read(hp))
            hub["status"] = "published"
            hub["publishedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(hp, "w", encoding="utf-8") as f:
                json.dump(hub, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("   ⚠ clawhub.json 状态更新失败：%s" % e)

    _log({"stage": "publish", "repo": slug, "ok": True,
          "score": info.get("score"), "license": info.get("license")})
    print("✔ 已上架：%s → %s" % (slug, os.path.relpath(dst, PROJECT)))

    # 展示页自动刷新
    try:
        import skill_gallery as SG
        items = SG.collect()
        with open(SG.OUT_HTML, "w", encoding="utf-8") as f:
            f.write(SG.render(items))
        print("   展示页已刷新：%s" % SG.OUT_HTML)
    except Exception as e:
        print("   ⚠ 展示页刷新失败（不影响上架）：%s" % e)
    return 0


def main():
    ap = argparse.ArgumentParser(description="上架闸门：待审包 → 已上架")
    ap.add_argument("--list", action="store_true", help="列出待审包和各自卡在哪")
    ap.add_argument("--check", help="检查某个包能否上架")
    ap.add_argument("--publish", help="上架某个包")
    ap.add_argument("--publish-all", action="store_true", help="上架所有能上的")
    ap.add_argument("--confirm", action="store_true", help="人工确认（硬闸门）")
    args = ap.parse_args()

    if args.list or (not args.check and not args.publish and not args.publish_all):
        return cmd_list()
    if args.check:
        return cmd_check(args.check)
    if args.publish:
        return cmd_publish(args.publish, args.confirm)
    if args.publish_all:
        if not args.confirm:
            print("✖ 没传 --confirm，**什么都不做**。")
            return 1
        n = 0
        for s in sorted(os.listdir(PENDING_DIR)):
            if not os.path.isdir(os.path.join(PENDING_DIR, s)):
                continue
            ok, _, _i = check_one(s)
            if ok:
                r = cmd_publish(s, True)
                if r == 0:
                    n += 1
        print("共上架 %d 个" % n)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

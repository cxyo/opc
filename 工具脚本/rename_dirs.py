# -*- coding: utf-8 -*-
"""一次性目录治理：把日期命名的目录改成语义化名字。

原则：
    1. 只改名和迁移，绝不删除任何内容
    2. 幂等：重复执行安全，已改名的会跳过
    3. 主工程提升到 WorkBuddy 顶层，目录名一眼可辨

映射表在 MAP 里。
"""
import os
import re
import shutil

ROOT = r"C:\Users\micky\WorkBuddy"

# 旧名 -> 新名
MAP = {
    "2026-09-22-00-24-37": "会话记忆_2026年9月批次",
    "2026-09-25-11-31-10": "项目_可转债提醒器",
    "2026-09-25-23-39-10": "项目_个人书签导航站",
    "2026-09-26-01-00-37": "项目_学习目标工作台",
    "2026-09-26-23-24-59": "项目_AI内容创作系统",
    "2026-09-27-00-18-50": "项目_知识库迁移执行表",
    "2026-09-27-00-29-39": "项目_AI导航站",
    "2026-09-27-12-51-12": "项目_一人AI公司_初版",
    "2026-09-27-23-34-40": "项目_家务奖惩记录本",
    "2026-09-29-00-11-34": "项目_债务管理器",
    "_回收_20261019": "回收站",
}

# 主工程：会话目录里的「一人AI公司」子目录要提升到 WorkBuddy 顶层
SESSION_DIR = "2026-09-27-21-38-58"
MAIN_PROJECT = "一人AI公司"

DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(-\d{2}-\d{2})?$")


def merge_up(session_dir):
    """把会话目录里的内容合并提升到 WorkBuddy 顶层。"""
    base = os.path.join(ROOT, session_dir)
    inner = os.path.join(base, MAIN_PROJECT)
    if not os.path.isdir(inner):
        return []
    moved = []
    for name in os.listdir(inner):
        src = os.path.join(inner, name)
        dst = os.path.join(ROOT, name)
        if os.path.exists(dst):
            # 目标已存在（例如 tools/ 同名），做内容合并
            if os.path.isdir(src) and os.path.isdir(dst):
                for sub in os.listdir(src):
                    s2 = os.path.join(src, sub)
                    d2 = os.path.join(dst, sub)
                    if not os.path.exists(d2):
                        shutil.move(s2, d2)
                        moved.append((sub, name))
                shutil.rmtree(src, ignore_errors=True)
            else:
                continue
        else:
            shutil.move(src, dst)
            moved.append((name, "(顶层)"))
    return moved


def main():
    print("=== 1. 提升主工程到 WorkBuddy 顶层 ===")
    for name, where in merge_up(SESSION_DIR):
        print(f"  {name}  ->  {where}")

    print("=== 2. 改名其余日期目录 ===")
    for old, new in MAP.items():
        src = os.path.join(ROOT, old)
        dst = os.path.join(ROOT, new)
        if not os.path.isdir(src):
            print(f"  跳过 {old}（不存在或已改名）")
            continue
        if os.path.isdir(dst):
            print(f"  跳过 {old}（目标 {new} 已存在）")
            continue
        shutil.move(src, dst)
        print(f"  {old}  ->  {new}")

    print("=== 3. 清理空的会话目录 ===")
    base = os.path.join(ROOT, SESSION_DIR)
    if os.path.isdir(base):
        for name in os.listdir(base):
            p = os.path.join(base, name)
            if os.path.isdir(p) and not os.listdir(p):
                shutil.rmtree(p, ignore_errors=True)
                print(f"  已移除空目录 {name}")
        if not os.listdir(base):
            shutil.rmtree(base, ignore_errors=True)
            print(f"  已移除 {SESSION_DIR}")

    print("=== 4. 残留日期命名检查 ===")
    left = [n for n in os.listdir(ROOT) if DATE_RE.match(n)]
    print("  " + (", ".join(left) if left else "无残留"))


if __name__ == "__main__":
    main()

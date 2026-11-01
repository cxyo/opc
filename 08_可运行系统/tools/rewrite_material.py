# -*- coding: utf-8 -*-
"""素材改写引擎 · 命令行入口（薄封装）。

核心逻辑在 `staff/rewrite.py` —— 必须在 staff/ 里，
因为免安装版（PyInstaller）只打包 staff 包，不含 tools/ 目录。
2026-10-31 实测踩过：核心逻辑放 tools/ 时，源码版正常、exe 版报错。

用法：
    python tools/rewrite_material.py --list            # 看有哪些素材可改写
    python tools/rewrite_material.py v_AMsMoY5KBF      # 改写某一条
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from staff import materials as M          # noqa: E402
from staff import rewrite as RW           # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python tools/rewrite_material.py <素材ID>")
        print("      python tools/rewrite_material.py --list")
        return 1

    if sys.argv[1] == "--list":
        for it in M.all_items():
            if it.get("类型") != "video":
                continue
            print("%s  %s  %s" % (it.get("id"),
                                  (it.get("作者") or "")[:10],
                                  (it.get("标题") or "")[:40]))
        return 0

    res = RW.rewrite(sys.argv[1])
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

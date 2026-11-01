#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一人AI公司 · 本地 AI 员工系统 · 入口

用法：
    python run.py doctor            系统体检（第一次跑这个）
    python run.py today             跑今天的完整内容链路
    python run.py product           生成商品详情页
    python run.py ask A07 "写一篇"  单独叫某个员工干活
    python run.py check "文字"       合规预检

无需安装任何第三方库，只用 Python 标准库。
"""

import os
import sys

# Windows 控制台 UTF-8
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.version_info < (3, 8):
    print("需要 Python 3.8 或更高版本，当前是", sys.version.split()[0])
    sys.exit(1)

from staff.cli import main

if __name__ == "__main__":
    sys.exit(main())

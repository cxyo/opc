"""一人AI公司 · 本地 AI 员工系统"""

import os
import sys

__version__ = "2.0"

# 第三方依赖装在工程内的 vendor 目录（不污染系统 Python）。
# 存在这里，staff 下所有模块都能直接 import PIL / imageio_ffmpeg。
_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

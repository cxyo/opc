"""serv00 (Phusion Passenger) 入口文件。

部署位置：~/domains/<域名>/public_python/passenger_wsgi.py

serv00 的 Passenger 固定找这个名字的文件，并要求里面有一个叫
``application`` 的 WSGI 可调用对象。

这个文件只做三件事：
1. 把项目根目录加进 sys.path（Passenger 的 cwd 就是本目录）
2. 导入真正的 WSGI 应用
3. 把名字暴露成 ``application``

代码改完不用手动重启：24 小时内第一次访问会自动重启进程。
"""

import os
import sys

# Passenger 的工作目录就是 passenger_wsgi.py 所在目录，
# 但显式加一次更保险（不同版本 Passenger 的 cwd 行为略有差异）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 08_可运行系统/staff 的上一级是 08_可运行系统，
# 再上一级才是项目根（03_知识库 / 07_AI员工 都在那一层）。
# staff 包本身要能被 import，所以要把 08_可运行系统 这一层加进去。
SYSTEM_DIR = os.path.join(BASE_DIR, "08_可运行系统")
if SYSTEM_DIR not in sys.path:
    sys.path.insert(0, SYSTEM_DIR)

from staff.wsgi_app import application  # noqa: E402

# 让应用内用绝对路径定位资源，不受 cwd 影响
os.environ.setdefault("STAFF_BASE_DIR", BASE_DIR)

if __name__ == "__main__":
    # 本地自检：能跑通不报错，说明配置正确
    print("passenger_wsgi.py 加载成功，application =", application)

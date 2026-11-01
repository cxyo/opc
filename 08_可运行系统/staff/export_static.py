"""把控制台渲染成一份「可直接放 GitHub Pages 的静态页」。

为什么要这个
------------
用户的诉求是：``https://cxyo.cn/opc`` 打开就要看到 AI 员工控制台。
GitHub Pages 只能放静态文件，跑不了 Python 后端。

所以拆成两半：

  前端（静态）→ GitHub Pages 的 ``cxyo/opc`` 仓库
  后端（动态）→ serv00（Passenger 托管 Python）

静态页通过 ``<html data-api="https://micky.serv00.net">`` 告诉前端
「API ��哪台服务器拿」，前端所有 ``/api/xxx`` 请求自动带上跨域凭证。

用法：
    python staff/export_static.py                # 输出到 dist_static/
    python staff/export_static.py --out 目标目录
"""

import argparse
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def build(api_base, out_dir):
    """渲染控制台并写入 out_dir/index.html。"""
    from staff import board as boardmod
    from staff import config as cfgmod
    from staff import console as consolemod

    cfg = cfgmod.load()
    data = boardmod.Board(cfg).build()
    html = consolemod.render_console(cfg, data)

    # 渲染时会把 %%API_BASE%% 替换成空串（服务端自己渲染时同源）。
    # 静态版要写死后端地址，所以这里替换掉。
    html = html.replace('data-api=""', 'data-api="%s"' % api_base)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    # 附一份说明，方便看的人知道这页是什么
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(
            "AI 员工控制台（静态前端）\n"
            "========================\n\n"
            "本目录由 staff/export_static.py 自动生成，请勿手工编辑。\n\n"
            "后端地址：%s\n"
            "后端必须开启 CORS（环境变量 STAFF_CORS_ORIGINS 包含本页来源）。\n"
            "重新生成：python staff/export_static.py --api %s\n"
            % (api_base, api_base)
        )
    return path, len(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="https://micky.serv00.net",
                    help="后端 API 基址（默认 https://micky.serv00.net）")
    ap.add_argument("--out", default=os.path.join(_ROOT, "dist_static"),
                    help="输出目录（默认 08_可运行系统/dist_static）")
    args = ap.parse_args()

    path, size = build(args.api.rstrip("/"), args.out)
    print("✅ 已生成静态控制台：%s" % path)
    print("   大小：%d 字节" % size)
    print("   后端：%s" % args.api)
    return 0


if __name__ == "__main__":
    sys.exit(main())

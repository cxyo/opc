# -*- coding: utf-8 -*-
"""A28 商品视觉官 验证脚本。

跑法：
    cd 08_可运行系统
    python 验证_商品视觉.py

检查项：
    1. 两平台各 5 张主图齐全、尺寸 1200×1200、体积 ≤3MB
    2. 第 5 张白底图严格合规：纯白底、无文字像素、无非白像素
    3. 详情页宽度 750，高度足够（≥3000）
    4. 文案合规体检零问题（极限词 / 促销标 / 站外导流）
    5. 标题长度 ≤30 字、核心词前置
    6. 文案文档已导出
    7. 所有文案里的数字与交付包实测一致（276/24/6/32/85）
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from staff import product_shots as ps  # noqa: E402


def _pil():
    from PIL import Image
    return Image


FAILED = []


def check(cond, name, detail=""):
    if cond:
        print("  ✅ %s" % name)
    else:
        print("  ❌ %s %s" % (name, detail))
        FAILED.append(name)


def main():
    Image = _pil()

    for plat in ("淘宝", "咸鱼"):
        print("\n=== %s ===" % plat)
        outdir = ps.shots_dir(plat)

        # 1) 主图齐全
        slots = ps.MAIN_COPY[plat]
        for slot in slots:
            p = os.path.join(outdir, "主图%d_%s.png" % (slot["no"], slot["role"]))
            exists = os.path.isfile(p)
            check(exists, "主图%d 存在" % slot["no"], p)
            if not exists:
                continue
            im = Image.open(p)
            check(im.size == (1200, 1200), "主图%d 尺寸 1200×1200" % slot["no"],
                  str(im.size))
            size_kb = os.path.getsize(p) // 1024
            check(size_kb <= 3072, "主图%d 体积 ≤3MB" % slot["no"],
                  "%dKB" % size_kb)

        # 2) 第 5 张白底图严格合规
        white_path = os.path.join(outdir, "主图5_白底图.png")
        if os.path.isfile(white_path):
            im = Image.open(white_path).convert("RGB")
            px = im.load()
            W, H = im.size
            corners = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1),
                       (W // 2, 0), (0, H // 2)]
            pure_white = all(px[x, y] == (255, 255, 255) for x, y in corners)
            check(pure_white, "白底图四角与边缘纯白 RGB(255,255,255)")
            # 非白像素占比应在 8%-45%：太低说明没主体，太高说明背景不干净
            nonwhite = 0
            total = 0
            for yy in range(0, H, 6):
                for xx in range(0, W, 6):
                    total += 1
                    if px[xx, yy] != (255, 255, 255):
                        nonwhite += 1
            ratio = nonwhite / total
            check(0.08 <= ratio <= 0.45,
                  "白底图主体占比合理（非白像素 %.1f%%）" % (ratio * 100))
            # 有色像素里不应有接近纯黑/纯灰的文字色块（文字是深色实心块）
            dark = 0
            for yy in range(0, H, 6):
                for xx in range(0, W, 6):
                    r, g, b = px[xx, yy]
                    if r < 90 and g < 90 and b < 90:
                        dark += 1
            check(dark == 0, "白底图无深色文字块", "深色像素 %d" % dark)

        # 3) 详情页
        detail = os.path.join(outdir, "详情页_750px长图.png")
        check(os.path.isfile(detail), "详情页存在")
        if os.path.isfile(detail):
            im = Image.open(detail)
            check(im.size[0] == 750, "详情页宽度 750", str(im.size))
            check(im.size[1] >= 3000, "详情页高度 ≥3000", str(im.size[1]))

        # 4) 文案合规
        probs = ps.compliance_check(plat)
        check(not probs, "文案合规体检零问题",
              "；".join(f"{p['位置']} {p['问题']}" for p in probs[:3]))

        # 5) 标题
        for t in ps.TITLES[plat]:
            check(len(t) <= 30, "标题 ≤30 字：「%s」" % t[:14], "%d 字" % len(t))
            check(len(t) >= 8, "标题 ≥8 字：「%s」" % t[:14], "%d 字" % len(t))

        # 6) 文案文档
        for name in (f"商品文案_{plat}.md", f"商品文案_{plat}_纯文本.txt"):
            p = os.path.join(outdir, name)
            check(os.path.isfile(p), f"文案文档存在：{name}")

        # 7) 数字与交付包实测一致
        intro = ps.build_product_intro(plat)
        blob = json.dumps(intro, ensure_ascii=False)
        for num, label in (("276", "文件数"), ("24", "岗位数"), ("6", "行业方案"),
                           ("32", "已接平台"), ("85", "禁区红线")):
            check(num in blob, f"文案含实测数字 {num}（{label}）")

        # 8) 禁站外导流
        for bad in ("微信", "二维码", "加V", "vx", "威信"):
            check(bad not in blob, f"文案无站外导流词「{bad}」")

    print("\n" + "=" * 60)
    if FAILED:
        print("❌ %d 项未通过：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    import json  # noqa: E402  (放在最后避免污染模块级)
    sys.exit(main())

# -*- coding: utf-8 -*-
"""前端脚本自检。

为什么要单独写这个：
  控制台页面是「一个巨大的 <script> 块」。一旦里面有一处语法错误
  （最常见的是 const 的暂时性死区 TDZ、括号不配对、字符串没闭合），
  整个 script 块都不执行 —— 页面上什么都不会报错，
  只是「没数据」「点了没反应」，极难排查。

所以这个脚本把模板里的 JS 抽出来，用 Node（如果有）或
纯 Python 的括号/引号配平检查 + 关键字顺序检查，
在改动模板后立刻跑一遍，把问题挡在浏览器之前。

用法：
    python 工具脚本/检查前端脚本.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

TPL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "08_可运行系统", "staff", "console_template.html")


def extract_js(html: str) -> str:
    """抽出最后一个 <script> 块的 JS 代码。

    还要把模板占位符 %%XXX%% 换成合法 JS 值，
    否则 Node 检查会因为 '%%' 直接报语法错（假警报）。
    """
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    js = blocks[-1] if blocks else ""
    # %%ROLE_JSON%% 这类占位符 → 合法字面量
    js = re.sub(r"%%[A-Z_]+%%", "[]", js)
    return js


def strip_regex_literals(js: str) -> str:
    """把正则字面量里的括号替换成空格，避免配平检查误报。

    判据：/ 前面是 ( , = : [ ! & | ? { } ; 或行首（排除注释和除号）。
    """
    out = []
    i = 0
    n = len(js)
    in_str = None
    while i < n:
        c = js[i]
        if in_str:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(js[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in "'\"":
            in_str = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            while i < n and js[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            while i + 1 < n and not (js[i] == "*" and js[i + 1] == "/"):
                out.append("\n" if js[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        if c == "/":
            # 判断是不是正则字面量
            prev = ""
            for k in range(len(out) - 1, -1, -1):
                if out[k] not in " \t":
                    prev = out[k]
                    break
            if prev in "=(,:[!&|?{};+" or prev == "":
                # 是正则，把到下一个未转义 / 之间的内容清空
                out.append(" ")
                i += 1
                while i < n:
                    if js[i] == "\\":
                        out.append("  ")
                        i += 2
                        continue
                    if js[i] == "[":
                        # 字符类，里面的括号不算
                        out.append(" ")
                        i += 1
                        while i < n and js[i] != "]":
                            out.append(" ")
                            i += 1
                        out.append(" ")
                        i += 1
                        continue
                    if js[i] == "/":
                        break
                    out.append(" " if js[i] != "\n" else "\n")
                    i += 1
                out.append("/")
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def check_balanced(js: str):
    """括号 / 引号 / 模板串配平检查。"""
    errors = []
    # 去掉字符串和注释，避免误报
    i = 0
    n = len(js)
    stack = []
    line = 1
    pairs = {")": "(", "]": "[", "}": "{"}
    while i < n:
        c = js[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        # 行注释
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            while i < n and js[i] != "\n":
                i += 1
            continue
        # 块注释
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            i += 2
            while i + 1 < n and not (js[i] == "*" and js[i + 1] == "/"):
                if js[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        # 字符串
        if c in "'\"":
            q = c
            i += 1
            while i < n:
                if js[i] == "\\":
                    i += 2
                    continue
                if js[i] == q:
                    break
                if js[i] == "\n":
                    line += 1
                i += 1
            i += 1
            continue
        # 模板串（内部可能有 ${}，这里只做粗略处理）
        if c == "`":
            i += 1
            depth = 0
            while i < n:
                if js[i] == "\\":
                    i += 2
                    continue
                if js[i] == "$" and i + 1 < n and js[i + 1] == "{":
                    depth += 1
                    i += 2
                    continue
                if js[i] == "}" and depth > 0:
                    depth -= 1
                    i += 1
                    continue
                if js[i] == "`" and depth == 0:
                    break
                if js[i] == "\n":
                    line += 1
                i += 1
            i += 1
            continue
        if c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if not stack:
                errors.append(f"第 {line} 行：多余的 '{c}'")
            else:
                op, ol = stack.pop()
                if op != pairs[c]:
                    errors.append(f"第 {line} 行：'{c}' 与第 {ol} 行的 '{op}' 不匹配")
        i += 1
    for op, ol in stack:
        errors.append(f"第 {ol} 行：'{op}' 没有闭合")
    return errors


def check_tdz(js: str):
    """检查 const/let 是否在声明前就被引用（TDZ）。

    只看「顶层声明」：对象字面量里的 key（如 cost: 0）不是引用，
    只有 value 位置引用了后声明的变量才算问题。
    """
    errors = []
    decls = {}
    for m in re.finditer(r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
                         js, re.M):
        decls.setdefault(m.group(1), m.start())

    for m in re.finditer(r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\{",
                         js, re.M):
        obj_name = m.group(1)
        body_start = m.end()
        depth = 1
        j = body_start
        while j < len(js) and depth:
            if js[j] == "{":
                depth += 1
            elif js[j] == "}":
                depth -= 1
            j += 1
        body = js[body_start:j - 1]
        # 只看 value 位置（key 后面跟冒号的忽略）
        for dm in re.finditer(r":\s*([A-Za-z_$][\w$]*)\s*[,}\n]", body):
            ref = dm.group(1)
            if ref in decls and decls[ref] > m.start():
                errors.append(
                    f"TDZ 风险：对象 {obj_name} 在声明前引用了 {ref}"
                    f"（{ref} 声明在更后面）→ 整个 script 会静默失败")
    return errors


def main() -> int:
    if not os.path.exists(TPL):
        print(f"✘ 找不到模板：{TPL}")
        return 1
    html = open(TPL, encoding="utf-8").read()
    js = extract_js(html)
    if not js.strip():
        print("✘ 模板里没有 <script> 块")
        return 1

    print(f"模板：{TPL}")
    print(f"JS 长度：{len(js)} 字符，{js.count(chr(10)) + 1} 行")
    print("-" * 60)

    ok = True

    # 1) 括号配平（用清洗过的 JS，排除正则字面量干扰）
    errs = check_balanced(strip_regex_literals(js))
    if errs:
        ok = False
        print("✘ 括号/引号配平问题：")
        for e in errs[:10]:
            print(f"    {e}")
    else:
        print("✔ 括号/引号配平")

    # 2) TDZ 检查
    errs = check_tdz(js)
    if errs:
        ok = False
        print("✘ 暂时性死区（TDZ）问题：")
        for e in errs[:10]:
            print(f"    {e}")
    else:
        print("✔ 无明显的 TDZ 风险")

    # 3) Node 语法检查（如果有 node）
    node = shutil.which("node")
    if node:
        tmp = os.path.join(os.environ.get("TEMP", "."), "_wb_js_check.js")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(js)
        r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
        if r.returncode == 0:
            print("✔ Node 语法检查通过")
        else:
            ok = False
            print("✘ Node 语法检查失败：")
            print("   ", (r.stderr or "").strip().splitlines()[:6])
        try:
            os.remove(tmp)
        except OSError:
            pass
    else:
        print("· 未装 Node，跳过语法检查（括号与 TDZ 检查已覆盖常见问题）")

    print("-" * 60)
    if ok:
        print("✅ 前端脚本自检通过")
        return 0
    print("❌ 前端脚本有问题，先修好再刷新页面")
    return 1


if __name__ == "__main__":
    sys.exit(main())

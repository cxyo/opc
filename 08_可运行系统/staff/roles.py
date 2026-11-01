"""岗位加载器：从 07_AI员工/01_Bot提示词/ 的 Markdown 里解析出可执行的角色定义。

每个文件结构固定：
    ## 一、人设与回复逻辑
    ```
    <人设正文>
    ```
    ## 二、开场白 ...
    ## 三、建议问题 ...
    ## 四、知识库挂载 ...
    ## 五、输入变量 ...

解析出：code / name / layer / persona(人设正文) / quick_questions / kb_mounts / input_vars
"""

import os
import re

# 岗位分层（与总纲一致）
LAYERS = {
    "L0 约束层": ["A01", "A02", "A23"],
    "L1 输入层": ["A03", "A04", "A05", "A19", "A25", "A26"],
    "L2 生产层": ["A06", "A07", "A08", "A09", "A17", "A20"],
    "L3 检测层": ["A10"],
    "L4 分发层": ["A11", "A12", "A13", "A16", "A22"],
    "L5 闭环层": ["A14", "A15", "A18", "A21"],
}

# 自动化等级
AUTOMATION = {
    "A01": "L1", "A02": "L1", "A03": "L1", "A04": "L1", "A05": "L2",
    "A06": "L1", "A07": "L2", "A08": "L2", "A09": "L2", "A10": "L2",
    "A11": "L2", "A12": "L1", "A13": "L3", "A14": "L1", "A15": "L2",
    "A16": "L2", "A17": "L2", "A18": "L3", "A19": "L1", "A20": "L2",
    "A21": "L2", "A22": "L3", "A23": "L2", "A25": "L2", "A26": "L2",
}

# 日常链路里真正需要调用的岗位（其余为按需/手动触发）
DAILY_PIPELINE = ["A03", "A04", "A05", "A07", "A10", "A08", "A09", "A11", "A14"]

# 每日自动同步类岗位（不进内容流水线，单独跑）
#
# A26 微信情报官：读微信聊天记录 → 分类客户/供应商/内部 → 抽取阶段与卡点。
# 它不产出文章，但老板每天要看一次"哪个客户推到哪一步了、哪个供应商货到哪了"。
# 所以单独列一张表，用途和 DAILY_PIPELINE 不一样，不能混进去。
DAILY_SYNC = {
    "A26": "微信情报：每天增量同步微信聊天记录，刷新客户/供应商/内部三线看板",
}

# 每日同步任务的顺序（早 → 晚）
DAILY_SYNC_ORDER = ["A26"]


def _layer_of(code):
    for layer, codes in LAYERS.items():
        if code in codes:
            return layer
    return "其他"


def extract_persona_from_text(raw: str) -> str:
    """从提示词文本里抽出人设正文（第一个 ``` 代码块）。

    单独做成函数，是为了给「行业包覆盖岗位人设」用 ——
    行业包里的岗位提示词文件格式跟通用的一模一样，
    所以可以复用同一套解析规则。
    """
    m = re.search(r"##\s*一、人设与回复逻辑.*?\n```\n(.*?)\n```", raw, re.S)
    if m:
        return m.group(1).strip()
    # 退化：取第一个代码块
    m = re.search(r"```\n(.*?)\n```", raw, re.S)
    return m.group(1).strip() if m else ""


class Role:
    """一个岗位。"""

    def __init__(self, code, name, path):
        self.code = code
        self.name = name
        self.path = path
        self.layer = _layer_of(code)
        self.automation = AUTOMATION.get(code, "L2")
        self.persona = ""
        self.quick_questions = []
        self.kb_mounts = []
        self.input_vars = []
        self._raw = ""
        self._load()

    def _load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            self._raw = f.read()
        self.persona = self._extract_persona()
        self.quick_questions = self._extract_quick_questions()
        self.kb_mounts = self._extract_kb_mounts()
        self.input_vars = self._extract_input_vars()

    def _extract_persona(self):
        """人设正文 = 第一个 ``` 代码块。"""
        return extract_persona_from_text(self._raw)

    def _section(self, title):
        m = re.search(r"##\s*" + re.escape(title) + r".*?\n(.*?)(?=\n##\s|\Z)", self._raw, re.S)
        return m.group(1).strip() if m else ""

    def _extract_quick_questions(self):
        sec = self._section("三、建议问题")
        qs = []
        for line in sec.split("\n"):
            s = line.strip()
            if not s or set(s) <= set("-= "):
                continue
            s = re.sub(r"^\d+[\.、]\s*", "", s)
            s = s.strip("`").strip()
            # 去掉 markdown 代码块残留
            if s.startswith("```") or s.endswith("```"):
                continue
            if s and len(s) > 2:
                qs.append(s)
        return qs[:5]

    def _extract_kb_mounts(self):
        sec = self._section("四、知识库挂载")
        out = []
        for line in sec.split("\n"):
            s = line.strip()
            if s.startswith("|") and "KB_" in s:
                cells = [c.strip() for c in s.strip("|").split("|")]
                if cells and cells[0] and not set(cells[0]) <= set("-: "):
                    out.append(cells[0])
        return out

    def _extract_input_vars(self):
        sec = self._section("五、输入变量")
        out = []
        for line in sec.split("\n"):
            s = line.strip()
            if s.startswith("|") and "变量名" not in s:
                cells = [c.strip() for c in s.strip("|").split("|")]
                if cells and cells[0] and not set(cells[0]) <= set("-: "):
                    out.append(cells[0])
        return out

    def system_prompt(self, extra_kb=""):
        """组装给 LLM 的 system prompt。"""
        parts = [self.persona]
        if extra_kb:
            parts.append("\n\n---\n\n【本次检索到的参考资料】\n" + extra_kb)
        return "\n\n".join(p for p in parts if p)

    def __repr__(self):
        return f"<Role {self.code} {self.name} {self.layer} {self.automation}>"


class RoleRegistry:
    """全部岗位的注册表。"""

    def __init__(self, roles_dir):
        self.roles_dir = roles_dir
        self.roles = {}
        self._load_all()

    def _load_all(self):
        if not os.path.isdir(self.roles_dir):
            return
        for fn in sorted(os.listdir(self.roles_dir)):
            if not fn.endswith(".md"):
                continue
            m = re.match(r"(A\d{2})_(.+)\.md$", fn)
            if not m:
                continue
            code, name = m.group(1), m.group(2)
            self.roles[code] = Role(code, name, os.path.join(self.roles_dir, fn))

    def get(self, code):
        r = self.roles.get(code.upper())
        if not r:
            raise KeyError(f"没有这个岗位：{code}。可用：{', '.join(sorted(self.roles))}")
        return r

    def all(self):
        return [self.roles[c] for c in sorted(self.roles)]

    def by_layer(self):
        out = {}
        for r in self.all():
            out.setdefault(r.layer, []).append(r)
        return out

    def daily(self):
        return [self.roles[c] for c in DAILY_PIPELINE if c in self.roles]

    def daily_sync(self):
        """每日自动同步类岗位（A26 微信情报官等）。"""
        return [self.roles[c] for c in DAILY_SYNC_ORDER if c in self.roles]

    def sync_descriptions(self):
        """每日同步任务说明（给日报/状态墙展示）。"""
        return [(c, DAILY_SYNC[c]) for c in DAILY_SYNC_ORDER if c in self.roles]

    def summary_table(self):
        lines = ["| 编号 | 岗位 | 层级 | 自动化 |", "|---|---|---|---|"]
        for r in self.all():
            lines.append(f"| {r.code} | {r.name} | {r.layer} | {r.automation} |")
        return "\n".join(lines)

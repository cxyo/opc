# -*- coding: utf-8 -*-
"""行业包机制 —— 让这套系统能卖给任何行业。

为什么要做这个：
    用户要的不是「一个 AI 公司」，而是「能给各行各业的企业客户搭一套能落地的
    AI 员工」。如果每个客户都要重新开发，那就不叫产品了。

行业包是什么：
    一个行业包 = 一套岗位 + 一批知识库 + 一套话术 + 一组交付物 + 一个视觉主题。
    切换行业 = 换掉这些，等于把「AI 员工」派到另一个车间。

怎么落地：
    - 行业包存在工程目录 `03_知识库/行业包/<key>/`，纯 Markdown + JSON，好读好改
    - 切换时写入 `data/state/industry.json`，作为「当前行业」
    - 岗位从「全量 23 个」过滤成该行业相关的几个
    - 知识库检索范围切到该行业的 raw 目录
    - 交付物清单跟着换，客户交付包生成器直接读它

不做什么：
    不改动原有的 23 个 AI 员工，行业包只是「筛选 + 换知识库 + 换话术」，
    所以换行业不会有任何破坏性风险，随时能切回来。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from . import config as cfgmod


# ---------------- 内置行业包 ----------------
# 这是「出厂预装」的几个。放在磁盘上后可以随意改，改完重启即生效。

BUILTIN: List[Dict[str, Any]] = [
    {
        "key": "general",
        "name": "通用办公提效",
        "icon": "🧠",
        "desc": "什么行业都能用：把老板自己的资料变成能问的知识库，再写材料、做方案、整理会议、写通知、总结汇报。",
        "roles": ["A01", "A02", "A05", "A07", "A10", "A11", "A15", "A16", "A24", "A25"],
        "role_names": {"A01": "安全合规员", "A02": "制度政策员", "A05": "选题策划", "A07": "撰稿员", "A10": "质检员", "A11": "发布排版", "A15": "客服售后", "A16": "GEO优化", "A24": "数字人视频官", "A25": "企业知识库管理员"},
        "knowledge": ["模板库", "写作规范", "案例库", "客户资料"],
        "deliverables": ["方案初稿", "汇报材料", "会议纪要", "通知公告", "带出处的问答"],
        "theme": "tech",
        "tone": "专业、克制、结论先行",
        "note": "默认行业，先用这个跑通流程",
        # 通用版的步骤链从「资料」开始 —— 老板的 AI 员工第一步就是读他的资料
        "chain": [
            {"key": "kb", "name": "建企业知识库", "role": "A25", "desc": "导入产品手册/报价单/SOP"},
            {"key": "ask", "name": "用资料回答", "role": "A15", "desc": "带出处的应答"},
            {"key": "draft", "name": "写材料", "role": "A07", "desc": "方案、通知、汇报"},
            {"key": "check", "name": "发布前质检", "role": "A10", "desc": "红线与事实核对"},
            {"key": "ship", "name": "排版发布", "role": "A11", "desc": "多平台适配"},
        ],
    },
    {
        "key": "robotics",
        "name": "工业机器人",
        "icon": "🤖",
        "desc": "机器人工作站、程序调试、故障排查、点表维护、交付培训。",
        "roles": ["A12", "A13", "A14", "A17", "A18", "A19", "A20", "A23"],
        "role_names": {"A23": "需求分析员", "A13": "设备档案员", "A14": "点表工程师", "A17": "调试工程师", "A18": "排障工程师", "A19": "培训交付员", "A12": "工位规划员", "A20": "数据回放员"},
        "knowledge": ["设备档案", "点位总表", "IO信号表", "程序逻辑", "故障案例"],
        "deliverables": ["设备档案", "操作手册", "排障手册", "培训材料"],
        "theme": "tech",
        "tone": "严谨、可执行、每条都能照着做",
        "note": "机器人现场技术员从零上手用这个",
        "chain": [
            {"key": "req", "name": "接需求", "role": "A23", "desc": "客户要解决什么问题"},
            {"key": "device", "name": "查设备档案", "role": "A13", "desc": "机型、轴数、参数"},
            {"key": "point", "name": "查点表与IO", "role": "A14", "desc": "点位坐标、信号定义"},
            {"key": "code", "name": "写调试程序", "role": "A17", "desc": "流程与安全互锁"},
            {"key": "fault", "name": "排障", "role": "A18", "desc": "报警码与处理办法"},
            {"key": "deliver", "name": "培训交付", "role": "A19", "desc": "上岗培训与文档"},
        ],
    },
    {
        "key": "helmet",
        "name": "头盔厂交付",
        "icon": "🪖",
        "desc": "面向完全没接触过 AI 的车间员工：从设备知识库开始，一步一步来。",
        "roles": ["A13", "A18", "A19", "A23"],
        "role_names": {"A13": "设备档案员", "A18": "操作指导员", "A19": "故障诊断员", "A23": "现场答复员"},
        "knowledge": ["设备档案", "操作SOP", "常见故障", "安全规范"],
        "deliverables": ["上岗手册", "操作卡", "故障速查表", "安全红线"],
        "theme": "warm",
        "tone": "大白话、不用术语、每步都有图",
        "note": "零基础员工第一天就能用",
        "chain": [
            {"key": "device", "name": "查设备档案", "role": "A13", "desc": "机器人型号、参数、点位"},
            {"key": "sop", "name": "查操作SOP", "role": "A18", "desc": "开机、点检、换料怎么操作"},
            {"key": "fault", "name": "查常见故障", "role": "A19", "desc": "报警码含义与处理办法"},
            {"key": "answer", "name": "出解决方案", "role": "A23", "desc": "给员工一条能照做的答案"},
        ],
    },
    {
        "key": "ecommerce",
        "name": "电商运营",
        "icon": "🛒",
        "desc": "商品文案、详情页、评价分析、竞品监控、活动方案。",
        "roles": ["A04", "A06", "A07", "A14", "A15", "A17", "A22"],
        "role_names": {"A04": "卖点素材策划", "A06": "详情页拆解", "A07": "商品文案", "A14": "评价复盘", "A15": "客服售后", "A17": "短视频", "A22": "主图视觉"},
        "knowledge": ["爆款话术", "平台规则", "竞品库", "评价高频词"],
        "deliverables": ["详情页文案", "主图文案", "评价分析", "活动方案"],
        "theme": "warm",
        "tone": "有网感、卖点前置、拒绝套话",
        "note": "电商团队日常内容生产",
    },
    {
        "key": "marketing",
        "name": "市场营销",
        "icon": "📣",
        "desc": "活动策划、投放文案、客户画像、私域运营、品牌内容。",
        "roles": ["A05", "A06", "A07", "A08", "A10", "A15", "A21"],
        "role_names": {"A05": "活动选题", "A06": "案例拆解", "A07": "投放文案", "A08": "文案改稿", "A10": "合规质检", "A15": "私域运营", "A21": "方案交付"},
        "knowledge": ["品牌手册", "活动案例", "渠道话术", "竞品分析"],
        "deliverables": ["活动方案", "投放文案", "私域话术", "品牌手册"],
        "theme": "warm",
        "tone": "有感染力、但不过度承诺",
        "note": "给市场部做内容弹药库",
    },
]


# ---------------- 步骤链 ----------------
# 「实时看板」上那一条会亮起来的链条：AI 员工现在跑到哪一步。
#
# 两种链：
#   1. 内容链（默认）—— 内容生产 10 步，对应 workflow.STEPS。
#      内容类行业（通用办公、电商、市场）用它，因为它们的活就是写内容。
#   2. 岗位链 —— 行业包自己定义（key/name/role/desc）。
#      现场服务类行业（机器人、头盔厂）用它，因为它们干的是
#      「查设备 → 查SOP → 查故障 → 出答案」，不是写公众号。
#
# 步骤链可以在 pack.json 里用 "chain" 字段覆盖，不用改代码。
DEFAULT_CHAIN = [
    {"key": "intel", "name": "情报采集", "stage": "intel"},
    {"key": "material", "name": "素材整理", "stage": "material"},
    {"key": "topic", "name": "选题确定", "stage": "topic"},
    {"key": "draft", "name": "撰稿", "stage": "draft"},
    {"key": "check", "name": "质量检查", "stage": "check"},
    {"key": "fix", "name": "改稿", "stage": "fix"},
    {"key": "human", "name": "人工确认", "stage": "human"},
    {"key": "adapt", "name": "去AI味", "stage": "adapt"},
    {"key": "media", "name": "配图与视频", "stage": "media"},
    {"key": "package", "name": "公众号排版", "stage": "package"},
]


# ---------------- 磁盘读写 ----------------

def _pack_dir(cfg) -> str:
    """行业包存放目录。放在知识库下面，跟着工程走。"""
    root = cfg["paths"].get("kb_wiki") or cfg["paths"]["raw"]
    # 03_知识库/wiki 的上一级就是 03_知识库
    base = os.path.dirname(root) if root else root
    return os.path.join(base, "行业包")


def _state_file(cfg) -> str:
    return os.path.join(cfg["paths"]["state"], "industry.json")


def _read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def list_industries(cfg) -> List[Dict[str, Any]]:
    """列出全部行业包：磁盘上的优先（用户改过的），内置的补齐。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    pdir = _pack_dir(cfg)
    if os.path.isdir(pdir):
        for key in sorted(os.listdir(pdir)):
            p = os.path.join(pdir, key, "pack.json")
            data = _read_json(p)
            if not data:
                continue
            data.setdefault("key", key)
            data["source"] = "本地"
            out.append(data)
            seen.add(data["key"])
    for b in BUILTIN:
        if b["key"] not in seen:
            b = dict(b)
            b["source"] = "内置"
            out.append(b)
    return out


def get_pack(cfg, key: str) -> Optional[Dict[str, Any]]:
    for i in list_industries(cfg):
        if i["key"] == key:
            return i
    return None


def current(cfg) -> str:
    st = _read_json(_state_file(cfg), {}) or {}
    return st.get("key") or "general"


def current_pack(cfg) -> Dict[str, Any]:
    key = current(cfg)
    p = get_pack(cfg, key)
    if p:
        return p
    return dict(BUILTIN[0])


def switch(cfg, key: str) -> Dict[str, Any]:
    """切换当前行业。"""
    pack = get_pack(cfg, key)
    if not pack:
        return {"ok": False, "error": f"没有这个行业包：{key}"}
    _write_json(_state_file(cfg), {
        "key": key, "name": pack.get("name", key),
        "switched_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
    })
    # 同步人设档案里的行业字段（如果有）
    try:
        from . import profile as profmod
        prof = profmod.load_profile(cfg)
        prof.setdefault("positioning", {})["industry"] = pack.get("name", key)
        prof["positioning"]["industry_key"] = key
        profmod.save_profile(cfg, prof)
    except Exception:
        pass
    return {"ok": True, "key": key, "name": pack.get("name", key),
            "pack": pack}


def allowed_roles(cfg) -> List[str]:
    """当前行业可用的岗位编号。"""
    pack = current_pack(cfg)
    return pack.get("roles") or []


def role_visible(cfg, code: str) -> bool:
    """岗位在当前行业是否可见。通用行业全部可见。"""
    pack = current_pack(cfg)
    if pack.get("key") == "general":
        return True
    return code in (pack.get("roles") or [])


# ---------------- 岗位人设覆盖 ----------------
#
# 为什么要这个机制：
#   岗位编号（A01–A23）带着内容生产体系的历史包袱。
#   行业包只改了显示名（「A19 培训交付员」），但底层人设提示词
#   还是内容行业的（写品牌实体卡、查账号定位卡…）。
#   结果：客户看到「培训交付员」却在回答公众号选题，名不副实。
#
#   所以行业包可以自带岗位人设，覆盖通用人设：
#       03_知识库/行业包/<行业>/岗位提示词/A19_培训交付员.md
#
#   文件格式跟 07_AI员工/01_Bot提示词/ 里的完全一样，
#   这样维护的人不用学新格式。

def _role_override_dir(cfg) -> str:
    """当前行业包的岗位提示词覆盖目录。"""
    pack = current_pack(cfg)
    key = pack.get("key") or "general"
    base = _pack_dir(cfg)
    return os.path.join(base, key, "岗位提示词")


def role_overrides(cfg) -> Dict[str, str]:
    """当前行业的岗位人设覆盖：{岗位编号: 人设正文}。

    返回的正文会直接顶替通用人设注入 system prompt。
    """
    out: Dict[str, str] = {}
    d = _role_override_dir(cfg)
    if not os.path.isdir(d):
        return out
    try:
        from . import roles as rolesmod
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md"):
                continue
            m = re.match(r"(A\d{2})_(.+)\.md$", fn)
            if not m:
                continue
            code = m.group(1)
            path = os.path.join(d, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except OSError:
                continue
            persona = rolesmod.extract_persona_from_text(raw)
            if persona:
                out[code] = persona
    except Exception:
        pass
    return out


def role_name(cfg, code: str, fallback: str = "") -> str:
    """岗位在当前行业的显示名。"""
    pack = current_pack(cfg)
    names = pack.get("role_names") or {}
    return names.get(code) or fallback or code


# ---------------- 步骤链 ----------------

def chain(cfg) -> List[Dict[str, Any]]:
    """当前行业在实时看板上显示的步骤链。

    行业包 pack.json 里写了 "chain" 就用它，
    没写就退回默认的内容生产 10 步。
    """
    pack = current_pack(cfg)
    c = pack.get("chain")
    if c and isinstance(c, list) and c:
        return c
    return list(DEFAULT_CHAIN)


def chain_key_map(cfg) -> Dict[str, str]:
    """岗位编号 → 步骤链里的步骤 key。

    用于把「A13 商品官正在跑」这类事件挂到链条正确的那一格上。
    """
    out: Dict[str, str] = {}
    for s in chain(cfg):
        role = s.get("role")
        key = s.get("key")
        if role and key:
            out[role] = key
    return out


def role_names(cfg) -> Dict[str, str]:
    """行业包自定义的岗位名。{"A13": "设备档案员", ...}

    为什么需要：
      岗位编号 A01–A23 是内容生产体系留下来的编号。
      换到头盔厂以后，直接显示「A13 商品官」客户看不懂，
      也不知道这个 AI 员工到底是干嘛的。
      所以每个行业包可以给自己这几个岗位重新起名，
      现场的人一眼就知道「这是查设备的那个」。
    """
    pack = current_pack(cfg)
    n = pack.get("role_names")
    return n if isinstance(n, dict) else {}


# ---------------- 交付物清单 ----------------

def deliverables(cfg) -> List[str]:
    return current_pack(cfg).get("deliverables") or []


def knowledge_dirs(cfg, key: str = None) -> List[str]:
    """指定行业（不传则当前行业）的知识库来源目录。

    传 key 是为了「不切换行业也能读别人的资料」，
    比如生成交付包时要把卡诺普的资料一并打进去。
    """
    if not key:
        key = current_pack(cfg).get("key", "general")
    dirs = []
    kdir = os.path.join(_pack_dir(cfg), key, "知识库")
    if os.path.isdir(kdir):
        dirs.append(kdir)
    return dirs


# ---------------- 行业包脚手架 ----------------

def create_pack(cfg, key: str, name: str, icon: str = "📦",
                desc: str = "", roles=None) -> Dict[str, Any]:
    """在磁盘上创建一个新的行业包目录（骨架 + 模板文件）。

    交付给客户时的标准做法：
      复制一个行业包目录 → 改 pack.json → 往 知识库/ 里塞客户资料
      → 控制台切到这个包 → 立刻就是那家客户专属的 AI 员工
    """
    pdir = os.path.join(_pack_dir(cfg), key)
    os.makedirs(os.path.join(pdir, "知识库"), exist_ok=True)
    pack = {
        "key": key, "name": name, "icon": icon, "desc": desc,
        "roles": list(roles or []),
        "knowledge": ["设备档案", "操作SOP", "常见故障"],
        "deliverables": ["上岗手册", "操作卡", "故障速查表"],
        "theme": "tech", "tone": "大白话、可执行", "note": "",
    }
    _write_json(os.path.join(pdir, "pack.json"), pack)

    # 模板：设备档案
    _write_text(os.path.join(pdir, "知识库", "设备档案.md"),
                f"# {name} · 设备档案\n\n"
                "> 这份文件是 AI 员工的知识来源。写得越具体，问得越准。\n\n"
                "## 设备基本信息\n\n| 项目 | 内容 |\n|---|---|\n"
                "| 设备名称 | |\n| 型号 | |\n| 生产厂家 | |\n"
                "| 出厂日期 | |\n| 责任人 | |\n\n"
                "## 主要参数\n\n"
                "| 参数 | 数值 | 单位 | 说明 |\n|---|---|---|---|\n| | | | |\n\n"
                "## 常见问题\n\n"
                "- \n")

    # 模板：操作 SOP
    _write_text(os.path.join(pdir, "知识库", "操作SOP.md"),
                f"# {name} · 操作 SOP\n\n"
                "> 每个动作写成一步一步的，新员工照着做就能干。\n\n"
                "## 日常开机\n\n"
                "```text\n第1步 \n第2步 \n第3步 \n```\n\n"
                "## 关机\n\n```text\n第1步 \n```\n\n"
                "## 换料 / 换件\n\n```text\n第1步 \n```\n")

    # 模板：常见故障
    _write_text(os.path.join(pdir, "知识库", "常见故障.md"),
                f"# {name} · 常见故障与解决\n\n"
                "> 一行一个故障。AI 员工靠这张表回答「设备不动了怎么办」。\n\n"
                "| 现象 | 可能原因 | 解决办法 | 紧急程度 |\n|---|---|---|---|\n"
                "| | | | |\n\n"
                "## 处理流程\n\n"
                "1. 先确认安全（急停复位、无人在危险区）\n"
                "2. 看报警号，对照上表\n"
                "3. 处理不了 → 记录现象，联系设备厂家\n")

    return {"ok": True, "dir": pdir, "pack": pack}


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def export_pack(cfg, key: str, out_dir: str) -> Dict[str, Any]:
    """把行业包导出成一个可发客户的文件夹。"""
    src = os.path.join(_pack_dir(cfg), key)
    if not os.path.isdir(src):
        return {"ok": False, "error": f"行业包不存在：{key}"}
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f"行业包_{key}")
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return {"ok": True, "dir": dst}

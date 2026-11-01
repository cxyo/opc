"""人设档案系统：AI 员工的「共同记忆」。

解决什么：
    之前每个岗位各自读知识库种子文件（账号定位卡、风格指纹卡……），
    资料散在 02_知识库种子/ 里，改一次要改好几处，而且网页上没法编辑。
    现在统一收进 data/profile/profile.json：
        - 网页上可编辑、可导入导出
        - 每次调用岗位时自动注入到 system prompt
        - 改了立刻对全部 23 个员工生效

档案结构（6 大块）：
    persona     人设：我是谁、我的身份、我的边界
    positioning 账号定位：给谁看、解决什么问题、差异点
    style       风格指纹：怎么说话、句子节奏、禁忌
    topics      选题库：能写什么、不能写什么
    experiences 经历库：我的真实故事（AI 编不出来，只能靠这里）
    channels    渠道矩阵：各平台账号与发布策略

导入导出：
    export_profile()  导出一份 JSON（含版本号与时间戳，可备份可迁移）
    import_profile()  从 JSON 文件导入，支持「合并」和「覆盖」两种模式
    import_from_seed() 从旧的知识库种子文件自动迁移（一次性）
"""

import json
import os
import shutil
from datetime import datetime

PROFILE_FILE = "profile.json"
PROFILE_VERSION = 2

# 档案字段定义：顺序即网页上的显示顺序
# type: text=多行文本, textarea=大文本, list=列表(每行一条), tags=标签
SECTIONS = [
    {
        "key": "persona",
        "title": "人设",
        "icon": "persona",
        "desc": "我是谁。这决定了所有员工说话的口吻和分寸。",
        "fields": [
            {"key": "name", "label": "称呼", "type": "text",
             "placeholder": "比如：老张 / 一人AI公司主理人"},
            {"key": "identity", "label": "身份定位", "type": "textarea",
             "placeholder": "我是一个工业机器人程序员，现在用 AI 做一人公司"},
            {"key": "expertise", "label": "专业领域", "type": "textarea",
             "placeholder": "工业机器人编程、PLC、AI办公提效、内容生产"},
            {"key": "personality", "label": "性格特质", "type": "textarea",
             "placeholder": "务实、不吹牛、爱较真"},
            {"key": "boundary", "label": "明确不做的事", "type": "textarea",
             "placeholder": "不编造经历、不接灰色单、不夸大产品效果"},
            {"key": "promise", "label": "对读者的承诺", "type": "textarea",
             "placeholder": "每篇至少给一个能立刻用上的东西"},
        ],
    },
    {
        "key": "positioning",
        "title": "账号定位",
        "icon": "target",
        "desc": "给谁看、解决什么问题、凭什么是你。这段决定选题边界。",
        "fields": [
            {"key": "audience", "label": "目标读者", "type": "textarea",
             "placeholder": "想用AI提效但不懂技术的中小企业主/职场人"},
            {"key": "problem", "label": "解决什么问题", "type": "textarea",
             "placeholder": "知道AI有用但用不起来，卡在没流程没提示词"},
            {"key": "differentiator", "label": "差异点", "type": "textarea",
             "placeholder": "真在一线干工业机器人，不是二手知识"},
            {"key": "value_prop", "label": "一句话价值", "type": "text",
             "placeholder": "把AI变成能落地的流程，而不是一堆提示词"},
            {"key": "content_pillars", "label": "内容支柱（每行一条）", "type": "list",
             "placeholder": "AI办公提效\n工业机器人实战\n一人公司方法论"},
            {"key": "monetize", "label": "变现方式（每行一条）", "type": "list",
             "placeholder": "资料包销售\n知识付费\n企业咨询"},
        ],
    },
    {
        "key": "style",
        "title": "风格指纹",
        "icon": "style",
        "desc": "怎么说话。去AI味的第一道工序就在这里。",
        "fields": [
            {"key": "tone", "label": "语气基调", "type": "textarea",
             "placeholder": "像一个干活的朋友在说话，不是老师"},
            {"key": "sentence", "label": "句子节奏", "type": "textarea",
             "placeholder": "短句为主，允许长短不齐，少用分号"},
            {"key": "structure", "label": "结构习惯", "type": "textarea",
             "placeholder": "开头直接说结论，中间分层，结尾给行动项"},
            {"key": "words", "label": "常用词（每行一条）", "type": "list",
             "placeholder": "落地\n可直接抄\n实测"},
            {"key": "forbidden", "label": "禁用表达（每行一条）", "type": "list",
             "placeholder": "赋能\n抓手\n闭环\n在当今这个时代"},
            {"key": "sample", "label": "标杆样例（照这个语气写）", "type": "textarea",
             "placeholder": "贴一段你自己最满意的原文"},
        ],
    },
    {
        "key": "topics",
        "title": "选题库",
        "icon": "topic",
        "desc": "能写什么。AI 官方的选题官会优先从这里取。",
        "fields": [
            {"key": "can_write", "label": "可写方向（每行一条）", "type": "list",
             "placeholder": "AI办公提效实操\n机器人调试避坑"},
            {"key": "not_write", "label": "不写方向（每行一条）", "type": "list",
             "placeholder": "热点政治\n未经核实的行业新闻"},
            {"key": "planned", "label": "计划选题（每行一条）", "type": "list",
             "placeholder": "一人公司第30天：我把流程跑通了"},
            {"key": "used", "label": "已写过的（每行一条，用于去重）", "type": "list"},
            {"key": "keywords", "label": "高频关键词（每行一条）", "type": "list"},
        ],
    },
    {
        "key": "experiences",
        "title": "经历库",
        "icon": "exp",
        "desc": "我的真实故事。这是唯一能让文章不像AI的地方。",
        "fields": [
            {"key": "stories", "label": "真实经历（每行一条）", "type": "list",
             "placeholder": "2026-10-28 用卡诺普CRA9做力控，光纤极性搞反了两次"},
            {"key": "numbers", "label": "关键数据（每行一条）", "type": "list",
             "placeholder": "流水线单篇成本 ¥0.018"},
            {"key": "quotes", "label": "原话金句（每行一条）", "type": "list"},
            {"key": "failures", "label": "踩过的坑（每行一条）", "type": "list"},
        ],
    },
    {
        "key": "channels",
        "title": "渠道矩阵",
        "icon": "channel",
        "desc": "各平台账号与发布策略。",
        "fields": [
            {"key": "accounts", "label": "账号清单（每行一条）", "type": "list",
             "placeholder": "公众号：AI员工日记\n小红书：AI办公提效"},
            {"key": "cadence", "label": "发布节奏", "type": "text",
             "placeholder": "公众号每周2更，小红书每周3更"},
            {"key": "priority", "label": "优先平台（每行一条）", "type": "list",
             "placeholder": "公众号\n视频号"},
            {"key": "notes", "label": "注意事项", "type": "textarea"},
        ],
    },
]

# 旧知识库种子 → 新档案的迁移映射
SEED_MAP = {
    "账号定位卡": ("positioning", "differentiator"),
    "风格指纹卡": ("style", "tone"),
    "选题池": ("topics", "can_write"),
}


def _profile_dir(cfg):
    d = os.path.join(cfg["paths"]["state"], "profile")
    os.makedirs(d, exist_ok=True)
    return d


def _profile_path(cfg):
    return os.path.join(_profile_dir(cfg), PROFILE_FILE)


def default_profile():
    """空白档案（带占位提示，方便新用户看懂每格该填什么）。"""
    p = {"_version": PROFILE_VERSION, "_updated": ""}
    for sec in SECTIONS:
        p[sec["key"]] = {f["key"]: "" for f in sec["fields"]}
    return p


def load_profile(cfg):
    """读档案。文件不存在返回空白档案。"""
    p = _profile_path(cfg)
    if not os.path.exists(p):
        return default_profile()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default_profile()
    base = default_profile()
    # 合并，保证新版本的字段也能读到
    for k, v in (data or {}).items():
        if k.startswith("_"):
            base[k] = v
        elif isinstance(v, dict):
            base.setdefault(k, {})
            base[k].update(v)
        else:
            base[k] = v
    return base


def save_profile(cfg, profile):
    """保存档案。返回 {ok, path, error}。"""
    p = _profile_path(cfg)
    try:
        prof = dict(profile or {})
        prof["_version"] = PROFILE_VERSION
        prof["_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 备份旧版
        if os.path.exists(p):
            shutil.copy2(p, p + ".bak")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": p, "updated": prof["_updated"]}
    except Exception as e:
        return {"ok": False, "error": f"保存失败：{e}"}


def reset_profile(cfg):
    """恢复默认（清空内容）。"""
    return save_profile(cfg, default_profile())


def import_profile(cfg, payload, mode="merge"):
    """导入档案。

    payload 可以是：
        - dict（已解析好的档案）
        - JSON 字符串
    mode: merge=只覆盖有值的字段；replace=整份替换
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as e:
            return {"ok": False, "error": f"不是合法的 JSON：{e}"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "档案格式不对，应为一个对象"}

    if mode == "replace":
        prof = default_profile()
        for k, v in payload.items():
            if not k.startswith("_") and isinstance(v, dict):
                prof[k].update(v)
    else:
        prof = load_profile(cfg)
        for k, v in payload.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and isinstance(prof.get(k), dict):
                for fk, fv in v.items():
                    if fv not in (None, "", [], {}):
                        prof[k][fk] = fv
            elif v not in (None, ""):
                prof[k] = v

    r = save_profile(cfg, prof)
    if r.get("ok"):
        r["message"] = "导入成功" if mode == "merge" else "已覆盖导入"
    return r


def export_profile(cfg):
    """导出档案为 JSON 字符串。"""
    prof = load_profile(cfg)
    prof["_exported_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return json.dumps(prof, ensure_ascii=False, indent=2)


def import_from_seed(cfg):
    """从旧的知识库种子文件自动迁移内容。

    一次性动作：把 02_知识库种子/账号定位卡.md 等的内容填进档案。
    """
    prof = load_profile(cfg)
    filled = []
    seeds = cfg["paths"]["seeds"]
    for seed_name, (sec, field) in SEED_MAP.items():
        p = os.path.join(seeds, seed_name + ".md")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except Exception:
            continue
        if not text:
            continue
        cur = (prof.get(sec) or {}).get(field) or ""
        if cur.strip():
            continue          # 已有内容，不覆盖
        prof.setdefault(sec, {})[field] = text
        filled.append(f"{seed_name} → {sec}.{field}")

    r = save_profile(cfg, prof)
    r["filled"] = filled
    r["message"] = (f"已迁移 {len(filled)} 项" if filled
                    else "没有可迁移的内容，或目标字段已经填过了")
    return r


# ---- 注入到 prompt ----------------------------------------------------

def profile_context(cfg, sections=None):
    """把档案渲染成一段可注入 system prompt 的文字。

    只输出有内容的字段，空字段一律跳过——避免给 AI 一堆空标题。
    """
    prof = load_profile(cfg)
    want = set(sections) if sections else None
    blocks = []

    for sec in SECTIONS:
        if want and sec["key"] not in want:
            continue
        data = prof.get(sec["key"]) or {}
        lines = []
        for f in sec["fields"]:
            val = (data.get(f["key"]) or "")
            if isinstance(val, list):
                val = "\n".join(str(x) for x in val)
            val = str(val).strip()
            if not val:
                continue
            lines.append(f"- {f['label']}：{val}")
        if not lines:
            continue
        blocks.append(f"【{sec['title']}】\n" + "\n".join(lines))

    if not blocks:
        return ""
    return ("以下是账号主人填写的人设档案（最高优先级，写作时必须严格遵守，"
            "不要与之冲突）：\n\n" + "\n\n".join(blocks))


def profile_completeness(cfg):
    """档案填写完整度，用于网页上显示进度条。"""
    prof = load_profile(cfg)
    total = 0
    filled = 0
    per_section = {}
    for sec in SECTIONS:
        data = prof.get(sec["key"]) or {}
        st = sf = 0
        for f in sec["fields"]:
            st += 1
            val = data.get(f["key"])
            if isinstance(val, list):
                val = "\n".join(str(x) for x in val)
            if str(val or "").strip():
                sf += 1
        total += st
        filled += sf
        per_section[sec["key"]] = {
            "title": sec["title"], "filled": sf, "total": st,
            "percent": round(sf * 100 / st) if st else 0,
        }
    return {
        "filled": filled, "total": total,
        "percent": round(filled * 100 / total) if total else 0,
        "sections": per_section,
    }


def schema():
    """给前端用的字段定义。"""
    return {"sections": SECTIONS, "version": PROFILE_VERSION}

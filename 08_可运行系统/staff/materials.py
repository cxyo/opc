"""素材库 —— 用户喂进来的所有原始素材，加上「学到了什么、用在哪」。

这块为什么存在
--------------
用户 2026-10-31 提出的原话：

    "我发了这么多视频给你，网页里没有看到……我发的那些文字、图片、视频、链接，
     最好也放到里面，做你的原始数据的素材来源。"
    "后期想做一期这样的视频，也有素材，可以根据这个自动改写成自己的素材。"

也就是说，之前的系统只存**我们自己生成的**东西（wiki、提示词、脚本），
用户喂进来的原始素材散落在 `tools/sph_meta.json`、`tools/sph_covers/` 这些
技术路径里，既看不见、也检索不到，更谈不上"学会了没有"。

本模块把这三件事一次做掉：

  ① **原始素材入库** —— 视频 / 文章 / 图片 / 链接 / 文字，统一登记进 `台账.json`
     原始件（封面图、原文）落在 `原始素材/` 下，与"我们生成的内容"物理隔离。

  ② **学习状态可追踪** —— 每条素材有明确的四种状态：
        待学 → 学习中 → 已学 → 已用
     并记录"值得借鉴什么""用在哪（指向具体文件）""起到什么效果"。
     这让"你吸收了多少"变成一个能查的数字，而不是一句感觉。

  ③ **可反查** —— 从任意一条学到的结论，都能回溯到它来自哪条素材、
     哪个账号、哪天的视频。结论没有出处就不算数。

设计约束
--------
· **零第三方依赖**：只用标准库，跟 kb.py / knowledge.py 保持一致。
· **只登记不臆造**：写进台账的字段必须来自原始素材本身（标题、作者、数据）。
  "值得借鉴""用在哪"是分析结论，会标 `来源` 字段指向分析文档。
· **台账是单一事实源**：控制台页面、统计、改写引擎都从这里读。
"""

import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 路径

# 素材库根目录：<项目根>/03_知识库/素材库
# 注意：项目根不是包目录，所以这里用相对路径 + 上溯定位。
REL_LIB = os.path.join("03_知识库", "素材库")
LEDGER_NAME = "台账.json"

# 素材类型（用户发过来的东西就这几种）
TYPES = {
    "video": "视频",
    "article": "文章",
    "image": "图片",
    "link": "链接",
    "text": "文字",
    "file": "文件",
}

# 学习状态（素材 → 结论 的进度）
STATUSES = {
    "pending": "待学",
    "learning": "学习中",
    "learned": "已学",
    "applied": "已用",
}

# 状态推进顺序（用于算"学到什么程度了"）
STATUS_ORDER = ["pending", "learning", "learned", "applied"]


# ---------------------------------------------------------------- 路径工具


def _project_root() -> str:
    """项目根目录（含 03_知识库 的那一层）。

    **必须用 config.PROJECT**，不能自己上溯 __file__：
    打包成 exe 后 __file__ 指向 PyInstaller 的临时解压目录
    （C:\\Users\\xxx\\AppData\\Local\\Temp\\_MEIxxxxx\\），
    上溯出来的路径是错的 —— 实测会指向 exe_build/ 而不是 exe_build/AI员工系统/，
    表现为「素材总数 0 条」。

    config._resolve_root() 已经处理了 exe 的目录布局判定（见该函数注释），
    所以这里直接用它。
    """
    # ① 首选：config.PROJECT（已解决 exe 布局）
    try:
        from . import config as cfgmod
        proj = getattr(cfgmod, "PROJECT", "")
        if proj and os.path.isdir(os.path.join(proj, "03_知识库")):
            return proj
    except Exception:
        pass

    # ② 退路：从 config 的 paths 反推（state → 08_可运行系统 → 项目根）
    try:
        from . import config as cfgmod
        cfg = cfgmod.load()
        p = cfg["paths"]["state"]
        for _ in range(4):
            p = os.path.dirname(p)
        if os.path.isdir(os.path.join(p, "03_知识库")):
            return p
    except Exception:
        pass

    # ③ 兜底：从包位置上溯
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.dirname(here)
    if os.path.isdir(os.path.join(cand, "03_知识库")):
        return cand
    return here


def lib_dir() -> str:
    """素材库根目录。不存在就建。"""
    d = os.path.join(_project_root(), REL_LIB)
    os.makedirs(d, exist_ok=True)
    return d


def ledger_path() -> str:
    return os.path.join(lib_dir(), LEDGER_NAME)


def raw_dir(kind: str = "") -> str:
    """原始素材目录。kind 为空则返回 原始素材/ 根。"""
    base = os.path.join(lib_dir(), "原始素材")
    sub = {
        "video": "视频封面",
        "article": "文章原文",
        "image": "图片",
        "link": "链接",
        "text": "文字片段",
        "file": "",
    }.get(kind, kind)
    d = os.path.join(base, sub) if sub else base
    os.makedirs(d, exist_ok=True)
    return d


def learn_dir() -> str:
    """学习成果目录。"""
    d = os.path.join(lib_dir(), "学习成果")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 台账读写


def _empty_ledger() -> Dict[str, Any]:
    return {
        "版本": 1,
        "创建时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "更新时间": "",
        "说明": ("素材台账：用户提供的原始素材 + 每条素材的学习结论。"
                 "原始件在 原始素材/，结论在 学习成果/，本文件是唯一索引。"),
        "素材": [],
    }


def load() -> Dict[str, Any]:
    """读台账。文件不存在或损坏时返回空台账（不抛异常）。"""
    p = ledger_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_ledger()
        data.setdefault("素材", [])
        return data
    except Exception:
        return _empty_ledger()


def save(data: Dict[str, Any]) -> bool:
    """写台账。自动更新时间戳。"""
    data["更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p = ledger_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, p)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 查询


def all_items() -> List[Dict[str, Any]]:
    return load().get("素材", []) or []


def get(item_id: str) -> Optional[Dict[str, Any]]:
    for it in all_items():
        if it.get("id") == item_id:
            return it
    return None


def by_type(kind: str) -> List[Dict[str, Any]]:
    return [it for it in all_items() if it.get("类型") == kind]


def by_status(status: str) -> List[Dict[str, Any]]:
    return [it for it in all_items() if it.get("学习状态") == status]


def search(keyword: str, kind: str = "", status: str = "") -> List[Dict[str, Any]]:
    """按关键词搜索标题/作者/借鉴点/来源。"""
    kw = (keyword or "").strip().lower()
    out = []
    for it in all_items():
        if kind and it.get("类型") != kind:
            continue
        if status and it.get("学习状态") != status:
            continue
        if not kw:
            out.append(it)
            continue
        hay = " ".join([
            str(it.get("标题") or ""),
            str(it.get("作者") or ""),
            str(it.get("主要内容") or ""),
            str(it.get("值得借鉴") or ""),
            str(it.get("用在哪") or ""),
            str(it.get("链接") or ""),
        ]).lower()
        if kw in hay:
            out.append(it)
    return out


# ---------------------------------------------------------------- 统计


def stats() -> Dict[str, Any]:
    """总览统计 —— 控制台顶部仪表盘直接用这个。"""
    items = all_items()
    by_t: Dict[str, int] = {}
    by_s: Dict[str, int] = {}
    for it in items:
        t = it.get("类型") or "link"
        s = it.get("学习状态") or "pending"
        by_t[t] = by_t.get(t, 0) + 1
        by_s[s] = by_s.get(s, 0) + 1

    total = len(items)
    learned = by_s.get("learned", 0) + by_s.get("applied", 0)
    applied = by_s.get("applied", 0)
    learning = by_s.get("learning", 0)
    pending = by_s.get("pending", 0)

    # 成果数：有「用在哪」的素材条数 + 学习成果文件数
    outcome_files = 0
    try:
        outcome_files = len([f for f in os.listdir(learn_dir())
                             if f.lower().endswith(".md")])
    except Exception:
        pass

    # 吸收程度：已学+已用 占比
    pct = round(learned * 100.0 / total, 1) if total else 0.0

    return {
        "素材总数": total,
        "按类型": {TYPES.get(k, k): v for k, v in sorted(by_t.items())},
        "按类型_原始": by_t,
        "已学": learned,
        "学习中": learning,
        "待学": pending,
        "已用": applied,
        "吸收率": pct,
        "成果数": applied + outcome_files,
        "成果文件数": outcome_files,
        "来源渠道": _channels(items),
    }


def _channels(items: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        c = it.get("来源渠道") or "未标注"
        out[c] = out.get(c, 0) + 1
    return out


# ---------------------------------------------------------------- 写入


def upsert(item: Dict[str, Any]) -> Dict[str, Any]:
    """新增或更新一条素材。按 id 匹配。

    **2026-10-31 修（严重数据丢失）**
    ------------------------------------
    原来更新已有条目时是 `items[i] = item` —— 整体替换。
    而 `import_sph_meta` 每次重新导入都会构造一个**只有原始元信息**
    的 item（值得借鉴/主要内容/用在哪/效果 全是空串）。
    于是「再跑一次抓取导入」就会把之前辛苦填的 31 条学习结论全部清空。
    表现是：台账里学习状态回到 pending，验证脚本报「含值得借鉴」失败。

    现在改成**字段级合并**：结论类字段只在「旧值为空 且 新值非空」时写入，
    已有的结论绝不覆盖。原始元信息（标题/数据/封面）仍然正常更新。
    """
    data = load()
    items = data.setdefault("素材", [])
    iid = item.get("id")
    if not iid:
        iid = _make_id(item)
        item["id"] = iid
    # 结论类字段：合并时保留已有结论
    PRESERVE = ("值得借鉴", "主要内容", "用在哪", "效果", "待用",
                "学习状态", "使用记录", "备注")
    for i, it in enumerate(items):
        if it.get("id") == iid:
            merged = dict(it)          # 以旧条目为底
            merged.update(item)        # 新元信息覆盖
            # 结论字段：旧值优先（除非旧值为空而新值有内容）
            for f in PRESERVE:
                old_v = it.get(f)
                new_v = item.get(f)
                if old_v not in (None, "", []):
                    merged[f] = old_v
                elif new_v not in (None, "", []):
                    merged[f] = new_v
            merged.setdefault("首次登记", it.get("首次登记"))
            items[i] = merged
            save(data)
            return merged
    item.setdefault("首次登记", datetime.now().strftime("%Y-%m-%d"))
    item.setdefault("学习状态", "pending")
    items.append(item)
    save(data)
    return item


def _make_id(item: Dict[str, Any]) -> str:
    """生成稳定 ID：类型前缀 + 标题前 12 字 + 内容哈希。

    2026-10-31 修：原来 ID 用了**整个标题**，导致
        a_01_01新媒体时代的内容传播法则_pdf
    这种超长 ID。ID 会进文件路径，跟标题拼在一起就超了 Windows 的
    260 字符上限，学习成果文档**静默创建失败**。
    现在 ID 固定短长度，标题信息放在文件名后半段。
    同一份素材重复导入仍得到同一个 ID（靠哈希保证）。
    """
    key = str(item.get("链接") or "") + "|" + str(item.get("标题") or "")
    import hashlib
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:6]
    t = (item.get("类型") or "link")[:1]
    title = str(item.get("标题") or "").strip()
    stem = re.sub(r"[^\w一-鿿]+", "", title)[:10]
    return "%s_%s_%s" % (t, stem, h) if stem else "%s_%s" % (t, h)


def mark_status(item_id: str, status: str, note: str = "") -> bool:
    """更新学习状态。"""
    if status not in STATUSES:
        return False
    data = load()
    for it in data.get("素材", []):
        if it.get("id") == item_id:
            it["学习状态"] = status
            if note:
                it["状态备注"] = note
            it["状态更新时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save(data)
            return True
    return False


def record_use(item_id: str, where: str, effect: str = "",
               outcome: str = "") -> bool:
    """记录"用在哪 + 什么效果"。

    追加而非覆盖 —— 一条素材可能用在多个地方。
    """
    data = load()
    for it in data.get("素材", []):
        if it.get("id") == item_id:
            uses = it.setdefault("使用记录", [])
            uses.append({
                "用在哪": where,
                "效果": effect,
                "产出物": outcome,
                "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            # 有使用记录就自动升级为"已用"
            if it.get("学习状态") in ("pending", "learning", "learned"):
                it["学习状态"] = "applied"
            save(data)
            return True
    return False


# ---------------------------------------------------------------- 导入


def import_sph_meta(meta_path: str = "") -> Dict[str, Any]:
    """把视频号抓取结果导入台账。

    导入的是**原始事实**：标题、作者、日期、互动数据、链接、封面。
    "值得借鉴""用在哪"这些结论单独由分析文档提供，不在这里编。
    """
    if not meta_path:
        meta_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools", "sph_meta.json")
    if not os.path.isfile(meta_path):
        return {"ok": False, "error": "找不到 %s" % meta_path, "新增": 0}

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception as e:
        return {"ok": False, "error": "读取失败：%s" % e, "新增": 0}

    added = 0
    skipped = 0
    for r in rows:
        if not isinstance(r, dict) or not r.get("ok"):
            skipped += 1
            continue
        sid = r.get("id")
        cover_local = ""
        if sid:
            src = os.path.join(os.path.dirname(meta_path), "sph_covers",
                               "%s.jpg" % sid)
            if os.path.isfile(src):
                dst = os.path.join(raw_dir("video"), "%s.jpg" % sid)
                if not os.path.isfile(dst):
                    try:
                        shutil.copy2(src, dst)
                    except Exception:
                        pass
                cover_local = os.path.relpath(dst, lib_dir())

        item = {
            "id": "v_%s" % sid,
            "类型": "video",
            "标题": (r.get("title") or "").strip(),
            "作者": r.get("author") or "",
            "日期": r.get("date") or "",
            "链接": r.get("url") or ("https://weixin.qq.com/sph/%s" % sid),
            "平台": "视频号",
            "来源渠道": "用户提供的视频号链接",
            "封面": cover_local,
            "数据": {
                "点赞": r.get("like") or 0,
                "评论": r.get("comment") or 0,
                "收藏": r.get("fav") or 0,
                "转发": r.get("forward") or 0,
            },
            "学习状态": "pending",
            "值得借鉴": "",
            "主要内容": "",
            "用在哪": "",
            "效果": "",
            "使用记录": [],
            "备注": "视频本体未获取（微信预览页只下发封面和文案），"
                    "分析仅基于元信息与封面实拍",
        }
        before = len(all_items())
        upsert(item)
        if len(all_items()) > before:
            added += 1
        else:
            skipped += 1

    return {"ok": True, "新增": added, "跳过": skipped, "总数": len(all_items())}


def import_dir(kind: str, src_dir: str, channel: str = "") -> Dict[str, Any]:
    """把一个目录里的文件批量登记为素材（文章/图片/文件通用）。"""
    added = 0
    if not os.path.isdir(src_dir):
        return {"ok": False, "error": "目录不存在：%s" % src_dir}
    for name in sorted(os.listdir(src_dir)):
        full = os.path.join(src_dir, name)
        if not os.path.isfile(full):
            continue
        stem, ext = os.path.splitext(name)
        item = {
            "id": "%s_%s" % (kind[:1], re.sub(r"[^\w一-鿿]+", "_", stem)),
            "类型": kind,
            "标题": stem,
            "作者": "",
            "日期": "",
            "链接": "",
            "平台": "本地文件",
            "来源渠道": channel or "用户提供的本地资料",
            "原始文件": os.path.relpath(full, lib_dir()),
            "数据": {},
            "学习状态": "pending",
            "值得借鉴": "",
            "主要内容": "",
            "用在哪": "",
            "效果": "",
            "使用记录": [],
        }
        before = len(all_items())
        upsert(item)
        if len(all_items()) > before:
            added += 1
    return {"ok": True, "新增": added, "总数": len(all_items())}


# ---------------------------------------------------------------- 学习成果


def write_outcome(item: Dict[str, Any]) -> str:
    """把一条素材的学习结论写成独立 Markdown（人可读，便于交接）。

    放在 学习成果/ 下，一个素材一个文件。

    文件名策略（2026-10-31 修）：
        原来用「素材ID + 完整标题」，视频号标题动辄上百字，
        拼出来超过 Windows 路径上限（260 字符），
        导致部分文件**静默创建失败** —— 台账里有结论，磁盘上却没有文档。
        现在改成：ID + 标题前 28 字 + 哈希后 4 位，保证唯一且不超长。
    """
    iid = item.get("id") or "x"
    title = (item.get("标题") or "").strip()
    # 标题可能自带后缀（如「..._pdf」），先去后缀再截断，避免出现
    # "a_01_XXX_pdf_01_XXX_pdf_哈希" 这种重复拼接
    stem = re.sub(r"[_\s]+", "-", title)
    stem = re.sub(r"[^\w一-鿿-]+", "", stem).strip("-")
    stem = stem[:26].strip("-")
    if not stem:
        stem = "未命名"
    import hashlib
    h = hashlib.md5(("%s|%s" % (iid, title)).encode("utf-8")).hexdigest()[:4]
    name = "%s_%s_%s.md" % (iid, stem, h)
    path = os.path.join(learn_dir(), name)

    uses = item.get("使用记录") or []
    lines = [
        "# %s" % (item.get("标题") or iid),
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 素材ID | `%s` |" % iid,
        "| 类型 | %s |" % TYPES.get(item.get("类型"), item.get("类型")),
        "| 作者 | %s |" % (item.get("作者") or "—"),
        "| 日期 | %s |" % (item.get("日期") or "—"),
        "| 链接 | %s |" % (item.get("链接") or "—"),
        "| 来源渠道 | %s |" % (item.get("来源渠道") or "—"),
        "| 学习状态 | %s |" % STATUSES.get(item.get("学习状态"), "—"),
        "",
        "## 主要内容",
        "",
        (item.get("主要内容") or "（尚未填写）"),
        "",
        "## 值得借鉴的点",
        "",
        (item.get("值得借鉴") or "（尚未填写）"),
        "",
        "## 用在哪 · 什么效果",
        "",
    ]
    if uses:
        for u in uses:
            lines.append("- **%s**" % (u.get("用在哪") or "—"))
            if u.get("效果"):
                lines.append("  - 效果：%s" % u["效果"])
            if u.get("产出物"):
                lines.append("  - 产出物：`%s`" % u["产出物"])
            if u.get("时间"):
                lines.append("  - 时间：%s" % u["时间"])
    else:
        lines.append("- （尚未记录）")
    lines += [
        "",
        "## 尚未用上的部分",
        "",
        (item.get("待用") or "（尚未填写 —— 这条素材还有哪些可挖的、为什么还没用）"),
        "",
        "---",
        "",
        "标签：#素材学习 #%s #%s" % (TYPES.get(item.get("类型"), "素材"),
                                  item.get("作者") or "无作者"),
        "",
    ]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        # 写失败要能被发现 —— 静默失败会让"台账有结论、磁盘没文档"
        # 这种不一致很难查（2026-10-31 就踩过）
        print("[materials] 学习成果写入失败：%s（%s）" % (name, e))
        return ""
    return path


def sync_outcomes() -> int:
    """把台账里所有有结论的素材同步成 Markdown 文件。

    返回成功写入的数量。失败会在控制台打印原因。
    """
    n = 0
    failed = 0
    for it in all_items():
        if it.get("值得借鉴") or it.get("主要内容") or it.get("使用记录"):
            if write_outcome(it):
                n += 1
            else:
                failed += 1
    if failed:
        print("[materials] %d 份学习成果写入失败（详见上方提示）" % failed)
    return n

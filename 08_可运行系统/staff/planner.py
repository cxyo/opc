"""AI规划师（A23）：管住「文件怎么放、怎么找、怎么改、怎么清」。

解决的现实问题：
    - 文件越跑越多，改名找不到
    - 每个项目各自一套命名，看不懂
    - 旧文件堆着占地方又不敢删
    - 不知道哪些是「当前有效版本」

规划师干四件事，全都可以在网页上一键执行：

    1. 规划（plan）    —— 按规范给出一套目录方案
    2. 巡检（inspect） —— 扫全盘，找出命名不规范/重复/过期的文件
    3. 整理（organize）—— 按规范重命名、归档、清理（全部先出预览再执行）
    4. 索引（index）   —— 生成一份可搜索的总索引，人和 AI 都能查

硬规则（写死在代码里，不靠 AI 自觉）：
    R1 目录名禁止出现日期数字（20260101、2026-01-01 这类）
    R2 文件名禁止出现日期数字
    R3 目录用「编号_中文名」，编号决定顺序，中文名决定含义
    R4 空目录必须清理
    R5 备份文件（.bak）只保留最近 3 份
    R6 超过保留期的中间产物进归档区，不直接删
    R7 任何删除动作先给预览，用户确认后才执行
"""

import json
import os
import re
import shutil
from datetime import datetime, timedelta

# ---- 硬规则 ------------------------------------------------------------

DATE_PATTERNS = [
    re.compile(r"\d{4}[-_.]?\d{2}[-_.]?\d{2}"),      # 20260101 / 2026-01-01
    re.compile(r"\d{2}[-_.]\d{2}[-_.]\d{2}"),        # 260101
    re.compile(r"^\d{1,2}月\d{1,2}[日号]"),          # 1月1日
]

# 允许出现的数字型目录（这些是系统目录，不算违规）
ALLOWED_NUMERIC = {
    "08_可运行系统", "05_产品交付", "03_知识库", "07_AI员工",
    "01_Agent提示词", "02_知识库种子", "04_产品设计", "06_组织架构",
    "09_AI超市模式", "10_AI智能体模式", "11_变现模式", "12_矩阵与电商运营",
}

# 标准目录结构（规划师建议的目标形态）
CANONICAL = {
    "00_总览": ["00_只看这个文件.md", "README.md", "01_项目索引.md"],
    "01_知识库": [],
    "02_选题与素材": [],
    "03_AI员工": [],
    "04_产出": ["文章", "视觉素材", "发布包", "数据"],
    "05_运营": [],
    "06_配置与工具": [],
    "07_归档": [],
}


def has_date(text):
    """文本里是否含日期型数字。"""
    if not text:
        return False
    for p in DATE_PATTERNS:
        if p.search(text):
            return True
    return False


def is_numbered(name):
    """是否形如「08_可运行系统」。"""
    return bool(re.match(r"^\d{2}_", name))


def check_name(name):
    """检查一个名字是否合规。返回问题列表（空=合规）。"""
    problems = []
    if has_date(name):
        problems.append("含日期数字")
    if name.strip() != name:
        problems.append("首尾有空格")
    if re.search(r"[<>:\"|?*\\]", name):
        problems.append("含非法字符")
    if len(name) > 60:
        problems.append("名称过长")
    # 「编号_中文名」是标准形态，不算问题。
    # 编号可以是任意位数：1_情报采集.md、01_项目索引.md、10_定价.md 都算合规。
    if re.match(r"^\d+_", name):
        return problems
    if re.match(r"^\d+[._]", name):
        problems.append("以数字开头且无中文名")
    return problems


def safe_name(name, fallback="未命名"):
    """把任意字符串转成合规名称。

    去掉日期部分，但保留其余含义。
    如果整个名字都是日期，就退回「未命名」并加序号由调用方处理。
    """
    name = (name or "").strip()
    name = re.sub(r"[<>:\"|?*\\]", "", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[（(].*?[)）]", "", name)
    # 去掉日期
    for p in DATE_PATTERNS:
        name = p.sub("", name)
    name = name.replace("__", "_").strip("_")
    if not name:
        name = fallback
    return name[:60]


# ---- 巡检 --------------------------------------------------------------

def inspect(cfg, root=None, max_depth=4):
    """扫描目录，返回问题清单。

    这是「只读」操作 —— 只报告，不动任何文件。
    """
    base = root or cfg["paths"]["outputs"]
    issues = []
    stats = {"dirs": 0, "files": 0, "bytes": 0, "empties": 0,
             "date_names": 0, "bad_names": 0, "backups": 0}

    if not os.path.isdir(base):
        return {"ok": False, "error": "目录不存在", "issues": [], "stats": stats}

    for dirpath, dirnames, filenames in os.walk(base):
        depth = dirpath[len(base):].count(os.sep)
        if depth > max_depth:
            dirnames[:] = []
            continue
        stats["dirs"] += 1

        for dn in list(dirnames):
            full = os.path.join(dirpath, dn)
            if has_date(dn):
                stats["date_names"] += 1
                issues.append({
                    "type": "目录名含日期", "path": full,
                    "rel": os.path.relpath(full, base).replace("\\", "/"),
                    "suggest": safe_name(dn),
                    "level": "高",
                })
            elif check_name(dn) and dn not in ALLOWED_NUMERIC:
                stats["bad_names"] += 1
                issues.append({
                    "type": "目录名不规范", "path": full,
                    "rel": os.path.relpath(full, base).replace("\\", "/"),
                    "suggest": safe_name(dn),
                    "level": "中",
                })

            # 空目录
            try:
                if not os.listdir(full):
                    stats["empties"] += 1
                    issues.append({
                        "type": "空目录", "path": full,
                        "rel": os.path.relpath(full, base).replace("\\", "/"),
                        "suggest": "（建议删除）",
                        "level": "低",
                    })
            except OSError:
                pass

        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                stats["bytes"] += os.path.getsize(full)
            except OSError:
                pass
            stats["files"] += 1

            if fn.endswith(".bak") or ".bak_" in fn:
                stats["backups"] += 1
                issues.append({
                    "type": "备份文件", "path": full,
                    "rel": os.path.relpath(full, base).replace("\\", "/"),
                    "suggest": "（建议清理旧备份）",
                    "level": "低",
                })
            elif has_date(fn):
                stats["date_names"] += 1
                issues.append({
                    "type": "文件名含日期", "path": full,
                    "rel": os.path.relpath(full, base).replace("\\", "/"),
                    "suggest": safe_name(os.path.splitext(fn)[0]) +
                              os.path.splitext(fn)[1],
                    "level": "中",
                })
            elif check_name(fn):
                stats["bad_names"] += 1
                issues.append({
                    "type": "文件名不规范", "path": full,
                    "rel": os.path.relpath(full, base).replace("\\", "/"),
                    "suggest": safe_name(os.path.splitext(fn)[0]) +
                              os.path.splitext(fn)[1],
                    "level": "低",
                })

    return {"ok": True, "root": base, "issues": issues, "stats": stats,
            "total": len(issues)}


# ---- 整理（预览） ------------------------------------------------------

def plan_organize(cfg, root=None, keep_backups=3, archive_days=30):
    """给出整理方案（预览）。不执行任何改动。

    规则：
        - 空目录 → 删除
        - 旧备份 → 删除（每组只留最近 keep_backups 份）
        - 过期中间产物 → 移到归档区
        - 日期命名 → 重命名成规范名
    """
    base = root or cfg["paths"]["outputs"]
    actions = []
    if not os.path.isdir(base):
        return {"ok": False, "error": "目录不存在", "actions": []}

    archive_dir = os.path.join(base, "归档区")

    # 空目录 + 备份
    backup_groups = {}
    for dirpath, dirnames, filenames in os.walk(base):
        for dn in list(dirnames):
            full = os.path.join(dirpath, dn)
            try:
                if not os.listdir(full):
                    actions.append({"action": "删除空目录", "from": full,
                                    "to": "", "level": "低"})
                    dirnames.remove(dn)
            except OSError:
                pass
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if fn.endswith(".bak") or ".bak_" in fn:
                key = re.sub(r"(\.bak_.*|\.bak)$", "", fn)
                backup_groups.setdefault(os.path.join(dirpath, key), []).append(full)

    for _key, files in backup_groups.items():
        if len(files) <= keep_backups:
            continue
        try:
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except OSError:
            pass
        for f in files[keep_backups:]:
            actions.append({"action": "删除旧备份", "from": f, "to": "",
                            "level": "低"})

    # 过期中间产物 → 归档
    cutoff = datetime.now() - timedelta(days=archive_days)
    for dirpath, dirnames, filenames in os.walk(base):
        if os.path.basename(dirpath) == "归档区":
            continue
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                if datetime.fromtimestamp(os.path.getmtime(full)) < cutoff:
                    rel = os.path.relpath(full, base)
                    actions.append({
                        "action": "移入归档区", "from": full,
                        "to": os.path.join(archive_dir, rel),
                        "level": "中",
                    })
            except OSError:
                pass

    # 日期命名 → 重命名
    for dirpath, dirnames, filenames in os.walk(base):
        for dn in list(dirnames):
            full = os.path.join(dirpath, dn)
            if has_date(dn):
                new = safe_name(dn)
                target = os.path.join(dirpath, new)
                if not os.path.exists(target):
                    actions.append({"action": "重命名目录", "from": full,
                                    "to": target, "level": "高"})
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if has_date(fn):
                stem, ext = os.path.splitext(fn)
                new = safe_name(stem) + ext
                target = os.path.join(dirpath, new)
                if not os.path.exists(target):
                    actions.append({"action": "重命名文件", "from": full,
                                    "to": target, "level": "中"})

    return {"ok": True, "root": base, "actions": actions,
            "total": len(actions),
            "keep_backups": keep_backups, "archive_days": archive_days}


def apply_plan(cfg, actions, dry_run=False):
    """执行整理方案。

    dry_run=True 时只演练，返回会发生什么。
    删除走「移到回收区」，不做真删。
    """
    done = []
    failed = []
    recycle = os.path.join(cfg["paths"]["state"], "回收区")
    os.makedirs(recycle, exist_ok=True)

    for a in actions:
        src = a.get("from")
        dst = a.get("to")
        act = a.get("action")
        if dry_run:
            done.append({**a, "result": "预演"})
            continue
        try:
            if act in ("删除空目录", "删除旧备份"):
                if os.path.exists(src):
                    trash = os.path.join(
                        recycle, f"{os.path.basename(src)}_{datetime.now().strftime('%H%M%S')}")
                    shutil.move(src, trash)
                    done.append({**a, "result": "已移到回收区"})
                else:
                    done.append({**a, "result": "已不存在，跳过"})
            elif act == "移入归档区":
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    done.append({**a, "result": "已归档"})
                else:
                    done.append({**a, "result": "已不存在，跳过"})
            elif act in ("重命名目录", "重命名文件"):
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    done.append({**a, "result": "已重命名"})
                else:
                    done.append({**a, "result": "已不存在，跳过"})
            else:
                failed.append({**a, "result": "未知动作"})
        except Exception as e:
            failed.append({**a, "result": f"失败：{e}"})

    return {"ok": not failed, "done": done, "failed": failed,
            "total": len(done) + len(failed)}


# ---- 索引 --------------------------------------------------------------

def build_index(cfg, root=None):
    """生成一份总索引，人和 AI 都能查。

    输出到 data/outputs/01_项目索引.md
    """
    base = root or cfg["paths"]["outputs"]
    entries = []
    for dirpath, dirnames, filenames in os.walk(base):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if fn.endswith((".bak", ".pyc")) or ".bak_" in fn:
                continue
            try:
                st = os.stat(full)
                size = st.st_size
                mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                size, mtime = 0, ""
            rel = os.path.relpath(full, base).replace("\\", "/")
            # 取标题：md 文件读第一行标题
            title = ""
            if fn.lower().endswith(".md"):
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        for _ in range(6):
                            ln = f.readline()
                            if not ln:
                                break
                            if ln.startswith("#"):
                                title = ln.lstrip("#").strip()
                                break
                except Exception:
                    pass
            entries.append({"rel": rel, "name": fn, "title": title,
                            "size": size, "mtime": mtime,
                            "folder": os.path.dirname(rel) or "(根)"})

    entries.sort(key=lambda e: e["mtime"], reverse=True)

    by_folder = {}
    for e in entries:
        by_folder.setdefault(e["folder"], []).append(e)

    lines = [
        "# 项目索引",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 文件总数：{len(entries)}",
        f"- 目录数：{len(by_folder)}",
        "",
        "> 这份索引由 AI 规划师自动生成。文件多了先看这里。",
        "",
    ]
    for folder in sorted(by_folder):
        lines.append(f"## {folder}")
        lines.append("")
        for e in by_folder[folder]:
            label = e["title"] or e["name"]
            lines.append(f"- [{label}]({e['rel']}) · {e['size']:,}B · {e['mtime']}")
        lines.append("")

    out = os.path.join(base, "01_项目索引.md")
    os.makedirs(base, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 同时出机器可读的 JSON，供控制台搜索用
    jout = os.path.join(base, "01_项目索引.json")
    with open(jout, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "total": len(entries), "entries": entries},
                  f, ensure_ascii=False, indent=2)

    return {"ok": True, "md": out, "json": jout, "total": len(entries),
            "folders": len(by_folder)}


def search_index(cfg, query, root=None, limit=40):
    """按关键词搜索产出。"""
    base = root or cfg["paths"]["outputs"]
    q = (query or "").strip().lower()
    if not q:
        return {"ok": True, "results": []}
    hits = []
    for dirpath, dirnames, filenames in os.walk(base):
        for fn in filenames:
            if fn.lower().endswith((".bak", ".pyc")):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, base).replace("\\", "/")
            score = 0
            snippet = ""
            if q in fn.lower():
                score += 5
            if q in rel.lower():
                score += 3
            if fn.lower().endswith((".md", ".txt")):
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        text = f.read()
                    if q in text.lower():
                        score += 2
                        idx = text.lower().find(q)
                        snippet = text[max(0, idx - 40):idx + 80].replace("\n", " ")
                except Exception:
                    pass
            if score:
                hits.append({"rel": rel, "name": fn, "score": score,
                             "snippet": snippet})
    hits.sort(key=lambda h: -h["score"])
    return {"ok": True, "results": hits[:limit], "total": len(hits)}


# ---- 备份 / 导出 / 导入 / 初始化 ---------------------------------------

def backup_all(cfg, tag=""):
    """把整个工程打包备份成 zip。"""
    import zipfile
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    tag = safe_name(tag) if tag else ""
    name = f"一人AI公司备份_{stamp}{('_' + tag) if tag else ''}.zip"
    out_dir = os.path.join(cfg["paths"]["state"], "备份")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name)

    # 备份范围：产出、档案、日志、状态、配置（不含 vendor 和密钥）
    roots = []
    for key in ("outputs", "logs", "state"):
        p = cfg["paths"].get(key)
        if p and os.path.isdir(p):
            roots.append(p)
    base = os.path.dirname(cfg["paths"]["outputs"])

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for r in roots:
            for dirpath, dirnames, filenames in os.walk(r):
                dirnames[:] = [d for d in dirnames
                               if d not in ("vendor", "__pycache__", "备份")]
                for fn in filenames:
                    if fn.endswith((".pyc", ".zip")):
                        continue
                    full = os.path.join(dirpath, fn)
                    arc = os.path.relpath(full, base).replace("\\", "/")
                    try:
                        z.write(full, arc)
                    except Exception:
                        pass
        # 配置文件脱敏后一起备份
        cfg_path = os.path.join(base, "config", "config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("api_key"):
                    data["api_key"] = "（备份中已脱敏，请从本机配置文件恢复）"
                z.writestr("config/config_脱敏.json",
                           json.dumps(data, ensure_ascii=False, indent=2))
            except Exception:
                pass

    return {"ok": True, "path": out, "name": name,
            "size": os.path.getsize(out)}


def export_data(cfg, kind="all"):
    """导出为可迁移的 JSON 包。"""
    from . import profile as profmod
    data = {}
    if kind in ("all", "profile"):
        data["profile"] = profmod.load_profile(cfg)
    if kind in ("all", "outputs"):
        outs = []
        base = cfg["paths"]["outputs"]
        for dirpath, dirnames, filenames in os.walk(base):
            for fn in filenames:
                if fn.lower().endswith((".bak", ".pyc")):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, base).replace("\\", "/")
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    continue
                outs.append({"rel": rel, "content": content})
        data["outputs"] = outs
    if kind in ("all", "logs"):
        import csv
        p = os.path.join(cfg["paths"]["logs"], "telemetry.jsonl")
        rows = []
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            continue
        data["telemetry"] = rows
    data["_exported_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["_version"] = 2
    return json.dumps(data, ensure_ascii=False, indent=2)


def import_data(cfg, payload, mode="merge"):
    """导入导出的 JSON 包。"""
    from . import profile as profmod
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as e:
            return {"ok": False, "error": f"不是合法 JSON：{e}"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "格式不对，应为一个对象"}

    report = {"profile": False, "outputs": 0, "logs": 0}

    if payload.get("profile"):
        r = profmod.import_profile(cfg, payload["profile"], mode=mode)
        report["profile"] = r.get("ok", False)

    if payload.get("outputs") and mode != "replace":
        base = cfg["paths"]["outputs"]
        for item in payload["outputs"]:
            rel = item.get("rel", "")
            if not rel or ".." in rel.replace("\\", "/").split("/"):
                continue
            dst = os.path.join(base, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(item.get("content", ""))
                report["outputs"] += 1
            except Exception:
                pass

    if payload.get("telemetry"):
        p = os.path.join(cfg["paths"]["logs"], "telemetry.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for row in payload["telemetry"]:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        report["logs"] = len(payload["telemetry"])

    return {"ok": True, "report": report}


def init_workspace(cfg, reset_profile=False):
    """初始化：补齐所有必需目录和文件。

    幂等 —— 已存在的不会覆盖。
    """
    created = []
    for key, p in cfg["paths"].items():
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created.append(p)

    # 产出目录的子结构
    out = cfg["paths"]["outputs"]
    for sub in ("文章", "视觉素材", "发布包", "数据", "归档区"):
        p = os.path.join(out, sub)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created.append(p)

    # 状态目录
    state = cfg["paths"]["state"]
    for sub in ("profile", "备份", "回收区"):
        p = os.path.join(state, sub)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created.append(p)

    # 档案文件
    from . import profile as profmod
    pf = os.path.join(state, "profile", "profile.json")
    if not os.path.exists(pf):
        profmod.save_profile(cfg, profmod.default_profile())
        created.append(pf)

    # 空队列文件
    for fn in ("orders.jsonl",):
        p = os.path.join(state, fn)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
            created.append(p)

    # 总索引
    idx = build_index(cfg)

    return {"ok": True, "created": created, "count": len(created),
            "index": idx.get("total", 0)}


def restore_defaults(cfg, keep_data=True):
    """恢复默认状态。

    keep_data=True  → 只重置档案和配置，保留产出（默认，安全）
    keep_data=False → 连产出一起归档到回收区（危险，需明确确认）
    """
    from . import profile as profmod
    from . import config as cfgmod

    actions = []

    # 1. 档案恢复默认
    profmod.reset_profile(cfg)
    actions.append("人设档案已恢复默认")

    # 2. 产出归档（可选）
    if not keep_data:
        recycle = os.path.join(cfg["paths"]["state"], "回收区")
        os.makedirs(recycle, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        trash = os.path.join(recycle, f"产出_重置_{stamp}")
        try:
            if os.path.isdir(cfg["paths"]["outputs"]):
                shutil.move(cfg["paths"]["outputs"], trash)
                os.makedirs(cfg["paths"]["outputs"], exist_ok=True)
            actions.append(f"产出已移入 {trash}")
        except Exception as e:
            actions.append(f"产出归档失败：{e}")

    # 3. 队列清空
    for fn in ("orders.jsonl",):
        p = os.path.join(cfg["paths"]["state"], fn)
        if os.path.exists(p):
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write("")
                actions.append("派活队列已清空")
            except Exception:
                pass

    # 4. 重新初始化
    init_workspace(cfg)

    return {"ok": True, "actions": actions}


def list_backups(cfg, limit=30):
    """列出备份。"""
    d = os.path.join(cfg["paths"]["state"], "备份")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d), reverse=True):
        if not fn.endswith(".zip"):
            continue
        p = os.path.join(d, fn)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"name": fn, "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime)
                    .strftime("%Y-%m-%d %H:%M")})
    return out[:limit]

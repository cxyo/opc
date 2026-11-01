"""产出工作台：让「AI 干完了但看不见」这件事消失。

解决三件事：
    1. 列出所有产出文件（按岗位聚合），让人知道 AI 到底产出了什么
    2. 读取任意产出文件的完整内容（在线看，不用去翻目录）
    3. 人工直接改写产出文件并落盘（人工闸的落地形式）

为什么要单独做：
    状态墙只显示「产出字数」，用户看到的是数字不是内容。
    台账只显示「耗时/花费」，看不到正文。
    这两处合起来的结果就是——钱花了，屏幕上什么都没有。

安全约束（重要）：
    - 只允许读写 outputs 目录内的文件，路径越界直接拒绝
    - 只允许 .md / .txt / .csv / .json 四类文本文件，不碰可执行文件
    - 每次人工改写都会留痕（人工修改台账），可追溯是谁改的
"""

import json
import os
import re
import shutil
from datetime import datetime

# 允许在网页里读写的扩展名
ALLOWED_EXT = (".md", ".txt", ".csv", ".json")

# 产出文件的角色识别：A07 撰稿 → A07
_ROLE_RE = re.compile(r"(A\d{2})")

# 人工修改台账
EDIT_LOG = "人工修改台账.jsonl"


# ---- 路径安全 --------------------------------------------------------

def _outputs_root(cfg):
    return os.path.realpath(cfg["paths"]["outputs"])


def _resolve(cfg, rel_path):
    """把相对路径解析成绝对路径，并确保没有越界。

    返回 (绝对路径, 错误信息)。出错时绝对路径为 None。
    """
    root = _outputs_root(cfg)
    if not os.path.isdir(root):
        return None, "产出目录不存在"

    # 规范化用户输入，去掉可能的 ../ 和绝对路径前缀
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel:
        return None, "缺少文件路径"
    rel = rel.lstrip("/")
    if ".." in rel.split("/"):
        return None, "路径中不允许出现 .."

    target = os.path.realpath(os.path.join(root, rel))

    # 双保险：解析后必须仍在 outputs 根目录内
    if not (target == root or target.startswith(root + os.sep)):
        return None, "只能访问产出目录内的文件"
    if not os.path.isfile(target):
        return None, "文件不存在"
    if not target.lower().endswith(ALLOWED_EXT):
        return None, "只允许读写 .md / .txt / .csv / .json 文件"
    return target, ""


def _rel(cfg, abs_path):
    """绝对路径 → 相对于 outputs 的路径（用于回给前端）。"""
    root = _outputs_root(cfg)
    if abs_path.startswith(root):
        return os.path.relpath(abs_path, root).replace("\\", "/")
    return abs_path


# ---- 列表 ------------------------------------------------------------

def _file_meta(cfg, abs_path, root):
    """一个产出文件的元信息。"""
    try:
        st = os.stat(abs_path)
        size = st.st_size
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        size, mtime = 0, ""

    rel = os.path.relpath(abs_path, root).replace("\\", "/")
    folder = os.path.dirname(rel) or "(根目录)"
    name = os.path.basename(rel)
    m = _ROLE_RE.search(name)
    role = m.group(1) if m else ""

    # 预估字数：中文按字数，粗略用字节数/2 兜底
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
        chars = len(text)
        lines = text.count("\n") + 1
        # 待补标记数量 —— 人工改稿时要重点看这些
        pending = len(re.findall(r"【待填[^】]*】|【待补[^】]*】|待填|待补", text))
    except Exception:
        chars, lines, pending = 0, 0, 0

    return {
        "rel": rel.replace(os.sep, "/"),
        "name": name,
        "folder": folder,
        "role": role,
        "size": size,
        "chars": chars,
        "lines": lines,
        "pending": pending,
        "mtime": mtime,
    }


def list_outputs(cfg):
    """列出所有产出文件，按修改时间倒序。"""
    root = _outputs_root(cfg)
    if not os.path.isdir(root):
        return {"files": [], "folders": [], "total_chars": 0}

    items = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith(ALLOWED_EXT):
                continue
            # 跳过状态墙这类系统生成物
            if fn in ("状态墙.html",):
                continue
            items.append(_file_meta(cfg, os.path.join(dirpath, fn), root))

    items.sort(key=lambda x: x["mtime"], reverse=True)

    folders = {}
    for it in items:
        folders.setdefault(it["folder"], 0)
        folders[it["folder"]] += 1

    return {
        "files": items,
        "folders": [{"name": k, "count": v} for k, v in folders.items()],
        "total_chars": sum(i["chars"] for i in items),
        "total_files": len(items),
    }


# ---- 读取 ------------------------------------------------------------

def read_output(cfg, rel_path):
    """读取一个产出文件的完整内容。"""
    abs_path, err = _resolve(cfg, rel_path)
    if err:
        return {"ok": False, "error": err}
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"ok": False, "error": f"读取失败：{e}"}

    meta = _file_meta(cfg, abs_path, _outputs_root(cfg))
    return {"ok": True, "content": content, "meta": meta}


# ---- 写回（人工修改） -------------------------------------------------

def _log_edit(cfg, rel_path, note, before_len, after_len):
    """人工修改留痕。"""
    state = cfg["paths"]["state"]
    os.makedirs(state, exist_ok=True)
    rec = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file": rel_path,
        "note": note,
        "before_chars": before_len,
        "after_chars": after_len,
        "operator": "人工",
    }
    with open(os.path.join(state, EDIT_LOG), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def save_output(cfg, rel_path, content, note="人工修改"):
    """人工改写产出文件并落盘。原文件会先备份成 .bak。

    返回 {ok, error, chars, backup}。
    """
    abs_path, err = _resolve(cfg, rel_path)
    if err:
        return {"ok": False, "error": err}

    content = content or ""
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            before = f.read()
    except Exception:
        before = ""

    if before == content:
        return {"ok": True, "chars": len(content), "unchanged": True,
                "message": "内容没有变化，未写入"}

    # 备份原文件（保留最近 5 份，文件名带时间戳）
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = abs_path + f".bak_{stamp}"
        shutil.copy2(abs_path, backup)
        _prune_backups(abs_path)
    except Exception:
        backup = ""

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"ok": False, "error": f"写入失败：{e}"}

    try:
        _log_edit(cfg, _rel(cfg, abs_path), note, len(before), len(content))
    except Exception:
        pass

    return {
        "ok": True,
        "chars": len(content),
        "backup": os.path.basename(backup) if backup else "",
        "message": "已保存（原文件已备份）",
    }


def _prune_backups(abs_path, keep=5):
    """只保留最近 keep 份备份，避免目录无限膨胀。"""
    d = os.path.dirname(abs_path)
    base = os.path.basename(abs_path)
    backups = []
    for fn in os.listdir(d):
        if fn.startswith(base + ".bak_"):
            p = os.path.join(d, fn)
            try:
                backups.append((os.path.getmtime(p), p))
            except OSError:
                pass
    backups.sort(reverse=True)
    for _t, p in backups[keep:]:
        try:
            os.remove(p)
        except OSError:
            pass


# ---- 返工：把人工意见交给 AI 重做 -------------------------------------

def revise_output(cfg, rel_path, instruction, code=None, tier="create",
                  use_kb=True, temp=0.7):
    """人工发现问题 → 下达一条返工指令给 AI 员工。

    这条指令会把「原文 + 人工意见」一起交给指定岗位，
    让它在原文基础上改，而不是从头重写。

    返回 {ok, error, order_id}。
    """
    if not (instruction or "").strip():
        return {"ok": False, "error": "请写清楚要改什么"}

    abs_path, err = _resolve(cfg, rel_path)
    if err:
        return {"ok": False, "error": err}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            original = f.read()
    except Exception as e:
        return {"ok": False, "error": f"读取失败：{e}"}

    meta = _file_meta(cfg, abs_path, _outputs_root(cfg))
    role = code or meta["role"]
    if not role:
        return {"ok": False, "error": "这个文件没有对应的岗位编号，请在下拉里选一个岗位"}

    from . import console as consolemod
    from . import roles as rolesmod

    reg = rolesmod.RoleRegistry(cfg["paths"]["roles"])
    if reg.get(role) is None:
        return {"ok": False, "error": f"没有这个岗位：{role}"}

    task = (
        f"【返工任务】请修改下面这份已有产出。\n\n"
        f"产出文件：{meta['name']}\n"
        f"原岗位：{role}\n\n"
        f"─── 原文开始 ───\n{original}\n─── 原文结束 ───\n\n"
        f"【人工修改意见】\n{instruction.strip()}\n\n"
        f"请直接输出修改后的完整正文，不要写「以下是修改后的内容」这类前缀，"
        f"不要解释你改了什么。保留原文中正确的部分，只改意见涉及的地方。"
    )

    rec = consolemod.submit_order(
        cfg["paths"]["state"], role, task,
        tier=tier, use_kb=bool(use_kb),
        save_as="", temp=float(temp),
        extra={
            "revise_from": meta["rel"],
            "instruction": instruction.strip()[:500],
        },
    )

    return {"ok": True, "order_id": rec["id"], "code": role,
            "file": meta["rel"], "message": "返工指令已下发"}


# ---- 人工修改记录 -----------------------------------------------------

def list_edits(cfg, limit=50):
    """读人工修改台账。"""
    p = os.path.join(cfg["paths"]["state"], EDIT_LOG)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return list(reversed(out))[-limit:]

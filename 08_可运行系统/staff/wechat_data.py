# -*- coding: utf-8 -*-
"""A26 微信情报官 —— 数据读取层。

从已解密的微信数据库里读出会话、联系人、消息，供关系识别与分析使用。

数据流
------
    db_storage/（加密）
        ↓ staff/wechat_key.py 解密
    data/wechat_plain/（标准 SQLite）
        ↓ 本模块
    会话列表 + 联系人 + 消息流

关键结构（实测 微信 4.1.15.13）
--------------------------------
    session/session.db   SessionTable   —— 会话（username / summary / 时间）
    contact/contact.db   contact        —— 联系人（remark / nick_name / username）
                          chat_room     —— 群信息（owner / ext_buffer 群名）
                          chatroom_member —— 群成员
    message/message_N.db Msg_<会话ID>   —— 消息（create_time / message_content）

    message_content 格式：
        type=1（文本）  "wxid_sender:\n实际内容"
        其他 type       压缩二进制（图片/语音/引用等）
"""

import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ------------------------------------------------------------------ 路径

PLAIN_DIRNAME = os.path.join("data", "wechat_plain")


def _project_root() -> str:
    """**必须用 config.PROJECT** —— 打包成 exe 后 __file__ 指向临时解压目录。"""
    try:
        from . import config as cfgmod
        proj = getattr(cfgmod, "PROJECT", "")
        if proj and os.path.isdir(os.path.join(proj, "08_可运行系统")):
            return proj
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def plain_dir() -> str:
    return os.path.join(_project_root(), "08_可运行系统", PLAIN_DIRNAME)


def is_ready() -> bool:
    d = plain_dir()
    return os.path.isfile(os.path.join(d, "session", "session.db"))


def _db(rel: str) -> str:
    return os.path.join(plain_dir(), rel.replace("/", os.sep))


def _connect(rel: str) -> Optional[sqlite3.Connection]:
    p = _db(rel)
    if not os.path.isfile(p):
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
        con.text_factory = bytes
        return con
    except Exception:
        return None


def _s(v: Any) -> str:
    """bytes/None → str（微信库里中文都是 utf-8 bytes）。"""
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("utf-8", errors="ignore")
    return str(v)


# ------------------------------------------------------------------ 联系人


def load_contacts() -> Dict[str, Dict[str, Any]]:
    """返回 {username: 联系人信息}。

    字段：remark（备注）nick_name（昵称）username local_type
          alias（微信号）is_in_chat_room description
    """
    out: Dict[str, Dict[str, Any]] = {}
    con = _connect("contact/contact.db")
    if not con:
        return out
    try:
        rows = con.execute(
            "SELECT username, remark, nick_name, alias, local_type, "
            "is_in_chat_room, description FROM contact").fetchall()
        for r in rows:
            u = _s(r[0])
            if not u:
                continue
            out[u] = {
                "username": u,
                "remark": _s(r[1]),
                "nick_name": _s(r[2]),
                "alias": _s(r[3]),
                "local_type": r[4],
                "is_in_chat_room": r[5],
                "description": _s(r[6]),
            }
    except Exception:
        pass
    finally:
        con.close()
    return out


def load_rooms() -> Dict[str, Dict[str, Any]]:
    """返回 {群username: 群信息}，含群名。

    群名的**真实位置**（实测 微信 4.1.15.13）：
        contact 表里 @chatroom 那一行的 **nick_name** 字段就是群名
        （如「🎁【宏毅资料库】」「家人2018」）
    注意：chat_room.ext_buffer 里存的是**成员列表 protobuf**，不是群名 ——
    早期误以为那是群名，会抽出 wxid 而非可读名字。
    """
    out: Dict[str, Dict[str, Any]] = {}
    con = _connect("contact/contact.db")
    if not con:
        return out
    try:
        rows = con.execute(
            "SELECT username, nick_name, remark FROM contact "
            "WHERE username LIKE '%@chatroom'").fetchall()
        for r in rows:
            u = _s(r[0])
            out[u] = {"username": u,
                      "name": _s(r[1]) or _s(r[2]),
                      "owner": ""}
        # 群主
        for u, owner in con.execute(
                "SELECT username, owner FROM chat_room"):
            u = _s(u)
            if u in out:
                out[u]["owner"] = _s(owner)
    except Exception:
        pass
    finally:
        con.close()
    return out


def load_room_members() -> Dict[str, List[str]]:
    """返回 {群username: [成员username]}。"""
    out: Dict[str, List[str]] = {}
    con = _connect("contact/contact.db")
    if not con:
        return out
    try:
        for rid, mid in con.execute(
                "SELECT room_id, member_id FROM chatroom_member"):
            out.setdefault(_s(rid), []).append(_s(mid))
    except Exception:
        pass
    finally:
        con.close()
    return out


def display_name(username: str, contacts: Dict[str, Dict[str, Any]],
                 rooms: Dict[str, Dict[str, Any]]) -> str:
    """会话 → 人可读名字。优先级：自己备注 > 群名 > 对方昵称 > 对方备注 > ID。"""
    if username in rooms:
        nm = (rooms[username].get("name") or "").strip()
        if nm:
            return nm
        return "群聊 " + username[-8:]
    c = contacts.get(username)
    if c:
        if c.get("remark"):
            return c["remark"]
        if c.get("nick_name"):
            return c["nick_name"]
    if username == "filehelper":
        return "文件传输助手"
    if username.endswith("@chatroom"):
        return "群聊 " + username[-8:]
    if username.endswith("@openim"):
        return "服务号 " + username.split("@")[0][-8:]
    if username.startswith("gh_"):
        return "公众号 " + username[3:11]
    return username


# ------------------------------------------------------------------ 会话


def load_sessions() -> List[Dict[str, Any]]:
    """返回**近期**会话列表（按最后活跃时间倒序）。

    数据源：session/session.db 的 SessionTable —— 只有 308 个近期会话，
    但带**最后活跃时间、未读数、消息摘要**。
    完整会话清单见 load_session_index()。
    """
    out: List[Dict[str, Any]] = []
    con = _connect("session/session.db")
    if not con:
        return out
    try:
        rows = con.execute(
            "SELECT username, type, summary, last_sender_display_name, "
            "last_timestamp, unread_count, status FROM SessionTable"
        ).fetchall()
        for r in rows:
            u = _s(r[0])
            out.append({
                "username": u,
                "type": r[1],
                "summary": _s(r[2]),
                "last_sender": _s(r[3]),
                "last_ts": r[4] or 0,
                "unread": r[5] or 0,
                "status": r[6],
            })
    except Exception:
        pass
    finally:
        con.close()
    out.sort(key=lambda x: -x["last_ts"])
    return out


def load_session_index() -> List[Dict[str, Any]]:
    """返回**完整**的会话索引。

    两个数据源合并（实测）：
      ① session/session.db 的 SessionTable —— 308 个，含**最近活跃时间、
         未读数、消息摘要**（只有近期会话）
      ② message/message_*.db 的 Name2Id —— 1046 个，含**历史会话**，
         但没有活跃时间/摘要

    合并后按 username 去重，SessionTable 的字段优先。
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for s in load_sessions():
        merged[s["username"]] = s
    base = plain_dir()
    msgdir = os.path.join(base, "message")
    if os.path.isdir(msgdir):
        for fn in sorted(os.listdir(msgdir)):
            if not (fn.startswith("message_") and fn.endswith(".db")):
                continue
            if fn in ("message_fts.db", "message_resource.db"):
                continue
            con = _connect("message/" + fn)
            if not con:
                continue
            try:
                for (u,) in con.execute("SELECT user_name FROM Name2Id"):
                    u = _s(u)
                    if not u or u in merged:
                        continue
                    merged[u] = {"username": u, "type": 0, "summary": "",
                                  "last_sender": "", "last_ts": 0,
                                  "unread": 0, "status": 0}
            except Exception:
                pass
            finally:
                con.close()
    out = list(merged.values())
    out.sort(key=lambda x: -x.get("last_ts", 0))
    return out


# ------------------------------------------------------------------ 消息

# WCDB 压缩内容的 zstd 帧魔数
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# 消息类型 → 可读名（微信 4.x local_type）
MSG_TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情",
    42: "名片", 48: "位置", 49: "文件/链接", 50: "通话",
    10000: "系统消息", 10002: "撤回", 10003: "提醒",
    244813135921: "直播", 8594229559345: "引用/合并转发",
}


def _zstd_decompress(b: bytes) -> bytes:
    """zstd 解压。失败返回空 bytes。"""
    if not b.startswith(ZSTD_MAGIC):
        return b""
    try:
        import zstandard as zstd
        dctx = zstd.ZstdDecompressor()
        try:
            return dctx.decompress(b)
        except Exception:
            return dctx.decompress(b, max_output_size=1 << 22)
    except ImportError:
        return b""


def _strip_xml(text: str) -> str:
    """把微信 XML 消息体压成一句可读摘要。

    `<msg><img aeskey=.../></msg>` → 【图片】
    `<msg><emoji .../></msg>`      → 【表情】
    `<msg><appmsg><title>xx</title>` → 【链接】xx
    `<refermsg>...<content>xx</content>` → 引用：xx
    纯文本 → 原样
    """
    if "<" not in text:
        return text.strip()
    import re as _re
    out = text
    m = _re.search(r"<appmsg[^>]*>.*?<title>(.*?)</title>", out, _re.S)
    if m:
        t = _re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return ("【链接】" + t) if t else "【链接】"
    m = _re.search(r"<refermsg.*?<content>(.*?)</content>", out, _re.S)
    if m:
        t = _re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return ("【引用】" + t) if t else "【引用】"
    if "<img" in out:
        return "【图片】"
    if "<emoji" in out:
        return "【表情】"
    if "<voice" in out:
        return "【语音】"
    if "<video" in out:
        return "【视频】"
    if "<location" in out:
        return "【位置】"
    if "<appmsg" in out:
        return "【应用消息】"
    if "<sysmsg" in out:
        return "【系统消息】"
    # 通用：剥掉所有标签留文本
    t = _re.sub(r"<[^>]+>", " ", out)
    t = _re.sub(r"\s+", " ", t).strip()
    return t[:300] if t else "【非文本消息】"


_SENDER_RE = re.compile(
    r"^([A-Za-z0-9_\-]+@chatroom|wxid_[A-Za-z0-9_\-]+|filehelper"
    r"|[0-9]{6,}@openim|[A-Za-z0-9_\-]{4,30}):\n?")


_FROM_ATTR = re.compile(r'fromusername\s*=\s*"([^"]+)"')


def _parse_content(raw: Any) -> Tuple[str, str]:
    """message_content → (发送者标识, 正文)。

    **实测 微信 4.1.15.13 的两种存储格式**：
    1. 明文 TEXT —— 纯正文，**不带** "wxid_sender:\\n" 前缀
    2. 压缩 BLOB —— zstd 帧（魔数 28b52ffd），解压后是 XML 消息体

    早期版本（4.0 前后）的文本带 "wxid_xxx:\\n" 前缀，
    这里仍兼容这种格式 —— 有前缀就取前缀，没有就返回空发送者，
    由调用方用 real_sender_id / 会话归属来补。

    注意：**local_type=1 不代表一定是明文**，压缩过的 type=1 同样存在。
    """
    if raw is None:
        return "", ""
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, (bytes, bytearray)):
        b = bytes(raw)
        if b.startswith(ZSTD_MAGIC):
            b = _zstd_decompress(b)
            if not b:
                return "", ""
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            return "", ""
    else:
        return "", ""

    text = text.strip()
    if not text:
        return "", ""

    # 兼容旧格式："wxid_sender:\n正文"
    m = _SENDER_RE.match(text)
    if m and "\n" in text[:m.end() + 1]:
        return m.group(1), _strip_xml(text[m.end():].strip())

    # XML 消息体：从 fromusername 取发送者
    if "<" in text:
        fm = _FROM_ATTR.search(text)
        sender = fm.group(1) if fm else ""
        return sender, _strip_xml(text)

    # 纯文本：无前缀
    return "", text[:2000]


def _msg_tables() -> List[Tuple[str, str]]:
    """列出 (db相对路径, 表名) 全部消息表。"""
    out: List[Tuple[str, str]] = []
    base = plain_dir()
    msgdir = os.path.join(base, "message")
    if not os.path.isdir(msgdir):
        return out
    for fn in sorted(os.listdir(msgdir)):
        if not fn.startswith("message_") or not fn.endswith(".db"):
            continue
        if fn in ("message_fts.db", "message_resource.db"):
            continue
        rel = "message/" + fn
        con = _connect(rel)
        if not con:
            continue
        try:
            for (t,) in con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name LIKE 'Msg\\_%' ESCAPE '\\'"):
                out.append((rel, _s(t)))
        except Exception:
            pass
        finally:
            con.close()
    return out


def table_name_for(username: str) -> str:
    """会话 username → 消息表名。

    **微信 4.x 的映射规则（实测验证）**：
        Msg_<md5(username)>
    例：wxid_xnr4efm0ys5632 → Msg_b96808e42402662882d27a2802a973b9
    """
    import hashlib
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def _name2id_map(rel: str) -> Dict[int, str]:
    """读某库的 Name2Id，返回 {is_session: user_name}。

    实测：message_8.db 里 Name2Id = [(自己, 0), (对方, 1)]，
    而消息行的 real_sender_id 就是这个索引值。
    """
    out: Dict[int, str] = {}
    con = _connect(rel)
    if not con:
        return out
    try:
        for u, idx in con.execute("SELECT user_name, is_session FROM Name2Id"):
            try:
                out[int(idx)] = _s(u)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        con.close()
    return out


def load_messages(session_id: str, limit: int = 300,
                  db_hint: str = "") -> List[Dict[str, Any]]:
    """读某个会话的消息（按时间正序）。

    session_id 就是 SessionTable.username，表名由 md5(username) 推出。
    同一会话可能分片在多个库（实测 Msg_10c6838b... 出现在 6 个库里），
    所以要遍历所有库。
    """
    want = table_name_for(session_id)
    out: List[Dict[str, Any]] = []
    base = plain_dir()
    msgdir = os.path.join(base, "message")
    if not os.path.isdir(msgdir):
        return out
    dbs = []
    if db_hint:
        dbs.append(db_hint)
    else:
        for fn in sorted(os.listdir(msgdir)):
            if (fn.startswith("message_") and fn.endswith(".db")
                    and fn not in ("message_fts.db", "message_resource.db")):
                dbs.append("message/" + fn)
    for rel in dbs:
        con = _connect(rel)
        if not con:
            continue
        try:
            # 表名参数必须用 str：text_factory=bytes 只影响**返回**的列值，
            # 不影响绑定参数。用 bytes 绑定会匹配不上（实测返回 None）。
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (want,)).fetchone()
            if not exists:
                continue
            n2i = _name2id_map(rel)
            rows = con.execute(
                'SELECT create_time, local_type, message_content, '
                'real_sender_id, status FROM "%s" ORDER BY create_time DESC '
                'LIMIT ?' % want, (limit,)).fetchall()
            for r in rows:
                sender, body = _parse_content(r[2])
                if not body:
                    continue
                if not sender:
                    # 用 real_sender_id 索引回查 Name2Id
                    try:
                        sender = n2i.get(int(r[3]), "")
                    except Exception:
                        sender = ""
                if not sender:
                    sender = session_id
                out.append({
                    "ts": r[0] or 0,
                    "type": r[1],
                    "type_name": MSG_TYPE_NAMES.get(r[1], "类型%s" % r[1]),
                    "sender": sender,
                    "body": body,
                    "db": rel,
                })
        except Exception:
            pass
        finally:
            con.close()
    out.sort(key=lambda x: x["ts"])
    return out


def iter_all_messages(limit_per_session: int = 60,
                       only_sessions: Optional[Iterable[str]] = None
                       ) -> Dict[str, List[Dict[str, Any]]]:
    """扫全部会话的消息（每人只取最近 N 条，控制耗时）。

    返回 {session_id: [msg...]}，session_id 就是 SessionTable.username。
    """
    want: Optional[set] = set(only_sessions) if only_sessions else None
    out: Dict[str, List[Dict[str, Any]]] = {}
    if want is None:
        # 未指定时，用完整会话索引（含历史会话）
        want = {s["username"] for s in load_session_index()}
    for sid in want:
        msgs = load_messages(sid, limit=limit_per_session)
        if msgs:
            out[sid] = msgs
    return out


def message_stats() -> Dict[str, Any]:
    """消息总量统计（用于控制台展示）。"""
    st = {"会话数": 0, "消息表数": 0, "消息总数": 0, "按库": {}}
    base = plain_dir()
    msgdir = os.path.join(base, "message")
    if not os.path.isdir(msgdir):
        return st
    for fn in sorted(os.listdir(msgdir)):
        if not (fn.startswith("message_") and fn.endswith(".db")):
            continue
        if fn in ("message_fts.db", "message_resource.db"):
            continue
        rel = "message/" + fn
        con = _connect(rel)
        if not con:
            continue
        n = 0
        tabs = 0
        try:
            for (t,) in con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name LIKE 'Msg\\_%' ESCAPE '\\'"):
                # text_factory=bytes 会把表名返回成 bytes，
                # 直接拼进 SQL 会变成 b'Msg_xxx' 导致语法错误（被 except 吞掉 → 统计恒为 0）。
                tname = _s(t)
                if not tname.startswith("Msg_"):
                    continue
                try:
                    n += con.execute('SELECT COUNT(*) FROM "%s"' % tname
                                     ).fetchone()[0]
                    tabs += 1
                except Exception:
                    pass
        finally:
            con.close()
        st["按库"][fn] = n
        st["消息总数"] += n
        st["消息表数"] += tabs
    con = _connect("session/session.db")
    if con:
        try:
            st["会话数"] = con.execute(
                "SELECT COUNT(*) FROM SessionTable").fetchone()[0]
        except Exception:
            pass
        con.close()
    return st

"""企业知识库：把老板自己的资料，变成能问、能答、能溯源的知识库。

为什么做这个
------------
2026-10-30 调研（依据见 `11_变现模式/双线规划_一人公司与Skill流水线_2026-10-30.md`）：

  · 国内中小企业 AI 智能体普及率同比 +62%，100 人以下企业增速最快
  · 他们的选型第一诉求是「安全」（占 45%），且明确要「开箱即用、不需要复杂部署」
  · 客服用例渗透率 69%，是全部用例里最高的

现有系统的知识库全是**我们写好的**（行业包里的那几十篇）。
但老板最想问的，恰恰是他自己的东西：产品手册、报价单、SOP、合同模板、FAQ。
这些进不来，"先做通用版让老板的 AI 员工跑起来"就落不了地。

本模块做四件事：

  ① 管客户资料目录   —— 客户把文件丢进来就行，不需要学怎么建知识库
  ② 建本地索引       —— 复用 kb.KB（TF-IDF + 中文二元切分，零依赖、离线、数据不出本机）
  ③ 问答并溯源       —— 检索 → 拼上下文 → 调模型 → 答案带「出自哪份文档」
  ④ 命中统计         —— 哪些文档被问到、哪些是死文档（给客户看，也是我们做行业包的依据）

设计取舍（都是为了不吹）
----------------------
· **零第三方依赖**：环境里没有 PDF / Word 解析库，所以只支持 md/txt/csv/json/html。
  PDF 与 Word **明确拒绝并给出改法**，不假装能读。
· **检索在本地，模型才联网**：客户资料不整份上传，只把命中片段发给模型。
  这是「数据不出本机」卖点能成立的前提，也是 45% 安全诉求的答案。
· **没有 Key 时不编答案**：返回命中原文 + 明确提示，让客户先看到"库建好了"。
· **检索库每次重建**：文档量在几十到几百量级，重建耗时毫秒级。
  不做索引缓存 —— 缓存会引入"文件改了索引没更新"这类难查的错。

对外的一句话（已实测的可以说，没实测的不能说）：
    「资料放进一个文件夹，0.66 秒就能问；答案永远带出处。」
"""

import base64
import json
import os
import re
import time
from datetime import datetime

from . import config as cfgmod
from . import kb as kbmod

# 客户资料目录：放在 05_产品交付 下，客户一眼能找到
CLIENT_DIR_REL = os.path.join("05_产品交付", "客户资料")
STATE_NAME = "kb_client.json"

# 支持的格式与展示名
SUPPORTED = {
    ".md": "Markdown",
    ".txt": "纯文本",
    ".csv": "表格",
    ".json": "JSON",
    ".html": "网页",
}

# 不支持的格式：给明确改法，不做静默忽略
UNSUPPORTED = {
    ".pdf": "PDF 请另存为 .txt，或用 WorkBuddy 的 PDF 技能转成 Markdown 再放进来",
    ".doc": "Word 请另存为 .txt 或 .md",
    ".docx": "Word 请另存为 .txt 或 .md",
    ".xls": "Excel 请另存为 .csv",
    ".xlsx": "Excel 请另存为 .csv",
    ".ppt": "PPT 请导出为 .md 或 .txt",
    ".pptx": "PPT 请导出为 .md 或 .txt",
    ".zip": "请解压后把里面的文档放进目录",
    ".rar": "请解压后把里面的文档放进目录",
}

# 文件名安全字符（Windows 保留字 + 路径分隔符）
_UNSAFE = re.compile(r'[\\/:*?"<>|]')


# ---------------------------------------------------------------- 路径与状态


def client_dir(cfg=None):
    """客户资料目录。不存在就建。"""
    d = os.path.join(cfgmod.PROJECT, CLIENT_DIR_REL)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def state_path(cfg=None):
    return os.path.join(cfgmod.ROOT, "data", "state", STATE_NAME)


def load_state(cfg=None):
    p = state_path(cfg)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(cfg, st):
    p = state_path(cfg)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- 读文件


def _read_text(path):
    """读文本。HTML 剥标签，避免标签噪声进索引。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            t = f.read()
    except Exception:
        return ""
    low = path.lower()
    if low.endswith(".html") or low.endswith(".htm"):
        t = re.sub(r"<script[\s\S]*?</script>", " ", t, flags=re.I)
        t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        t = t.replace("&nbsp;", " ").replace("&amp;", "&")
        t = re.sub(r"\s+", " ", t)
    return t


def _walk_files(d):
    """遍历目录里所有文件，返回 (完整路径, 相对名, 扩展名)。"""
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            p = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            out.append((p, os.path.relpath(p, d), ext))
    return out


# ---------------------------------------------------------------- 扫描与索引


def scan(cfg=None):
    """扫描客户资料目录，列出可用文档与被拒文件。"""
    d = client_dir(cfg)
    docs, skipped = [], []
    for p, rel, ext in _walk_files(d):
        try:
            stt = os.stat(p)
            size, mtime = stt.st_size, stt.st_mtime
        except OSError:
            size, mtime = 0, 0
        if ext in SUPPORTED:
            docs.append({
                "name": rel,
                "ext": ext,
                "type": SUPPORTED[ext],
                "size": size,
                "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                "path": p,
            })
        else:
            skipped.append({
                "name": rel,
                "ext": ext,
                "hint": UNSUPPORTED.get(ext, "不支持的类型，请转成 .md 或 .txt 再放进来"),
            })
    return {"dir": d, "docs": docs, "skipped": skipped,
            "total": len(docs), "skipped_total": len(skipped)}


def build_index(cfg=None):
    """把客户资料灌进本地检索库，并记录索引元信息。"""
    d = client_dir(cfg)
    kb = kbmod.KB()
    n = 0
    chars = 0
    files = _walk_files(d)
    for p, rel, ext in files:
        if ext not in SUPPORTED:
            continue
        text = _read_text(p)
        if not text.strip():
            continue
        kb.add(rel, os.path.basename(d), rel, text, {"path": p})
        n += 1
        chars += len(text)

    st = load_state(cfg)
    st["indexed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st["doc_count"] = n
    st["char_count"] = chars
    st["dir"] = d
    st["docs"] = [{"name": rel, "ext": ext} for _p, rel, ext in files if ext in SUPPORTED]
    st.setdefault("hits", {})
    save_state(cfg, st)

    return {"ok": True, "docs": n, "chars": chars, "dir": d,
            "indexed_at": st["indexed_at"]}


def get_kb(cfg=None):
    """构造检索库（每次重建，毫秒级，换来"文件改了立刻生效"）。"""
    d = client_dir(cfg)
    kb = _make_kb(cfg)
    for p, rel, ext in _walk_files(d):
        if ext not in SUPPORTED:
            continue
        text = _read_text(p)
        if not text.strip():
            continue
        kb.add(rel, os.path.basename(d), rel, text, {"path": p})
    return kb


# ---------------------------------------------------------------- 检索与问答

# 价格意图词：老板的第一个问题几乎总是这一类
PRICE_INTENT = ["多少钱", "价格", "报价", "费用", "收费", "单价", "预算",
                "费用是", "几个钱", "卖多少", "成本"]

# 金额信号：文档里出现真实金额
MONEY_RE = re.compile(r"(?:¥|￥)\s*[\d,]+(?:\.\d+)?|\d[\d,]*\s*元")

# 意图组：(查询触发词, 权重, 文档侧匹配条件)
#
# 为什么用"组"而不是单列关键词：
#   客户问「保修多久」，资料里写的是「质保期为验收后 12 个月」——
#   字面一个都对不上，纯检索必然漏。意图组把同义说法归到一组，
#   只要**查询命中组内任一说法、且文档出现组内任一说法**就加分。
#   这类同义差异在中文企业资料里极常见（保修/质保、保修期/保固期）。
#   金额组用正则（要看到 ¥ 或 元 才算），其余用字面包含。
INTENT_GROUPS = [
    (["多少钱", "价格", "报价", "费用", "收费", "单价", "预算", "卖多少", "成本"],
     0.6, MONEY_RE),
    (["保修", "质保", "保固", "包修"], 0.3,
     ["保修", "质保", "保固", "质保期", "保修期"]),
    (["交期", "交货", "发货", "多久到", "什么时候到"], 0.3,
     ["交期", "交货", "发货", "工作日", "现货"]),
    (["故障", "报警", "坏了", "不工作", "不转", "急停"], 0.3,
     ["故障", "报警", "急停", "互锁", "不工作", "故障码"]),
]


def _rerank(query, scored):
    """二次排序：把"字面没命中、但语义该排前面"的文档提上来。

    为什么必须有这一步：
        纯 TF-IDF 只认字面。老板问「这个打磨头多少钱」，
        「多少钱」三个字在报价单里一次都没出现 —— 检索反而把含"打磨头"三个字的
        FAQ 排在前面。**老板第一个问题就答错，等于整套系统当场失去信任。**

    三条规则，都可解释、可关掉：

        ① 标题加权：文件名命中查询词（「报价单」命中「报价」）→ 0.25 × 命中数
        ② 意图组：  查询命中意图组 且 文档出现该组的对应说法 → 加权
        ③ 价格金额：查询在问价 且 文档里有真实金额 → 额外 +0.2

    权重是经验值，可用 A/B 调；关掉只需把 kb.rerank 置 None。
    """
    q = (query or "").lower()
    qt = set(kbmod.tokenize(query))
    out = []
    for score, d in scored:
        s = score
        # ① 标题加权
        name_t = set(kbmod.tokenize(d.title))
        hit = len(qt & name_t)
        if hit:
            s += 0.25 * hit
        # ② 意图组
        text = d.text or ""
        for words, weight, match in INTENT_GROUPS:
            if not any(w in q for w in words):
                continue
            if isinstance(match, re.Pattern):
                if match.search(text):
                    s += weight
            elif any(m in text for m in match):
                s += weight
        # ③ 问价且文档有金额 → 再加一点
        if any(w in q for w in PRICE_INTENT) and MONEY_RE.search(text):
            s += 0.2
        out.append((s, d))
    out.sort(key=lambda x: -x[0])
    return out


# 让 kb.search 把**全部文档**作为候选池交给 rerank，
# 否则原始字面分为 0 的文档永远没机会被语义规则捞回来。
# （例：客户问「保修多久」，资料里写的是「质保期为验收后 12 个月」，
#   纯字面匹配为 0 分，会被阈值直接过滤掉。）
_rerank.expand = True


def _make_kb(cfg=None):
    kb = kbmod.KB()
    kb.rerank = _rerank
    return kb


SYSTEM = """你是「企业知识库管理员」，只根据下面提供的企业资料回答老板的问题。

铁律：
1. **只用资料里的内容回答。** 不要用通用知识补充，不要猜测，不要编造数字、条款、价格。
2. **资料里没有的，直接说"资料里没有这一条"**，并指出该问谁或该补哪份资料。
3. **每个结论标注出处**，格式：`[文件名]`。资料里没写来源的，不要加引用。
4. **涉及金额、承诺、合同、赔付的结论，标一行「需人工确认」。**
5. 回答用中文，结论先行，能一段说清就不分段。
6. 不寒暄，不说"好的""以下是"，直接给答案。"""


def search(cfg=None, query="", top_k=5):
    """只检索，不调模型。用于前端"实时提示命中了什么"。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "关键词不能为空"}
    kb = get_kb(cfg)
    if not kb.docs:
        return {"ok": False, "error": "知识库还是空的，先把资料放进去并建索引",
                "dir": client_dir(cfg)}
    hits = kb.search(q, top_k=top_k)
    return {
        "ok": True,
        "query": q,
        "hits": [{"title": d.title, "source": d.source, "score": round(s, 3),
                  "excerpt": kb._excerpt(d, set(kbmod.tokenize(q)), max_chars=300)}
                 for d, s in hits],
    }


def ask(cfg=None, question="", top_k=5, role="A25"):
    """问答主流程：检索 → 拼上下文 → 调模型 → 答案带出处。

    没有 API Key 时**不编答案**，只返回命中原文。
    """
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "问题不能为空"}

    kb = get_kb(cfg)
    if not kb.docs:
        return {"ok": False, "error": "知识库还是空的。先把资料放进目录，再建索引。",
                "dir": client_dir(cfg), "stage": "empty"}

    hits = kb.search(q, top_k=top_k)
    ctx = kb.context(q, top_k=top_k)
    sources = [{"title": d.title, "source": d.source, "score": round(s, 3)}
               for d, s in hits]

    if not ctx:
        return {"ok": False, "error": "知识库里没找到能回答这个问题的内容",
                "question": q, "sources": sources, "stage": "no_hit"}

    cfg = cfg or cfgmod.load()
    if not cfg.get("api_key") and not cfg.get("simulate"):
        return {"ok": False, "need_key": True,
                "error": "还没配置 API Key。先把命中的原文给你看 —— 这里绝不编答案。",
                "question": q, "sources": sources, "excerpt": ctx[:2500],
                "stage": "no_key"}

    try:
        from . import live as livemod
        livemod.emit("kb", f"知识库检索：{q[:24]}", "good",
                      data={"hits": len(hits), "sources": [s["title"] for s in sources[:3]]})
    except Exception:
        pass

    from . import llm as llmmod
    user = ("企业资料片段：\n" + ctx +
            "\n\n---\n老板的问题：" + q +
            "\n\n按格式回答：先给结论，再给依据（每条结论标 [文件名]），最后一行给「需人工确认」或「无需人工确认」。")

    try:
        content, usage = llmmod.chat([{"role": "user", "content": user}],
                                     cfg=cfg, system=SYSTEM, temperature=0.2,
                                     max_tokens=2000, role=role,
                                     note="企业知识库问答")
    except Exception as e:
        return {"ok": False, "error": f"调用模型失败：{getattr(e, 'message', e)}",
                "hint": getattr(e, "hint", ""), "question": q,
                "sources": sources, "excerpt": ctx[:2500], "stage": "llm_error"}

    # 命中统计：哪些文档被问到（给客户看，也是我们做行业包的依据）
    st = load_state(cfg)
    hits_map = st.get("hits") or {}
    for s in sources:
        hits_map[s["title"]] = hits_map.get(s["title"], 0) + 1
    st["hits"] = hits_map
    st["last_question"] = q
    st["last_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(cfg, st)

    return {"ok": True, "question": q, "answer": content, "sources": sources,
            "usage": usage, "excerpt": ctx[:2500], "stage": "answered"}


def demo_questions(cfg=None, limit=5):
    """按已入库文档，给出"你可以这样问"。

    为什么需要：客户打开演示版一脸茫然，不知道能问什么。
    直接从他的文档名里长出问句，见效最快。

    问法按文档类型走不同模板 —— 通用模板（"《X》里是怎么说的？"）太机械，
    不像老板会说的话，问了也不像在测产品。
    """
    kb = get_kb(cfg)
    if not kb.docs:
        return []

    def make(title):
        t = title.lower()
        base = re.sub(r"\.(md|txt|csv|json|html)$", "", title, flags=re.I)
        base = re.sub(r"[_\-]+", " ", base).strip()
        if any(w in t for w in ("报价", "价格", "资费", "费用")):
            return "我们的报价是怎样的？有哪些项？"
        if any(w in t for w in ("faq", "问答", "问题", "常见")):
            return "客户最常问的几个问题是什么？怎么答？"
        if any(w in t for w in ("手册", "说明", "规格", "参数", "技术")):
            return "这个产品的关键参数和限制是什么？"
        if any(w in t for w in ("流程", "sop", "操作", "规程", "规范")):
            return "这个流程分几步？每一步谁来做？"
        if any(w in t for w in ("合同", "协议", "条款")):
            return "合同里最需要注意的条款是哪几条？"
        if any(w in t for w in ("培训", "上手", "指南")):
            return "新人上手要学会哪几件事？"
        return "《%s》里主要写了什么？" % base

    out, seen = [], set()
    for d in kb.docs:
        q = make(d.title)
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
        if len(out) >= limit:
            break
    return out


def stats(cfg=None):
    """命中统计。"""
    st = load_state(cfg)
    hits = st.get("hits") or {}
    ranked = sorted(hits.items(), key=lambda x: -x[1])
    total = sum(hits.values())
    scanned = scan(cfg)
    dead = [d["name"] for d in scanned["docs"]
            if d["name"] not in hits] if total else []
    return {
        "indexed_at": st.get("indexed_at", ""),
        "doc_count": st.get("doc_count", scanned["total"]),
        "char_count": st.get("char_count", 0),
        "total_questions": total,
        "ranked": [{"name": k, "hits": v} for k, v in ranked[:10]],
        "dead_docs": dead[:10],
        "last_question": st.get("last_question", ""),
        "last_at": st.get("last_at", ""),
    }


# ---------------------------------------------------------------- 上传


def save_upload(cfg=None, filename="", content_b64=""):
    """接收前端上传的文件（base64），落到客户资料目录。

    为什么用 base64 不用 multipart：标准库手写 multipart 解析容易出边界 bug，
    base64 走 JSON 最稳，代价是体积大 33%——而资料通常只有几十 KB。
    """
    name = (filename or "").strip()
    if not name:
        return {"ok": False, "error": "缺少文件名"}
    name = os.path.basename(_UNSAFE.sub("_", name))
    ext = os.path.splitext(name)[1].lower()
    if ext not in SUPPORTED:
        return {"ok": False,
                "error": UNSUPPORTED.get(ext, "不支持的类型，请转成 .md 或 .txt")}
    try:
        raw = base64.b64decode(content_b64 or "", validate=False)
    except Exception:
        return {"ok": False, "error": "文件内容解码失败"}
    if not raw:
        return {"ok": False, "error": "文件是空的"}
    d = client_dir(cfg)
    path = os.path.join(d, name)
    try:
        with open(path, "wb") as f:
            f.write(raw)
    except Exception as e:
        return {"ok": False, "error": f"写入失败：{e}"}
    # 落盘后立刻重建索引，文件改了立刻能问
    res = build_index(cfg)
    res["uploaded"] = name
    return res


def reset(cfg=None):
    """清空客户资料目录与统计（演示版切客户时用）。"""
    d = client_dir(cfg)
    removed = 0
    for p, rel, ext in _walk_files(d):
        try:
            os.remove(p)
            removed += 1
        except Exception:
            pass
    save_state(cfg, {"hits": {}})
    return {"ok": True, "removed": removed, "dir": d}


if __name__ == "__main__":
    import sys
    cfg = cfgmod.load()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        r = scan(cfg)
        print("目录：", r["dir"])
        for d in r["docs"]:
            print("  ✔", d["name"], d["type"], d["size"], "字节")
        for s in r["skipped"]:
            print("  ✖", s["name"], "→", s["hint"])
        print(f"可用 {r['total']} 份，跳过 {r['skipped_total']} 份")
    elif cmd == "index":
        print(build_index(cfg))
    elif cmd == "ask":
        q = sys.argv[2] if len(sys.argv) > 2 else "这个产品多少钱"
        r = ask(cfg, q)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "stats":
        print(json.dumps(stats(cfg), ensure_ascii=False, indent=2))

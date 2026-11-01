"""知识库检索：纯本地，零依赖，无需 embedding。

用 TF-IDF + 中文二元切分做检索。理由：
1. 禁区清单是结构化表格，关键词精确匹配比语义检索更可靠
2. 条款判定依赖「判定关键词」字段命中，语义检索反而会漏
3. 本地检索不花钱、不联网、可离线运行

同时提供 ensure_platform_prefix / chunk_forbidden 两项专门为禁区设计的处理。
"""

import math
import os
import re
from collections import Counter

# 中文停用词
STOP = set("""的 了 和 是 在 我 有 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这
那 这个 那个 什么 怎么 为什么 可以 我们 他们 它 她 他 被 把 从 与 及 或 等 但 而 因为 所以 如果 就是 这样 那样
以及 并且 或者 或者 通过 进行 使用 需要 出现 存在 表示 认为 主要 相关 一般 属于 以及 等等 一些 一种 一样
""".split())


def tokenize(text):
    """中文按二元切分 + 英文数字按词切分。"""
    text = text.lower()
    tokens = []
    # 英文/数字
    for w in re.findall(r"[a-z0-9]+", text):
        if len(w) >= 2 or w.isdigit():
            tokens.append(w)
    # 中文连续段做二元切分
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) == 1:
            tokens.append(seg)
        else:
            for i in range(len(seg) - 1):
                tokens.append(seg[i:i + 2])
    return [t for t in tokens if t not in STOP]


class Document:
    def __init__(self, doc_id, source, title, text, meta=None):
        self.id = doc_id
        self.source = source
        self.title = title
        self.text = text
        self.meta = meta or {}
        self.tokens = Counter(tokenize(text))
        self.length = sum(self.tokens.values())


class KB:
    """轻量本地检索库。"""

    def __init__(self):
        self.docs = []
        self._df = Counter()
        # 可选的二次排序钩子：rerank(query, scored) -> scored
        #
        # 为什么留这个口子：纯 TF-IDF 只认字面，不认语义。
        # 「多少钱」这三个字在报价单里一个都没有，老板问价时答案会被排到后面。
        # 企业知识库场景必须补这一层，但规则要可解释、可关掉（不设就是原行为）。
        self.rerank = None

    def add(self, doc_id, source, title, text, meta=None):
        d = Document(doc_id, source, title, text, meta)
        self.docs.append(d)
        for t in d.tokens:
            self._df[t] += 1
        return d

    def add_file(self, path, source=None, meta=None):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return None
        name = os.path.basename(path)
        return self.add(name, source or path, name, text, meta)

    def add_dir(self, d, source=None, pattern=".md", meta=None, recursive=True):
        """加载目录下的文档。recursive=True 时递归子目录。"""
        if not os.path.isdir(d):
            return 0
        n = 0
        if recursive:
            for root, _dirs, files in os.walk(d):
                for fn in sorted(files):
                    if fn.endswith(pattern):
                        rel = os.path.relpath(os.path.join(root, fn), d)
                        if self.add_file(os.path.join(root, fn), source or d, meta):
                            n += 1
        else:
            for fn in sorted(os.listdir(d)):
                if fn.endswith(pattern):
                    if self.add_file(os.path.join(d, fn), source or d, meta):
                        n += 1
        return n

    def search(self, query, top_k=5, min_score=0.01):
        if not self.docs:
            return []
        q = tokenize(query)
        if not q:
            return []
        n = len(self.docs)
        idf = {}
        for t in set(q):
            idf[t] = math.log((n + 1) / (self._df.get(t, 0) + 1)) + 1

        scored = []
        for d in self.docs:
            score = 0.0
            for t in set(q):
                tf = d.tokens.get(t, 0)
                if tf:
                    score += idf[t] * (tf / (d.length ** 0.5 + 1e-9))
            scored.append((score, d))

        # 二次排序（可选）。context() 也走这里，所以重排序对拼上下文同样生效。
        #
        # 关键细节：候选池要不要先过阈值，取决于 rerank 能不能"捞回"文档。
        # 纯字面检索下，原始分为 0 的文档永远进不了候选池；
        # 但语义规则（如「保修」↔「质保」）恰恰能给原始分为 0 的文档加分。
        # 所以带 expand 标记的 rerank 会拿到**全部文档**作为候选池。
        rk = self.rerank
        if rk is not None and getattr(rk, "expand", False):
            pool = scored
        else:
            pool = [x for x in scored if x[0] > min_score]

        if rk:
            try:
                pool = rk(query, pool)
            except Exception:
                pool = [x for x in scored if x[0] > min_score]
        else:
            pool.sort(key=lambda x: -x[0])

        return [(d, s) for s, d in pool[:top_k] if s > min_score]

    def context(self, query, top_k=5, max_chars=4000):
        """把检索结果拼成可注入 prompt 的上下文块。

        注意：不能简单取每篇的前 N 字。
        问答库这类文档，关键答案（信号极性、故障码）常在文档中后段，
        硬截前 1200 字会把答案切掉 —— AI 检索到了却答不出来。
        所以改成「命中段优先 + 全文兜底」。
        """
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        q = set(tokenize(query))
        out = []
        total = 0
        for i, (d, s) in enumerate(hits, 1):
            body = self._excerpt(d, q, max_chars=1400)
            block = f"### [{i}] {d.title}（来源：{d.source}，相关度 {s:.2f}）\n{body}"
            if total + len(block) > max_chars:
                break
            out.append(block)
            total += len(block)
        return "\n\n".join(out)

    @staticmethod
    def _excerpt(doc, q, max_chars=1400):
        """按段落打分，挑出最相关的段落。

        命中的段落排前面，前面的内容才不会被挤掉。
        """
        text = doc.text or ""
        if len(text) <= max_chars:
            return text
        paras = [p for p in text.split("\n\n") if p.strip()]
        if not paras:
            return text[:max_chars]
        scored = []
        for i, p in enumerate(paras):
            toks = tokenize(p)
            hit = sum(1 for t in toks if t in q)
            # 命中词密度优先，同时略微偏好靠前的段落
            scored.append((hit / (len(toks) ** 0.5 + 1e-9) + (1.0 - i / len(paras)) * 0.02,
                           i, p))
        scored.sort(key=lambda x: -x[0])
        picked = sorted(scored[:12], key=lambda x: x[1])
        out = []
        n = 0
        for _s, _i, p in picked:
            if n + len(p) > max_chars:
                break
            out.append(p)
            n += len(p)
        body = "\n\n".join(out)
        if len(body) < max_chars // 2:
            # 相关段落太少，补上文档开头（通常有背景说明）
            head = text[: max_chars - len(body)]
            body = head.rstrip() + "\n\n" + body
        return body[:max_chars]


# ---------- 禁区专用处理 ----------

# 禁区清单里"判定关键词"列的占位符。
# 这些**不是**关键词，是"本条没有可自动判定的关键词"的意思。
# 早先版本没过滤，于是正文里任意一个破折号（——）都会命中所有空关键词条目，
# 一个合格包被判 27 处红线、0 分。这里一次性堵住。
_PLACEHOLDER_KW = {
    "—", "——", "－", "-", "–", "/", "／", "|", "无", "暂无", "略",
    "N/A", "n/a", "NA", "无。", "—", "空", "空关键词",
}


def clean_keywords(raw):
    """清洗"判定关键词"列：拆词 + 去占位符 + 去纯符号。"""
    out = []
    for k in re.split(r"[、,，/]", raw or ""):
        k = k.strip().strip("。").strip()
        if not k:
            continue
        if k in _PLACEHOLDER_KW:
            continue
        # 纯符号/纯标点的词没有判定价值
        if not re.search(r"[一-鿿A-Za-z0-9]", k):
            continue
        out.append(k)
    return out


def _scope_of(platform, section):
    """判定一条红线的**适用域**。

    这是审核能不能用对的关键。
    禁区清单里的条款其实是两类东西混在一张表里：

      · **内容发布域**（scope="publish"）
        管的是"你在小红书/抖音发一条内容时不能写什么"——
        标题党、擦边、私信导流、主图极限词……

      · **产品行为域**（scope="product"）
        管的是"这个软件/服务本身能不能做"——
        洗稿搬运、破解外挂、灰产工具、去标识服务、
        未经同意的批量外呼与群发短信……

    一份 SKILL.md 是**产品文档**，不是**要发到平台的内容**。
    所以内容发布域的条款扫 SKILL.md 属于范畴错配——
    "绝对"两个字出现在"## 绝对禁止"这种小节标题里，
    按"抖音标题党"去判它违规，是把审核做成了噪音。

    分流规则：
      · 国家政策 + 通用红线 + 获客红线 → product（法律底线，任何产物都必须干净）
      · 平台专属条款（公众号/小红书/抖音/闲鱼/淘宝）→ publish
    """
    p = platform or ""
    sec = section or ""
    if "国家政策" in p or "国家政策" in sec:
        return "product"
    if "跨平台通用" in sec or "通用红线" in sec or "获客" in sec:
        return "product"
    if "产品行为域" in sec:
        return "product"
    if p in ("公众号", "小红书", "抖音", "闲鱼", "淘宝"):
        return "publish"
    return "product"


# ---------- 否定语境守卫 ----------

# 否定词：出现在关键词**前面**且距离很近，说明文档在"禁止使用这个词"
_NEG_PREFIX = ("不", "勿", "禁", "拒绝", "避免", "杜绝", "非", "无", "未",
               "别", "少", "莫", "勿用", "禁止", "严禁", "不得", "不许",
               "无需", "不再", "远离", "杜绝使用", "切勿")
# 否定词：出现在关键词**后面**且距离很近，说明文档在"这个位置不许这么写"
_NEG_SUFFIX = ("禁用", "禁止", "不得", "勿用", "不可", "不许", "杜绝", "别用")


def _is_negated_context(content, pos, kw):
    """判断 content[pos:pos+len(kw)] 这个命中是否处于否定语境。

    返回 True = 否定语境（合规声明，不算违规命中）。
    """
    klen = len(kw)
    # 前窗口：关键词左侧 24 字
    pre = content[max(0, pos - 24):pos]
    # 后窗口：关键词右侧 16 字
    post = content[pos + klen: pos + klen + 16]

    # 规则 1：紧邻否定词（只取最后一个否定词后到关键词之间的短距离）
    for neg in _NEG_PREFIX:
        idx = pre.rfind(neg)
        if idx >= 0:
            between = pre[idx + len(neg):]
            # 否定词与关键词之间允许隔着少量词（如「不批量私信」「不群控刷量」），
            # 但不能跨过**分句**边界——
            #   「不做破解，能帮你脱壳」：逗号后是新分句，不再受前面的「不」约束。
            # 顿号（、）不算分句边界，它是同一句里的并列列举：
            #   「禁止使用群控、批量私信」——整串都在「禁止」的辖域内。
            if len(between) <= 10 and not re.search(r"[。；;！!？?，,]", between):
                return True

    # 规则 2：关键词被引号包住，且引号里是"不写X""别用X"这种举例
    for lq, rq in (("「", "」"), ("『", "』"), ("“", "”"), ("《", "》")):
        if pre.endswith(lq) and post.startswith(rq):
            return True

    # 规则 3：关键词后紧跟否定词（"绝对禁止""保证禁用"）
    for neg in _NEG_SUFFIX:
        if post.startswith(neg):
            return True

    # 规则 4：关键词处于括号内的禁止举例中
    #   例：不承诺效果（不写「保证」「100%」「必定」「稳赚」）
    #   这里"保证"前面隔着「100%」，不满足规则 2（引号不对称），
    #   但整段处在"不承诺效果（…）"的否定框架内。
    lb = max(pre.rfind("（"), pre.rfind("("))
    if lb >= 0:
        frame = content[max(0, lb - 30):lb]
        if any(n in frame for n in ("不承诺", "不写", "不说", "不用", "禁止",
                                    "严禁", "避免", "杜绝", "不得")):
            return True

    return False


def chunk_forbidden(text):
    """把禁区清单切成一条条红线。

    禁区清单的每条红线是一行表格：| 平台 | 生效日期 | 禁止行为 | 处罚后果 | 来源 | 判定关键词 |
    这里按行切，并带上所属平台小节标题，保证「条款+判定关键词+严重级别」不被拆散。
    """
    chunks = []
    current_section = ""
    current_level = ""
    in_table = False

    for line in text.split("\n"):
        s = line.strip()

        if s.startswith("## "):
            current_section = s[3:].strip()
            in_table = False
            continue
        if s.startswith("### "):
            current_level = s[4:].strip()
            in_table = False
            continue

        # 表头行
        if s.startswith("|") and "判定关键词" in s:
            in_table = True
            continue
        # 分隔行
        if s.startswith("|") and set(s) <= set("|-: "):
            continue

        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 6:
                continue
            platform, date, forbid, penalty, source, keywords = cells[:6]
            if not forbid:
                continue
            level = "灰区" if "灰区" in current_level else ("红线" if "红线" in current_level else "注意")
            chunk = (
                f"平台：{platform}\n"
                f"生效日期：{date}\n"
                f"禁止行为：{forbid}\n"
                f"处罚后果：{penalty}\n"
                f"来源：{source}\n"
                f"判定关键词：{keywords}\n"
                f"严重级别：{level}"
            )
            chunks.append({
                "platform": platform,
                "section": current_section,
                "level": level,
                "forbid": forbid,
                "penalty": penalty,
                "date": date,
                "source": source,
                "keywords": clean_keywords(keywords),
                "scope": _scope_of(platform, current_section),
                "text": chunk,
            })
    return chunks


class ForbiddenIndex:
    """禁区专用索引：支持按平台过滤 + 关键词命中判定。"""

    def __init__(self, entries):
        self.entries = entries

    @classmethod
    def from_dir(cls, d):
        entries = []
        if not os.path.isdir(d):
            return cls(entries)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                continue
            for c in chunk_forbidden(text):
                c["source_file"] = fn
                c["source_path"] = path
                entries.append(c)
        return cls(entries)

    def by_platform(self, platform=None):
        if not platform:
            return self.entries
        return [e for e in self.entries if platform in e["platform"]]

    def match(self, content, platform=None, level_filter=None, scope=None,
              negation_guard=True):
        """在内容里匹配红线。

        判定逻辑：命中「判定关键词」任一即算命中（这是清单的设计意图）。
        level_filter: None=全部 / "红线" / "灰区" / "注意"
        scope: None=全部 / "product"（商品本体）/ "publish"（发到平台的内容）
        negation_guard: 是否启用否定语境守卫（默认开）

        **否定语境守卫**是这套审核能不能用的关键。
        我们自己写的 SKILL.md 里必然出现「不写『保证』『100%』」「不刷量」
        「## 绝对禁止」这类**合规声明**——它们是关键词，但方向相反：
        清单说"不许写绝对"，我们的文档说"禁止用绝对"。
        不加守卫，这些会被判成违规，一个合格包直接 0 分。

        守卫规则（任一成立即视为否定语境，不算命中）：
          · 关键词前面紧邻否定词（不/勿/禁/拒绝/避免/杜绝/非/无/未）
          · 关键词被引号包住，且引号内是「不写…」「别用…」的举例
          · 关键词前面是「不承诺效果（…」这类括注列举
        """
        pool = self.by_platform(platform)
        if level_filter:
            pool = [e for e in pool if e["level"] == level_filter]
        if scope:
            pool = [e for e in pool if e.get("scope", "product") == scope]
        hits = []
        for e in pool:
            for kw in e["keywords"]:
                if not kw:
                    continue
                pos = content.find(kw)
                if pos < 0:
                    continue
                if negation_guard and _is_negated_context(content, pos, kw):
                    continue
                hits.append({**e, "hit_keyword": kw, "hit_pos": pos})
                break
        return hits

    def stats(self):
        from collections import Counter
        byplat = Counter(e["platform"] for e in self.entries)
        bylevel = Counter(e["level"] for e in self.entries)
        return {
            "total": len(self.entries),
            "by_platform": dict(byplat),
            "by_level": dict(bylevel),
        }

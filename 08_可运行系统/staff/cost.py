"""成本估算器：不调用 API，算出每个岗位、每条链路的 token 与人民币花费。

为什么单独写一个模块：
1. 用户需要充值前就知道「大概要花多少钱」，不能等真跑完才看账单
2. 每次调用的输入 token 里，system prompt（人设）是固定的，
   缓存命中后几乎免费；变化的主要是任务内容与知识库检索结果
3. 需要区分「单次典型调用」与「整条链路」，还要区分空闲/高峰单价

token 估算方法：
- 优先用 tiktoken（cl100k_base）精确编码；没装则退化为「中文按字数 ×0.7 + 英文按词」
- DeepSeek 用的是自己的 BPE，cl100k 会有偏差，因此估算结果乘一个 1.15 的
  安全系数，宁可高估也不要让用户充值后不够用
"""

import os
import re

# ---------- token 估算 ----------

_ENCODER = None
_ENCODER_TRIED = False


def _encoder():
    global _ENCODER, _ENCODER_TRIED
    if not _ENCODER_TRIED:
        _ENCODER_TRIED = True
        try:
            import tiktoken
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENCODER = None
    return _ENCODER


# DeepSeek 的分词器与 cl100k 不同，实测中文偏少，这里加安全系数
SAFETY = 1.15


def count_tokens(text):
    """估算一段文本的 token 数。"""
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        return int(len(enc.encode(text)) * SAFETY)
    # 退化算法：中文一字约 0.7 token，英文单词约 1.3 token
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    punct = len(re.findall(r"[^\w\s]", text))
    return int((cjk * 0.7 + words * 1.3 + punct * 0.5) * SAFETY)


# ---------- 单价 ----------

def price_table(cfg):
    return cfg.get("pricing") or {}


def unit_price(cfg, model, peak=False):
    """返回 (输入命中, 输入未命中, 输出) 三个单价，单位：元/百万 tokens。"""
    p = price_table(cfg).get(model)
    if not p:
        return (0.0, 0.0, 0.0)
    suffix = "peak" if peak else "offpeak"
    return (p.get("input_cache_hit_" + suffix, 0),
            p.get("input_miss_" + suffix, 0),
            p.get("output_" + suffix, 0))


def calc_cost(cfg, model, input_hit, input_miss, output, peak=False):
    """算一笔钱。返回元。"""
    ph, pm, po = unit_price(cfg, model, peak)
    return (input_hit * ph + input_miss * pm + output * po) / 1_000_000


# ---------- 每个岗位的典型用量 ----------

# 每个岗位跑一次「典型任务」时：
#   - system prompt：来自 Bot 提示词的人设正文（真实测量）
#   - 知识库注入：按 top_k=5 检索的实际长度
#   - 输出：按任务类型的典型产出量估计
#
# 输出量的估计依据（中文 1 token ≈ 1.4 字）：
TYPICAL_OUTPUT = {
    "A01": 800, "A02": 800, "A03": 1800, "A04": 1500, "A05": 1800,
    "A06": 1500, "A07": 3200, "A08": 3200, "A09": 3200, "A10": 2500,
    "A11": 3000, "A12": 1200, "A13": 3000, "A14": 1500, "A15": 1200,
    "A16": 2000, "A17": 2500, "A18": 1500, "A19": 2000, "A20": 3500,
    "A21": 2000,
}

# 各岗位在日常链路中的典型输入长度（用户任务 + 上一步产出 + 知识库）
TYPICAL_INPUT = {
    "A01": 300, "A02": 300, "A03": 300, "A04": 1800, "A05": 1500,
    "A06": 1200, "A07": 1200, "A08": 6000, "A09": 6000, "A10": 4500,
    "A11": 5000, "A12": 1200, "A13": 1500, "A14": 1000, "A15": 800,
    "A16": 2500, "A17": 1500, "A18": 1200, "A19": 1200, "A20": 1500,
    "A21": 1500,
}


def _role_meta(cfg):
    """读出每个岗位的层级、自动化等级、system prompt 长度。"""
    from . import roles as rolesmod
    reg = rolesmod.RoleRegistry(cfg["paths"]["roles"])
    out = {}
    for r in reg.all():
        out[r.code] = {
            "code": r.code,
            "name": r.name,
            "layer": r.layer,
            "automation": r.automation,
            "sys_tokens": count_tokens(r.persona),
            "sys_chars": len(r.persona),
        }
    return out


def _tier_of(cfg, code):
    """按岗位判断默认档位：质检/判定类走 judge，其余走 create。"""
    if code in ("A10", "A16"):
        return cfg.get("judge_tier", "judge")
    return "create"


def _resolve_tier(cfg, tier):
    """把档位展开成 (模型, 是否思考)。judge 档可被 judge_tier 覆盖。"""
    if tier == "judge":
        tier = cfg.get("judge_tier", "judge")
    model = cfg["model_tier"].get(tier, cfg["model"])
    thinking = bool(cfg.get("thinking_tier", {}).get(tier, False))
    return model, thinking, tier


def estimate_role(cfg, code, peak=False, kb_tokens=1200):
    """估算单个岗位跑一次典型任务的 token 与花费。"""
    meta = _role_meta(cfg).get(code)
    if meta is None:
        return None

    tier = _tier_of(cfg, code)
    model, thinking, tier = _resolve_tier(cfg, tier)

    sys_tok = meta["sys_tokens"]
    # 缓存命中：system prompt 前缀可命中；任务内容随每次变化 → 算未命中
    hit = sys_tok
    miss = TYPICAL_INPUT.get(code, 1000) + kb_tokens
    out = TYPICAL_OUTPUT.get(code, 1500)
    if thinking:
        # 开启思考模式，思维链额外约等于产出的 60%，且按输出价计费
        out = int(out * 1.6)

    cost = calc_cost(cfg, model, hit, miss, out, peak)
    return {
        **meta,
        "tier": tier,
        "model": model,
        "thinking": thinking,
        "input_hit": hit,
        "input_miss": miss,
        "output": out,
        "total_tokens": hit + miss + out,
        "cost": cost,
    }


def estimate_all_roles(cfg, peak=False):
    """21 个岗位逐个估算。"""
    out = []
    for code in sorted(_role_meta(cfg)):
        e = estimate_role(cfg, code, peak)
        if e:
            out.append(e)
    return out


# ---------- 整条链路 ----------

def estimate_daily_chain(cfg, peak=False):
    """8 步日常链路的费用。步骤与 pipeline.daily() 严格对应。"""
    steps = [
        ("A03", "情报采集", "scan"),
        ("A04", "素材整理", "scan"),
        ("A05", "选题生成", "create"),
        ("A07", "撰写初稿", "create"),
        ("A10", "合规质检", "judge"),
        ("A08", "按质检修改", "create"),
        ("A09", "去AI味定稿", "create"),
        ("A11", "多平台适配", "create"),
    ]
    rows = []
    for code, title, tier in steps:
        e = estimate_role(cfg, code, peak)
        # 链路里的档位以 pipeline 实际用的为准
        model, thinking, tier = _resolve_tier(cfg, tier)
        e["tier"] = tier
        e["model"] = model
        e["thinking"] = thinking
        out = TYPICAL_OUTPUT.get(code, 1500)
        if thinking:
            out = int(out * 1.6)
        e["output"] = out
        e["total_tokens"] = e["input_hit"] + e["input_miss"] + out
        e["cost"] = calc_cost(cfg, model, e["input_hit"], e["input_miss"], out, peak)
        e["title"] = title
        rows.append(e)
    total_tokens = sum(r["total_tokens"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    return {"steps": rows, "total_tokens": total_tokens, "total_cost": total_cost}


def estimate_product_chain(cfg, peak=False):
    """2 步商品链路的费用。"""
    rows = []
    plan = [("A13", "商品详情页", "create"), ("A10", "合规复核", "judge")]
    for code, title, tier in plan:
        e = estimate_role(cfg, code, peak)
        model, thinking, tier = _resolve_tier(cfg, tier)
        e["tier"] = tier
        e["model"] = model
        e["thinking"] = thinking
        out = TYPICAL_OUTPUT.get(code, 1500)
        if thinking:
            out = int(out * 1.6)
        e["output"] = out
        e["total_tokens"] = e["input_hit"] + e["input_miss"] + out
        e["cost"] = calc_cost(cfg, model, e["input_hit"], e["input_miss"], out, peak)
        e["title"] = title
        rows.append(e)
    return {"steps": rows,
            "total_tokens": sum(r["total_tokens"] for r in rows),
            "total_cost": sum(r["cost"] for r in rows)}


def compare_judge_modes(cfg, peak=False):
    """对比质检官不同档位的成本，给出性价比建议。"""
    out = []
    for tier_name, label in [("judge", "v4-pro + 思考（最高质量）"),
                             ("judge_econ", "flash + 思考（均衡，推荐）"),
                             ("judge_fast", "flash 不思考（最便宜）")]:
        test = dict(cfg)
        test["judge_tier"] = tier_name
        e = estimate_role(test, "A10", peak)
        if not e:
            continue
        out.append({"tier": tier_name, "label": label, "model": e["model"],
                    "thinking": e["thinking"], "total_tokens": e["total_tokens"],
                    "cost": e["cost"]})
    return out


def estimate_single_ask(cfg, code, peak=False, with_kb=True):
    """单次调用某个岗位（run.py ask）的费用。"""
    return estimate_role(cfg, code, peak, kb_tokens=1200 if with_kb else 0)

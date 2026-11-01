"""DeepSeek API 客户端 —— 零第三方依赖，只用标准库 urllib。

三个关键设计：
1. 模拟模式（simulate=True）：不调 API也能跑通全部链路逻辑，用于开发与验收
2. 余额/鉴权错误单独识别，给出能照做的修复指引，而不是抛异常
3. 成本记账：每次调用记录 token 消耗，防止"全量跑 21 个 Agent"烧钱
"""

import csv
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config as cfgmod


def is_peak_time(now=None):
    """判断当前是否处于 DeepSeek 高峰时段。

    高峰：北京时间周一至周五（不含中国法定节假日）9:00-12:00、14:00-18:00。
    其余时间（含周末与节假日）为空闲时段，价格为高峰的一半。
    这里只按工作日判断，未接入法定节假日日历——节假日按工作日算会略高估成本，
    属于保守估计。
    """
    now = now or datetime.now(timezone(timedelta(hours=8)))
    if now.weekday() >= 5:      # 5=周六6=周日
        return False
    h = now.hour + now.minute / 60
    return (9 <= h < 12) or (14 <= h < 18)


class LLMError(Exception):
    """调用失败。message 里已经包含可执行的修复建议。"""

    def __init__(self, message, hint="", status=0):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status


class BalanceError(LLMError):
    """余额不足。"""


class AuthError(LLMError):
    """密钥无效。"""


class RateLimitError(LLMError):
    """触发限流。"""


class TokenLedger:
    """Token 用量记账。

    记录四项：输入（命中缓存）/ 输入（未命中）/ 输出 / 推理链（thinking）。
    DeepSeek 的 usage 返回 prompt_tokens / completion_tokens /
    prompt_cache_hit_tokens / prompt_cache_miss_tokens，
    reasoning_content 的长度计入 completion_tokens。
    """

    def __init__(self, budget=200000, pricing=None, logs_dir=None):
        self.budget = budget
        self.pricing = pricing or {}
        self.logs_dir = logs_dir
        self.used = 0
        self.calls = 0
        self.cost = 0.0
        self.rows = []          # 每次调用的明细
        self.by_role = {}       # 按岗位聚合

    def record(self, usage, model="", role="", note=""):
        if not usage:
            return
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        hit = usage.get("prompt_cache_hit_tokens") or 0
        miss = usage.get("prompt_cache_miss_tokens")
        if miss is None:
            miss = max(prompt - hit, 0)
        total = usage.get("total_tokens") or (prompt + completion)

        self.used += total
        self.calls += 1

        c = self.price(model, hit, miss, completion)
        self.cost += c

        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "role": role or "-",
            "model": model,
            "input_hit": hit,
            "input_miss": miss,
            "output": completion,
            "total": total,
            "cost_yuan": round(c, 6),
            "note": note,
        }
        self.rows.append(row)

        k = row["role"]
        agg = self.by_role.setdefault(k, {"calls": 0, "total": 0, "cost": 0.0,
                                          "input_hit": 0, "input_miss": 0, "output": 0})
        agg["calls"] += 1
        agg["total"] += total
        agg["cost"] += c
        agg["input_hit"] += hit
        agg["input_miss"] += miss
        agg["output"] += completion

    def price(self, model, hit, miss, completion):
        """按官方单价换算人民币。未知模型返回 0。"""
        p = self.pricing.get(model)
        if not p:
            return 0.0
        peak = is_peak_time()
        k_hit = "input_cache_hit_" + ("peak" if peak else "offpeak")
        k_miss = "input_miss_" + ("peak" if peak else "offpeak")
        k_out = "output_" + ("peak" if peak else "offpeak")
        return (hit * p.get(k_hit, 0) + miss * p.get(k_miss, 0)
                + completion * p.get(k_out, 0)) / 1_000_000

    def check_budget(self):
        if self.budget and self.used >= self.budget:
            raise LLMError(
                f"今日 token 用量已达预算上限（{self.used}/{self.budget}）",
                "提高 config.json 的 daily_token_budget，或明天再跑。",
            )

    def check_wallet(self, cfg):
        """可选的余额闸：配置了 max_cost_yuan 就拦住超额调用。"""
        limit = cfg.get("max_cost_yuan") or 0
        if limit and self.cost >= limit:
            raise LLMError(
                f"本次会话花费已达上限（¥{self.cost:.4f} / ¥{limit}）",
                "调高 config.json 的 max_cost_yuan，或充值后再跑。",
            )

    def summary(self):
        s = f"{self.calls} 次调用 / {self.used} tokens / ¥{self.cost:.4f}"
        return s

    def dump_csv(self, path):
        """把明细写到 CSV，方便对账。"""
        if not path:
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        exists = os.path.exists(path)
        cols = ["time", "role", "model", "input_hit", "input_miss",
                "output", "total", "cost_yuan", "note"]
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            if not exists:
                w.writeheader()
            for r in self.rows:
                w.writerow(r)
        return path


LEDGER = TokenLedger()


def _post(url, payload, headers, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(messages, cfg=None, model=None, temperature=0.7, max_tokens=4000,
         system=None, json_mode=False, thinking=False, role="", note=""):
    """调用对话补全。

    返回 (内容, usage字典)。模拟模式返回占位内容并在内容前加标记。

    参数
    ----
    thinking : bool
        是否开启思考模式。开启后模型会先产生一段思维链，这部分计入
        输出 token（按输出价计费），成本约翻倍。只有判断类任务才值得开。
    role : str
        岗位编号，用于成本记账与后续对账。
    """
    cfg = cfg or cfgmod.load()
    model = model or cfg["model"]

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    if cfg.get("simulate"):
        return _simulate(msgs, model), {"total_tokens": 0, "simulate": True}

    if not cfg.get("api_key"):
        raise AuthError(
            "未配置 API Key",
            "在 config/config.json 填 api_key，或设置环境变量 DEEPSEEK_API_KEY。"
            "临时体验可用 STAFF_SIMULATE=1 启动。",
        )

    LEDGER.budget = cfg.get("daily_token_budget", 200000)
    LEDGER.pricing = cfg.get("pricing") or {}
    LEDGER.check_budget()
    LEDGER.check_wallet(cfg)

    # 思考模式下 reasoning_content 也计入 completion_tokens，
    # 会跟正文抢 max_tokens 预算。预算打满时模型只能输出推理链、正文为空。
    # 所以开思考就自动把上限放大（可配），保证正文有地方写。
    # DeepSeek 单次输出上限 8192，放大后不能超过这个数，否则请求会被拒。
    if thinking:
        mult = float(cfg.get("thinking_token_multiplier", 1.6))
        max_tokens = int(max_tokens * mult)
    max_tokens = min(int(max_tokens), int(cfg.get("max_output_tokens", 8192)))

    payload = {
        "model": model,
        "messages": msgs,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if thinking:
        # 官方：思考模式默认开启且 effort 默认 high，用 extra_body 显式传参
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = "high"
        # 思考模式不支持 temperature，删掉以免误以为生效
        payload.pop("temperature", None)

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["api_key"],
    }

    retries = cfg.get("max_retries", 3)
    backoff = cfg.get("retry_backoff", 5)
    last_err = None

    for attempt in range(1, retries + 1):
        try:
            data = _post(url, payload, headers, cfg.get("timeout", 180))
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            # DeepSeek 开思考模式时，正文有时落在 reasoning_content，
            # 或者正文为空但推理链有内容。这种情况不能当成功返回空串，
            # 否则下游会把空文件当成品交出去。
            reasoning = msg.get("reasoning_content") or ""
            if not content.strip() and reasoning.strip():
                content = reasoning
                if cfg.get("debug_empty_content"):
                    print("[llm] content 为空，已回退到 reasoning_content")
            if not content.strip():
                # 正文和推理链都是空：当成失败重试，
                # 否则会产出一个 0 字节文件，下游还以为干完了。
                last_err = LLMError(
                    f"模型返回空内容（第 {attempt} 次尝试）",
                    "重试仍为空的话，把 max_tokens 调大，或换更深的模型档位。",
                )
                if attempt < retries:
                    time.sleep(backoff)
                    continue
                raise last_err
            LEDGER.record(data.get("usage"), model=model, role=role, note=note)
            return content, data.get("usage") or {}
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "ignore")
            except Exception:
                pass
            code, msg = _parse_error(raw)
            status = e.code

            if status == 402 or "balance" in msg.lower():
                raise BalanceError(
                    f"账户余额不足（HTTP 402）",
                    "充值后即可使用：platform.deepseek.com → 充值。"
                    "如果你只想先看流程，可以设置 STAFF_SIMULATE=1 跑模拟模式。",
                    status=402,
                )
            if status in (401, 403):
                raise AuthError(
                    f"密钥无效或无权限（HTTP {status}）：{msg}",
                    "检查 config/config.json 的 api_key 是否完整、有无多余空格。",
                    status=status,
                )
            if status == 429:
                last_err = RateLimitError(
                    f"触发限流（HTTP 429）", "等待 60 秒后重试，或降低并发。", status=429
                )
                if attempt < retries:
                    time.sleep(backoff * attempt)
                    continue
                raise last_err
            if status >= 500:
                last_err = LLMError(f"服务端错误（HTTP {status}）：{msg}",
                                    "服务端临时故障，稍后重试。", status=status)
                if attempt < retries:
                    time.sleep(backoff * attempt)
                    continue
                raise last_err
            raise LLMError(f"请求失败（HTTP {status}）：{msg}", raw[:300], status=status)
        except urllib.error.URLError as e:
            last_err = LLMError(f"网络连接失败：{e.reason}",
                                "检查网络，或设置 proxies。", status=0)
            if attempt < retries:
                time.sleep(backoff * attempt)
                continue
            raise last_err
        except Exception as e:
            raise LLMError(f"调用异常：{type(e).__name__}: {e}", "", status=0)

    raise last_err or LLMError("调用失败，未知原因")


def _parse_error(raw):
    try:
        d = json.loads(raw)
        err = d.get("error", {})
        return err.get("code", ""), err.get("message", raw[:200])
    except Exception:
        return "", raw[:200]


def _simulate(messages, model):
    """模拟模式：按岗位角色返回结构化的占位内容。

    目的不是骗人，而是让链路逻辑、文件读写、状态流转在无 API 情况下可被完整验收。
    """
    user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user = m.get("content", "")
            break
    head = f"[模拟模式·{model}]\n"
    body = (
        "（本内容由模拟模式生成，未调用真实模型）\n\n"
        f"收到的输入长度：{len(user)} 字符\n"
        "系统链路正常：提示词已加载、上下文已组装、输出已捕获。\n\n"
        "配置真实 API Key 后重跑同一条命令，即可得到真实产出。"
    )
    return head + body


def health_check(cfg=None):
    """探活：返回 (是否可用, 说明文字)。"""
    cfg = cfg or cfgmod.load()
    if cfg.get("simulate"):
        return True, "模拟模式（未调用真实 API）"
    if not cfg.get("api_key"):
        return False, "未配置 API Key"
    try:
        txt, _ = chat(
            [{"role": "user", "content": "回复：OK"}],
            cfg=cfg, max_tokens=10, temperature=0.1,
        )
        return True, f"连通正常，模型返回：{txt.strip()[:20]}"
    except BalanceError as e:
        return False, f"余额不足 —— {e.hint}"
    except LLMError as e:
        return False, f"{e.message} —— {e.hint}"

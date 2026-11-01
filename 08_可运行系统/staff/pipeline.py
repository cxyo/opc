"""流水线编排：把岗位串成端到端链路。

两条链路：
1. daily —— 日常内容链路（选题→撰写→质检→改稿→去AI味→发布稿）
2. product —— 商品链路（卖点提炼→详情页→上架检查）

每一步都落盘到 data/outputs/<日期>/，可追溯、可回看。
"""

import csv
import json
import os
import re
import time
from datetime import datetime

from . import kb as kbmod
from . import llm
from . import roles as rolesmod
from . import telemetry


def _live(fn_name, *args, **kw):
    """发一条实时看板事件。

    单独包一层的原因：
      埋点绝不能把主流程搞挂（AI 调用成功但看板报错 = 白白花钱），
      但也不能静默失败（埋点坏了没人知道）。
      所以这里捕获异常后记到 live 模块的失败列表，概览页能显示。
    """
    try:
        from staff import live as livemod
        fn = getattr(livemod, fn_name)
        return livemod.safe(fn, *args, **kw)
    except Exception as e:
        try:
            from staff import live as livemod
            livemod.note_error(f"{fn_name}: {type(e).__name__}: {e}")
        except Exception:
            pass
        return None


class Pipeline:
    def __init__(self, cfg=None):
        self.cfg = cfg
        self.registry = rolesmod.RoleRegistry(self.cfg["paths"]["roles"])
        self.forbidden = kbmod.ForbiddenIndex.from_dir(self.cfg["paths"]["forbidden"])
        self.kb = kbmod.KB()
        self.kb.add_dir(self.cfg["paths"]["seeds"], source="知识库种子")
        self.kb.add_dir(self.cfg["paths"]["kb_wiki"], source="知识库wiki")
        # 行业包知识库：跟着当前行业方案走。
        # 不加这段的话，切到「头盔厂交付」问设备问题，AI 检索到的还是
        # 通用内容运营资料，然后回答「本岗位无该领域资料」—— 行业方案就白做了。
        self.industry_kb = self._load_industry_kb()
        self.outdir = self.cfg["paths"]["outputs"]

    def _load_industry_kb(self):
        """加载当前行业包的知识库。返回 (条数, 目录列表)。"""
        try:
            from . import industry as indmod
            pack = indmod.current_pack(self.cfg)
            key = pack.get("key")
            if not key or key == "general":
                return 0, []
            dirs = indmod.knowledge_dirs(self.cfg, key)
            n = 0
            for d in dirs:
                n += self.kb.add_dir(d, source=f"行业包·{key}")
            return n, dirs
        except Exception:
            return 0, []

    def _kb_query(self, code, task):
        """构造检索关键词。

        原来的写法是硬编码「AI提效 内容生产 提示词 知识库 工作流」，
        换行业后这个串就完全不相关了。
        现在用「岗位快速问题 + 当前任务 + 行业语气关键词」三段拼，
        让检索跟着人话走。
        """
        parts = [task or ""]
        try:
            role = self.registry.get(code)
            if role:
                # 岗位名 + 快速问题示例（代表这个岗位常查的东西）
                parts.append(role.name or "")
                for q in (role.quick_questions or [])[:3]:
                    parts.append(q)
        except Exception:
            pass
        try:
            from . import industry as indmod
            pack = indmod.current_pack(self.cfg)
            for kw in (pack.get("keywords") or pack.get("knowledge") or []):
                parts.append(str(kw))
            # 行业包步骤链里的岗位描述也能当关键词
            for s in (pack.get("chain") or []):
                parts.append(str(s.get("name") or ""))
                parts.append(str(s.get("desc") or ""))
        except Exception:
            pass
        return " ".join(p for p in parts if p)[:600]

    # ---------- 基础 ----------

    def _tier_model(self, tier):
        """档位 → 模型名。judge 档可被 config的 judge_tier 覆盖。"""
        if tier == "judge":
            tier = self.cfg.get("judge_tier", "judge")
        return self.cfg["model_tier"].get(tier, self.cfg["model"])

    def _tier_thinking(self, tier):
        """档位 → 是否开思考模式。"""
        if tier == "judge":
            tier = self.cfg.get("judge_tier", "judge")
        return bool(self.cfg.get("thinking_tier", {}).get(tier, False))

    def run_role(self, code, task, extra_kb="", tier="create", temperature=0.7,
                 max_tokens=4000, save_as=None, system_extra="", subdir=None,
                 use_profile=True):
        """调用单个岗位。返回 (内容, 输出文件路径或None)。

        每次调用都会在工作台账（telemetry.jsonl）里留一笔，
        面板 `run.py board` 就是读这份台账判断谁在干活、干得好不好。

        subdir：输出子目录。每日链路传日期，保证每次运行的文件不互相覆盖。
        use_profile：是否自动注入人设档案（默认注入）。
        """
        role = self.registry.get(code)
        sys_p = role.system_prompt(extra_kb)
        # 行业包可以自带岗位人设，覆盖通用人设。
        # 为什么需要：岗位编号带着内容生产体系的历史包袱，
        # 换到机器人行业后，「培训交付员」如果还用内容行业的人设，
        # 就会答出「请提供账号定位卡」这种驴唇不对马嘴的内容。
        # 行业包里有 岗位提示词/A19_xxx.md 就用它，没有就用通用的。
        try:
            from staff import industry as indmod
            _ov = indmod.role_overrides(self.cfg).get(code)
            if _ov:
                sys_p = _ov
        except Exception:
            pass
        # 检索知识库并注入。
        # 不注入的话，AI 只能靠通用大模型知识瞎答，切了行业包也没用。
        # 检索关键词跟着岗位 + 当前任务 + 行业走（见 _kb_query）。
        _kb_ctx = ""
        try:
            _kb_ctx = self.kb.context(self._kb_query(code, task), top_k=8)
        except Exception:
            _kb_ctx = ""
        if _kb_ctx:
            sys_p += ("\n\n---\n\n"
                      "【你的专属知识库 · 当前行业方案的实机资料】\n"
                      "下面是你这一行当口的真实资料（现场实测 / 官方手册摘录 / "
                      "故障处理记录）。回答必须以它为准，"
                      "资料里没有的不要编，缺资料就明说。\n\n"
                      + _kb_ctx)
        # 人设档案：所有岗位共享的「共同记忆」，优先级最高
        if use_profile:
            try:
                from . import profile as profmod
                arch = profmod.profile_context(self.cfg)
                if arch:
                    sys_p += "\n\n---\n\n" + arch
            except Exception:
                pass
        if system_extra:
            sys_p += "\n\n" + system_extra
        thinking = self._tier_thinking(tier)
        model = self._tier_model(tier)

        rec = telemetry.Recorder(self.cfg["paths"]["logs"])
        rec.begin(code, task[:60], tier=tier, model=model)

        before = llm.LEDGER.calls
        before_tok = llm.LEDGER.used
        before_cost = llm.LEDGER.cost

        # 实时看板埋点：所有 AI 调用都从这里过，所以埋这里最准
        _t0 = time.time()
        # 岗位名按当前行业显示：头盔厂里 A13 是「设备档案员」，
        # 不是内容体系里的「商品官」。
        try:
            from staff import live as livemod
            _role_name = livemod.role_name(code, getattr(role, "name", "") or code)
        except Exception:
            _role_name = getattr(role, "name", "") or code
        _live("emit_ai", code, _role_name, "running",
              msg=f"调用 {_role_name}（{model}）",
              data={"model": model, "tier": tier, "task": task[:60]})

        try:
            content, usage = llm.chat(
                [{"role": "user", "content": task}],
                cfg=self.cfg, model=model,
                temperature=temperature, max_tokens=max_tokens, system=sys_p,
                thinking=thinking, role=code,
            )
        except Exception as e:
            rec.fail(e, issues=[f"调用失败：{type(e).__name__}"])
            _live("emit_ai", code, _role_name, "fail",
                  msg=f"{_role_name} 调用失败：{type(e).__name__}",
                  data={"error": str(e)[:200]},
                  elapsed=int((time.time() - _t0) * 1000))
            raise

        path = None
        if save_as:
            path = self.save(save_as, content, subdir=subdir)

        # 这一次调用消耗了多少（LEDGER 是全局累加的，用前后差值切出来）
        used_calls = max(llm.LEDGER.calls - before, 1)
        tok = llm.LEDGER.used - before_tok
        cost = llm.LEDGER.cost - before_cost
        issues = telemetry.detect_issues(content)
        # completion 打满 max_tokens = 很可能被截断
        used_completion = int(usage.get("completion_tokens") or 0)
        if used_completion >= max_tokens * 0.98:
            issues.append(f"输出可能被截断（completion {used_completion} 已达上限 {max_tokens}）")
        rec.end(status="ok", tokens=tok,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cost_yuan=cost, output_chars=len(content or ""),
                output_file=path or "", issues=issues)

        # 成功事件：带上耗时与花费，看板要显示
        _live("emit_ai", code, _role_name, "ok",
              msg=f"{_role_name} 完成，{len(content or '')} 字"
                  + (f"，产出 {os.path.basename(path)}" if path else ""),
              data={"chars": len(content or ""), "cost": round(cost, 6),
                    "tokens": tok, "model": model,
                    "file": os.path.basename(path) if path else ""},
              elapsed=int((time.time() - _t0) * 1000))

        return content, path

    def save(self, name, content, subdir=None):
        d = os.path.join(self.outdir, subdir) if subdir else self.outdir
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    # ---------- 链路 1：日常内容 ----------

    def daily(self, topic=None, date=None, auto_publish=False):
        """跑完一篇文章的完整链路。

        流程：
          A03 情报官 → A04 素材官 → A05 选题官 → [人工选或自动取首条]
          A07 撰稿官 → A10 质检官 → A08 改稿官 → A09 去AI味官 → A11 发布官
        """
        date = date or datetime.now().strftime("%Y%m%d")
        outdir = os.path.join(self.outdir, date)
        os.makedirs(outdir, exist_ok=True)
        log = []

        # 本次运行的编号：让面板能把 8 步归成一组，看出"链路有没有跑完"
        run_id = telemetry.set_run(telemetry.new_run_id("daily"))
        started = datetime.now()

        def step(n, code, title, task, **kw):
            print(f"  [{n}/8] {code} {title} ...", flush=True)
            # 输出存进本次日期目录，避免多次运行互相覆盖
            content, path = self.run_role(code, task, save_as=f"{n}_{code}_{title}.md",
                                         subdir=date, **kw)
            log.append({"step": n, "code": code, "title": title, "file": path})
            return content

        # A03 情报
        intel = step(1, "A03", "情报采集",
                     "采集今天与「AI提效/内容生产」相关的热点情报。列出 5 条，每条含："
                     "标题、来源、核心观点、对内容创作者的价值。\n"
                     "如果今天没有明显热点，就输出近期仍然有效的 5 条长期话题。",
                     tier="scan", temperature=0.5, max_tokens=2000)

        # A04 素材
        kb_ctx = self.kb.context(self._kb_query("A04", topic or "素材整理"),
                                  top_k=8)
        material = step(2, "A04", "素材整理",
                        "把下面的情报整理成可复用的素材卡。每张卡含：素材标题、"
                        "核心内容、可用在什么选题、需要我补充的真实经历（明确标出）。\n\n"
                        f"【情报】\n{intel}\n\n"
                        f"【知识库】\n{kb_ctx or '（暂无）'}",
                        tier="scan", temperature=0.5, max_tokens=2000)

        # A05 选题
        topic_pool = self._read_topic_pool()
        topic_raw = step(3, "A05", "选题生成",
                         "基于下面素材，生成 5 个候选选题。每个选题含：选题标题、"
                         "一句话切口、目标读者、四维评分（需求强度/时效性/我方优势/合规风险，满分各10）、"
                         "预估难度。\n\n"
                         f"【素材】\n{material}\n\n"
                         f"【已有选题池（避免重复）】\n{topic_pool or '（空）'}",
                         tier="create", temperature=0.8, max_tokens=2500)

        # 选题：用户指定就用指定的，否则取第一条
        chosen = topic or self._first_topic(topic_raw)
        print(f"      → 选定选题：{chosen[:60]}", flush=True)

        # A07 撰写
        position = self._read_seed("账号定位卡")
        fingerprint = self._read_seed("风格指纹卡")
        draft = step(4, "A07", "撰写初稿",
                     f"请围绕选题《{chosen}》写一篇公众号文章。\n\n"
                     f"【账号定位】\n{position or '（未填，用通用写法：帮想用AI提效的人把AI变成能用的方法）'}\n\n"
                     f"【风格指纹】\n{fingerprint or '（未填，要求：口语化、短句、不用排比堆砌、有具体细节）'}\n\n"
                     "输出：正文（1500-3000字，Markdown，含小标题）+ 10个候选标题 + 素材使用说明。\n"
                     "凡是需要我真实经历的地方，用 【待补：xxx】 标出来，不要编造。",
                     tier="create", temperature=0.8, max_tokens=8000)

        # A10 质检（带禁区）
        hits = self.forbidden.match(draft)
        forbidden_ctx = self._forbidden_context(hits)
        report = step(5, "A10", "合规质检",
                      "对下面这篇初稿做三合一检测：违规检测、AI味检测、错误检测。\n"
                      "输出格式：\n"
                      "## 一、违规检测\n逐条列出命中项，标注【严重】/【注意】，没有就写「未发现违规」\n"
                      "## 二、AI味检测\n列出所有AI痕迹句（原文引用），给出改法\n"
                      "## 三、错误检测\n列出事实错误、逻辑矛盾、表述含糊处\n"
                      "## 四、修改建议（按优先级排序，最多10条）\n"
                      "## 五、结论\n给出 【通过】/【修改后通过】/【不通过】 和英文 verdict_code\n\n"
                      f"【已命中的红线（系统预检结果，需你复核）】\n{forbidden_ctx or '系统预检未命中，但不代表无违规，请独立判断'}\n\n"
                      f"【初稿】\n{draft}",
                      extra_kb=forbidden_ctx, tier="judge", temperature=0.3, max_tokens=4000)

        # A08 改稿
        fixed = step(6, "A08", "按质检修改",
                     "根据质检报告修改初稿，输出修改后的完整正文（保留 Markdown 格式）。\n"
                     "要求：逐条落实修改建议；不删掉原有的真实细节；不要新增任何编造的事实。\n\n"
                     f"【初稿】\n{draft}\n\n【质检报告】\n{report}",
                     tier="create", temperature=0.6, max_tokens=8000)

        # A09 去AI味
        final = step(7, "A09", "去AI味定稿",
                     "给下面这篇稿子去AI味，输出最终定稿。\n"
                     "要求：\n"
                     "1. 拆掉所有排比句、三段式对仗、「不仅…而且…」这类AI腔\n"
                     "2. 把书面语改成口语，允许句子长短不齐\n"
                     "3. 检查每段开头——AI最爱用「在当今这个…」式起手，改成直接说事\n"
                     "4. 保留所有具体数字、细节、真实经历\n"
                     "5. 不要为了「像人」而改掉准确的技术表述\n\n"
                     f"【稿子】\n{fixed}",
                     tier="create", temperature=0.9, max_tokens=8000)

        # A11 发布稿
        platforms = self._read_seed("平台规则库")
        publish = step(8, "A11", "多平台适配",
                       "把下面定稿改写成 3 个平台的版本：公众号（1500-3000字，深度论证）、"
                       "小红书（300-600字，短句+emoji+标签）、抖音（口播稿60秒，标注分镜）。\n"
                       "每个平台单独输出，含建议发布时间。\n\n"
                       f"【平台规则】\n{platforms or '（未填，用通用规则）'}\n\n【定稿】\n{final}",
                       tier="create", temperature=0.7, max_tokens=7000)

        # 汇总
        summary = [
            f"# {date} 流水线产出",
            "",
            f"- 选题：{chosen}",
            f"- 链路：8 步全部完成",
            f"- token 用量：{llm.LEDGER.summary()}",
            "",
            "## 人工闸（必须你确认）",
            "- [ ] 终稿质量：读一遍定稿，不像人写的就重跑 A09",
            "- [ ] AI 声明：发布时勾选「AI 辅助创作」标识（2025-09-01 起的强制要求）",
            "- [ ] 【待补】标记：文中所有 【待补：xxx】 都要填成你的真实经历",
            "- [ ] 禁区复核：质检官报告里的命中项，你逐条确认是否真违规",
            "",
            "## 文件",
        ]
        for item in log:
            summary.append(f"- `{os.path.basename(item['file'])}`")
        self.save(f"0_汇总_{date}.md", "\n".join(summary), subdir=date)

        # 追加发布日志
        self._append_publish_log(date, chosen)

        return {
            "date": date, "topic": chosen, "outdir": outdir,
            "files": [i["file"] for i in log],
            "summary": os.path.join(outdir, f"0_汇总_{date}.md"),
            "final": os.path.join(outdir, "7_A09_去AI味定稿.md"),
        }

    # ---------- 链路 2：商品 ----------

    def product(self, product_name=None, price="49"):
        """资料包详情页生成链路：A13 商品官 + A10 质检。"""
        name = product_name or "AI内容流水线实战包"
        value_card = self._read_seed("资料包价值设计卡")
        decision = self._read_seed("产品设计决策表")

        print(f"  [1/2] A13 商品官 生成详情页 ...", flush=True)
        detail, p1 = self.run_role(
            "A13",
            f"为资料包《{name}》（定价 {price} 元）写一份可直接上架的商品详情页。\n"
            "包含：标题（含关键词）、5条核心卖点、目录树、适合谁、不适合谁、"
            "购买须知、常见问题、交付方式与更新承诺、免责声明。\n"
            "要求：卖点要具体到「你能拿走什么」，不要空话；\n"
            "「不适合谁」这一段必须诚实写，这是降低退款率的关键。\n\n"
            f"【价值设计卡】\n{value_card or '（未填）'}\n\n"
            f"【产品决策表】\n{decision or '（未填）'}",
            tier="create", temperature=0.7, max_tokens=5000,
            save_as=f"商品详情页_{name}.md")

        print(f"  [2/2] A10 质检官 合规复核 ...", flush=True)
        hits = self.forbidden.match(detail)
        fctx = self._forbidden_context(hits)
        check, p2 = self.run_role(
            "A10",
            f"复核这份商品详情页的合规性，重点查：虚假承诺、绝对化用语（最/第一/国家级）、"
            f"AI 声明义务、版权风险。\n"
            f"逐条给出是否需要改写，改写后给出正确表述。\n\n"
            f"【预检命中】\n{fctx or '系统预检未命中'}\n\n【详情页】\n{detail}",
            extra_kb=fctx, tier="judge", temperature=0.3, max_tokens=3000,
            save_as=f"商品合规复核_{name}.md")

        return {"detail": p1, "check": p2}

    # ---------- 检索工具 ----------

    def search(self, query, top_k=5):
        return self.kb.search(query, top_k=top_k)

    def check_forbidden(self, content, platform=None):
        hits = self.forbidden.match(content, platform=platform)
        return hits

    # ---------- 内部工具 ----------

    def _forbidden_context(self, hits):
        if not hits:
            return ""
        parts = []
        for h in hits[:10]:
            parts.append(f"【{h['level']}】{h['platform']} · {h['forbid']}\n"
                         f"处罚：{h['penalty']}\n判定关键词：{', '.join(h['keywords'])}\n"
                         f"来源：{h['source_file']}")
        return "\n\n".join(parts)

    def _read_seed(self, name):
        p = os.path.join(self.cfg["paths"]["seeds"], name + ".md")
        if not os.path.exists(p):
            return ""
        with open(p, "r", encoding="utf-8") as f:
            return f.read()

    def _read_topic_pool(self):
        p = os.path.join(self.cfg["paths"]["state"], "选题池.csv")
        if not os.path.exists(p):
            return ""
        try:
            with open(p, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            return "\n".join(f"- {r.get('标题', '')}（已用：{r.get('状态', '')}）" for r in rows[:20])
        except Exception:
            return ""

    def _first_topic(self, raw):
        m = re.search(r"选题标题[：:]\s*(.+)", raw)
        if m:
            return m.group(1).strip().strip("《》\"'")
        m = re.search(r"《(.+?)》", raw)
        if m:
            return m.group(1)
        lines = [l.strip() for l in raw.split("\n") if l.strip().startswith(("1.", "2.", "-", "•"))]
        return lines[0][:60] if lines else raw[:60].strip()

    def _append_publish_log(self, date, topic):
        p = os.path.join(self.cfg["paths"]["state"], "publish_log.csv")
        exists = os.path.exists(p)
        with open(p, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["日期", "选题", "平台", "状态", "阅读", "点赞", "涨粉",
                            "成交", "备注"])
            w.writerow([date, topic, "公众号", "待发布", "", "", "", "", "流水线生成"])

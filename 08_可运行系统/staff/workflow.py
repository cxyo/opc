"""一键成稿工作流：像 Coze 那样点一下，东西全出来。

对比原来的 daily 链路：
    原来：8 步只产出 8 个 .md 文件，图没有、视频没有、公众号稿没有
    现在：8 步 + 自动配图 + 自动排版 + 发布包，产出物是一套可直接发的东西

三种工作流：
    article  一篇文章全套（选题→写稿→质检→改稿→去AI味→配图→排版→发布包）
    visual   只要视觉素材（给已有文章配图配视频）
    product  商品详情页（含配图）

设计原则：
    - 每一步都落盘，中途失败不丢前面的成果
    - 人审闸口保留：AI 产出后不自动发，等人点
    - 人设档案全程注入，产出风格跟着档案走
"""

import os
import re
import time
from datetime import datetime

from . import media as mediamod
from . import pipeline as piplmod
from . import profile as profmod
from . import publish as pubmod
from . import telemetry


# 步骤 key → 中文名。实时看板要显示人话，不能显示 intel/material 这种英文 key。
_STEP_NAMES = {
    "intel": "情报采集", "material": "素材整理", "topic": "选题确定",
    "draft": "撰稿", "check": "质量检查", "fix": "改稿",
    "human": "人工确认", "adapt": "去AI味", "media": "配图与视频",
    "package": "公众号排版",
}

# 工作流内部状态 → 看板状态
_STATUS_MAP = {
    "进行中": "running", "完成": "ok", "失败": "fail", "重试": "retry",
}


def _live(fn_name, *args, **kw):
    """发一条实时看板事件（失败不影响工作流，但会留痕）。"""
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


class Workflow:
    """一键成稿的执行器。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.pipe = piplmod.Pipeline(cfg)
        self.outdir = cfg["paths"]["outputs"]

    # ---- 步骤定义（给前端看进度用）----
    STEPS = [
        {"key": "intel", "code": "A03", "name": "情报采集", "auto": True},
        {"key": "material", "code": "A04", "name": "素材整理", "auto": True},
        {"key": "topic", "code": "A05", "name": "选题生成", "auto": True},
        {"key": "draft", "code": "A07", "name": "撰写初稿", "auto": True},
        {"key": "check", "code": "A10", "name": "合规质检", "auto": True},
        {"key": "fix", "code": "A08", "name": "按质检修改", "auto": True},
        {"key": "human", "code": "A09", "name": "去AI味定稿", "auto": True},
        {"key": "adapt", "code": "A11", "name": "多平台适配", "auto": True},
        {"key": "media", "code": "A22", "name": "生成图片视频", "auto": False},
        {"key": "package", "code": "A22", "name": "排版发布包", "auto": False},
    ]

    def _sub(self, name):
        """本次运行的输出子目录：按主题命名，不按日期堆。

        同名目录已存在时追加序号，绝不覆盖旧产出。
        """
        if not name:
            name = "文章_未命名成稿"
        base = name
        i = 2
        while os.path.isdir(os.path.join(self.outdir, name)):
            name = f"{base}_{i}"
            i += 1
        return name

    def _save(self, sub, filename, content):
        d = os.path.join(self.outdir, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, filename)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def run_article(self, topic=None, theme="tech", with_video=False,
                    with_cards=True, publish_package=True,
                    progress=None, run_id=None):
        """跑完整的一篇文章工作流。

        topic：指定选题；不指定就由 A05 生成后取第一条。
        返回一份完整的进度与产出清单。
        """
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        # 目录名不许带日期数字（规划师 R1）：选题为空时用「未命名成稿」兜底，
        # 靠 _sub 的去重序号区分，也不靠时间戳。
        base_name = self._safe_name(topic) if topic else self._safe_name("未命名成稿")
        sub = self._sub(base_name)
        outdir = os.path.join(self.outdir, sub)
        os.makedirs(outdir, exist_ok=True)

        run_id = run_id or telemetry.new_run_id("wf")
        telemetry.set_run(run_id)
        started = time.time()

        state = {
            "run_id": run_id, "subdir": sub, "outdir": outdir,
            "topic": topic or "", "theme": theme,
            "steps": [], "files": [], "media": {}, "package": {},
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cost_yuan": 0.0,
        }

        def emit(step_key, status, detail=""):
            if progress:
                progress(step_key, status, detail)
            # 实时看板：把每一步的进出都广播出去
            _live("emit_step", step_key, _STEP_NAMES.get(step_key, step_key),
                  _STATUS_MAP.get(status, status), msg=detail,
                  data={"detail": detail})

        def done(step_key, name, code, path, chars, cost):
            state["steps"].append({
                "key": step_key, "name": name, "code": code, "status": "完成",
                "file": path, "chars": chars, "cost": cost,
            })
            if path:
                state["files"].append(path)
            state["cost_yuan"] = round(state["cost_yuan"] + cost, 6)
            emit(step_key, "完成", os.path.basename(path or ""))

        def fail(step_key, name, code, err):
            state["steps"].append({
                "key": step_key, "name": name, "code": code, "status": "失败",
                "error": str(err),
            })
            emit(step_key, "失败", str(err))

        def call(code, task, attempts=2, **kw):
            """带重试的岗位调用。

            首次调用偶尔会因为网络抖动或超时失败，
            而一步失败会污染后面所有步骤，所以这里自动重试一次。

            重试这件事必须让用户看见 —— 看板会显示「第几次重试」。
            """
            last = None
            for i in range(attempts):
                try:
                    return self.pipe.run_role(code, task, **kw)
                except Exception as e:
                    last = e
                    if i + 1 < attempts:
                        time.sleep(3)
                        _live("emit_ai", code, code, "retry",
                              msg=f"{code} 第 {i + 1} 次调用失败，"
                                  f"3 秒后自动重试（{type(e).__name__}）",
                              data={"attempt": i + 1, "max": attempts,
                                    "error": str(e)[:160]})
            raise last

        # 人设档案 —— 全程注入
        arch = profmod.profile_context(self.cfg)
        prof = profmod.load_profile(self.cfg)
        brand = (prof.get("persona") or {}).get("name") or ""
        position = (prof.get("positioning") or {}).get("value_prop") or ""
        fingerprint = (prof.get("style") or {}).get("tone") or ""

        before_cost = self.pipe and 0.0
        from . import llm

        # ---------- 1. 情报 ----------
        emit("intel", "进行中")
        try:
            c0 = llm.LEDGER.cost
            intel, p = call(
                "A03",
                "采集今天与「AI提效/内容生产」相关的热点情报。列出 5 条，每条含："
                "标题、来源、核心观点、对内容创作者的价值。\n"
                "如果今天没有明显热点，就输出近期仍然有效的 5 条长期话题。",
                system_extra=arch, tier="scan", temperature=0.5, max_tokens=2000,
                save_as="1_情报采集.md", subdir=sub)
            done("intel", "情报采集", "A03", p, len(intel or ""), llm.LEDGER.cost - c0)
        except Exception as e:
            fail("intel", "情报采集", "A03", e)
            intel = ""

        # ---------- 2. 素材 ----------
        emit("material", "进行中")
        try:
            c0 = llm.LEDGER.cost
            kb_ctx = self.pipe.kb.context("AI提效 内容生产 知识库 工作流", top_k=5)
            material, p = call(
                "A04",
                "把下面的情报整理成可复用的素材卡。每张卡含：素材标题、核心内容、"
                "可用在什么选题、需要补充的真实经历（明确标出）。\n\n"
                f"【情报】\n{intel}\n\n"
                f"【知识库】\n{kb_ctx or '（暂无）'}",
                system_extra=arch, tier="scan", temperature=0.5, max_tokens=2000,
                save_as="2_素材整理.md", subdir=sub)
            done("material", "素材整理", "A04", p, len(material or ""),
                 llm.LEDGER.cost - c0)
        except Exception as e:
            fail("material", "素材整理", "A04", e)
            material = ""

        # ---------- 3. 选题 ----------
        emit("topic", "进行中")
        try:
            c0 = llm.LEDGER.cost
            pool = self.pipe._read_topic_pool()
            can_write = (prof.get("topics") or {}).get("can_write", "")
            topic_raw, p = call(
                "A05",
                "基于下面素材，生成 5 个候选选题。每个选题含：选题标题、一句话切口、"
                "目标读者、四维评分（需求强度/时效性/我方优势/合规风险，满分各10）、"
                "预估难度。\n\n"
                f"【素材】\n{material}\n\n"
                f"【我方可写方向】\n{can_write or '（未填）'}\n\n"
                f"【已有选题池（避免重复）】\n{pool or '（空）'}",
                system_extra=arch, tier="create", temperature=0.8,
                max_tokens=2500, save_as="3_选题生成.md", subdir=sub)
            done("topic", "选题生成", "A05", p, len(topic_raw), llm.LEDGER.cost - c0)
        except Exception as e:
            fail("topic", "选题生成", "A05", e)
            topic_raw = ""

        chosen = topic or self.pipe._first_topic(topic_raw)
        state["topic"] = chosen
        # 选题确定后，目录名改成主题名，产出更好找
        if not topic:
            newsub = self._safe_name(chosen)
            # 目标名已存在就换序号，不覆盖别人的产出
            if newsub and newsub != sub and not os.path.isdir(os.path.join(self.outdir, newsub)):
                try:
                    os.rename(outdir, os.path.join(self.outdir, newsub))
                    sub = newsub
                    outdir = os.path.join(self.outdir, sub)
                    state["subdir"] = sub
                    state["outdir"] = outdir
                except Exception:
                    pass

        # ---------- 4. 撰写 ----------
        emit("draft", "进行中")
        try:
            c0 = llm.LEDGER.cost
            draft, p = call(
                "A07",
                f"请围绕选题《{chosen}》写一篇公众号文章。\n\n"
                f"【账号定位】\n{position or '（未填）'}\n\n"
                f"【风格指纹】\n{fingerprint or '（未填，要求：口语化、短句、有具体细节）'}\n\n"
                "输出：正文（1500-3000字，Markdown，含小标题）+ 10个候选标题 + 素材使用说明。\n"
                "凡是需要我真实经历的地方，用 【待补：xxx】 标出来，不要编造。",
                system_extra=arch, tier="create", temperature=0.8, max_tokens=8000,
                save_as="4_撰写初稿.md", subdir=sub)
            done("draft", "撰写初稿", "A07", p, len(draft), llm.LEDGER.cost - c0)
        except Exception as e:
            fail("draft", "撰写初稿", "A07", e)
            draft = ""

        # ---------- 5. 质检 ----------
        emit("check", "进行中")
        try:
            c0 = llm.LEDGER.cost
            hits = self.pipe.forbidden.match(draft)
            fctx = self.pipe._forbidden_context(hits)
            report, p = call(
                "A10",
                "对下面这篇初稿做三合一检测：违规检测、AI味检测、错误检测。\n"
                "输出格式：\n"
                "## 一、违规检测\n逐条列出命中项，标注【严重】/【注意】，没有就写「未发现违规」\n"
                "## 二、AI味检测\n列出所有AI痕迹句（原文引用），给出改法\n"
                "## 三、错误检测\n列出事实错误、逻辑矛盾、表述含糊处\n"
                "## 四、修改建议（按优先级排序，最多10条）\n"
                "## 五、结论\n给出 【通过】/【修改后通过】/【不通过】 和英文 verdict_code\n\n"
                f"【已命中的红线（系统预检结果，需你复核）】\n{fctx or '系统预检未命中，但不代表无违规，请独立判断'}\n\n"
                f"【初稿】\n{draft}",
                extra_kb=fctx, system_extra=arch, tier="judge", temperature=0.3,
                max_tokens=4000, save_as="5_合规质检.md", subdir=sub)
            done("check", "合规质检", "A10", p, len(report), llm.LEDGER.cost - c0)
        except Exception as e:
            fail("check", "合规质检", "A10", e)
            report = ""

        # ---------- 6. 改稿 ----------
        emit("fix", "进行中")
        try:
            c0 = llm.LEDGER.cost
            fixed, p = call(
                "A08",
                "根据质检报告修改初稿，输出修改后的完整正文（保留 Markdown 格式）。\n"
                "要求：逐条落实修改建议；不删掉原有的真实细节；不要新增任何编造的事实。\n\n"
                f"【初稿】\n{draft}\n\n【质检报告】\n{report}",
                system_extra=arch, tier="create", temperature=0.6, max_tokens=8000,
                save_as="6_按质检修改.md", subdir=sub)
            done("fix", "按质检修改", "A08", p, len(fixed), llm.LEDGER.cost - c0)
        except Exception as e:
            fail("fix", "按质检修改", "A08", e)
            fixed = draft

        # ---------- 7. 去AI味 ----------
        emit("human", "进行中")
        try:
            c0 = llm.LEDGER.cost
            style = profmod.profile_context(self.cfg, ["style", "experiences"])
            final, p = call(
                "A09",
                "给下面这篇稿子去AI味，输出最终定稿。\n"
                "要求：\n"
                "1. 拆掉所有排比句、三段式对仗、「不仅…而且…」这类AI腔\n"
                "2. 把书面语改成口语，允许句子长短不齐\n"
                "3. 检查每段开头——AI最爱用「在当今这个…」式起手，改成直接说事\n"
                "4. 保留所有具体数字、细节、真实经历\n"
                "5. 不要为了「像人」而改掉准确的技术表述\n\n"
                f"【稿子】\n{fixed}",
                system_extra=style or arch, tier="create", temperature=0.9,
                max_tokens=8000, save_as="7_去AI味定稿.md", subdir=sub)
            done("human", "去AI味定稿", "A09", p, len(final), llm.LEDGER.cost - c0)
        except Exception as e:
            fail("human", "去AI味定稿", "A09", e)
            final = fixed

        # ---------- 8. 多平台适配 ----------
        emit("adapt", "进行中")
        try:
            c0 = llm.LEDGER.cost
            platforms = self.pipe._read_seed("平台规则库")
            adapt, p = call(
                "A11",
                "把下面定稿改写成 3 个平台的版本：公众号（1500-3000字，深度论证）、"
                "小红书（300-600字，短句+emoji+标签）、抖音（口播稿60秒，标注分镜）。\n"
                "每个平台单独输出，含建议发布时间。\n\n"
                f"【平台规则】\n{platforms or '（未填，用通用规则）'}\n\n【定稿】\n{final}",
                system_extra=arch, tier="create", temperature=0.7, max_tokens=7000,
                save_as="8_多平台适配.md", subdir=sub)
            done("adapt", "多平台适配", "A11", p, len(adapt), llm.LEDGER.cost - c0)
        except Exception as e:
            fail("adapt", "多平台适配", "A11", e)
            adapt = ""

        # ---------- 9. 配图 + 视频（不花 Token）----------
        emit("media", "进行中")
        try:
            title = pubmod.extract_title(final) or chosen
            md_for_media = self._extract_body(final)
            suite = mediamod.make_media_suite(
                md_for_media, os.path.join(outdir, "视觉素材"),
                title=title, theme=theme, brand=brand,
                with_video=with_video)
            state["media"] = {
                "cover": suite.get("cover") or "",
                "cards": suite.get("cards") or [],
                "video": suite.get("video") or "",
                "dir": suite.get("dir") or "",
            }
            for f in [suite.get("cover")] + list(suite.get("cards") or []) + \
                    ([suite.get("video")] if suite.get("video") else []):
                if f:
                    state["files"].append(f)
            done("media", "生成图片视频", "A22",
                 suite.get("dir") or "", 0, 0.0)
        except Exception as e:
            fail("media", "生成图片视频", "A22", e)

        # ---------- 10. 排版发布包 ----------
        emit("package", "进行中")
        try:
            md_for_pub = self._extract_body(final)
            pkg = pubmod.build_package(
                md_for_pub, os.path.join(outdir, "发布包"),
                title=pubmod.extract_title(final) or chosen,
                theme=theme, brand=brand, with_video=with_video)
            state["package"] = {
                "dir": pkg["dir"], "html": pkg["html"],
                "meta": pkg["meta"], "guide": pkg["guide"],
                "cover": pkg["cover"], "cards": pkg["cards"],
            }
            state["files"].extend([pkg["html"], pkg["guide"]])
            pubmod.log_publish(self.cfg, pkg["meta"], status="待发布",
                               note="工作流生成")
            done("package", "排版发布包", "A22", pkg["html"], 0, 0.0)
        except Exception as e:
            fail("package", "排版发布包", "A22", e)

        # ---------- 汇总 ----------
        elapsed = round(time.time() - started, 1)
        summary = [
            f"# {chosen}",
            "",
            f"- 运行编号：{run_id}",
            f"- 完成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 耗时：{elapsed} 秒",
            f"- Token 花费：¥{state['cost_yuan']:.4f}",
            "",
            "## 产出清单",
            "",
        ]
        for st in state["steps"]:
            mark = "✅" if st["status"] == "完成" else "❌"
            detail = f"{st['chars']:,} 字" if st.get("chars") else ""
            cost = f"¥{st.get('cost', 0):.4f}" if st.get("cost") else ""
            summary.append(f"- {mark} **{st['name']}**（{st['code']}） {detail} {cost}")
        summary += [
            "",
            "## 视觉产出",
            "",
            f"- 封面图：{'✅ ' + os.path.basename(state['media'].get('cover', '')) if state['media'].get('cover') else '未生成'}",
            f"- 图卡：{len(state['media'].get('cards', []))} 张",
            f"- 短视频：{'✅ 已生成' if state['media'].get('video') else '未生成（需要 ffmpeg）'}",
            "",
            "## 人审闸（必须你确认）",
            "- [ ] 终稿质量：读一遍定稿，不像人写的就重跑 A09",
            "- [ ] AI 声明：发布时勾选「AI 辅助创作」标识",
            "- [ ] 【待补】标记：文中所有 【待补：xxx】 都要填成你的真实经历",
            "- [ ] 禁区复核：质检官报告里的命中项，你逐条确认",
            "",
            "## 下一步",
            "",
            "打开 `发布包/发布指引.md` 就能发。",
            "",
        ]
        sp = self._save(sub, "0_汇总.md", "\n".join(summary))
        state["files"].append(sp)
        state["summary"] = sp
        state["elapsed"] = elapsed
        state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return state

    def run_visual(self, markdown, out_dir=None, theme="tech", with_video=False):
        """只给已有文章配图配视频（不调任何 AI）。"""
        prof = profmod.load_profile(self.cfg)
        brand = (prof.get("persona") or {}).get("name") or ""
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        out = out_dir or os.path.join(self.outdir, f"视觉素材_{stamp}")
        title = pubmod.extract_title(markdown)
        suite = mediamod.make_media_suite(
            markdown, out, title=title, theme=theme, brand=brand,
            with_video=with_video)
        return {"cover": suite.get("cover"), "cards": suite.get("cards"),
                "video": suite.get("video"), "dir": suite.get("dir"),
                "title": title}

    # ---- 内部 ----

    def _extract_body(self, text):
        """从 AI 产出里挑出正文部分。

        A09 的产出常常前面带「## 定稿」之类的小标题，
        公众号排版不需要这些，要剥掉。
        """
        t = (text or "").strip()
        # 去掉开头的说明性段落（常见于 A07/A09 的输出头）
        lines = t.split("\n")
        keep = []
        started = False
        for ln in lines:
            if not started:
                # 跳过第一个一级标题之前的内容
                if ln.startswith("#"):
                    started = True
                    keep.append(ln)
                continue
            keep.append(ln)
        if keep:
            t = "\n".join(keep)
        return t.strip()

    def _safe_name(self, s, prefix="文章_"):
        """把标题变成安全的目录名。

        硬规则（规划师 R1）：
          1. 目录名里不许出现日期数字——所以只保留含义，不带时间戳；
          2. 产出目录统一加类型前缀（文章_ / 数据_ / 视觉素材 / 发布包），
             一眼看出这是正文还是数据还是素材。

        prefix 传空串则不加前缀。
        """
        if not s:
            return ""
        s = re.sub(r"[\\/:*?\"<>|]", "", s)
        s = s.strip().strip("《》【】")
        s = re.sub(r"\s+", "_", s)
        s = s[:40].strip("_")
        if prefix and not s.startswith(prefix):
            s = prefix + s
        return s

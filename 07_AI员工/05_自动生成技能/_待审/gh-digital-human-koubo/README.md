# 视频制作员 —— 让它变成你能用的岗位

> 由 GitHub 项目 [andyhuo520/digital-human-koubo](https://github.com/andyhuo520/digital-human-koubo) 自动转换，**待人工确认后上架**。

## 这个技能替你干什么

- **Digital human from one photo** — a fixed presenter (face, outfit, voice description, prompt template) stored in `persona.json`, so every video is the same person. Composite her into any scene (livestream studio, shop, classroom, Kyoto street) with GPT Image first, then make her talk.
- **Lip-synced talking clips** — the script is split into ≤ 15-second segments and generated with **Grok Video 1.5** through **[Renoise MCP](https://renoise.ai/mcp?utm_medium=xcom&utm_source=official)**. Other models (Wan 3.0, OmniHuman for your own recording) are documented.
- **Cost gate** — the agent always quotes *segments · seconds · credits · balance* first and waits for your "yes". It generates segment 1 for you to check before batching the rest.
- **Quality control** — every segment is transcribed with whisper and compared with the script, and 4 frames per segment are inspected (Grok sometimes burns subtitles into the picture; those segments get re-shot).
- **Voice unification** — 1/3-octave spectral matching, de-essing, one shared small-room reverb and loudness normalisation, so 30 separately-generated clips sound like one recording (sibilance spread 13.2 dB → 5.9 dB in our tests).
- **5 editing styles, chosen for your content** — the agent diagnoses your content, recommends 2 styles with preview images and asks you everything in one go (style · ratio & length · caption intensity · presenter).
- **48 packaging components** — measured frame-by-frame from 25 videos by top creators (MrBeast, Mark Rober, Ryan Trahan, MKBHD, MediaStorm/影视飓风, 何同学, Johnny Harris, Vox, Kurzgesagt, 回形针, 半佛仙人, 差评, 毕导). Each style has a default packaging recipe; add or remove techniques by just asking.
- **Text behind the presenter** — big words / numbers between the background and the person (macOS Vision person segmentation → transparent video → 3-layer composite).
- **Multi-language** — English, Japanese, Korean, French, Spanish, Arabic tested (whisper back-check 94–100 %).
- **Works with your agent** — a standard `SKILL.md` Agent Skill plus scripts; any agent that can load skills, connect a remote MCP server and run local commands can drive it.

## 适合谁 / 不适合谁

**适合**：
- 有明确重复性工作、想交给 AI 执行的中小企业
- 愿意自己准备依赖与凭据的动手型用户

**不适合**：
- 完全不想碰环境配置的人（装不上就是装不上）
- 需要法律/财务专业结论的场景（本技能只做信息处理）

## 怎么用

```bash
git clone https://github.com/andyhuo520/digital-human-koubo.git
cd digital-human-koubo
mkdir -p ~/.claude/skills && cp -R skills/digital-human-koubo skills/finance-explainer ~/.claude/skills/
mkdir -p ~/.codex/skills && cp -R skills/digital-human-koubo skills/finance-explainer ~/.codex/skills/
cd skills && zip -r ../digital-human-skills.zip digital-human-koubo finance-explainer && cd ..
bash ~/.claude/skills/digital-human-koubo/scripts/doctor.sh
./align.py
./assemble.py
```

## 来源与授权

- 原项目：https://github.com/andyhuo520/digital-human-koubo
- 协议：MIT
- 本包装的是**调用方式与工作流**，不重新分发原项目代码

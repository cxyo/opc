---
name: gh-digital-human-koubo
description: 视频制作员 —— AI digital-human talking-head videos from one photo: Renoise MCP generation + 5 editing styles + 48 creator-grade packaging components. Agent skill for Claude Code / Codex / OpenClaw / WorkBuddy …。自动从 GitHub 项目 andyhuo520/digital-human-koubo 生成，待人工确认后使用。
version: 1.0.0
author: lobster-skill
tags:
  - github
  - auto-generated
license: MIT
---

# 视频制作员 · andyhuo520/digital-human-koubo

> AI 智能体 / 员工 ｜ 来源：https://github.com/andyhuo520/digital-human-koubo ｜ 自动生成时间：2026-10-31 06:36:14

## 这个技能做什么

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

**可包装成**：企业知识库问答：把老板的资料变成能问的知识库

## 使用前提

**必须自带**：
- 目标环境的运行权限（本地命令行或目标平台账号）
- 该项目 README 中列出的全部依赖与凭据

> 缺依赖就装不上，本技能会直接失败而不是降级乱跑。

## 安装与调用

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

## 绝对禁止

1. 不承诺效果（不写「保证」「100%」「必定」「稳赚」）
2. 不代替人工做金额、合同、赔付的决定
3. 不上传用户数据到第三方（除非项目本身就是云服务且已说明）
4. 不绕过付费墙、不破解、不代理他人账号
5. 不群控、不批量私信、不刷量

## 降级行为

| 情况 | 处理 |
|---|---|
| 依赖缺失或凭据无效 | 明确报出缺什么，**不用通用知识替代真实结果** |
| 输出涉及金额、合同、承诺 | 标记「需人工确认」，不自动执行 |
| 原项目行为与本说明冲突 | 以原项目实际行为为准，并提示人工复核 |

---

标签：#自动生成 #GitHub项目 #待人工审核
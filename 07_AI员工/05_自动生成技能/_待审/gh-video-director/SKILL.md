---
name: gh-video-director
description: 视频制作员 —— A Claude skill for directing, building and verifying code-rendered videos (HTML/Canvas → MP4): principles, gated workflow, QA scripts and evals.。自动从 GitHub 项目 win4r/video-director 生成，待人工确认后使用。
version: 1.0.0
author: lobster-skill
tags:
  - github
  - auto-generated
license: MIT
---

# 视频制作员 · win4r/video-director

> Skill / 技能包 ｜ 来源：https://github.com/win4r/video-director ｜ 自动生成时间：2026-10-31 08:40:35

## 这个技能做什么

**Primary model: Claude Opus 5.5 · For Claude Code**

<details>
<summary>看完整说明（来自项目 README）</summary>

# video-director

**Primary model: Claude Opus 5.5 · For Claude Code**

**English** | [简体中文](README.zh-CN.md)

**video-director is built primarily for Claude Opus 5.5 in Claude Code.** It guides the model to create short videos as code (HTML, Canvas, SVG, Three.js, GSAP or Remotion), rendered frame by frame to MP4. It covers launch films, promos, explainers, data stories, kinetic type, character and illustrated shorts, loops and social reels.

The skill treats **Claude Opus 5.5** as the director and coding agent: plan the film in numbers, build it deterministically, then measure what it can't watch or hear. It holds principles with their reasons, a workflow with a gate at each phase, and two QA scripts. It deliberately ships no shot library to copy.

## Animation demo

### Hand-drawn city

[![Animation demo: a hand-drawn floating city comes to life in color](assets/demo.gif)](assets/demo.mp4)

[Watch the original MP4 (30 seconds, 720 × 720, 60 fps)](assets/demo.mp4)

### 3D character animation

[![3D character animation: a small orange character working at a laptop](assets/character-demo.gif)](assets/character-demo.mp4)

[Watch the original MP4 with audio (16 seconds, 1920 × 1080, 

</details>

**可包装成**：内容生产岗：视频/配音/数字人生成

## 使用前提

**必须自带**：
- 目标环境的运行权限（本地命令行或目标平台账号）
- 该项目 README 中列出的全部依赖与凭据

> 缺依赖就装不上，本技能会直接失败而不是降级乱跑。

## 安装与调用

```bash
git clone https://github.com/win4r/video-director.git
cp -R video-director/video-director ~/.claude/skills/
./place-files.sh
./check_timeline.py
./qa_video.py
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
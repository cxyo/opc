# 视频制作员 —— 让它变成你能用的岗位

> 由 GitHub 项目 [win4r/video-director](https://github.com/win4r/video-director) 自动转换，**待人工确认后上架**。

## 这个技能替你干什么

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

## 适合谁 / 不适合谁

**适合**：
- 有明确重复性工作、想交给 AI 执行的中小企业
- 愿意自己准备依赖与凭据的动手型用户

**不适合**：
- 完全不想碰环境配置的人（装不上就是装不上）
- 需要法律/财务专业结论的场景（本技能只做信息处理）

## 怎么用

```bash
git clone https://github.com/win4r/video-director.git
cp -R video-director/video-director ~/.claude/skills/
./place-files.sh
./check_timeline.py
./qa_video.py
```

## 来源与授权

- 原项目：https://github.com/win4r/video-director
- 协议：MIT
- 本包装的是**调用方式与工作流**，不重新分发原项目代码

# 技能封装员 —— 让它变成你能用的岗位

> 由 GitHub 项目 [MiniMax-AI/mcode-stack](https://github.com/MiniMax-AI/mcode-stack) 自动转换，**待人工确认后上架**。

## 这个技能替你干什么

<img src="docs/assets/hero.png" alt="MCode Stack: the workflows and skills the MiniMax Code team uses to build MiniMax Code" width="100%">
 

 
 
<a href=" alt="validate" src=" 
 <img alt="skills: stacks/" src=" 
 <img alt="for MiniMax Code" src=" 
 

 English · 中文 

**The workflows and skills the MiniMax Code team uses to build MiniMax Code. Open source, install and use.**

This is not a prompt collection or a list of "best practices". It is what
people on the team actually use every day: how to keep AI-written code at a
high bar, how to decide whether a task should go to a subagent, how long to
wait on a long command, how to keep long sessions from going dull, and what
goes into context on every turn. Each piece is a `SKILL.md` skill or
checklist that installs straight into
[MiniMax Code](https://github.com/MiniMax-AI/minimax-code).

- [Quick start](#quick-start)
- [What's inside](#whats-inside)
- [How it works](#how-it-works)
- [Articles](#articles)
- [Stacks](#stacks)
- [Contributing](#contributing)

## Quick start

**Claude Code, one command.** The command adds this marketplace and installs
a whole stack. In a shell:

```bash
claude plugin install ronny --marketplace MiniMax-A

## 适合谁 / 不适合谁

**适合**：
- 有明确重复性工作、想交给 AI 执行的中小企业
- 愿意自己准备依赖与凭据的动手型用户

**不适合**：
- 完全不想碰环境配置的人（装不上就是装不上）
- 需要法律/财务专业结论的场景（本技能只做信息处理）

## 怎么用

```bash
git clone https://github.com/MiniMax-AI/mcode-stack.git ~/mcode-stack
mkdir -p ~/.agents/skills
cp -R ~/mcode-stack/stacks/ronny/skills/long-command-check ~/.agents/skills/
./install.sh
./digest.py
./capture.py
./fetch_assets.py
./lint.py
```

## 来源与授权

- 原项目：https://github.com/MiniMax-AI/mcode-stack
- 协议：未标注 —— **上架前必须确认**
- 本包装的是**调用方式与工作流**，不重新分发原项目代码

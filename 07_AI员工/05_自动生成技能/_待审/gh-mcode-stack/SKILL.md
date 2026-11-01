---
name: gh-mcode-stack
description: 技能封装员 —— <p align="center"> <img src="docs/assets/hero.png" alt="MCode Stack: the workflows and skills the MiniMax Code team uses to build MiniMax Code" width="100%"> </p> <p align="center"。自动从 GitHub 项目 MiniMax-AI/mcode-stack 生成，待人工确认后使用。
version: 1.0.0
author: lobster-skill
tags:
  - github
  - auto-generated
license: SEE_PARENT_REPO
---

# 技能封装员 · MiniMax-AI/mcode-stack

> AI 智能体 / 员工 ｜ 来源：https://github.com/MiniMax-AI/mcode-stack ｜ 自动生成时间：2026-10-31 06:48:08

## 这个技能做什么

**<a href=" alt="validate" src=**

<details>
<summary>看完整说明（来自项目 README）</summary>

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

</details>

**可包装成**：某个岗位的执行技能（如：自动调研、自动日报）（**待人工细化包装方向**）

## 使用前提

**必须自带**：
- 目标环境的运行权限（本地命令行或目标平台账号）
- 该项目 README 中列出的全部依赖与凭据

> 缺依赖就装不上，本技能会直接失败而不是降级乱跑。

## 安装与调用

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
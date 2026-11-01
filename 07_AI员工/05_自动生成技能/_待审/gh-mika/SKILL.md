---
name: gh-mika
description: 任务执行员 —— Mika — 你手机上的私人 AI 助理，替你操作 App、上网、把事办成。An on-device AI agent for Android that operates apps and the web to get things done for you.。自动从 GitHub 项目 wangzan101/Mika 生成，待人工确认后使用。
version: 1.0.0
author: lobster-skill
tags:
  - accessibility
  - ai-agent
  - android
  - automation
  - jetpack-compose
  - kotlin
license: Apache-2.0
---

# 任务执行员 · wangzan101/Mika

> AI 智能体 / 员工 ｜ 来源：https://github.com/wangzan101/Mika ｜ 自动生成时间：2026-10-31 08:40:22

## 这个技能做什么

**你手机上的私人 AI 助理，替你操作 App、上网、把事办成。**

<details>
<summary>看完整说明（来自项目 README）</summary>

# Mika

简体中文 | [English](README.en.md)

**你手机上的私人 AI 助理，替你操作 App、上网、把事办成。**

它在一块看不见的后台屏上干活，你照样刷你的手机。

An on-device AI agent for Android that operates apps and the web to get things done for you — on a hidden background screen, so you can keep using your phone.

 <img src=" alt="version" /> 
 
 
 <img src=" alt="license" /> 

[下载 APK](https://github.com/wangzan101/Mika/releases/latest) · [安装](#安装) · [快速开始](#快速开始) · [能做什么](#能做什么) · [常见问题](#常见问题) · [参与贡献](#参与贡献)

 

## 安装

**方式一：在手机上自己装**

1. 从 [Releases](https://github.com/wangzan101/Mika/releases/latest) 下载最新的 APK，安装。
2. 打开 Mika 设置里的「设置手机操作」向导，配对一次无线调试（[设置指南](docs/setup-guide.md)）。
3. 在设置里的「模型」添加服务商、填 API Key，或者用 ChatGPT 登录。

每一步的细节见下文的[快速开始](#快速开始)。

**方式二：让电脑上的 AI 助手帮你装**

手机用数据线连上电脑，在手机上打开 USB 调试，然后把下面这句话发给电脑上能执行命令的 AI 助手：

```
请按 的步骤，帮我把 Mika 安装到通过 USB 连着的安卓手机上。
```

</details>

**可包装成**：某个岗位的执行技能（如：自动调研、自动日报）（**待人工细化包装方向**）

## 使用前提

**必须自带**：
- 目标环境的运行权限（本地命令行或目标平台账号）
- 该项目 README 中列出的全部依赖与凭据

> 缺依赖就装不上，本技能会直接失败而不是降级乱跑。

## 安装与调用

```bash
./extract_pdf.py
./gradlew.bat
./build-native.sh
./build-runtime.py
./runtime-sources.py
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
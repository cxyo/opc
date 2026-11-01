# 任务执行员 —— 让它变成你能用的岗位

> 由 GitHub 项目 [wangzan101/Mika](https://github.com/wangzan101/Mika) 自动转换，**待人工确认后上架**。

## 这个技能替你干什么

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

## 适合谁 / 不适合谁

**适合**：
- 有明确重复性工作、想交给 AI 执行的中小企业
- 愿意自己准备依赖与凭据的动手型用户

**不适合**：
- 完全不想碰环境配置的人（装不上就是装不上）
- 需要法律/财务专业结论的场景（本技能只做信息处理）

## 怎么用

```bash
./extract_pdf.py
./gradlew.bat
./build-native.sh
./build-runtime.py
./runtime-sources.py
```

## 来源与授权

- 原项目：https://github.com/wangzan101/Mika
- 协议：Apache-2.0
- 本包装的是**调用方式与工作流**，不重新分发原项目代码

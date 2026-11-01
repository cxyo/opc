---
name: gh-geoinflow
description: 信息采集员 —— Self-hosted web and AI crawler analytics for Generative Engine Optimization (GEO). Track visitors, AI search referrals, crawl logs, content coverage, and conversions. Jointly published by 猎看科技 · 北京日报社智源GEO. MIT licensed.。自动从 GitHub 项目 yaojingang/GEOInflow 生成，待人工确认后使用。
version: 1.0.0
author: lobster-skill
tags:
  - ai-analytics
  - geo
  - mit
  - postgresql
  - self-hosted
  - web-analytics
license: MIT
---

# 信息采集员 · yaojingang/GEOInflow

> AI 智能体 / 员工 ｜ 来源：https://github.com/yaojingang/GEOInflow ｜ 自动生成时间：2026-10-31 08:40:10

## 这个技能做什么

**Self-hosted web and AI crawler analytics for Generative Engine Optimization (GEO).**

<details>
<summary>看完整说明（来自项目 README）</summary>

# GEOInflow

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Self-hosted web and AI crawler analytics for Generative Engine Optimization (GEO).

**Jointly published by 猎看科技 · 北京日报社智源GEO**

[Quick start](docs/getting-started.en.md) · [Deployment](docs/deployment.en.md) · [AI crawler analytics](docs/ai-crawler-analytics.md) · [Architecture](docs/system-overview.md) · [Documentation](docs/README.md) · [Contributing](CONTRIBUTING.md)

GEOInflow brings website visitors, AI search referrals, server crawl logs, and content inventories into a shared website context. It helps teams understand which AI crawlers visit their sites, which pages they request, which important pages have no observed crawl activity, where requests fail, and how AI search contributes visitors and conversions.

The system includes browser tracking, account permissions, GEO reports, crawler evidence, log collection, and analytics acceleration. The current product version is **v1.2**, with build version **1.2.0**. Application source code is distributed under the [MIT license](LICENSE).

Quick start and deployment guides are available in English and Simplified Chinese. Other linked reference guides are curr

</details>

**可包装成**：企业知识库问答：把老板的资料变成能问的知识库

## 使用前提

**必须自带**：
- 目标环境的运行权限（本地命令行或目标平台账号）
- 该项目 README 中列出的全部依赖与凭据

> 缺依赖就装不上，本技能会直接失败而不是降级乱跑。

## 安装与调用

```bash
sh scripts/liekan-deploy.sh
docker compose -f docker-compose.liekan.yml exec app node scripts/liekan-seed-demo.mjs
./install-docker.template.sh
./install.template.sh
./liekan-deploy.sh
./liekan-start.sh
./start-docker.sh
./deployment-docker.sh
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
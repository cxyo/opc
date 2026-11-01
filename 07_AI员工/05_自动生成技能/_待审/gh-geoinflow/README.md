# 信息采集员 —— 让它变成你能用的岗位

> 由 GitHub 项目 [yaojingang/GEOInflow](https://github.com/yaojingang/GEOInflow) 自动转换，**待人工确认后上架**。

## 这个技能替你干什么

# GEOInflow

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Self-hosted web and AI crawler analytics for Generative Engine Optimization (GEO).

**Jointly published by 猎看科技 · 北京日报社智源GEO**

[Quick start](docs/getting-started.en.md) · [Deployment](docs/deployment.en.md) · [AI crawler analytics](docs/ai-crawler-analytics.md) · [Architecture](docs/system-overview.md) · [Documentation](docs/README.md) · [Contributing](CONTRIBUTING.md)

GEOInflow brings website visitors, AI search referrals, server crawl logs, and content inventories into a shared website context. It helps teams understand which AI crawlers visit their sites, which pages they request, which important pages have no observed crawl activity, where requests fail, and how AI search contributes visitors and conversions.

The system includes browser tracking, account permissions, GEO reports, crawler evidence, log collection, and analytics acceleration. The current product version is **v1.2**, with build version **1.2.0**. Application source code is distributed under the [MIT license](LICENSE).

Quick start and deployment guides are available in English and Simplified Chinese. Other linked reference guides are curr

## 适合谁 / 不适合谁

**适合**：
- 有明确重复性工作、想交给 AI 执行的中小企业
- 愿意自己准备依赖与凭据的动手型用户

**不适合**：
- 完全不想碰环境配置的人（装不上就是装不上）
- 需要法律/财务专业结论的场景（本技能只做信息处理）

## 怎么用

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

## 来源与授权

- 原项目：https://github.com/yaojingang/GEOInflow
- 协议：MIT
- 本包装的是**调用方式与工作流**，不重新分发原项目代码

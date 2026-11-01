# 信息采集员 —— 让它变成你能用的岗位

> 由 GitHub 项目 [yaojingang/GEOInflow](https://github.com/yaojingang/GEOInflow) 自动转换，**待人工确认后上架**。

## 这个技能替你干什么

https://github.com/user-attachments/assets/81e57cc4-3441-4e5f-b2fb-cd929c6c27bd

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

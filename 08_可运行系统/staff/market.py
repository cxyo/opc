# -*- coding: utf-8 -*-
"""AI 超市 —— 能力接入中心。

这个项目**不做**数字人、**不做**大模型，只做「对接」。

就像超市一样：货架上摆着市面上主流的数字人平台、大模型、工作流平台，
客户自己挑一个，填自己的账号密钥，一键测试通不通，接进来，
之后 AI 员工就能调用它去干活。

为什么要这么设计：
    「数字人」这件事，没有哪家企业愿意被一套私有实现绑死。
    客户已经有账号、已经付过费、已经跟供应商谈好了价格。
    我们再自己造一个数字人，客户凭什么不用现成的？
    所以真正的价值不在「造平台」，而在「把客户的平台接进 AI 员工体系」。

六类能力（货架分区）：
    1. 数字人  —— 腾讯云 / 阿里云 / 百度曦灵 / 硅基 / 讯飞 / HeyGen / Tavus ...
    2. 大模型  —— DeepSeek / 通义 / 文心 / 智谱 / Kimi / 豆包 / 混元 / Claude ...
    3. 工作流  —— Coze 扣子 / Dify / n8n / 腾讯元器 / 百度千帆 ...
    4. 语音    —— TTS 配音 / 语音识别
    5. 图像    —— 配图 / 视频画面
    6. 知识库  —— 外部知识库（Coze / Dify / FastGPT / MaxKB）

三层结构：
    目录（CATALOG） → 凭据（market.json） → 适配器（adapter）

凭据存哪：
    data/state/market.json（本地，不上云）。
    读取时优先用环境变量 MARKET_<KEY>_<FIELD>，
    这样生产环境可以走环境变量，本地调试走文件。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# ---------------- 货架分区 ----------------

CATEGORIES: List[Dict[str, Any]] = [
    {"key": "avatar", "name": "AI 数字人", "icon": "🧑‍💼",
     "desc": "口播视频、直播替身、形象定制。选一个客户已有账号的平台接进来。"},
    {"key": "llm", "name": "大模型", "icon": "🧠",
     "desc": "绝大多数都兼容 OpenAI 协议，填 Key 就能用。"},
    {"key": "flow", "name": "工作流", "icon": "🔀",
     "desc": "扣子 Coze、Dify、n8n 等已有的工作流，直接接进来当能力用。"},
    {"key": "voice", "name": "语音", "icon": "🔊",
     "desc": "配音 TTS、语音识别 ASR。"},
    {"key": "image", "name": "图像与视频", "icon": "🎨",
     "desc": "配图、分镜、画面生成。"},
    {"key": "kb", "name": "知识库", "icon": "📚",
     "desc": "外部知识库检索。让 AI 员工去查客户自己的知识库。"},
]

# ---------------- 适配器说明 ----------------
# 一个适配器 = 一种接入协议。平台清单里的 adapter 字段指向这里。
ADAPTERS: Dict[str, Dict[str, Any]] = {
    "openai_chat": {
        "name": "OpenAI 兼容协议",
        "desc": "绝大多数国产大模型和海外模型都支持这个协议，填 Key 即可。",
        "needs": ["base_url", "api_key"],
    },
    "openai_image": {
        "name": "OpenAI 兼容图像协议",
        "desc": "文生图 / 图生图，接口与 OpenAI images 一致。",
        "needs": ["base_url", "api_key"],
    },
    "coze_workflow": {
        "name": "扣子 Coze 工作流",
        "desc": "调用客户在扣子上已经配好的工作流，传入参数、拿回结果。",
        "needs": ["api_key", "workflow_id"],
    },
    "dify_workflow": {
        "name": "Dify 工作流",
        "desc": "调用 Dify 应用，工作流模式下返回结构化输出。",
        "needs": ["base_url", "api_key"],
    },
    "heygen_video": {
        "name": "HeyGen 视频",
        "desc": "提交口播任务并轮询生成状态。",
        "needs": ["api_key"],
    },
    "tavus_replica": {
        "name": "Tavus 数字人",
        "desc": "用自己的形象生成口播视频，支持实时对话。",
        "needs": ["api_key"],
    },
    "generic_rest": {
        "name": "通用 REST（万能对接）",
        "desc": "填自己的接口地址和请求模板，任意平台都能接。请求体里用 {{字段名}} 占位。",
        "needs": ["endpoint"],
    },
}


# ---------------- 平台清单 ----------------
# 这是货架。要加新平台，往这里加一条就行，别的都不用改。
# fields 里的每一项 = 客户要填的一个框。
#   type: text | password | textarea | number
#   required: 是否必填
#   env: 环境变量名（生产环境可以走环境变量，不落盘）
#
# endpoint 默认值只给「我确信」的那些；
# 数字人这类需要企业资质、地址因客户而异的，交给客户填 + 给申请入口。

def _f(name, label, type_="text", required=False, placeholder="", env=None, tip=""):
    return {"name": name, "label": label, "type": type_,
            "required": required, "placeholder": placeholder,
            "env": env, "tip": tip}


CATALOG: List[Dict[str, Any]] = [
    # ================= AI 数字人 =================
    {
        "key": "tencent_avatar", "name": "腾讯云智能数智人", "vendor": "腾讯云",
        "category": "avatar", "icon": "🐧", "adapter": "generic_rest",
        "capabilities": ["口播视频", "数字人直播", "形象定制"],
        "desc": "腾讯云的数智人服务，适合做企业培训、讲解视频。",
        "fields": [
            _f("app_id", "AppID", required=True, env="TENCENT_APP_ID"),
            _f("secret_id", "SecretId", required=True, env="TENCENT_SECRET_ID"),
            _f("secret_key", "SecretKey", type_="password", required=True,
               env="TENCENT_SECRET_KEY"),
            _f("endpoint", "接口地址", required=True,
               placeholder="https://<你的腾讯云数智人接口地址>"),
            _f("template_id", "形象/模板 ID"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"Text":"{{text}}","TemplateId":"{{template_id}}"}'),
        ],
        "docs": "https://cloud.tencent.com/product/tlh",
        "note": "需要腾讯云企业账号并开通数智人服务，开通后按官方文档填接口地址。",
    },
    {
        "key": "aliyun_avatar", "name": "阿里云数字人", "vendor": "阿里云",
        "category": "avatar", "icon": "☁️", "adapter": "generic_rest",
        "capabilities": ["口播视频", "直播推流", "形象定制"],
        "desc": "阿里云智能视频服务，模板丰富，支持直播。",
        "fields": [
            _f("access_key", "AccessKey", required=True, env="ALIYUN_AK"),
            _f("access_secret", "AccessSecret", type_="password", required=True,
               env="ALIYUN_SK"),
            _f("endpoint", "接口地址", required=True,
               placeholder="https://<你的数字人服务地址>"),
            _f("template_id", "模板 ID"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"Text":"{{text}}","TemplateId":"{{template_id}}"}'),
        ],
        "docs": "https://help.aliyun.com/product/175539.html",
        "note": "先在阿里云开通智能视频/数字人，按文档给出的实际地址填写。",
    },
    {
        "key": "baidu_xiling", "name": "百度智能云曦灵", "vendor": "百度",
        "category": "avatar", "icon": "🐻", "adapter": "generic_rest",
        "capabilities": ["数字人视频", "直播", "交互问答"],
        "desc": "百度的数字人平台，形象库大，支持互动。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True,
               env="BAIDU_API_KEY"),
            _f("secret_key", "Secret Key", type_="password", required=True),
            _f("endpoint", "接口地址", required=True,
               placeholder="https://aip.baidubce.com/rpc/2.0/ai_custom/..."),
            _f("avatar_id", "形象 ID"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"text":"{{text}}","avatar_id":"{{avatar_id}}"}'),
        ],
        "docs": "https://cloud.baidu.com/product/wenxin/builder",
        "note": "曦灵是定制化接口，每个客户的地址和字段不同，请按官方文档填。",
    },
    {
        "key": "silicon_avatar", "name": "硅基智能", "vendor": "硅基智能",
        "category": "avatar", "icon": "🔷", "adapter": "generic_rest",
        "capabilities": ["数字人视频", "克隆形象", "直播"],
        "desc": "国内数字人头部厂商，克隆真人形象是强项。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True),
            _f("endpoint", "接口地址", required=True,
               placeholder="https://<硅基智能开放平台地址>"),
            _f("avatar_id", "形象 ID"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"text":"{{text}}","avatar_id":"{{avatar_id}}"}'),
        ],
        "docs": "https://www.guiji.ai/",
        "note": "商业化接口通常需要商务开通，拿到文档后再填。",
    },
    {
        "key": "xfyun_avatar", "name": "讯飞智作/虚拟人", "vendor": "科大讯飞",
        "category": "avatar", "icon": "🦜", "adapter": "generic_rest",
        "capabilities": ["口播视频", "语音合成"],
        "desc": "讯飞的虚拟人平台，配音是它最强的部分。",
        "fields": [
            _f("app_id", "AppID", required=True),
            _f("api_key", "APIKey", type_="password", required=True),
            _f("api_secret", "APISecret", type_="password", required=True),
            _f("endpoint", "接口地址", required=True,
               placeholder="https://<讯飞虚拟人接口地址>"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"text":"{{text}}"}'),
        ],
        "docs": "https://www.xfyun.cn/",
        "note": "讯飞接口带签名，按官方文档填完整鉴权参数。",
    },
    {
        "key": "heygen", "name": "HeyGen", "vendor": "HeyGen",
        "category": "avatar", "icon": "🎥", "adapter": "heygen_video",
        "capabilities": ["口播视频", "形象克隆", "视频翻译", "数字人直播"],
        "desc": "国际主流数字人平台，接口文档公开，海外业务首选。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True),
            _f("avatar_id", "Avatar ID", tip="在 HeyGen 后台 Choose Avatar 里拿"),
        ],
        "docs": "https://docs.heygen.com/",
        "note": "提交后是异步任务，需要轮询视频状态才能拿到下载地址。",
    },
    {
        "key": "tavus", "name": "Tavus", "vendor": "Tavus",
        "category": "avatar", "icon": "🗣️", "adapter": "tavus_replica",
        "capabilities": ["数字人视频", "实时数字人", "克隆形象"],
        "desc": "支持实时对话的数字人，适合做客服替代。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True),
            _f("replica_id", "Replica ID", tip="用你的形象创建 Replica 后获得"),
        ],
        "docs": "https://docs.tavus.io/",
        "note": "先创建 Replica 形象，再用它生成视频。",
    },
    {
        "key": "synthesia", "name": "Synthesia", "vendor": "Synthesia",
        "category": "avatar", "icon": "🎭", "adapter": "generic_rest",
        "capabilities": ["口播视频", "模板视频"],
        "desc": "老牌企业视频平台，模板化做培训视频很成熟。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True),
            _f("endpoint", "接口地址",
               placeholder="https://api.synthesia.io/v2/videos"),
            _f("template_id", "Template ID"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"template_id":"{{template_id}}","title":"{{title}}"}'),
        ],
        "docs": "https://docs.synthesia.io/",
        "note": "同样是一步提交、异步生成，需要轮询。",
    },

    # ================= 大模型 =================
    {
        "key": "deepseek", "name": "DeepSeek", "vendor": "DeepSeek",
        "category": "llm", "icon": "🐋", "adapter": "openai_chat",
        "capabilities": ["对话", "写作", "推理"],
        "desc": "国产第一梯队，价格低，本项目默认已在用。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://api.deepseek.com/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="deepseek-chat"),
        ],
        "docs": "https://platform.deepseek.com/",
        "note": "本项目默认已接，配好了不用重复填。",
    },
    {
        "key": "qwen", "name": "通义千问", "vendor": "阿里云",
        "category": "llm", "icon": "🐦", "adapter": "openai_chat",
        "capabilities": ["对话", "长文本", "多模态"],
        "desc": "阿里云百炼，兼容 OpenAI 协议。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="qwen-plus"),
        ],
        "docs": "https://help.aliyun.com/zh/model-studio/",
    },
    {
        "key": "wenxin", "name": "文心一言 / 千帆", "vendor": "百度",
        "category": "llm", "icon": "🪁", "adapter": "openai_chat",
        "capabilities": ["对话", "知识库", "Agent"],
        "desc": "百度智能云千帆平台。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://qianfan.baidubce.com/v2"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="ernie-4.5-turbo"),
        ],
        "docs": "https://cloud.baidu.com/product/wenxinworkshop",
    },
    {
        "key": "zhipu", "name": "智谱 GLM", "vendor": "智谱 AI",
        "category": "llm", "icon": "🟣", "adapter": "openai_chat",
        "capabilities": ["对话", "Agent", "长文本"],
        "desc": "GLM 系列，AutoGLM 适合做手机操作代理。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://open.bigmodel.cn/api/paas/v4"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="glm-4-plus"),
        ],
        "docs": "https://open.bigmodel.cn/",
    },
    {
        "key": "kimi", "name": "Kimi 月之暗面", "vendor": "月之暗面",
        "category": "llm", "icon": "🌙", "adapter": "openai_chat",
        "capabilities": ["长文本", "文档解析"],
        "desc": "长文本和文件解析是强项。",
        "fields": [
            _f("base_url", "接口地址", placeholder="https://api.moonshot.cn/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="moonshot-v1-8k"),
        ],
        "docs": "https://platform.moonshot.cn/",
    },
    {
        "key": "doubao", "name": "豆包（火山方舟）", "vendor": "字节跳动",
        "category": "llm", "icon": "🫘", "adapter": "openai_chat",
        "capabilities": ["对话", "多模态", "视频"],
        "desc": "火山引擎的模型服务，价格低、速度快。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://ark.cn-beijing.volces.com/api/v3"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="ep-xxxxxxxx"),
        ],
        "docs": "https://www.volcengine.com/product/ark",
    },
    {
        "key": "hunyuan", "name": "腾讯混元", "vendor": "腾讯云",
        "category": "llm", "icon": "🐲", "adapter": "openai_chat",
        "capabilities": ["对话", "文档理解"],
        "desc": "腾讯云的混元大模型。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://api.hunyuan.cloud.tencent.com/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="hunyuan-turbo"),
        ],
        "docs": "https://cloud.tencent.com/product/hunyuan",
    },
    {
        "key": "minimax", "name": "MiniMax", "vendor": "MiniMax",
        "category": "llm", "icon": "🎏", "adapter": "openai_chat",
        "capabilities": ["对话", "语音", "视频"],
        "desc": "国内少数同时做大模型、语音、视频的厂商。",
        "fields": [
            _f("base_url", "接口地址", placeholder="https://api.minimax.chat/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="abab6.5s-chat"),
        ],
        "docs": "https://platform.minimaxi.com/",
    },
    {
        "key": "siliconflow", "name": "硅基流动", "vendor": "SiliconFlow",
        "category": "llm", "icon": "🧊", "adapter": "openai_chat",
        "capabilities": ["对话", "多模型聚合", "图像", "语音"],
        "desc": "一个 Key 调 dozens 个开源模型，做模型聚合省事。",
        "fields": [
            _f("base_url", "接口地址", placeholder="https://api.siliconflow.cn/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="Qwen/Qwen2.5-72B-Instruct"),
        ],
        "docs": "https://docs.siliconflow.cn/",
    },
    {
        "key": "openai", "name": "OpenAI", "vendor": "OpenAI",
        "category": "llm", "icon": "🌀", "adapter": "openai_chat",
        "capabilities": ["对话", "多模态", "Agent"],
        "desc": "海外业务或需要 o 系列推理时用。",
        "fields": [
            _f("base_url", "接口地址", placeholder="https://api.openai.com/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="gpt-4o"),
        ],
        "docs": "https://platform.openai.com/docs",
    },
    {
        "key": "claude", "name": "Claude", "vendor": "Anthropic",
        "category": "llm", "icon": "🍊", "adapter": "openai_chat",
        "capabilities": ["长文本", "写作", "代码"],
        "desc": "长文本和写作质量高。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://api.anthropic.com/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="claude-sonnet-4-5"),
        ],
        "docs": "https://docs.anthropic.com/",
        "note": "Anthropic 原生协议与 OpenAI 略有差异，若不通用请改用「通用 REST」接入。",
    },

    # ================= 工作流 =================
    {
        "key": "coze", "name": "扣子 Coze", "vendor": "字节跳动",
        "category": "flow", "icon": "🫧", "adapter": "coze_workflow",
        "capabilities": ["工作流运行", "知识库检索", "插件调用"],
        "desc": "扣子上配好的工作流直接接进来当能力用，不用重写。",
        "fields": [
            _f("api_key", "个人访问令牌 PAT", type_="password", required=True),
            _f("workflow_id", "工作流 ID", required=True,
               placeholder="7xxxxxxxxxxxxxxxxxx"),
            _f("base_url", "接口地址",
               placeholder="https://api.coze.cn"),
            _f("is_async", "异步模式（1 是 / 0 否）", type_="number",
               placeholder="0"),
        ],
        "docs": "https://www.coze.cn/open/docs/developer_guides/workflow_run",
        "note": "国内版 api.coze.cn，海外版 api.coze.com。工作流 ID 在工作流编辑页 URL 里。",
    },
    {
        "key": "dify", "name": "Dify", "vendor": "Dify",
        "category": "flow", "icon": "🐙", "adapter": "dify_workflow",
        "capabilities": ["工作流运行", "知识库检索", "Agent"],
        "desc": "自己部署的 Dify 也接，工作流当子流程用。",
        "fields": [
            _f("base_url", "服务地址", required=True,
               placeholder="https://dify.你的域名.com"),
            _f("api_key", "API Key（app 密钥）", type_="password", required=True),
        ],
        "docs": "https://docs.dify.ai/",
    },
    {
        "key": "n8n", "name": "n8n", "vendor": "n8n",
        "category": "flow", "icon": "🔧", "adapter": "generic_rest",
        "capabilities": ["自动化编排", "Webhook 触发"],
        "desc": "用 Webhook 触发 n8n 工作流，适合接通知、ERP、爬虫。",
        "fields": [
            _f("webhook_url", "Webhook 地址", required=True,
               placeholder="https://n8n.你的域名.com/webhook/xxx"),
            _f("auth_header", "鉴权头名称", placeholder="X-Api-Key"),
            _f("auth_value", "鉴权值", type_="password"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"text":"{{text}}"}'),
        ],
        "docs": "https://docs.n8n.io/integrations/webhook/",
    },
    {
        "key": "yuanqi", "name": "腾讯元器", "vendor": "腾讯",
        "category": "flow", "icon": "🎏", "adapter": "generic_rest",
        "capabilities": ["智能体发布", "插件调用"],
        "desc": "腾讯的智能体平台，通常通过 Webhook 接入。",
        "fields": [
            _f("webhook_url", "Webhook 地址", required=True),
            _f("auth_header", "鉴权头名称"),
            _f("auth_value", "鉴权值", type_="password"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"query":"{{text}}"}'),
        ],
        "docs": "https://yuanqi.tencent.com/",
    },

    # ================= 语音 =================
    {
        "key": "openai_tts", "name": "OpenAI 语音", "vendor": "OpenAI",
        "category": "voice", "icon": "🔉", "adapter": "generic_rest",
        "capabilities": ["TTS 配音", "语音识别"],
        "desc": "简单好用，配音质量够用。",
        "fields": [
            _f("base_url", "接口地址", placeholder="https://api.openai.com/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("voice", "音色", placeholder="alloy"),
        ],
        "docs": "https://platform.openai.com/docs/guides/text-to-speech",
    },
    {
        "key": "elevenlabs", "name": "ElevenLabs", "vendor": "ElevenLabs",
        "category": "voice", "icon": "🎙️", "adapter": "generic_rest",
        "capabilities": ["TTS 配音", "声音克隆", "配音翻译"],
        "desc": "配音效果最好，声音克隆是杀手锏。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True),
            _f("endpoint", "接口地址",
               placeholder="https://api.elevenlabs.io/v1/text-to-speech"),
            _f("voice_id", "Voice ID"),
        ],
        "docs": "https://elevenlabs.io/docs",
    },
    {
        "key": "aliyun_tts", "name": "阿里云智能语音", "vendor": "阿里云",
        "category": "voice", "icon": "🎐", "adapter": "generic_rest",
        "capabilities": ["TTS 配音", "语音识别"],
        "desc": "国内合规性好，支持多方言。",
        "fields": [
            _f("access_key", "AccessKey", required=True),
            _f("access_secret", "AccessSecret", type_="password", required=True),
            _f("app_id", "AppID"),
            _f("endpoint", "接口地址", required=True),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"text":"{{text}}","format":"mp3"}'),
        ],
        "docs": "https://help.aliyun.com/product/30413.html",
    },

    # ================= 图像与视频 =================
    {
        "key": "jimeng", "name": "即梦 AI", "vendor": "字节跳动",
        "category": "image", "icon": "🎨", "adapter": "generic_rest",
        "capabilities": ["文生图", "图生图", "视频生成"],
        "desc": "字节的创作工具，画面风格讨喜。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True),
            _f("endpoint", "接口地址", required=True),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"prompt":"{{prompt}}"}'),
        ],
        "docs": "https://jimeng.jianying.com/",
    },
    {
        "key": "kling", "name": "可灵 AI", "vendor": "快手",
        "category": "image", "icon": "🎞️", "adapter": "generic_rest",
        "capabilities": ["文生视频", "图生视频"],
        "desc": "视频生成质量高，适合做分镜视频。",
        "fields": [
            _f("api_key", "API Key", type_="password", required=True),
            _f("endpoint", "接口地址", required=True),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"prompt":"{{prompt}}"}'),
        ],
        "docs": "https://klingai.kuaishou.com/",
    },
    {
        "key": "tongyi_wanxiang", "name": "通义万相", "vendor": "阿里云",
        "category": "image", "icon": "🌈", "adapter": "openai_image",
        "capabilities": ["文生图", "图像编辑", "视频"],
        "desc": "阿里云的图像视频大模型，接口与 OpenAI images 兼容。",
        "fields": [
            _f("base_url", "接口地址",
               placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"),
            _f("api_key", "API Key", type_="password", required=True),
            _f("model", "模型名", placeholder="wanx-v1"),
        ],
        "docs": "https://help.aliyun.com/zh/model-studio/",
    },

    # ================= 知识库 =================
    {
        "key": "coze_kb", "name": "扣子知识库", "vendor": "字节跳动",
        "category": "kb", "icon": "🗃️", "adapter": "generic_rest",
        "capabilities": ["知识库检索", "文档问答"],
        "desc": "让 AI 员工去查客户已经维护好的扣子知识库。",
        "fields": [
            _f("api_key", "个人访问令牌 PAT", type_="password", required=True),
            _f("dataset_id", "知识库 ID", required=True),
            _f("base_url", "接口地址", placeholder="https://api.coze.cn"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"query":"{{text}}","dataset_id":"{{dataset_id}}"}'),
        ],
        "docs": "https://www.coze.cn/open/docs/developer_guides/knowledge_search",
    },
    {
        "key": "dify_kb", "name": "Dify 知识库", "vendor": "Dify",
        "category": "kb", "icon": "📗", "adapter": "generic_rest",
        "capabilities": ["知识库检索"],
        "desc": "检索自己部署的 Dify 知识库。",
        "fields": [
            _f("base_url", "服务地址", required=True),
            _f("api_key", "API Key", type_="password", required=True),
            _f("dataset_id", "数据集 ID"),
            _f("endpoint", "检索接口地址", required=True,
               placeholder="https://dify.你的域名.com/v1/datasets/{{dataset_id}}/retrieve"),
        ],
        "docs": "https://docs.dify.ai/",
    },
    {
        "key": "fastgpt", "name": "FastGPT", "vendor": "FastGPT",
        "category": "kb", "icon": "⚡", "adapter": "generic_rest",
        "capabilities": ["知识库检索", "工作流"],
        "desc": "开源知识库，支持 OpenAI 兼容接口。",
        "fields": [
            _f("base_url", "服务地址", required=True),
            _f("api_key", "API Key", type_="password", required=True),
            _f("chat_id", "对话应用 ID"),
            _f("body", "请求体模板", type_="textarea",
               placeholder='{"chatId":"{{chat_id}}","stream":false,"messages":[]}'),
        ],
        "docs": "https://doc.fastgpt.io/",
    },
]


# ---------------- 凭据存取 ----------------

MASKED = "••••••"   # 密码字段回填时的占位符


def _state_file(cfg) -> str:
    return os.path.join(cfg["paths"]["state"], "market.json")


def _read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_provider(key: str) -> Optional[Dict[str, Any]]:
    for p in CATALOG:
        if p["key"] == key:
            return p
    return None


def _env_name(provider_key: str, field_name: str) -> str:
    return f"MARKET_{provider_key.upper()}_{field_name.upper()}"


def load_config(cfg, key: str) -> Dict[str, str]:
    """取某个平台的配置值。

    优先级：环境变量 > 本地文件。
    生产环境走环境变量（不进仓库），本地调试走 market.json。
    """
    p = get_provider(key)
    if not p:
        return {}
    all_cfg = _read_json(_state_file(cfg), {}) or {}
    saved = (all_cfg.get(key) or {}).get("fields") or {}
    out: Dict[str, str] = {}
    for f in p["fields"]:
        name = f["name"]
        env = f.get("env") or _env_name(key, name)
        val = os.environ.get(env) or saved.get(name) or ""
        if val:
            out[name] = str(val)
    return out


def save_config(cfg, key: str, fields: Dict[str, str],
                enabled: bool = True) -> Dict[str, Any]:
    """保存平台配置。"""
    p = get_provider(key)
    if not p:
        return {"ok": False, "error": f"没有这个平台：{key}"}
    all_cfg = _read_json(_state_file(cfg), {}) or {}
    item = all_cfg.get(key) or {}
    merged = dict(item.get("fields") or {})
    for k, v in (fields or {}).items():
        if v is None or v == "":
            continue
        # 密码字段回填的是占位符，值没变就说明「这一项不改」
        if v == MASKED:
            continue
        merged[k] = str(v)
    item["fields"] = merged
    item["enabled"] = bool(enabled)
    item["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    all_cfg[key] = item
    try:
        _write_json(_state_file(cfg), all_cfg)
    except Exception as e:
        return {"ok": False, "error": f"写配置失败：{e}"}
    return {"ok": True, "key": key, "fields": merged, "enabled": bool(enabled)}


def remove_config(cfg, key: str) -> Dict[str, Any]:
    """移除某个平台的接入。"""
    all_cfg = _read_json(_state_file(cfg), {}) or {}
    if key in all_cfg:
        del all_cfg[key]
        try:
            _write_json(_state_file(cfg), all_cfg)
        except Exception as e:
            return {"ok": False, "error": f"写配置失败：{e}"}
    return {"ok": True, "key": key}


def status_of(cfg, key: str) -> Dict[str, Any]:
    """某个平台的接入状态。"""
    p = get_provider(key)
    if not p:
        return {"key": key, "known": False, "configured": False}
    all_cfg = _read_json(_state_file(cfg), {}) or {}
    item = all_cfg.get(key) or {}
    cfgv = load_config(cfg, key)
    missing = [f["label"] for f in p["fields"]
               if f.get("required") and not cfgv.get(f["name"])]
    return {
        "key": key,
        "known": True,
        "configured": not missing,
        "missing": missing,
        "enabled": bool(item.get("enabled", True)),
        "tested_at": item.get("tested_at", ""),
        "test_status": item.get("test_status", ""),
        "test_detail": item.get("test_detail", ""),
        "updated_at": item.get("updated_at", ""),
        "fields_set": sorted(cfgv.keys()),
    }


def list_all(cfg) -> List[Dict[str, Any]]:
    """全部平台 + 各自的接入状态。"""
    out = []
    for p in CATALOG:
        st = status_of(cfg, p["key"])
        out.append({
            "key": p["key"], "name": p["name"], "vendor": p["vendor"],
            "category": p["category"], "icon": p["icon"],
            "adapter": p["adapter"], "desc": p.get("desc", ""),
            "capabilities": p.get("capabilities", []),
            "fields": p["fields"], "docs": p.get("docs", ""),
            "note": p.get("note", ""),
            "config": config_public(cfg, p),
            "status": st,
        })
    return out


def config_public(cfg, provider: Dict[str, Any]) -> Dict[str, str]:
    """回填给前端看的配置值。

    密码类字段不回填原值（怕截图泄露），只给一个占位符。
    保存时如果值等于占位符，就跳过 —— 相当于「不改这一项」。
    """
    out: Dict[str, str] = {}
    cfgv = load_config(cfg, provider["key"])
    for f in provider["fields"]:
        v = cfgv.get(f["name"]) or ""
        if not v:
            continue
        out[f["name"]] = MASKED if f.get("type") == "password" else v
    return out


def connected(cfg) -> List[Dict[str, Any]]:
    """已接入的平台列表。"""
    return [p for p in list_all(cfg) if p["status"]["configured"]]


# ---------------- HTTP 小工具 ----------------

def _http(url: str, method: str = "GET", headers: Optional[Dict] = None,
          body: Optional[Dict] = None, timeout: int = 30) -> Dict[str, Any]:
    """发一个 HTTP 请求，返回 {ok, status, data, text, error}。"""
    data = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore") if e.fp else ""
        status = e.code
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "status": 0}
    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    return {"ok": 200 <= status < 300, "status": status, "data": parsed,
            "text": raw[:4000]}


def _render_template(tpl: str, values: Dict[str, str]) -> str:
    """把 {{字段}} 替换成实际值。"""
    out = tpl or ""
    for k, v in (values or {}).items():
        out = out.replace("{{%s}}" % k, str(v))
    return out


# ---------------- 适配器 ----------------

def _test_openai_chat(cfgv: Dict[str, str]) -> Dict[str, Any]:
    base = (cfgv.get("base_url") or "").rstrip("/")
    key = cfgv.get("api_key") or ""
    model = cfgv.get("model") or "gpt-4o-mini"
    if not base or not key:
        return {"ok": False, "detail": "还缺接口地址或 API Key"}
    r = _http(base + "/chat/completions", "POST",
              {"Authorization": f"Bearer {key}"},
              {"model": model, "messages": [{"role": "user",
                                             "content": "ping"}],
               "max_tokens": 5})
    if r["ok"]:
        return {"ok": True, "detail": f"连通正常，模型 {model} 可用"}
    return {"ok": False, "detail": f"HTTP {r['status']}：{(r.get('text') or r.get('error') or '')[:200]}"}


def _test_openai_image(cfgv: Dict[str, str]) -> Dict[str, Any]:
    base = (cfgv.get("base_url") or "").rstrip("/")
    key = cfgv.get("api_key") or ""
    if not base or not key:
        return {"ok": False, "detail": "还缺接口地址或 API Key"}
    r = _http(base + "/models", "GET", {"Authorization": f"Bearer {key}"})
    if r["ok"]:
        return {"ok": True, "detail": "连通正常，鉴权通过"}
    return {"ok": False, "detail": f"HTTP {r['status']}：{(r.get('text') or r.get('error') or '')[:200]}"}


def _test_coze(cfgv: Dict[str, str]) -> Dict[str, Any]:
    key = cfgv.get("api_key") or ""
    base = (cfgv.get("base_url") or "https://api.coze.cn").rstrip("/")
    wid = cfgv.get("workflow_id") or ""
    if not key or not wid:
        return {"ok": False, "detail": "还缺访问令牌或工作流 ID"}
    r = _http(base + "/v1/workflow/run", "POST",
              {"Authorization": f"Bearer {key}"},
              {"workflow_id": wid, "parameters": {}})
    if r["ok"]:
        return {"ok": True, "detail": "连通正常，工作流可调用"}
    return {"ok": False, "detail": f"HTTP {r['status']}：{(r.get('text') or r.get('error') or '')[:200]}"}


def _test_dify(cfgv: Dict[str, str]) -> Dict[str, Any]:
    base = (cfgv.get("base_url") or "").rstrip("/")
    key = cfgv.get("api_key") or ""
    if not base or not key:
        return {"ok": False, "detail": "还缺服务地址或 API Key"}
    r = _http(base + "/v1/parameters", "GET", {"Authorization": f"Bearer {key}"})
    if r["ok"]:
        return {"ok": True, "detail": "连通正常，Dify 鉴权通过"}
    return {"ok": False, "detail": f"HTTP {r['status']}：{(r.get('text') or r.get('error') or '')[:200]}"}


def _test_heygen(cfgv: Dict[str, str]) -> Dict[str, Any]:
    key = cfgv.get("api_key") or ""
    if not key:
        return {"ok": False, "detail": "还缺 API Key"}
    r = _http("https://api.heygen.com/v2/avatars", "GET",
              {"X-Api-Key": key})
    if r["ok"]:
        return {"ok": True, "detail": "连通正常，HeyGen 鉴权通过"}
    return {"ok": False, "detail": f"HTTP {r['status']}：{(r.get('text') or r.get('error') or '')[:200]}"}


def _test_tavus(cfgv: Dict[str, str]) -> Dict[str, Any]:
    key = cfgv.get("api_key") or ""
    if not key:
        return {"ok": False, "detail": "还缺 API Key"}
    r = _http("https://api.tavus.io/v2/replicas", "GET",
              {"Tavus-API-Key": key})
    if r["ok"]:
        return {"ok": True, "detail": "连通正常，Tavus 鉴权通过"}
    return {"ok": False, "detail": f"HTTP {r['status']}：{(r.get('text') or r.get('error') or '')[:200]}"}


def _test_generic_rest(cfgv: Dict[str, str]) -> Dict[str, Any]:
    ep = (cfgv.get("endpoint") or cfgv.get("webhook_url") or "").strip()
    if not ep:
        return {"ok": False, "detail": "还缺接口地址"}
    headers = {"Content-Type": "application/json"}
    ah = cfgv.get("auth_header")
    av = cfgv.get("auth_value")
    if ah and av:
        headers[ah] = av
    elif cfgv.get("api_key"):
        headers["Authorization"] = f"Bearer {cfgv['api_key']}"
    body_tpl = cfgv.get("body") or ""
    body = None
    if body_tpl:
        try:
            body = json.loads(_render_template(body_tpl, cfgv))
        except Exception:
            body = {"text": _render_template(body_tpl, cfgv)}
    r = _http(ep, "POST", headers, body)
    if r["ok"]:
        return {"ok": True, "detail": "连通正常，接口返回成功"}
    return {"ok": False, "detail": f"HTTP {r['status']}：{(r.get('text') or r.get('error') or '')[:200]}"}


TESTS = {
    "openai_chat": _test_openai_chat,
    "openai_image": _test_openai_image,
    "coze_workflow": _test_coze,
    "dify_workflow": _test_dify,
    "heygen_video": _test_heygen,
    "tavus_replica": _test_tavus,
    "generic_rest": _test_generic_rest,
}


def test_connection(cfg, key: str) -> Dict[str, Any]:
    """测试某个平台能不能连通。"""
    p = get_provider(key)
    if not p:
        return {"ok": False, "error": f"没有这个平台：{key}"}
    cfgv = load_config(cfg, key)
    missing = [f["label"] for f in p["fields"]
               if f.get("required") and not cfgv.get(f["name"])]
    if missing:
        return {"ok": False, "error": "还缺：" + "、".join(missing)}
    fn = TESTS.get(p["adapter"], _test_generic_rest)
    try:
        res = fn(cfgv)
    except Exception as e:
        res = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    # 记一下测试结果，看板上能显示
    all_cfg = _read_json(_state_file(cfg), {}) or {}
    item = all_cfg.get(key) or {}
    item["test_status"] = "ok" if res.get("ok") else "bad"
    item["test_detail"] = res.get("detail", "")
    item["tested_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    all_cfg[key] = item
    try:
        _write_json(_state_file(cfg), all_cfg)
    except Exception:
        pass
    return {"ok": True, "key": key, "result": res}


# ---------------- 统一调用 ----------------

def invoke(cfg, key: str, text: str = "",
           extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """调用一个已接入的平台。

    返回统一格式：
        {"ok": bool, "output": str, "raw": Any, "error": str}
    """
    p = get_provider(key)
    if not p:
        return {"ok": False, "error": f"没有这个平台：{key}",
                "output": "", "raw": None}
    cfgv = load_config(cfg, key)
    missing = [f["label"] for f in p["fields"]
               if f.get("required") and not cfgv.get(f["name"])]
    if missing:
        return {"ok": False, "error": "平台未接入，还缺：" + "、".join(missing),
                "output": "", "raw": None}

    adapter = p["adapter"]
    try:
        if adapter in ("openai_chat",):
            base = (cfgv.get("base_url") or "").rstrip("/")
            model = cfgv.get("model") or "gpt-4o-mini"
            msgs = [{"role": "user", "content": text}]
            if extra and extra.get("system"):
                msgs.insert(0, {"role": "system", "content": extra["system"]})
            r = _http(base + "/chat/completions", "POST",
                      {"Authorization": f"Bearer {cfgv.get('api_key', '')}"},
                      {"model": model, "messages": msgs})
            if not r["ok"]:
                return {"ok": False, "error": f"HTTP {r['status']}",
                        "output": (r.get("text") or "")[:2000], "raw": r.get("data")}
            out = ""
            try:
                out = r["data"]["choices"][0]["message"]["content"]
            except Exception:
                out = str(r.get("data"))[:2000]
            return {"ok": True, "output": out, "raw": r.get("data"), "error": ""}

        if adapter == "coze_workflow":
            base = (cfgv.get("base_url") or "https://api.coze.cn").rstrip("/")
            wid = cfgv.get("workflow_id") or ""
            params = dict(extra or {})
            params.setdefault("input", text)
            r = _http(base + "/v1/workflow/run", "POST",
                      {"Authorization": f"Bearer {cfgv.get('api_key', '')}"},
                      {"workflow_id": wid, "parameters": params})
            if not r["ok"]:
                return {"ok": False, "error": f"HTTP {r['status']}",
                        "output": (r.get("text") or "")[:2000], "raw": r.get("data")}
            data = r.get("data") or {}
            out = ""
            try:
                out = data.get("data", "")
            except Exception:
                out = str(data)[:2000]
            return {"ok": True, "output": out, "raw": data, "error": ""}

        if adapter == "dify_workflow":
            base = (cfgv.get("base_url") or "").rstrip("/")
            payload = {"inputs": dict(extra or {}), "response_mode": "blocking",
                       "user": "console"}
            if text:
                payload["query"] = text
            r = _http(base + "/v1/workflows/run", "POST",
                      {"Authorization": f"Bearer {cfgv.get('api_key', '')}"},
                      payload)
            if not r["ok"]:
                return {"ok": False, "error": f"HTTP {r['status']}",
                        "output": (r.get("text") or "")[:2000], "raw": r.get("data")}
            data = r.get("data") or {}
            out = ""
            try:
                out = data.get("data", {}).get("outputs", {}).get("text", "")
            except Exception:
                out = str(data)[:2000]
            return {"ok": True, "output": out, "raw": data, "error": ""}

        if adapter in ("heygen_video", "tavus_replica"):
            headers = {}
            url = ""
            body = {}
            if adapter == "heygen_video":
                headers["X-Api-Key"] = cfgv.get("api_key", "")
                url = "https://api.heygen.com/v2/video/generate"
                body = {"title": (extra or {}).get("title", "AI员工视频"),
                        "avatar_id": cfgv.get("avatar_id", ""),
                        "voice": {"type": "text", "input": text,
                                  "voice_id": cfgv.get("voice_id", "")}}
            else:
                headers["Tavus-API-Key"] = cfgv.get("api_key", "")
                url = "https://api.tavus.io/v2/videos"
                body = {"replica_id": cfgv.get("replica_id", ""),
                        "video_script": text}
            r = _http(url, "POST", headers, body)
            if not r["ok"]:
                return {"ok": False, "error": f"HTTP {r['status']}",
                        "output": (r.get("text") or "")[:2000], "raw": r.get("data")}
            return {"ok": True,
                    "output": f"任务已提交（异步生成，稍后到平台后台取成品）",
                    "raw": r.get("data"), "error": ""}

        # generic_rest
        ep = (cfgv.get("endpoint") or cfgv.get("webhook_url") or "").strip()
        headers = {"Content-Type": "application/json"}
        ah, av = cfgv.get("auth_header"), cfgv.get("auth_value")
        if ah and av:
            headers[ah] = av
        elif cfgv.get("api_key"):
            headers["Authorization"] = f"Bearer {cfgv['api_key']}"
        body_tpl = cfgv.get("body") or ""
        body = None
        if body_tpl:
            try:
                body = json.loads(_render_template(body_tpl, cfgv))
            except Exception:
                body = {"text": _render_template(body_tpl, cfgv)}
        if isinstance(body, dict):
            body.setdefault("text", text)
        r = _http(ep, "POST", headers, body)
        if not r["ok"]:
            return {"ok": False, "error": f"HTTP {r['status']}",
                    "output": (r.get("text") or "")[:2000], "raw": r.get("data")}
        out = ""
        if isinstance(r.get("data"), (str, int, float)):
            out = str(r["data"])
        elif isinstance(r.get("data"), dict):
            for kk in ("output", "text", "result", "data", "message"):
                if isinstance(r["data"].get(kk), str):
                    out = r["data"][kk]
                    break
            else:
                out = json.dumps(r["data"], ensure_ascii=False)[:2000]
        else:
            out = (r.get("text") or "")[:2000]
        return {"ok": True, "output": out, "raw": r.get("data"), "error": ""}

    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "output": "", "raw": None}


def categories() -> List[Dict[str, Any]]:
    """货架分区。"""
    return CATEGORIES


def category_of(key: str) -> str:
    p = get_provider(key)
    return p["category"] if p else ""


def pick_avatar_provider(cfg) -> Optional[str]:
    """挑一个已接入的数字人平台。

    优先级：先看是不是「已测试通过」，再按清单顺序。
    AI 员工生成数字人视频时用它。
    """
    cands = [p for p in connected(cfg) if p["category"] == "avatar"]
    if not cands:
        return None
    cands.sort(key=lambda p: (p["status"].get("test_status") != "ok",))
    return cands[0]["key"]


def pick_provider(cfg, category: str) -> Optional[str]:
    """挑一个已接入的某类平台。"""
    cands = [p for p in connected(cfg) if p["category"] == category]
    if not cands:
        return None
    cands.sort(key=lambda p: (p["status"].get("test_status") != "ok",))
    return cands[0]["key"]

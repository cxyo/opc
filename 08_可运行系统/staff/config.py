"""配置管理：所有可调参数集中在这里。

设计原则：
1. 密钥永远从 config.json 或环境变量读，不硬编码在代码里
2. 缺配置时给出可执行的报错，而不是 traceback
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve_root() -> str:
    """找出「可运行系统」这个目录该在哪。

    为什么要分两种情况：
      正常跑源码时：staff/ 的上一级就是 08_可运行系统/，直接推。
      打包成 exe 时：PyInstaller 会把程序解到临时目录
        （C:\\Users\\xxx\\AppData\\Local\\Temp\\_MEIxxxxx\\），
        __file__ 指向那个临时目录 —— 岗位、知识库、行业包全都找不到，
        表现为「加载 0 个岗位」「禁区 0 条红线」。
        所以打包运行时要改用 exe 自己所在的目录。

    免安装版的目录布局是：
        AI员工系统/
        ├─ AI员工系统.exe
        ├─ 08_可运行系统/     ← 这一层才是 ROOT
        ├─ 03_知识库/
        └─ 07_AI员工/

    判定顺序：
        1) exe 同级有 08_可运行系统/ → 用它（免安装版标准布局）
        2) exe 同级有 staff/       → 用 exe 同级（exe 直接放在 08_可运行系统/ 里）
        3) exe 同级的上一级有 08_可运行系统/ → 用那个
        4) 兜底用 exe 同级
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands = [
            os.path.join(exe_dir, "08_可运行系统"),
            exe_dir,
            os.path.join(os.path.dirname(exe_dir), "08_可运行系统"),
        ]
        for c in cands:
            if os.path.isdir(os.path.join(c, "config")) or \
               os.path.isdir(os.path.join(c, "staff")):
                return c
        return exe_dir
    return os.path.dirname(HERE)


ROOT = _resolve_root()                    # 08_可运行系统/
PROJECT = os.path.dirname(ROOT)           # 一人AI公司/
WORKSPACE = os.path.dirname(PROJECT)      # 工作区根

CONFIG_PATH = os.path.join(ROOT, "config", "config.json")

DEFAULTS = {
    "provider": "deepseek",
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    # DeepSeek 已于2026-07-24 弃用 deepseek-chat / deepseek-reasoner 旧模型名。
    # 现在对应关系：deepseek-flash（V4.1-Flash，快、便宜）/ deepseek-v4-pro（强、贵）
    "model": "deepseek-flash",
    "reasoner_model": "deepseek-v4-pro",
    "timeout": 180,
    "max_retries": 3,
    "retry_backoff": 5,
    "simulate": False,
    "daily_token_budget": 200000,
    # 模型分级：扫描/检索用便宜模型，创作用好模型
    "model_tier": {
        "scan": "deepseek-flash",
        "create": "deepseek-flash",
        "judge": "deepseek-v4-pro",
        "judge_econ": "deepseek-flash",
        "judge_fast": "deepseek-flash",
    },
    # judge 档实际用哪一档：judge（v4-pro 最高质量）/ judge_econ（flash+思考，均衡）
    # / judge_fast（flash 不思考，最便宜）。质检官是成本大头，这里直接决定总花费。
    "judge_tier": "judge_econ",
    # 思考模式开关：思考链会额外产生输出 token（按输出价计费），成本敏感场景关闭
    "thinking_tier": {
        "scan": False,
        "create": False,
        "judge": True,
        "judge_econ": True,
        "judge_fast": False,
    },
    # 官方单价，单位：元 / 百万 tokens。peak = 高峰时段，offpeak = 空闲时段
    "pricing": {
        "deepseek-flash": {
            "input_cache_hit_peak": 0.04, "input_cache_hit_offpeak": 0.02,
            "input_miss_peak": 2.0, "input_miss_offpeak": 1.0,
            "output_peak": 8.0, "output_offpeak": 4.0,
        },
        "deepseek-v4-pro": {
            "input_cache_hit_peak": 0.30, "input_cache_hit_offpeak": 0.15,
            "input_miss_peak": 9.0, "input_miss_offpeak": 4.5,
            "output_peak": 27.0, "output_offpeak": 13.5,
        },
    },
    "paths": {
        "roles": os.path.join(PROJECT, "07_AI员工", "01_Bot提示词"),
        "forbidden": os.path.join(PROJECT, "03_知识库", "wiki", "10_禁区"),
        "seeds": os.path.join(PROJECT, "02_知识库种子"),
        "kb_wiki": os.path.join(PROJECT, "03_知识库", "wiki"),
        "raw": os.path.join(PROJECT, "03_知识库", "raw"),
        "outputs": os.path.join(ROOT, "data", "outputs"),
        "logs": os.path.join(ROOT, "data", "logs"),
        "state": os.path.join(ROOT, "data", "state"),
    },
}


class ConfigError(Exception):
    """配置缺失或非法。"""


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path=None):
    """读取配置。文件不存在时返回默认配置。"""
    path = path or CONFIG_PATH
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"配置文件格式错误：{path}\n{e}")
    cfg = _deep_merge(DEFAULTS, data)

    # 路径以「按当前工程位置推导」为准。
    # 原因：配置文件里的绝对路径一旦目录改名就全盘失效，
    # 而且用户经常把工程整体搬走。这里让路径永远跟随代码所在位置，
    # 配置文件里存的旧路径只作为「用户自定义路径」的候选保留。
    for k, v in DEFAULTS["paths"].items():
        cfg["paths"][k] = v

    # 环境变量优先于配置文件
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        cfg["api_key"] = env_key
    if os.environ.get("STAFF_SIMULATE") == "1":
        cfg["simulate"] = True

    # 测试实例隔离：把 state / outputs 重定向到独立目录。
    #
    # 为什么需要（2026-10-31 现场问题）：
    #   验证脚本要起自己的控制台跑测试，主控制台（8899）正占着生产锁，
    #   共用锁会被单实例闸门拦下 → 表现为「服务起不来 / 502」。
    #   但就算绕开锁，两个实例共用 state/outputs 也会互相污染：
    #   测试写的产出物会盖掉真实产出。
    #
    #   所以：STAFF_INSTANCE_DIR=<目录> 时，所有可写目录都搬过去。
    #   素材库、配置这些**只读**的目录不动 —— 测试要看到真实素材。
    inst_dir = os.environ.get("STAFF_INSTANCE_DIR", "").strip()
    if inst_dir:
        inst_dir = os.path.abspath(inst_dir)
        for k in ("state", "outputs", "logs", "reports"):
            cfg["paths"][k] = os.path.join(inst_dir, k)

    for p in cfg["paths"].values():
        os.makedirs(p, exist_ok=True)
    return cfg


def save(cfg, path=None):
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def mask_key(k):
    if not k:
        return "(未设置)"
    return k[:7] + "..." + k[-4:] if len(k) > 14 else "***"

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
接入模型网关（new-api）· 一人AI公司
==================================

作用：把「一人AI公司」控制台的模型调用，从直连 DeepSeek 改成走本机 new-api 网关。
好处：以后加图像模型 / Claude / GPT，只在网关加渠道，代码一行不用动。

用法：
    python 工具脚本/接入模型网关.py            # 干跑：只检查和报告，不改任何东西
    python 工具脚本/接入模型网关.py --apply    # 真执行：填渠道 Key + 建令牌 + 输出配置

安全：
    · --apply 之前会先备份 new-api 的 SQLite 数据库
    · 不会修改「一人AI公司」工程里的任何文件（只打印该改的配置，你手动确认）
    · 令牌只在最后打印，不写进工程
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

NEW_API_URL = "http://127.0.0.1:3001"
NEW_API_CONTAINER = "ai-hub-new-api"
NEW_API_DB_IN_CONTAINER = "/data/one-api.db"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_DIR = os.path.join(PROJECT_ROOT, "08_可运行系统")
CONFIG_JSON = os.path.join(SYSTEM_DIR, "config", "config.json")

PROXY = urllib.request.ProxyHandler({})


# ─────────────────────────── 工具函数 ───────────────────────────

def _opener():
    return urllib.request.build_opener(PROXY)


def http_json(url, timeout=8):
    """GET 一个 JSON 接口，失败返回 (None, 错误信息)。"""
    try:
        r = _opener().open(url, timeout=timeout)
        return json.loads(r.read().decode("utf-8", "ignore")), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"


def get_deepseek_key():
    """从工程配置里读出 DeepSeek Key。"""
    if not os.path.exists(CONFIG_JSON):
        return None, f"找不到配置文件：{CONFIG_JSON}"
    try:
        with open(CONFIG_JSON, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        return None, f"读配置失败：{e}"
    key = (cfg.get("api_key") or "").strip()
    if not key:
        key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return None, "配置里没有 api_key，也没设环境变量 DEEPSEEK_API_KEY"
    return key, None


def backup_db(local_db):
    """备份导出的 SQLite 数据库。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = f"{local_db}.bak_{stamp}"
    shutil.copy2(local_db, bak)
    return bak


def export_db(local_db):
    """从容器里把 new-api 的 SQLite 库导到本地。

    关键处理：
    1. 先删掉本地残留的 db/-wal/-shm。SQLite 打开库时会自动应用同目录的
       -wal 文件，若残留的 wal 属于旧库，会与新拷来的主库不匹配，
       直接报 "database disk image is malformed"。
    2. 拷回来后立刻把日志模式切成 DELETE（单文件模式）。
       new-api 的库是 WAL 模式，多连接交替读写时依赖 -shm 索引，
       脱离容器后很容易出现 malformed；切成 DELETE 后本地操作完全自洽。
    """
    for suffix in ("", "-wal", "-shm"):
        p = local_db + suffix
        if os.path.exists(p):
            os.remove(p)
    subprocess.run(
        ["docker", "cp", f"{NEW_API_CONTAINER}:{NEW_API_DB_IN_CONTAINER}", local_db],
        check=True, capture_output=True,
    )
    # 切成单文件日志模式，从此不依赖 wal/shm
    conn = sqlite3.connect(local_db)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.commit()
    conn.close()
    for suffix in ("-wal", "-shm"):
        p = local_db + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def write_back_db(local_db):
    """把改好的库写回容器。

    本地副本已是 DELETE 日志模式，是自洽的单文件，直接整份替换即可。
    同时清掉容器里的旧 wal/shm，避免它们跟新主库打架。
    """
    subprocess.run(
        ["docker", "cp", local_db, f"{NEW_API_CONTAINER}:{NEW_API_DB_IN_CONTAINER}"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["docker", "exec", NEW_API_CONTAINER,
         "rm", "-f", NEW_API_DB_IN_CONTAINER + "-wal", NEW_API_DB_IN_CONTAINER + "-shm"],
        capture_output=True,
    )
    # 换库必须重启 new-api，否则进程还握着旧句柄
    subprocess.run(["docker", "restart", NEW_API_CONTAINER], capture_output=True)
    time.sleep(4)


# ─────────────────────────── 检查 ───────────────────────────

def check_gateway():
    """检查 new-api 是否在线。"""
    data, err = http_json(f"{NEW_API_URL}/api/status")
    if err:
        return False, f"new-api 连不上（{NEW_API_URL}）：{err}"
    return True, "new-api 在线"


def check_docker():
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, "docker 命令不可用"
    names = r.stdout.split()
    if NEW_API_CONTAINER not in names:
        return False, f"容器 {NEW_API_CONTAINER} 没在运行"
    return True, f"容器 {NEW_API_CONTAINER} 运行中"


def list_channels(local_db):
    """列出 new-api 里的渠道。"""
    conn = sqlite3.connect(local_db)
    cur = conn.cursor()
    rows = list(cur.execute(
        "SELECT id, name, type, base_url, models, `key` FROM channels ORDER BY id"))
    conn.close()
    return rows


# ─────────────────────────── 主流程 ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="接入 new-api 模型网关")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只干跑）")
    args = ap.parse_args()

    apply = args.apply
    print("=" * 66)
    print("  一人AI公司 · 接入模型网关（new-api）")
    print(f"  模式：{'【真执行】' if apply else '【干跑】只检查不改任何东西'}")
    print("=" * 66)

    # ── 0. 环境检查 ──
    ok, msg = check_docker()
    print(f"  [{'OK' if ok else '!!'}] Docker / 容器：{msg}")
    if not ok:
        return 1

    ok, msg = check_gateway()
    print(f"  [{'OK' if ok else '!!'}] 网关服务：{msg}")
    if not ok:
        return 1

    key, err = get_deepseek_key()
    if err:
        print(f"  [!!] DeepSeek Key：{err}")
        return 1
    print(f"  [OK] DeepSeek Key：{key[:7]}...{key[-4:]}（{len(key)} 字符）")

    # ── 1. 导出数据库并看现状 ──
    local_db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_newapi_working.db")
    try:
        export_db(local_db)
    except subprocess.CalledProcessError as e:
        print(f"  [!!] 导出数据库失败：{e.stderr.decode('utf-8', 'ignore')[:200]}")
        return 1

    channels = list_channels(local_db)
    print(f"\n  当前渠道 {len(channels)} 个：")
    ds_channel = None
    for cid, name, ctype, base_url, models, ckey in channels:
        placeholder = "PASTE" in (ckey or "").upper()
        tag = "占位符" if placeholder else "已配置"
        print(f"    #{cid} {name:<16} {tag:<8} {base_url}")
        if ckey == key:
            ds_channel = (cid, name, ctype, base_url, models, ckey)
            print(f"        ↑ 这个渠道已经填了正确的 Key，不用改")

    # ── 2. 找 DeepSeek 渠道 ──
    target = ds_channel
    if not target:
        for cid, name, ctype, base_url, models, ckey in channels:
            if "deepseek" in (base_url or "").lower() or "deepseek" in (name or "").lower():
                target = (cid, name, ctype, base_url, models, ckey)
                break

    if not target:
        print("\n  [!!] 没找到 DeepSeek 渠道，无法自动接入。")
        print("      请在 new-api 后台手动新建一个「DeepSeek」渠道，填好 Key，")
        print("      然后重跑本脚本。")
        return 1

    cid, name, ctype, base_url, models, ckey = target
    print(f"\n  目标渠道：#{cid} {name}")

    if not apply:
        print("\n" + "-" * 66)
        print("  干跑结果：以上是当前状态，什么都没改。")
        print("\n  真执行会做这四件事：")
        print(f"    1. 备份 new-api 数据库")
        print(f"    2. 把渠道 #{cid}「{name}」的 Key 换成你的真实 Key")
        print(f"    3. 建一个访问令牌（名字：一人AI公司）")
        print(f"    4. 打印工程里该改的配置（不自动改工程文件）")
        print("\n  确认无误后执行：")
        print("    python 工具脚本/接入模型网关.py --apply")
        print("-" * 66)
        return 0

    # ── 3. 真执行：备份 ──
    bak = backup_db(local_db)
    print(f"\n  [1/3] 已备份数据库 → {os.path.basename(bak)}")

    # ── 4. 写渠道 Key ──
    conn = sqlite3.connect(local_db)
    cur = conn.cursor()
    cur.execute("UPDATE channels SET `key` = ? WHERE id = ?", (key, cid))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    print(f"  [2/3] 已更新渠道 #{cid} 的 Key（{changed} 行）")

    # ── 5. 建令牌 ──
    # 三个坑（都实测踩过）：
    # 1) new-api 的 token.key 存的是【不带 sk- 前缀】的 32 位 hex，
    #    请求时才拼成 sk-<key>。存完整串会导致认证时变成 sk-sk-xxx 而 401。
    # 2) user_id 必须填，否则 new-api 认证时取不到用户，报 "id 为空" 500。
    # 3) 该库的 token.key 上有索引，DELETE / UPDATE key 这两种写法会触发
    #    "database disk image is malformed"。能安全跑的只有：
    #    UPDATE 改状态（软删）+ INSERT 插新行。
    raw_key = os.urandom(16).hex()
    token_key = "sk-" + raw_key
    conn = sqlite3.connect(local_db)
    cur = conn.cursor()
    cols = [d[1] for d in cur.execute("PRAGMA table_info(tokens)")]
    row = {
        "user_id": 1, "name": "一人AI公司", "key": raw_key, "status": 1,
        "created_time": int(time.time()), "accessed_time": int(time.time()),
        "expired_time": -1, "remain_quota": 0, "unlimited_quota": True,
        "used_quota": 0, "model_limits_enabled": False, "model_limits": "",
        "allow_ips": "", "group": "default",
    }
    use = {k: v for k, v in row.items() if k in cols}
    # group / key 等是 SQL 保留字，列名必须加双引号
    quoted = ", ".join(f'"{k}"' for k in use)
    ph = ", ".join("?" for _ in use)
    # 幂等：先把旧的同名令牌软删除（不能 DELETE，key 索引会炸）
    cur.execute(
        "UPDATE tokens SET status = 0, deleted_at = datetime('now') WHERE name = ?",
        ("一人AI公司",))
    cur.execute(f"INSERT INTO tokens ({quoted}) VALUES ({ph})", list(use.values()))
    conn.commit()
    conn.close()
    print("  [3/4] 已创建访问令牌：一人AI公司")

    # ── 6. 开启自用模式 ──
    # 不开自用模式，new-api 会因为「模型价格未配置」直接 400 拒绝调用。
    # 一人公司是单人自用，不需要网关计费，开自用模式最省事。
    conn = sqlite3.connect(local_db)
    cur = conn.cursor()
    cur.execute("UPDATE options SET value = 'true' WHERE `key` = 'SelfUseModeEnabled'")
    if cur.rowcount == 0:
        cur.execute("INSERT INTO options (`key`, value) VALUES ('SelfUseModeEnabled', 'true')")
    conn.commit()
    conn.close()
    print("  [4/4] 已开启网关自用模式（免模型价格检查）")

    # ── 7. 注册工程用的模型名 + 映射到上游真实模型 ──
    # 工程里写死两个模型名：deepseek-flash（主力）、deepseek-v4-pro（质检档）。
    # 这两个名字是 DeepSeek 侧的叫法，网关默认不认，所以：
    #   models        —— 把工程模型名加进渠道可用列表
    #   model_mapping —— 把工程模型名映射到上游渠道真实存在的模型
    # 这样工程里的 model 字段一个字都不用改，费用统计也照旧按原名算。
    PROJECT_MODELS = {"deepseek-flash": "deepseek-chat",
                      "deepseek-v4-pro": "deepseek-reasoner"}
    conn = sqlite3.connect(local_db)
    cur = conn.cursor()
    row = list(cur.execute("SELECT models, model_mapping FROM channels WHERE id = ?", (cid,)))[0]
    have = [m.strip() for m in (row[0] or "").split(",") if m.strip()]
    for m in PROJECT_MODELS:
        if m not in have:
            have.append(m)
    mapping = {}
    if row[1]:
        try:
            mapping = json.loads(row[1])
        except (ValueError, TypeError):
            mapping = {}
    mapping.update(PROJECT_MODELS)
    cur.execute("UPDATE channels SET models = ?, model_mapping = ? WHERE id = ?",
                (",".join(have), json.dumps(mapping, ensure_ascii=False), cid))
    # abilities 表才是 new-api 真正的模型路由表：
    # 只有 channels.models 改了不够，请求时会报 "No available channel for model xxx"。
    for m in PROJECT_MODELS:
        cur.execute(
            "INSERT OR REPLACE INTO abilities (`group`, model, channel_id, enabled, priority, weight, tag) "
            "VALUES ('default', ?, ?, 1, 0, 1, NULL)", (m, cid))
    conn.commit()
    conn.close()
    print(f"  [5/4] 已注册模型路由：{json.dumps(PROJECT_MODELS, ensure_ascii=False)}")

    # ── 6. 写回容器 ──
    print("  正在写回容器并重启 new-api ...")
    write_back_db(local_db)

    ok, msg = check_gateway()
    print(f"  网关重启后：{'在线' if ok else '异常 ' + msg}")

    # ── 8. 给出工程该改的配置 ──
    # 说明：base_url / api_key / model 都是 config.json 的【顶层键】，不在 llm 段里。
    # llm.py 里 url = base_url.rstrip("/") + "/chat/completions"
    # new-api 的 OpenAI 兼容端点在 /v1 下，所以 base_url 必须带 /v1。
    print("\n" + "=" * 66)
    print("  ✅ 接入完成。请手动改一处工程配置：")
    print("=" * 66)
    print(f"  文件：{CONFIG_JSON}")
    print("")
    print("  把这两个【顶层键】改掉（不在 llm 段里，是平级的）：")
    print("")
    print('    "base_url": "http://127.0.0.1:3001/v1",')
    print(f'    "api_key":  "{token_key}",')
    print("")
    print("  · base_url 结尾的 /v1 不能省，代码会自己拼 /chat/completions")
    print("  · model 不用改。网关已注册 deepseek-flash / deepseek-v4-pro，")
    print("    并映射到上游真实模型，工程侧零改动")
    print("")
    print("  改完打开控制台 → 「📊 概览」，确认 API 地址已变成 127.0.0.1:3001。")
    print("  然后去「⚡ 一键成稿」跑一篇，验证产出没坏（应仍是 22 个文件）。")
    print("")
    print(f"  数据库备份：{bak}")
    print("  出问题可回滚：把备份文件拷回容器并重启 new-api")
    print("=" * 66)

    try:
        os.remove(local_db)
        for s in ("-wal", "-shm"):
            if os.path.exists(local_db + s):
                os.remove(local_db + s)
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

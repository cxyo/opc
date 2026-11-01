"""视频号批量元信息抓取器（免登录）。

原理
----
视频号分享页是纯 SPA，HTML 里没有数据。但前端 JS 调的是这个接口：

    POST https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info
    body: {"baseReq":{"generalToken":""},"shortUri":"<链接里的 id>"}

当路径含 /sph 时，前端用 URL 参数 id 作为 shortUri，token 可以为空。
所以只要拿到 weixin.qq.com/sph/xxx 里的 xxx，就能直接换到这条视频的
标题、作者昵称、发布时间、互动数据。

用法
----
    python fetch_sph.py ID1 ID2 ID3
    python fetch_sph.py --file ids.txt --json out.json
    python fetch_sph.py --file new_ids.txt --append   # 增量：并入情报库，不覆盖旧数据
    python fetch_sph.py ID1 --cover                  # 同时下载封面图到 tools/sph_covers/

能力边界（重要，实测结论）
----------------------------
    这个接口**只能拿到元信息和封面图，拿不到视频本体**：
      - get_feed_info 的响应里只有 description / coverUrl / 互动数，
        没有 video / media / m3u8 字段
      - 视频流地址只在微信客户端内才下发，
        预览页设计成"可扫码前往微信观看此内容"
      - 导出类接口（export_dynamic）需要微信登录态签名，
        外部直连返回 401 permission verification failed

    所以：**凡是需要口播稿、分镜、逐字金句的分析，必须人工补料。**
    别拿脚本抓不到的东西编内容 —— 拆解官的铁律是忠于原文。
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

API = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _rid():
    ts = format(int(time.time()), "x")
    return f"{ts}-{random.randint(0, 0xffffffff):08x}"


def _ts(unix):
    try:
        return datetime.fromtimestamp(int(unix)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def fetch_one(sid, retries=3):
    """抓一条视频的元信息。返回 dict。"""
    url = (f"{API}?_rid={_rid()}&_pageUrl=https:%2F%2Fchannels.weixin.qq.com"
           f"%2Ffinder-preview%2Fpages%2Ffeed")
    body = json.dumps({"baseReq": {"generalToken": ""},
                       "shortUri": sid}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": f"https://channels.weixin.qq.com/finder-preview/pages/sph?id={sid}",
        "User-Agent": UA,
    }
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            data = raw.get("data") or {}
            fi = data.get("feedInfo") or {}
            ai = data.get("authorInfo") or {}
            desc = (fi.get("description") or "").strip()
            if not desc and not ai.get("nickname"):
                err = data.get("errMsg") or {}
                last = err.get("title") or "无描述"
            else:
                return {
                    "id": sid,
                    "ok": True,
                    "title": desc,
                    "author": ai.get("nickname", ""),
                    "date": _ts(fi.get("createtime")),
                    "location": (fi.get("jumpInfo") or {}).get("wording", ""),
                    "like": fi.get("likeCountFmt", ""),
                    "comment": fi.get("commentCountFmt", ""),
                    "forward": fi.get("forwardCountFmt", ""),
                    "fav": fi.get("favCountFmt", ""),
                    "cover": fi.get("coverUrl", ""),
                    "url": f"https://weixin.qq.com/sph/{sid}",
                }
        except Exception as e:
            last = str(e)
        time.sleep(0.6 + attempt * 0.8)
    return {"id": sid, "ok": False, "error": last}


def download_cover(rec, outdir):
    """下载封面图。

    为什么单独强调：视频号预览接口**只下发封面和文案，不下发视频流地址**。
    封面是唯一能拿到的视觉素材，所以必须抓下来存进素材库，
    否则这条情报只剩几个数字，等于什么都没留下。

    封面 URL 带 token，有时效（约几十分钟），过期后会返回 400。
    """
    url = rec.get("cover") or ""
    if not url:
        return None
    sid = rec.get("id", "x")
    ext = ".jpg"
    if ".png" in url[:200].lower():
        ext = ".png"
    path = os.path.join(outdir, f"{sid}{ext}")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://channels.weixin.qq.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()
        if not data or data[:2] == b"{}":
            return None
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception:
        return None


def main(argv):
    ids = []
    append = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--file="):
            with open(a.split("=", 1)[1], "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        ids.append(line)
            i += 1
            continue
        if a == "--file":
            # 空格形式：下一个参数是文件名，必须消费掉，
            # 否则文件名会被当成视频 ID 去请求（报"此内容暂时无法播放"）
            if i + 1 < len(argv):
                with open(argv[i + 1], "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            ids.append(line)
                i += 2
                continue
            i += 1
            continue
        if a == "--append":
            append = True
            i += 1
            continue
        if a == "--cover":
            # 开关型参数，不消费后面的内容，
            # 否则 "--cover" 会被当成视频 ID 去请求（报"此内容暂时无法播放"）
            i += 1
            continue
        # 兼容直接贴链接
        for part in a.replace(",", " ").split():
            if "weixin.qq.com/sph/" in part:
                part = part.split("/sph/")[-1].strip("/?&= ")
            if part:
                ids.append(part)
        i += 1

    if not ids:
        print("没有 ID。用法：python fetch_sph.py ID1 ID2 ...")
        return 1

    # 去重保序
    seen = set()
    uniq = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)

    results = []
    for n, sid in enumerate(uniq, 1):
        r = fetch_one(sid)
        results.append(r)
        mark = "✓" if r["ok"] else "✗"
        if r["ok"]:
            t = r["title"].split("\n")[0][:70]
            print(f"[{n}/{len(uniq)}] {mark} {sid}  {r['author']}  {r['date']}  {t}",
                  flush=True)
        else:
            print(f"[{n}/{len(uniq)}] {mark} {sid}  {r.get('error')}", flush=True)
        time.sleep(0.25)

    ok = sum(1 for r in results if r["ok"])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sph_meta.json")

    # 增量模式：与已有数据合并（同 ID 以新数据覆盖），情报库只增不减
    if append and os.path.exists(out):
        try:
            with open(out, "r", encoding="utf-8") as f:
                old = json.load(f)
            merged = {x["id"]: x for x in old if x.get("id")}
            for x in results:
                if x.get("id"):
                    merged[x["id"]] = x
            results = list(merged.values())
            ok = sum(1 for r in results if r["ok"])
            print(f"\n合并后情报库共 {len(results)} 条")
        except Exception as e:
            print(f"合并失败，改为覆盖：{e}")

    print(f"成功 {ok}/{len(results)}")

    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"已保存：{out}")

    # ---- 封面下载（可选）----
    # 封面是唯一能拿到的视觉素材，值得单独存一份进素材库。
    if "--cover" in argv:
        cov_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "sph_covers")
        os.makedirs(cov_dir, exist_ok=True)
        n = 0
        for rec in results:
            if not rec.get("ok"):
                continue
            p = download_cover(rec, cov_dir)
            if p:
                n += 1
        print(f"封面已下载 {n}/{ok} 张 → {cov_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

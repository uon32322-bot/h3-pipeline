#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TT Image 2 (灵炫国际站) 接入封装 —— 只用标准库，无依赖。

用法:
  python3 ttimg.py gen  <out.png> <size> <prompt_file>
  python3 ttimg.py edit <out.png> <size> <prompt_file> <ref1.png> [ref2.png ...]

size 只稳定决定宽高比档位，实际像素以下载到的图片为准。
"""
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.lk888.ai"
KEY = os.environ.get("LK888_KEY", "sk-dafee1450167a9d3ca0d9ed0224b313ae054f49437836a55")
MODEL = "tt-image-2"


def _post(path, payload, timeout=120):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path, timeout=60):
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": "Bearer " + KEY}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def as_data_uri(path):
    """本地文件 -> data URI；若传入 http(s) 链接则原样返回。"""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))


def _is_retryable(e):
    """是否值得重试：网络中断/TLS 抖动/超时/5xx/429 可重试；4xx（除 429）不重试。"""
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500 or e.code == 429
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def _retry(fn, tries=4, base=1.6, label="req"):
    """指数退避重试。实测：长任务轮询会被中间设备掐断（SSL UNEXPECTED_EOF），
    而服务端任务其实仍在跑 —— 不重试就会白白报废一张图。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if not _is_retryable(e):
                raise
            last = e
            if i < tries - 1:
                w = base ** (i + 1)
                print("   ⚠️ %s 网络异常 %s → %.1fs 后重试 (%d/%d)"
                      % (label, type(e).__name__, w, i + 1, tries - 1), flush=True)
                time.sleep(w)
    raise last


def create_task(prompt, size, images=None, quality="auto"):
    params = {"size": size, "quality": quality, "n": 1}
    if images:
        params["images"] = images
    payload = {"model": MODEL, "prompt": prompt, "params": params}
    return _retry(lambda: _post("/v1/media/generate", payload), label="create")


def poll(task_id, interval=4, timeout=420, verbose=True):
    t0 = time.time()
    last = None
    net_fail = 0
    path = "/v1/media/status?task_id=%s" % urllib.parse.quote(str(task_id))
    while time.time() - t0 < timeout:
        try:
            r = _retry(lambda: _get(path), tries=3, label="status")
            net_fail = 0
        except Exception as e:
            # 网络持续异常 ≠ 任务失败：服务端仍在推进，继续等，不要误判成废片
            net_fail += 1
            print("   ⚠️ 轮询连续失败 %d 次（%s），任务仍在服务端运行" % (net_fail, type(e).__name__), flush=True)
            if net_fail >= 5:
                raise
            time.sleep(interval * 2)
            continue
        state = r.get("state")
        prog = r.get("progress")
        if verbose and prog != last:
            print("   [%4.0fs] state=%s progress=%s" % (time.time() - t0, state, prog), flush=True)
            last = prog
        if r.get("is_final"):
            return r
        time.sleep(interval)
    raise TimeoutError("task %s 超时未终态" % task_id)


def download(url, out_path):
    def _once():
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=180) as r, open(out_path, "wb") as f:
            f.write(r.read())
        return os.path.getsize(out_path)
    return _retry(_once, label="download")


def run(out_path, size, prompt, refs=None):
    imgs = [as_data_uri(p) for p in refs] if refs else None
    print("提交任务: size=%s refs=%s" % (size, len(imgs or [])), flush=True)
    r = create_task(prompt, size, imgs)
    if r.get("code") != 200:
        print("创建失败:", json.dumps(r, ensure_ascii=False)[:600])
        return None
    tid = r["data"]["task_id"]
    print("task_id=%s 轮询中..." % tid, flush=True)
    st = poll(tid)
    if st.get("state") != "success":
        print("任务失败:", json.dumps(st, ensure_ascii=False)[:600])
        return None
    url = st.get("result_url")
    n = download(url, out_path)
    print("✅ 已保存 %s (%d bytes) cost=%s" % (out_path, n, st.get("cost")), flush=True)
    print("   result_url=%s" % url)
    return out_path


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)
    mode, out_path, size, pf = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    prompt = open(pf, encoding="utf-8").read().strip()
    refs = sys.argv[5:] or None
    if mode == "gen":
        run(out_path, size, prompt)
    elif mode == "edit":
        if not refs:
            print("edit 模式必须给参考图")
            sys.exit(1)
        run(out_path, size, prompt, refs)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

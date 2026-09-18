#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gpu_lease.py — 全局 GPU 仲裁器的薄命令行包裹（h3p 项目侧）

⚠️⚠️ 本文件【不实现任何锁逻辑】，一行都没有。
   互斥、排队、优先级、心跳续租、过期回收、死锁兜底，全部由机器自带的
       /opt/shared/gpu_arbiter.py   (服务端, 监听 127.0.0.1:6200)
       /opt/shared/gpu_lock.py      (官方客户端库)
   承担 —— 同机 digital-human / h3-gateway / heygem / tts 四个服务用的就是同一套。
   （/opt/shared 是指向 /root/autodl-tmp/relocate/shared 的软链，同一份文件。）

   本文件只做两件事：
     ① 把命令行参数翻译成 gpu_lock.acquire() 的入参
     ② 用 try/finally 保证租约一定被释放（含 Ctrl-C / SIGTERM）

   为什么需要它：gpu_lock.py 是【库】没有 CLI，而 shell 侧（run_*.sh）需要
   一个能跨进程持有并释放租约的入口。

用法:
  gpu_lease.py status                      # 看当前谁持有、队列里有谁（只读）
  gpu_lease.py probe [选项]                # 申请一次租约立刻释放，验证链路
  gpu_lease.py run [选项] -- CMD [ARGS]    # 持租约跑任意命令，结束自动释放

  选项（status 之外都可用）:
    --priority N     优先级，【越小越优先】，默认 1（与 dh 平等，不插队）
    --vram-mb N      名义显存 MB，默认 21000（上限 22528）
    --label S        租约标签，格式 <stage>:<detail>，默认 h3p:cmd
    --timeout S      排队最长等待秒数，默认 7200
    --allow-flock    允许 arbiter 不可达时回退 flock（危险，见下）

⚠️ 两个必须知道的坑（都已在 acquire_lease() 里处理）：

  坑 1｜gpu_lock 会「超时即回退 flock」
      gpu_lock.acquire() 在服务端排队超时（ok=False）时 raise TimeoutError，
      而这个异常被它自己的 `except Exception` 吞掉 ⇒ 静默回退 flock。
      但「排队超时」≠「仲裁器挂了」。
  坑 2｜本机 arbiter 的 flock 层是空转的（既有缺陷，非本文件引入）
      gpu_arbiter.py 的 check_foreign_lock() 每 10s 用同一个 fd 对自己刚持有的
      gpu.lock 做 try_lock() + unlock()。flock 对同一 fd 是幂等的 ⇒ try_lock()
      必成功 ⇒ 紧接着的 unlock() 把锁【解开】。实测 /proc/locks 里 gpu.lock
      长期无记录。⇒ 超时回退拿到的 flock 是一把【假锁】。

  两者叠加 = 「排队超时 → 拿到假锁 → 与 dh 并发抢显存」，正是要避免的事。
  ⇒ acquire_lease() 的对策：只要仲裁器可达，就丢弃 flock 假锁并【分段重排】，
    直到总超时；只有仲裁器真不可达且显式 --allow-flock 才接受 flock。

  对「接入 arbiter 的进程之间」，互斥仍然有效 —— /acquire 的排队由服务端
  active/queue 状态机保证，不依赖 flock。flock 失效只影响「挡野蛮进程」。

示例:
  ./gpu_lease.py status
  ./gpu_lease.py --label h3p:calib run -- /root/autodl-tmp/h3p/venv/bin/python gen.py
  ./gpu_lease.py run --label h3p:film -- bash build_film.sh      # 两种位置都接受
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import types
import urllib.request

SHARED_DIR = os.environ.get("GPU_SHARED_DIR", "/opt/shared")
ARBITER = os.environ.get("GPU_ARBITER_URL", "http://127.0.0.1:6200")
LOCK_FILE = os.environ.get("GPU_LOCK_FILE", "/opt/shared/gpu.lock")
PROJECT = os.environ.get("H3P_GPU_PROJECT", "h3p")
FLOCK_TTL_WARN = 3600
SEG_CAP = 300.0     # 单段排队上限：每段超时后回查一次、重排，避免长时间盲等


class LeaseError(RuntimeError):
    """拿不到可用租约（排队超时 / 仲裁器失联 / 拒绝 flock 回退）。"""


def arbiter_status(timeout=6):
    """GET /status；不可达返回 None（只读探测，不改变任何状态）。"""
    try:
        with urllib.request.urlopen(ARBITER + "/status", timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def _describe_holder(st):
    act = (st or {}).get("active")
    if not act:
        return "无人持有"
    return "project=%s label=%s prio=%s vram=%sMB" % (
        act.get("project"), act.get("label"), act.get("priority"), act.get("vram_mb"))


def acquire_lease(priority=1, vram_mb=21000, timeout=7200.0, label="h3p:cmd",
                  allow_flock=False, log=print, seg_cap=SEG_CAP):
    """拿 GPU 租约。**唯一入口是现成的 gpu_lock.acquire()**，本函数不实现锁。

    ⚠️ 为什么要包一层循环（不是重造轮子，是补 gpu_lock 的行为缺口）：
       gpu_lock.acquire() 在 /acquire 返回 ok=False（= 服务端排队超时）时会
       raise TimeoutError，而这个异常被它自己的 `except Exception` 吞掉
       ⇒【静默回退 flock】。它假设"拿不到 arbiter = arbiter 挂了"，但排队超时
       并不等于 arbiter 挂了。

       更麻烦的是本机 arbiter 有个已知缺陷：check_foreign_lock() 每 10s 用同一个
       文件描述符对自己刚持有的 gpu.lock 做 try_lock()+unlock()，因为 flock 对
       同一 fd 是幂等的 ⇒ 每次都会把锁【解开】。实测 /proc/locks 里 gpu.lock
       常无记录。所以超时回退拿到的 flock 是一把【假锁】—— 拿着它就会与 dh
       并发抢显存，正是我们要避免的事。

       ⇒ 本函数：只要 arbiter 可达，就【丢弃 flock 假锁并继续排队】，直到总超时。
         只有 arbiter 真不可达（且显式 --allow-flock）才接受 flock。
    """
    gpu_lock = _load_gpu_lock_checked()
    os.environ.setdefault("GPU_PROJECT", PROJECT)

    st = arbiter_status()
    if st is None:
        log("[lease] ⚠️ 仲裁器不可达 %s" % ARBITER)
        if not allow_flock:
            raise LeaseError(
                "⛔ 仲裁器不可达，且未允许 flock 回退。\n"
                "   长任务（H3 长片 1h+）在 flock 模式下会被 STALE_LOCK_TIMEOUT(%ds) 判死锁强杀。\n"
                "   处置：① gpu_lease.py status 检查；② 重启 arbiter：\n"
                "         nohup python3 %s/gpu_arbiter.py >> %s/arbiter.log 2>&1 &\n"
                "   ③ 短片/调试确要冒险：加 --allow-flock" % (FLOCK_TTL_WARN, SHARED_DIR, SHARED_DIR))
        log("[lease] 已显式允许 flock 回退；⚠️ 无心跳，超 %ds 会被仲裁器当死锁强杀" % FLOCK_TTL_WARN)
        lease = gpu_lock.acquire(block=True, timeout=timeout, priority=priority,
                                 vram_mb=vram_mb, label=label)
        log("[lease] ✅ mode=%s lease_id=%s" % (lease.get("mode"), lease.get("lease_id")))
        return lease

    log("[lease] 仲裁器在线；当前持有：%s" % _describe_holder(st))
    log("[lease] 申请租约 project=%s label=%s priority=%d vram=%dMB，最长排队 %ds"
        % (os.environ.get("GPU_PROJECT"), label, priority, vram_mb, int(timeout)))
    log("[lease] （排队期间阻塞等待，完全不碰 GPU；不会去抢显存）")

    t0 = time.time()
    seg = max(5.0, min(float(seg_cap), float(timeout)))
    seg_no = 0
    while True:
        remain = timeout - (time.time() - t0)
        if remain <= 2:
            raise LeaseError("⛔ 排队超时：等了 %.0fs 仍未取得 arbiter 租约（当前 %s）"
                             % (time.time() - t0, _describe_holder(arbiter_status())))
        seg_no += 1
        try:
            lease = gpu_lock.acquire(block=True, timeout=min(seg, remain),
                                     priority=priority, vram_mb=vram_mb, label=label)
        except Exception as e:
            # 段超时（gpu_lock 抛 TimeoutError）或 arbiter 抖动
            if arbiter_status() is None:
                raise LeaseError("⛔ 排队中仲裁器失联：%s" % e)
            continue

        if lease and lease.get("mode") == "arbiter":
            log("[lease] ✅ 拿到 arbiter 租约 lease_id=%s（排队 %.0fs）"
                % (lease.get("lease_id"), time.time() - t0))
            return lease

        # 走到这里 = gpu_lock 把"排队超时"误当"arbiter 故障"回退成了 flock。
        # 本机 flock 是假锁（见上方 docstring）⇒ 立刻还回去，绝不能拿着跑。
        try:
            gpu_lock.release(lease)
        except Exception:
            pass
        log("[lease] …排队中（已 %.0fs，第 %d 段超时；当前 %s）— 已丢弃 flock 假锁并继续排"
            % (time.time() - t0, seg_no, _describe_holder(arbiter_status())))


def release_lease(lease, log=print):
    """释放租约（幂等）。进程被 SIGKILL 时仲裁器会在 LEASE_TTL 后自动回收。"""
    if lease is None:
        return
    try:
        gpu_lock = _load_gpu_lock()
        gpu_lock.release(lease)
        log("[lease] 🔓 已释放租约 %s" % lease.get("lease_id"))
    except Exception as e:
        log("[lease] ⚠️ 释放租约异常（仲裁器将按 TTL 兜底回收）: %s" % e)

OPTS = {
    "--priority": ("priority", int, 1),
    "--vram-mb": ("vram_mb", int, 21000),
    "--label": ("label", str, "h3p:cmd"),
    "--timeout": ("timeout", float, 7200.0),
}


def _load_gpu_lock():
    if SHARED_DIR not in sys.path:
        sys.path.insert(0, SHARED_DIR)
    import gpu_lock
    return gpu_lock


def _load_gpu_lock_checked():
    try:
        return _load_gpu_lock()
    except ImportError as e:
        raise LeaseError(
            "⛔ 找不到 gpu_lock.py（期望路径 %s/gpu_lock.py）: %s\n"
            "   本机是共享 GPU，必须接入全局仲裁器；同机 digital-human / h3-gateway / "
            "heygem / tts 用的就是这套。\n"
            "   处置：确认 /opt/shared（→ /root/autodl-tmp/relocate/shared）里有 "
            "gpu_lock.py 与 gpu_arbiter.py。" % (SHARED_DIR, e))


def _reconfigure_stdout():
    """日志重定向到文件时 Python 默认是块缓冲（4–8 KB），排一小时队都看不到一行进度。
    改成行缓冲，让 `nohup ... > log 2>&1 &` 能实时 tail —— 排长队时这点很重要。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(line_buffering=True)
        except Exception:
            pass


def _install_signal_handlers():
    """SIGTERM/SIGHUP 默认会直接终止进程、跳过 finally ⇒ 租约要等 TTL 才回收。
    转成 SystemExit 让 finally 正常执行。"""
    def _h(signum, frame):
        raise SystemExit(128 + signum)
    for s in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(s, _h)
        except Exception:
            pass


def split_args(args):
    """把参数拆成 (opts, cmd)。遇到 '--' 则其后全部归 cmd（原样透传）。

    手写而不走 argparse 子命令：`run -- CMD` 这种透传语义用 argparse 会被
    '--' 之后的未知选项干扰，手写更确定。
    """
    opts = types.SimpleNamespace(**{k[0]: k[2] for k in OPTS.values()})
    opts.allow_flock = False
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--":
            return opts, args[i + 1:]
        if t in OPTS:
            name, cast, _ = OPTS[t]
            i += 1
            if i >= len(args):
                raise SystemExit("缺参数值: %s" % t)
            try:
                setattr(opts, name, cast(args[i]))
            except ValueError:
                raise SystemExit("%s 的值不合法: %r" % (t, args[i]))
            i += 1
            continue
        if t == "--allow-flock":
            opts.allow_flock = True
            i += 1
            continue
        # 其它（裸命令 / 未知选项）一律当作命令起点
        return opts, args[i:]
    return opts, []


def cmd_status(_o):
    st = arbiter_status()
    if st is None:
        print("⛔ 仲裁器不可达 %s" % ARBITER)
        print("   ⇒ gpu_lock 会回退 flock；长任务请先修好仲裁器再跑：")
        print("     nohup python3 %s/gpu_arbiter.py >> %s/arbiter.log 2>&1 &" % (SHARED_DIR, SHARED_DIR))
        return 2
    act = st.get("active")
    print("=== GPU 仲裁器 %s ===" % ARBITER)
    print("总显存 %d MB（保留 %d MB）" % (st.get("vram_total_mb", 0), st.get("vram_reserve_mb", 0)))
    if act:
        print("🟢 当前持有: project=%s stage=%s label=%s priority=%s vram=%sMB lease=%s"
              % (act.get("project"), act.get("stage"), act.get("label"),
                 act.get("priority"), act.get("vram_mb"), act.get("lease_id")))
        g = act.get("granted_at")
        if g:
            print("   已持有 %.0fs" % max(0.0, time.time() - g))
    else:
        print("⚪ 当前无人持有（卡空闲，acquire 会立即返回）")
    q = st.get("queue") or []
    if q:
        print("⏳ 排队中 %d 个:" % len(q))
        for i, w in enumerate(q, 1):
            print("   %d) project=%s label=%s priority=%s vram=%sMB 已等 %ss"
                  % (i, w.get("project"), w.get("label"), w.get("priority"),
                     w.get("vram_mb"), w.get("wait_s", "?")))
    else:
        print("   队列为空")
    # ⚠️ 已知缺陷提示：flock 层是空的说明 arbiter 的物理锁被自己的 sweeper 误释了
    if act and not _flock_held():
        print("⚠️ 注：/opt/shared/gpu.lock 当前【无常驻 flock】—— 本机 arbiter 的")
        print("   check_foreign_lock 每 10s 会把自持有的锁 unlock 掉（已知缺陷）。")
        print("   接入 arbiter 的进程之间仍受 /acquire 排队保护；")
        print("   但 flock 挡不住不接入 arbiter 的野蛮进程。")
    return 0


def _flock_held():
    """只读检查 gpu.lock 是否真被外部 flock 持有（用于诊断，不长期持锁）。"""
    fd = None
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except OSError:
            return True
    except Exception:
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass


def _acquire(o, label):
    lease = acquire_lease(priority=o.priority, vram_mb=o.vram_mb, timeout=o.timeout,
                          label=label, allow_flock=o.allow_flock)
    return lease


def cmd_probe(o):
    """申请→立刻释放：验证「仲裁器可达 + 能拿到 arbiter 租约 + 能干净释放」三件事。"""
    lease = _acquire(o, o.label)
    release_lease(lease)
    print("✅ 链路验证通过：acquire / release 均正常（mode=%s）" % lease.get("mode"))
    return 0


def cmd_run(o, cmd):
    if not cmd:
        print("用法: gpu_lease.py run [选项] -- CMD [ARGS...]")
        return 2
    try:
        lease = _acquire(o, o.label)
    except LeaseError as e:
        print(str(e))
        return 3
    t0 = time.time()
    try:
        print("[lease] ▶️ 持租约执行: %s" % " ".join(cmd))
        return subprocess.call(cmd)
    finally:
        print("[lease] 命令耗时 %.1fs" % (time.time() - t0))
        release_lease(lease)


def find_subcommand(argv):
    """定位子命令：允许它出现在选项之前或之后。

    例：`--timeout 15 --label x run -- cmd` 与 `run --timeout 15 -- cmd` 等价。
    遇到 '--' 即以命令起点计，说明没写子命令，返回 None。
    """
    i = 0
    while i < len(argv):
        t = argv[i]
        if t in OPTS:
            i += 2
            continue
        if t == "--allow-flock":
            i += 1
            continue
        if t in ("-h", "--help"):
            i += 1
            continue
        if t == "--":
            return None
        return i
    return None


def main():
    _reconfigure_stdout()
    _install_signal_handlers()
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0 if argv else 1
    idx = find_subcommand(argv)
    if idx is None:
        print("缺少子命令（status | probe | run）\n")
        print(__doc__)
        return 2
    sub = argv[idx]
    if sub not in ("status", "probe", "run"):
        print("未知子命令: %s\n" % sub)
        print(__doc__)
        return 2
    rest = argv[:idx] + argv[idx + 1:]      # 其余参数（选项 + 命令）保持原顺序
    opts, cmd = split_args(rest)
    try:
        if sub == "status":
            return cmd_status(opts)
        if sub == "probe":
            return cmd_probe(opts)
        return cmd_run(opts, cmd)
    except LeaseError as e:
        print(str(e))
        return 3


if __name__ == "__main__":
    sys.exit(main())

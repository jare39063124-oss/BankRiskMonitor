"""R5 退出回收 + 单实例验证脚本（Windows）。

验证项（ARCH-v2-increment 附录 A.2）：
1) **关闭窗口后进程及时退出**，任务管理器无 ``BankRiskMonitor.exe`` /
   ``QtWebEngineProcess.exe`` 残留；
2) **反复开关 N 次无进程堆积**；
3) **双击启动两次 → 仅 1 实例**，第二实例激活已有窗口后立刻退出（不新建窗口）。

两种模式：
* ``real``      ：存在打包 exe（默认 ``dist/BankRiskMonitor.exe``）时，启动 exe，
                  用 Windows API（``ctypes`` ``EnumWindows``）等待/统计窗口，
                  用 ``WM_CLOSE`` 触发 ``closeEvent`` 关闭，再 ``tasklist`` 断言无残留；
* ``offscreen`` ：无 exe 时以 ``python main.py``（``QT_QPA_PLATFORM=offscreen`` +
                  ``BRM_AUTOCLOSE_MS``）运行，验证 ``closeEvent → 进程退出`` 路径；
                  单实例用两个子进程验证（第二实例应秒退）。

用法::

    python tools/verify_shutdown.py                     # 自动选择模式
    python tools/verify_shutdown.py --mode offscreen
    python tools/verify_shutdown.py --mode real --runs 10
    python tools/verify_shutdown.py --mode real --exe dist/BankRiskMonitor.exe

退出码：``0``=全部通过；``1``=存在断言失败；``2``=环境不具备（非 Windows / 无法启动）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_TITLE = "银行客户风险监控工作台"
EXE_NAME = "BankRiskMonitor.exe"
WEBENGINE_NAME = "QtWebEngineProcess.exe"
WM_CLOSE = 0x0010


# ---------------------------------------------------------------------------
# Windows 断言工具
# ---------------------------------------------------------------------------
def image_running(image: str) -> bool:
    """``tasklist`` 断言某镜像是否有进程在运行（同时匹配 PID 行）。"""
    try:
        raw = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
            capture_output=True, timeout=30).stdout or b""
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] tasklist 调用失败：{exc}")
        return False
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    return image.lower() in text.lower()


def _enum_top_windows():
    """枚举当前可见顶层窗口，返回 [(hwnd, title)]。"""
    user32 = ctypes.windll.user32
    result = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        result.append((hwnd, buf.value))
        return True

    user32.EnumWindows(proc_type(_cb), 0)
    return result


def count_windows_with_title(substr: str) -> int:
    """统计标题含 ``substr`` 的可见顶层窗口数（ctypes EnumWindows）。"""
    return sum(1 for _hwnd, title in _enum_top_windows() if substr in title)


def post_close_by_title(substr: str) -> int:
    """向标题含 ``substr`` 的可见顶层窗口发送 ``WM_CLOSE``，返回发送数量。"""
    user32 = ctypes.windll.user32
    sent = 0
    for hwnd, title in _enum_top_windows():
        if substr in title:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            sent += 1
    return sent


def wait_for(predicate, timeout_s: float, interval_s: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


# ---------------------------------------------------------------------------
# 模式一：打包 exe（真实窗口 + WM_CLOSE + tasklist）
# ---------------------------------------------------------------------------
def verify_real(exe: str, runs: int) -> bool:
    print(f"[real] 使用 exe：{exe}")
    if not os.path.exists(exe):
        print(f"  [fail] exe 不存在：{exe}")
        return False
    ok = True
    cwd = os.path.dirname(exe) or PROJECT_ROOT

    for i in range(1, runs + 1):
        print(f"  run {i}/{runs}：启动 exe…")
        proc = subprocess.Popen([exe], cwd=cwd)
        appeared = wait_for(lambda: count_windows_with_title(APP_TITLE) >= 1, 30.0)
        if not appeared:
            print("    [fail] 30s 内未出现主窗口")
            proc.kill()
            ok = False
            continue

        # 单实例：再启动一次，应不新建窗口且第二进程迅速退出
        print("    启动第二个实例（应被拒绝并激活已有窗口）…")
        before = count_windows_with_title(APP_TITLE)
        proc2 = subprocess.Popen([exe], cwd=cwd)
        try:
            rc2 = proc2.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc2.kill()
            rc2 = None
        time.sleep(0.6)
        after = count_windows_with_title(APP_TITLE)
        if rc2 is None:
            print("    [fail] 第二实例未退出（超时）")
            ok = False
        if after != before:
            print(f"    [fail] 窗口数变化：{before} → {after}（应保持 1 个实例）")
            ok = False
        if ok:
            print(f"    单实例 OK（第二实例退出码={rc2}，窗口数={after}）")

        # 关闭主实例：WM_CLOSE → closeEvent
        t0 = time.monotonic()
        sent = post_close_by_title(APP_TITLE)
        if sent == 0:
            print("    [fail] 未找到可关闭的主窗口")
            ok = False
        try:
            proc.wait(timeout=3)
            elapsed = time.monotonic() - t0
            print(f"    closeEvent 后进程退出耗时 {elapsed:.2f}s（<=3s {'OK' if elapsed <= 3 else 'FAIL'}）")
            if elapsed > 3:
                ok = False
        except subprocess.TimeoutExpired:
            print("    [fail] 关闭窗口后 3s 内进程未退出")
            proc.kill()
            ok = False

        time.sleep(0.8)
        if image_running(EXE_NAME):
            print(f"    [fail] 仍有 {EXE_NAME} 残留")
            ok = False
        if image_running(WEBENGINE_NAME):
            print(f"    [fail] 存在 {WEBENGINE_NAME} 残留（回归）")
            ok = False
        if ok:
            print("    关闭后无进程残留 OK")

    print(f"[real] {'全部通过' if ok else '存在失败'}")
    return ok


# ---------------------------------------------------------------------------
# 模式二：无头 python main.py（closeEvent → 退出 + 单实例）
# ---------------------------------------------------------------------------
def _offscreen_env(autoclose_ms: int = 0) -> dict:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    if autoclose_ms > 0:
        env["BRM_AUTOCLOSE_MS"] = str(autoclose_ms)
    else:
        env.pop("BRM_AUTOCLOSE_MS", None)
    return env


def _launch(autoclose_ms: int = 0) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, os.path.join(PROJECT_ROOT, "main.py")],
        cwd=PROJECT_ROOT, env=_offscreen_env(autoclose_ms),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def verify_offscreen_close_loop(runs: int, autoclose_ms: int = 1200) -> bool:
    print(f"[offscreen] 关闭退出循环：{runs} 次（BRM_AUTOCLOSE_MS={autoclose_ms}）")
    ok = True
    for i in range(1, runs + 1):
        t0 = time.monotonic()
        proc = _launch(autoclose_ms)
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"  run {i}: [fail] 30s 内进程未退出")
            ok = False
            continue
        elapsed = time.monotonic() - t0
        if rc != 0:
            print(f"  run {i}: [fail] 退出码 {rc}（应为 0）")
            ok = False
        else:
            print(f"  run {i}: OK（自动关闭后 {elapsed:.2f}s 内退出，退出码 0）")
    if image_running(WEBENGINE_NAME):
        print(f"  [fail] 存在 {WEBENGINE_NAME} 残留（回归）")
        ok = False
    if ok:
        print("  [offscreen] 反复开关无进程残留 OK")
    return ok


def verify_offscreen_single_instance() -> bool:
    print("[offscreen] 单实例：启动 A（8s 后自动关闭），再启动 B（应秒退且不新建实例）")
    a = _launch(autoclose_ms=8000)
    time.sleep(2.5)  # 等待 A 建立命名管道监听
    if a.poll() is not None:
        print(f"  [fail] 实例 A 提前退出（码={a.returncode}）")
        return False

    t0 = time.monotonic()
    b = _launch(autoclose_ms=0)
    try:
        rc_b = b.wait(timeout=15)
    except subprocess.TimeoutExpired:
        b.kill()
        print("  [fail] 第二实例 B 未退出（未识别已有实例）")
        a.kill()
        return False
    dt_b = time.monotonic() - t0

    ok = True
    if rc_b != 0:
        print(f"  [fail] 第二实例 B 退出码 {rc_b}（应为 0）")
        ok = False
    if a.poll() is not None:
        print("  [fail] 实例 A 意外退出（应保持运行）")
        ok = False
    if dt_b > 5:
        print(f"  [fail] 第二实例 B 退出耗时 {dt_b:.2f}s（应 <5s）")
        ok = False
    if ok:
        print(f"  OK：A 保持运行（PID={a.pid}），B 在 {dt_b:.2f}s 内退出（码 0）")

    try:
        rc_a = a.wait(timeout=15)
        if rc_a != 0:
            print(f"  [fail] 实例 A 退出码 {rc_a}（应为 0）")
            ok = False
        else:
            print(f"  OK：A 自动关闭退出（码 0）")
    except subprocess.TimeoutExpired:
        a.kill()
        print("  [fail] 实例 A 未自动关闭")
        ok = False
    return ok


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="R5 退出回收 + 单实例验证")
    p.add_argument("--mode", choices=("auto", "real", "offscreen"), default="auto")
    p.add_argument("--exe", default=os.path.join("dist", EXE_NAME))
    p.add_argument("--runs", type=int, default=0, help="反复开关次数（默认 offscreen=10 / real=3）")
    p.add_argument("--autoclose-ms", type=int, default=1200)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if os.name != "nt":
        print("本脚本依赖 Windows API（tasklist / EnumWindows），当前平台不受支持。")
        return 2

    if args.mode == "auto":
        mode = "real" if os.path.exists(args.exe) else "offscreen"
    else:
        mode = args.mode
    print(f"=== R5 验证：mode={mode} ===")

    if mode == "real":
        runs = args.runs or 3
        ok = verify_real(os.path.abspath(args.exe), runs)
    else:
        runs = args.runs or 10
        ok = verify_offscreen_close_loop(runs, args.autoclose_ms)
        ok = verify_offscreen_single_instance() and ok

    print("=== 结果：" + ("全部通过 ✅" if ok else "存在失败 ❌") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

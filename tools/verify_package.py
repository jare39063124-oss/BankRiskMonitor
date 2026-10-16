"""打包瘦身验证脚本（ARCH-v2 附录 A.4）。

遍历 ``dist/`` 产物，断言：

1. 总体积 ≤ ``MAX_MB``（默认 80MB）；
2. 不含被排除产物：``Qt6WebEngineCore.dll`` / ``QtWebEngineProcess.exe`` /
   ``resources.pak`` / ``icudtl.dat`` / ``Qt6Pdf.dll`` / ``Qt6Designer.dll`` /
   ``Qt6Multimedia.dll`` / ``Qt6Charts.dll`` / ``Qt6Qml.dll`` / ``opengl32sw.dll``；
3. ``translations`` 目录仅保留 ``zh_CN``；
4. 打印体积明细 top20。

退出码非零即验证失败（供 ``build_exe.bat`` 串联）。

用法::

    python tools/verify_package.py                 # 校验 dist 产物
    python tools/verify_package.py --launch-timed  # 附加冷启动计时
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIST = os.path.join(ROOT, "dist")
DEFAULT_MAX_MB = 80.0

# 禁止出现的产物（小写 basename 匹配）
FORBIDDEN = [
    "qt6webenginecore.dll",
    "qtwebengineprocess.exe",
    "resources.pak",
    "icudtl.dat",
    "qt6pdf.dll",
    "qt6designer.dll",
    "qt6multimedia.dll",
    "qt6charts.dll",
    "qt6qml.dll",
    "opengl32sw.dll",
]


def _base(name: str) -> str:
    return str(name).replace("\\", "/").split("/")[-1].lower()


def _norm(name: str) -> str:
    return str(name).replace("\\", "/").lower()


def _human(num_bytes: float) -> str:
    return "{0:.2f} MB".format(num_bytes / 1048576.0)


def _iter_files(path: str):
    """递归产出 (相对路径, 绝对路径, 字节数)。"""
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = 0
            yield (os.path.relpath(fp, path), fp, size)


def _product_paths(dist: str):
    """返回 (exe_path_or_None, onedir_path_or_None)。"""
    exe = os.path.join(dist, "BankRiskMonitor.exe")
    onedir = os.path.join(dist, "BankRiskMonitor")
    exe = exe if os.path.isfile(exe) else None
    onedir = onedir if os.path.isdir(onedir) else None
    return exe, onedir


def _archive_entries(exe: str):
    """读取 onefile 内嵌归档的 TOC。

    返回 ``(entries, error)``；``entries`` 为 ``{name: uncompressed_size}``。
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as exc:  # pragma: no cover - 依赖缺失时的兜底
        return None, "PyInstaller 不可用: %s" % exc
    try:
        arch = CArchiveReader(exe)
    except Exception as exc:
        return None, "无法解析内嵌归档: %s" % exc
    entries = {}
    for name, meta in arch.toc.items():
        # meta = (offset, data_length, uncompressed_length, compression_flag, typecode)
        size = meta[2] if len(meta) > 2 else 0
        entries[name] = size
    return entries, None


def _check_names(names, label: str):
    """对一组名称做排除项与 translations 校验，返回 (errors, warnings)。"""
    errors = []
    warnings = []
    lowered = {_norm(n): n for n in names}

    hits = []
    for forbidden in FORBIDDEN:
        for norm, orig in lowered.items():
            if _base(norm) == forbidden:
                hits.append(orig)
    if hits:
        errors.append("含被排除产物: " + ", ".join(sorted(set(hits))))

    trans = [orig for norm, orig in lowered.items() if "translations/" in norm]
    bad_trans = [t for t in trans if "zh_cn" not in _base(t)]
    if bad_trans:
        errors.append("translations 仅应保留 zh_CN，实际含: "
                      + ", ".join(sorted(set(bad_trans))[:10]))
    elif trans:
        warnings.append("translations 保留 %d 项（均含 zh_CN）" % len(trans))

    qml = [orig for norm, orig in lowered.items() if "/qml/" in norm or "/qml/" in "/" + norm]
    # qml 目录若存在通常已由 excludes 处理；此处仅提示
    if qml:
        warnings.append("检测到 qml 相关条目 %d 项" % len(qml))

    return errors, warnings


def _report_top20(pairs):
    """pairs: list[(label, size)] -> 打印 top20。"""
    pairs = sorted(pairs, key=lambda x: x[1], reverse=True)[:20]
    print("  —— 体积明细 top20 ——")
    for label, size in pairs:
        print("    {0:>12}  {1}".format(_human(size), label))


def _timed_launch(exe: str):
    """冷启动计时：启动 exe 并测量到首个可见窗口出现的时间（秒）。

    Windows 上使用 EnumWindows 检测属于该进程的可见窗口；非 Windows/失败返回 None。
    """
    import subprocess

    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    t0 = time.time()
    try:
        proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    except Exception as exc:
        print("  ! 启动失败: %s" % exc)
        return None

    pid = proc.pid
    state = {"elapsed": None}

    def _cb(hwnd, _lparam):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            state["elapsed"] = time.time() - t0
            return False
        return True

    cb = WNDENUMPROC(_cb)
    deadline = t0 + 30.0
    while state["elapsed"] is None and time.time() < deadline:
        if proc.poll() is not None:
            break
        user32.EnumWindows(cb, 0)
        if state["elapsed"] is None:
            time.sleep(0.05)

    try:
        proc.terminate()
    except Exception:
        pass
    return state["elapsed"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BankRiskMonitor 打包瘦身校验")
    parser.add_argument("--dist", default=DEFAULT_DIST, help="产物目录（默认 dist/）")
    parser.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB,
                        help="体积上限 MB（默认 80）")
    parser.add_argument("--launch-timed", action="store_true",
                        help="附加冷启动计时（测量到首个窗口出现）")
    args = parser.parse_args(argv)

    dist = os.path.abspath(args.dist)
    max_bytes = args.max_mb * 1048576.0
    print("=" * 68)
    print("BankRiskMonitor 打包瘦身校验")
    print("产物目录:", dist)
    print("体积上限:", "{0:.0f} MB".format(args.max_mb))
    print("=" * 68)

    if not os.path.isdir(dist):
        print("[FAIL] 产物目录不存在:", dist)
        return 2

    exe, onedir = _product_paths(dist)
    if not exe and not onedir:
        print("[FAIL] 未找到产物：dist/BankRiskMonitor.exe 或 dist/BankRiskMonitor/")
        return 2

    errors = []
    warnings = []

    # ---- 1) 体积 ----
    total = 0
    if onedir:
        entries = list(_iter_files(onedir))
        total = sum(s for _rel, _fp, s in entries)
        print("[onedir] 目录: %s" % onedir)
        print("  文件数: %d" % len(entries))
    if exe:
        total = max(total, os.path.getsize(exe)) if not onedir else total + os.path.getsize(exe)
        print("[onefile] exe: %s (%s)" % (exe, _human(os.path.getsize(exe))))
    print("总体积: %s (上限 %s)" % (_human(total), _human(max_bytes)))
    if total <= max_bytes:
        print("[PASS] 体积达标")
    else:
        errors.append("总体积 %s 超过上限 %s" % (_human(total), _human(max_bytes)))
        print("[FAIL] 体积超标")

    # ---- 2) + 3) 排除项 / translations ----
    if onedir:
        names = [rel for rel, _fp, _s in _iter_files(onedir)]
        err2, warn2 = _check_names(names, "onedir")
        errors += err2
        warnings += warn2
        _report_top20([(rel, s) for rel, _fp, s in entries])

    if exe:
        arch_entries, arch_err = _archive_entries(exe)
        if arch_err:
            print("[WARN] 无法读取内嵌归档，退化为原始字节扫描:", arch_err)
            warnings.append(arch_err)
            with open(exe, "rb") as fh:
                blob = fh.read()
            hits = [n for n in FORBIDDEN if n.encode("utf-8") in blob]
            if hits:
                errors.append("内嵌归档疑似含被排除产物: " + ", ".join(hits))
            else:
                print("[PASS] 字节扫描未发现被排除产物")
        else:
            err2, warn2 = _check_names(list(arch_entries.keys()), "onefile")
            errors += err2
            warnings += warn2
            _report_top20([(n, s) for n, s in arch_entries.items()])

    # ---- 4) 冷启动计时 ----
    if args.launch_timed and exe:
        print("—— 冷启动计时 ——")
        elapsed = _timed_launch(exe)
        if elapsed is None:
            print("[WARN] 无法测量冷启动（未检测到窗口或平台不支持）")
        else:
            print("冷启动耗时: %.2f s" % elapsed)
            if elapsed > 3.0:
                warnings.append("冷启动 %.2f s 超过 3s 目标" % elapsed)

    # ---- 汇总 ----
    print("-" * 68)
    if warnings:
        print("警告:")
        for w in warnings:
            print("  - " + w)
    if errors:
        print("失败:")
        for e in errors:
            print("  - " + e)
        print("[FAIL] 打包瘦身校验未通过")
        return 1
    print("[PASS] 打包瘦身校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

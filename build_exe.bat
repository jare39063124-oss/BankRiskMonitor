@echo off
REM ============================================================
REM BankRiskMonitor PyInstaller build script (onefile)
REM Output: dist\BankRiskMonitor.exe  (免安装、双击即用的单个 exe)
REM   - BankRiskMonitor.spec: bundles src/assets, collects providers
REM     submodules as top-level providers.* (config.yaml declares class
REM     paths like providers.social_weibo.*; spec pins sys.path to src/
REM     so the real package is collected, not any same-named package)
REM   - v2 瘦身：spec 内 excludes + datas/binaries 过滤（translations 仅
REM     zh_CN / 剔除 resources、qml、opengl32sw.dll），剥离 195MB 浏览器内核
REM   - Bundled assets are resolved via sys._MEIPASS by src/core/paths.py
REM ============================================================
pyinstaller --noconfirm BankRiskMonitor.spec

if errorlevel 1 (
  echo [ERROR] Build failed. Check the log above.
  exit /b 1
)
echo [OK] Build finished: dist\BankRiskMonitor.exe

REM ---- 瘦身校验：非零退出即构建失败 ----
python tools\verify_package.py
if errorlevel 1 (
  echo [ERROR] Package verification failed (see tools\verify_package.py).
  exit /b 1
)
echo [OK] Package verification passed.

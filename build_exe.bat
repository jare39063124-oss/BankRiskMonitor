@echo off
REM ============================================================
REM BankRiskMonitor PyInstaller build script (onefile)
REM Output: dist\BankRiskMonitor.exe
REM   - BankRiskMonitor.spec: bundles src/assets, collects providers
REM     submodules as top-level providers.* (config.yaml declares class
REM     paths like providers.social_weibo.*; spec pins sys.path to src/
REM     so the real package is collected, not any same-named package)
REM   - Bundled assets are resolved via sys._MEIPASS by src/core/paths.py
REM ============================================================
pyinstaller --noconfirm BankRiskMonitor.spec

if errorlevel 1 (
  echo [ERROR] Build failed. Check the log above.
  exit /b 1
)
echo [OK] Build finished: dist\BankRiskMonitor.exe

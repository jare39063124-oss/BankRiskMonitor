@echo off
REM ============================================================
REM BankRiskMonitor PyInstaller build script (onefile)
REM Output: dist\BankRiskMonitor.exe
REM   - --add-data bundles src/assets (china.json / echarts.min.js / map*.html)
REM   - --collect-submodules providers: provider modules are loaded dynamically
REM     from config.yaml (importlib.import_module in src/providers/base.py),
REM     so static analysis cannot find them
REM   - Bundled assets are resolved via sys._MEIPASS by src/core/paths.py
REM   - If onefile fails due to QtWebEngine, change --onefile to --onedir
REM ============================================================
pyinstaller --noconfirm --onefile --windowed --name BankRiskMonitor ^
  --add-data "src/assets;src/assets" ^
  --collect-submodules providers ^
  main.py

if errorlevel 1 (
  echo [ERROR] Build failed. Check the log above.
  exit /b 1
)
echo [OK] Build finished: dist\BankRiskMonitor.exe

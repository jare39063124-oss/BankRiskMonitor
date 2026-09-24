# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# providers 以顶层包名 providers.* 被动态导入（config.yaml sources.class 声明），
# 源码物理位于 src/providers。构建机 sys.path 若存在其他同名 providers 包会收错，
# 故把 src 插到最前，确保 collect_submodules('providers') 枚举的是本项目模块。
_src_dir = os.path.join(SPECPATH, 'src')
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('providers')


a = Analysis(
    ['main.py'],
    pathex=[_src_dir],
    binaries=[],
    datas=[('src/assets', 'src/assets')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BankRiskMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

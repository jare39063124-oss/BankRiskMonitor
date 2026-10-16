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

# ============================================================
# ARCH-v2 附录 A.1：excludes 清单（移除 WebEngine/QML/Quick/多媒体/图表等未用模块）
# ============================================================
excludes = [
    # —— WebEngine（核心 195MB，纯自绘后彻底剥离）——
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtWebView',
    # —— QML/Quick/3D ——
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
    'PySide6.QtQuick3D', 'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    # —— 多媒体（含 avcodec/avformat 16MB）——
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtSpatialAudio',
    # —— 图表/可视化 ——
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs', 'PySide6.QtPdf',
    'PySide6.QtPdfWidgets', 'PySide6.QtDesigner', 'PySide6.QtUiTools',
    'PySide6.QtShaderTools', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    # —— 其余未用子模块 ——
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtHelp', 'PySide6.QtLocation',
    'PySide6.QtPositioning', 'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtSensors',
    'PySide6.QtSerialPort', 'PySide6.QtRemoteObjects', 'PySide6.QtScxml',
    'PySide6.QtStateMachine', 'PySide6.QtNetworkAuth', 'PySide6.QtTextToSpeech',
    # —— 标准库/其它 ——
    'tkinter', 'unittest', 'pydoc', 'doctest',
]

a = Analysis(
    ['main.py'],
    pathex=[_src_dir],
    binaries=[],
    datas=[('src/assets', 'src/assets')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)


# ============================================================
# ARCH-v2 附录 A.2：datas/binaries 过滤（瘦身大头）
#   translations 仅留 zh_CN / 剔除 resources / qml / opengl32sw.dll
#   另：PySide6 的 PyInstaller hook 会无差别收集全部 Qt6*.dll，模块级 excludes
#   只挡 Python 模块、挡不住底层 DLL，故此处按 DLL 名再剔一遍未用子模块的库。
# 说明：对 entry[0] 做 '\'→'/' 归一化后匹配，避免 Windows 反斜杠与缺失前导斜杠导致漏匹配。
# ============================================================

# 未用 Qt 子模块对应的底层 DLL（小写 basename）——与 excludes 一一对应
_DROP_QT_DLLS = {
    'qt6pdf.dll', 'qt6pdfwidgets.dll',
    'qt6qml.dll', 'qt6qmlmeta.dll', 'qt6qmlmodels.dll', 'qt6qmlworkerscript.dll',
    'qt6quick.dll', 'qt6quickwidgets.dll', 'qt6quickcontrols2.dll',
    'qt6quickcontrols2impl.dll', 'qt6quick3d.dll',
    'qt6virtualkeyboard.dll',
    'qt6webenginecore.dll', 'qt6webenginequick.dll', 'qt6webenginewidgets.dll',
    'qt6webchannel.dll', 'qt6websockets.dll', 'qt6webview.dll',
    'qt6charts.dll', 'qt6datavisualization.dll', 'qt6graphs.dll',
    'qt6multimedia.dll', 'qt6multimediawidgets.dll', 'qt6spatialaudio.dll',
    'qt6designer.dll', 'qt6shadertools.dll', 'qt6sql.dll', 'qt6test.dll',
    'qt6uitools.dll', 'qt6help.dll', 'qt6bluetooth.dll', 'qt6nfc.dll',
    'qt6positioning.dll', 'qt6location.dll', 'qt6sensors.dll', 'qt6serialport.dll',
    'qt6remoteobjects.dll', 'qt6scxml.dll', 'qt6statemachine.dll',
    'qt6texttospeech.dll', 'qt6networkauth.dll',
    # 与 Qt6Pdf / Qt6VirtualKeyboard 配套的插件（随库一并剔除）
    'qpdf.dll', 'qtuiotouchplugin.dll', 'qtvirtualkeyboardplugin.dll',
}
_DROP_PATH_PARTS = (
    'pyside6/plugins/platforminputcontexts/',
)


def _keep(entry):
    norm = str(entry[0]).replace('\\', '/').lower()
    if 'pyside6/translations/' in norm:
        return 'zh_cn' in norm
    if 'pyside6/resources/' in norm:   # WebEngine 专属资源
        return False
    if 'pyside6/qml/' in norm:
        return False
    if norm.endswith('opengl32sw.dll'):  # 见风险 R2，默认剔除，异常则放开
        return False
    base = norm.split('/')[-1]
    if base in _DROP_QT_DLLS:
        return False
    for part in _DROP_PATH_PARTS:
        if part in norm:
            return False
    return True


a.datas = [d for d in a.datas if _keep(d)]
a.binaries = [b for b in a.binaries if _keep(b)]


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

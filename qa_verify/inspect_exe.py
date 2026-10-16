"""独立校验打包产物内容（不被 tools/verify_package.py 覆盖 Qt6Network 断言）。"""
import os
import sys

from PyInstaller.archive.readers import CArchiveReader

exe = sys.argv[1] if len(sys.argv) > 1 else "dist_qa_verify/BankRiskMonitor.exe"
arch = CArchiveReader(exe)
names = [n.replace("\\", "/").lower() for n in arch.toc.keys()]


def has(sub):
    return any(sub in n for n in names)


print("EXE size MB              : %.2f" % (os.path.getsize(exe) / 1048576))
print("Qt6Network.dll present   :", has("qt6network.dll"))
print("Qt6WebEngineCore.dll     :", has("qt6webenginecore.dll"))
print("QtWebEngineProcess.exe   :", has("qtwebengineprocess.exe"))
print("icudtl.dat               :", has("icudtl.dat"))
print("resources.pak            :", has("resources.pak"))
print("Qt6Qml.dll               :", has("qt6qml.dll"))
print("opengl32sw.dll           :", has("opengl32sw.dll"))
print("Qt6Pdf.dll               :", has("qt6pdf.dll"))
print("translations entries     :", sum(1 for n in names if "translations/" in n))
print("qml entries              :", sum(1 for n in names if "/qml/" in n))

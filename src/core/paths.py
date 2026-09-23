"""便携路径：数据一律存放在 exe 同目录（开发模式为项目根目录）下，实现免安装便携。"""
import os
import sys

def app_root() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return os.path.dirname(sys.executable)
    # 本文件位于 src/core/ 下，向上三级为项目根（开发模式，config.yaml/data 均在根目录）
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def data_dir() -> str:
    d = os.path.join(app_root(), "data")
    os.makedirs(d, exist_ok=True)
    return d

def db_path() -> str:
    return os.path.join(data_dir(), "monitor.db")

def asset_path(name: str) -> str:
    # 打包后资源在 sys._MEIPASS / src_assets 中
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p = os.path.join(base, "src", "assets", name)
    if os.path.exists(p):
        return p
    return os.path.join(app_root(), "src", "assets", name)

def reports_dir() -> str:
    d = os.path.join(data_dir(), "reports")
    os.makedirs(d, exist_ok=True)
    return d

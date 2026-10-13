"""启动主窗口并截图（视觉验收用，非正式功能）。

用法：D:/hermes/hermes-agent/venv/Scripts/python.exe tools/shot_demo.py [输出.png] [等待毫秒]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.gui.main_window import MainWindow  # noqa: E402


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "shot.png")
    wait_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1600, 950)
    win.show()

    def snap():
        win.grab().save(out)
        print("saved:", out)
        app.quit()

    QTimer.singleShot(wait_ms, snap)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""验证三级下钻：辽宁省 → 沈阳市（区县级）截图。

v2：MapView 已改为纯 Qt 自绘（无 WebEngine），下钻走公开 API
``MapView.drill_to(name)``。
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
    out = os.path.join(ROOT, "shot_district.png")
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1600, 950)
    win.show()

    def step1():
        win.map_view.drill_to("辽宁省")
        QTimer.singleShot(500, step2)

    def step2():
        win.map_view.drill_to("沈阳市")   # 辽宁省沈阳市 -> 区县级（210100）
        QTimer.singleShot(500, snap)

    def snap():
        win.grab().save(out)
        print("saved:", out)
        app.quit()

    QTimer.singleShot(1200, step1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

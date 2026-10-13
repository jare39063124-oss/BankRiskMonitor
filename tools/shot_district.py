"""验证三级下钻：辽宁省 → 沈阳市（区县级）截图。"""
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
        win.map_view.view.page().runJavaScript("drillTo('辽宁省');")
        QTimer.singleShot(4000, step2)

    def step2():
        win.map_view.view.page().runJavaScript(
            "var hit=Object.keys(cityAdcode).find(function(n){return n.indexOf('抚顺')>=0});"
            "if(hit){drillToDistrict(hit);} true;")
        QTimer.singleShot(3500, snap)

    def snap():
        win.grab().save(out)
        print("saved:", out)
        app.quit()

    QTimer.singleShot(8000, step1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

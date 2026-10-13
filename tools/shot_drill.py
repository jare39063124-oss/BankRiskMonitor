"""验证地图下钻与详情弹层（加载真实 MapView，注入演示数据后截图）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.core import db  # noqa: E402
from src.gui.main_window import MainWindow  # noqa: E402

STEPS = []


def main() -> int:
    out1 = os.path.join(ROOT, "shot_province.png")
    out2 = os.path.join(ROOT, "shot_popup.png")
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1600, 950)
    win.show()

    # 1) 下钻浙江省
    def drill():
        win.map_view.view.page().runJavaScript("drillTo('浙江省');")
        QTimer.singleShot(4000, popup)

    # 2) 弹出第一个光点的详情卡
    def popup():
        win.map_view.view.page().runJavaScript(
            "if(curSpots.length){showPopup(curSpots[0]);} true;")
        QTimer.singleShot(1500, snap1)

    def snap1():
        win.grab().save(out1)
        print("saved:", out1)
        app.quit()

    QTimer.singleShot(9000, drill)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""BankRiskMonitor 入口：初始化数据库并启动主窗口。"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from src.core import db  # noqa: E402

def main():
    db.init_db()
    from src.gui.main_window import MainWindow
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

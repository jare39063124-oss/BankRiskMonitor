"""BankRiskMonitor 入口：初始化数据库并启动主窗口。"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from src.core import db, settings  # noqa: E402

def main():
    # 启动自举：补齐 exe 同目录缺失的 config.yaml / model_policy.yaml / .env（永不抛异常）
    created = settings.ensure_boot_files()
    if created:
        logging.info("启动自举：已生成缺失配置文件: %s", ", ".join(created))
    db.init_db()
    from src.gui.main_window import MainWindow
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

"""BankRiskMonitor 入口：单实例守卫 + 初始化数据库 + 启动主窗口。

单实例（R5）：``SingleInstanceGuard``（QLocalServer/QLocalSocket）在创建窗口前
拦截第二实例——探测到已有实例则请求激活其窗口并 ``sys.exit(0)``，绝不出现两个进程。

自检钩子（供 ``tools/verify_shutdown.py`` 无头验证）：环境变量 ``BRM_AUTOCLOSE_MS``
为毫秒数时，主窗口到点自动 ``close()``，用于在 offscreen 平台验证
``closeEvent → 进程退出`` 路径。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    # 启动自举：补齐 exe 同目录缺失的 config.yaml / model_policy.yaml / .env（永不抛异常）
    from src.core import db, settings  # noqa: E402

    created = settings.ensure_boot_files()
    if created:
        logging.info("启动自举：已生成缺失配置文件: %s", ", ".join(created))

    from PySide6.QtCore import QTimer  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402

    from src.gui.main_window import MainWindow  # noqa: E402
    from src.gui.single_instance import SingleInstanceGuard  # noqa: E402

    app = QApplication(sys.argv)

    guard = SingleInstanceGuard("BankRiskMonitor")
    if not guard.is_primary():
        guard.notify_existing()  # 激活已有实例的窗口
        logging.info("检测到已有实例，已请求激活其窗口并退出本进程")
        sys.exit(0)

    db.init_db()
    win = MainWindow()
    guard.activated.connect(win._on_instance_activated)
    win.show()

    autoclose_ms = os.environ.get("BRM_AUTOCLOSE_MS", "").strip()
    if autoclose_ms.isdigit() and int(autoclose_ms) > 0:
        QTimer.singleShot(int(autoclose_ms), win.close)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

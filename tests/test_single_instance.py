"""T05 单实例 + 退出回收离线测试（QT_QPA_PLATFORM=offscreen）。

- ``SingleInstanceGuard``：首个实例成为主、第二实例被拒绝、``notify_existing`` 触发
  ``activated``、``close()`` 释放后新实例可再成为主；
- ``MainWindow.closeEvent``：停下 ``_tick_timer``、``requestInterruption`` 回收在跑线程。

无头环境说明：单实例守卫依赖 QtNetwork 命名管道（平台无关，offscreen 下可完整验证）；
真正"双击两次只出现一个窗口"的端到端断言由 ``tools/verify_shutdown.py`` 覆盖。

运行：python -m unittest tests.test_single_instance -v
"""
import gc
import os
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import yaml

from src.core import db, settings  # noqa: E402

with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as _f:
    settings._config = yaml.safe_load(_f) or {}

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from src.gui.main_window import MainWindow  # noqa: E402
from src.gui.single_instance import SingleInstanceGuard  # noqa: E402


def _unique_key() -> str:
    return f"BankRiskMonitorTest-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class _LoopWorker(QThread):
    """可被 ``requestInterruption`` 及时打断的后台线程（模拟刷新/AI 线程）。"""

    def run(self):
        for _ in range(2000):
            if self.isInterruptionRequested():
                return
            QThread.msleep(5)


class SingleInstanceGuardTest(unittest.TestCase):
    def test_first_instance_is_primary(self):
        guard = SingleInstanceGuard(_unique_key())
        try:
            self.assertTrue(guard.is_primary())
        finally:
            guard.close()
            QApplication.processEvents()

    def test_second_instance_rejected_and_activates(self):
        key = _unique_key()
        primary = SingleInstanceGuard(key)
        self.assertTrue(primary.is_primary())
        fired = []
        primary.activated.connect(lambda: fired.append(True))
        second = None
        try:
            second = SingleInstanceGuard(key)
            self.assertFalse(second.is_primary())       # 第二实例：非主
            self.assertTrue(second.notify_existing())   # 请求激活已有窗口
            for _ in range(100):
                QApplication.processEvents()
                if fired:
                    break
            self.assertTrue(fired, "主实例应收到 SHOW 并发射 activated")
        finally:
            primary.close()
            QApplication.processEvents()
            del second  # noqa: F841 - 显式释放，避免跨用例残留连接
            QApplication.processEvents()

    def test_release_allows_new_primary(self):
        key = _unique_key()
        first = SingleInstanceGuard(key)
        self.assertTrue(first.is_primary())
        first.close()
        QApplication.processEvents()
        second = SingleInstanceGuard(key)
        try:
            self.assertTrue(second.is_primary())  # 释放后新实例可成为主
        finally:
            second.close()
            QApplication.processEvents()

    def test_notify_existing_without_server_returns_false(self):
        guard = SingleInstanceGuard(_unique_key())
        self.assertFalse(guard.notify_existing())


class CloseEventRecycleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        patcher = mock.patch.object(
            db, "db_path", return_value=os.path.join(self._tmp.name, "gui.db"))
        patcher.start()
        self._db_patcher = patcher
        db.init_db()

    def tearDown(self):
        self._db_patcher.stop()
        gc.collect()
        self._tmp.cleanup()

    def _window(self):
        w = MainWindow()
        w.show()
        QApplication.processEvents()
        return w

    def test_close_stops_tick_timer(self):
        w = self._window()
        self.assertTrue(w._tick_timer.isActive())
        w.close()
        QApplication.processEvents()
        self.assertFalse(w._tick_timer.isActive())
        w.deleteLater()

    def test_close_recycles_running_worker_within_timeout(self):
        w = self._window()
        worker = _LoopWorker()
        w._worker = worker
        worker.start()
        QApplication.processEvents()
        self.assertTrue(worker.isRunning())

        start = time.time()
        w.close()
        elapsed = time.time() - start
        QApplication.processEvents()
        self.assertFalse(worker.isRunning(), "closeEvent 应回收在跑线程")
        self.assertLess(elapsed, 3.0, "回收应在 3s 超时内完成")
        w.deleteLater()

    def test_close_without_workers_is_safe(self):
        w = self._window()
        self.assertIsNone(w._worker)
        w.close()
        QApplication.processEvents()
        self.assertFalse(w._tick_timer.isActive())
        w.deleteLater()


if __name__ == "__main__":
    unittest.main()

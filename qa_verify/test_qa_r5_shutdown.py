"""QA 独立验收 —— E. R5 进程残留 / 单实例（重新实测，不引用工程师数字）。

本文件覆盖**可离线复现**的证据：
- ``SingleInstanceGuard``：首个为主、第二被拒并激活已有、释放后新实例可再为主；
- ``MainWindow.closeEvent``：停定时器 + 3s 内回收在跑线程；
- 子进程级：两个 ``python main.py``（offscreen + 自动关闭）→ 第二实例秒退、无残留。

真实 exe（``dist/BankRiskMonitor.exe``）的可见窗口 / WM_CLOSE / tasklist 断言
由 ``tools/verify_shutdown.py --mode real`` 覆盖，见报告 §E。
"""
import gc
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from src.core import db  # noqa: E402
from src.gui.main_window import MainWindow  # noqa: E402
from src.gui.single_instance import SingleInstanceGuard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _key():
    return f"QA-BankRiskMonitor-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class _LoopWorker(QThread):
    def run(self):
        for _ in range(4000):
            if self.isInterruptionRequested():
                return
            QThread.msleep(5)


class GuardQA(unittest.TestCase):
    def test_primary_second_rejected_and_reactivated(self):
        key = _key()
        primary = SingleInstanceGuard(key)
        self.assertTrue(primary.is_primary())
        fired = []
        primary.activated.connect(lambda: fired.append(True))
        second = SingleInstanceGuard(key)
        try:
            self.assertFalse(second.is_primary(), "第二实例不得成为主")
            self.assertTrue(second.notify_existing(), "第二实例应能请求激活已有窗口")
            for _ in range(120):
                _app.processEvents()
                if fired:
                    break
            self.assertTrue(fired, "主实例应收到 SHOW 并 emit activated")
        finally:
            primary.close()
            _app.processEvents()
            del second
            _app.processEvents()

    def test_release_allows_new_primary(self):
        key = _key()
        first = SingleInstanceGuard(key)
        self.assertTrue(first.is_primary())
        first.close()
        _app.processEvents()
        second = SingleInstanceGuard(key)
        try:
            self.assertTrue(second.is_primary())
        finally:
            second.close()
            _app.processEvents()


class CloseEventQA(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        p = mock.patch.object(db, "db_path", return_value=os.path.join(self._tmp.name, "qa.db"))
        p.start()
        self._p = p
        db.init_db()

    def tearDown(self):
        self._p.stop()
        gc.collect()
        self._tmp.cleanup()

    def test_close_recycles_worker_within_3s(self):
        w = MainWindow()
        w.show()
        _app.processEvents()
        self.assertTrue(w._tick_timer.isActive())
        worker = _LoopWorker()
        w._worker = worker
        worker.start()
        _app.processEvents()
        self.assertTrue(worker.isRunning())
        t0 = time.monotonic()
        w.close()
        dt = time.monotonic() - t0
        _app.processEvents()
        self.assertFalse(worker.isRunning(), "closeEvent 应回收线程")
        self.assertLess(dt, 3.0, f"回收耗时 {dt:.2f}s 超 3s")
        self.assertFalse(w._tick_timer.isActive(), "closeEvent 应停 tick 定时器")
        w.deleteLater()


class SubprocessSingleInstanceQA(unittest.TestCase):
    """offline 可复现替代证据：两个 main.py 子进程，第二实例秒退。"""

    def _launch(self, autoclose_ms):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        if autoclose_ms:
            env["BRM_AUTOCLOSE_MS"] = str(autoclose_ms)
        else:
            env.pop("BRM_AUTOCLOSE_MS", None)
        return subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py")],
                                cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_second_instance_exits_quickly(self):
        a = self._launch(autoclose_ms=8000)
        try:
            time.sleep(2.5)  # 等 A 建立命名管道
            self.assertIsNone(a.poll(), "实例 A 不应提前退出")
            t0 = time.monotonic()
            b = self._launch(autoclose_ms=0)
            try:
                rc = b.wait(timeout=12)
            except subprocess.TimeoutExpired:
                b.kill()
                self.fail("第二实例 B 未退出（未识别已有实例）")
            dt = time.monotonic() - t0
            self.assertEqual(rc, 0, f"第二实例退出码 {rc} 应为 0")
            self.assertLess(dt, 5.0, f"第二实例耗时 {dt:.2f}s 应 <5s")
        finally:
            try:
                a.wait(timeout=12)
            except Exception:
                a.kill()


if __name__ == "__main__":
    unittest.main()

"""GUI 设置中心离线测试（QT_QPA_PLATFORM=offscreen）：

- apply_visibility 显隐各区域并可恢复；panel_visibility JSON 持久化 round-trip；
- due_categories 按 category_frequency 过滤（禁用域不参与、0.5x 隔次执行）；
- run_refresh_cycle 跳过禁用信息域（mock sync.run_with_fallback 断言查询域）。

运行：python -m unittest tests.test_gui_settings -v
"""
import atexit
import gc
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 下禁用 GPU：规避 Chromium 在 Windows 进程退出清理时的访问违例（不影响断言）
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

# Chromium（QtWebEngine）在解释器关闭阶段析构时仍可能触发 Windows 访问违例
# （0xC0000005），导致用例全部通过却报非零退出码。此处经 atexit 在测试流程
# 结束后直接终止进程、跳过 Qt 清理：退出码沿用 unittest 的 sys.exit 结果
# （True->1 / False->0），未捕获异常按 1 处理。仅影响本测试进程。
_EXIT_CODE = [0]
_real_exit, _real_excepthook = sys.exit, sys.excepthook


def _capture_exit(code=0):
    _EXIT_CODE[0] = int(bool(code))
    _real_exit(code)


def _capture_exc(*args):
    _EXIT_CODE[0] = 1
    _real_excepthook(*args)


sys.exit, sys.excepthook = _capture_exit, _capture_exc


@atexit.register
def _skip_qt_teardown():
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(_EXIT_CODE[0])

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import yaml

from src.core import db, settings
from src.core import sync as sync_mod  # noqa: E402

# 把真实配置预热进缓存（同 test_social 做法，只读，不写回 config.yaml）
with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as _f:
    settings._config = yaml.safe_load(_f) or {}

# 含 QtWebEngineWidgets 的模块必须先于 QApplication 导入
from src.gui import main_window as mw_mod  # noqa: E402
from src.gui.main_window import MainWindow, run_refresh_cycle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from src.core.models import CATEGORIES, Enterprise  # noqa: E402


class GuiDbBase(unittest.TestCase):
    """把 SQLite 重定向到临时文件，避免污染项目 data 目录。"""

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


class VisibilityTest(GuiDbBase):
    def _new_window(self):
        w = MainWindow()
        w.show()
        QApplication.processEvents()
        return w

    def test_default_all_visible(self):
        """无 panel_visibility 记录（首次启动）时全部区域显示。"""
        w = self._new_window()
        for child in (w.map_view, w.ent_panel, w.feed_panel, w.alert_panel, w.ai_panel):
            self.assertTrue(child.isVisible(), child.__class__.__name__)
        self.assertTrue(w.countdown_label.isVisible())  # 默认周期 30 分钟
        self.assertTrue(w.settings_btn.isVisible())     # 状态栏常驻设置按钮
        w.close()
        w.deleteLater()

    def test_apply_visibility_toggles_and_restores(self):
        w = self._new_window()
        w.apply_visibility({"map": False, "ai_summary": False, "countdown": False})
        self.assertFalse(w.map_view.isVisible())
        self.assertFalse(w.ai_panel.isVisible())
        self.assertFalse(w.countdown_label.isVisible())
        # 其余区域不受影响
        self.assertTrue(w.ent_panel.isVisible())
        self.assertTrue(w.feed_panel.isVisible())
        self.assertTrue(w.alert_panel.isVisible())
        # 恢复后重新可见
        w.apply_visibility({"map": True, "ai_summary": True, "countdown": True})
        self.assertTrue(w.map_view.isVisible())
        self.assertTrue(w.ai_panel.isVisible())
        self.assertTrue(w.countdown_label.isVisible())
        w.close()
        w.deleteLater()

    def test_visibility_persist_roundtrip(self):
        """set_setting 写入 JSON 后重建窗口可恢复显隐（持久化 round-trip）。"""
        db.set_setting("panel_visibility", json.dumps({
            "map": False, "enterprise_list": True, "event_feed": False,
            "alerts": True, "ai_summary": True, "countdown": True}))
        w = self._new_window()
        self.assertFalse(w.map_view.isVisible())
        self.assertFalse(w.feed_panel.isVisible())
        self.assertTrue(w.ent_panel.isVisible())
        self.assertTrue(w.alert_panel.isVisible())
        self.assertTrue(w.ai_panel.isVisible())
        w.close()
        w.deleteLater()


class FrequencyFilterTest(GuiDbBase):
    def test_due_categories_respects_enabled_and_rate(self):
        """禁用域任何轮次不参与；0.5x 隔次执行；1x/2x 每轮执行。"""
        w = MainWindow()
        db.set_setting("category_frequency", json.dumps({
            "news": {"enabled": False, "rate": 1},
            "biz_change": {"enabled": True, "rate": 0.5},
            "lawsuit": {"enabled": True, "rate": 2},
            "enforcement": {"enabled": True, "rate": 1},
            "social": {"enabled": True, "rate": 1}}))
        w._load_ui_settings()

        w._refresh_counter = 0
        cats0 = w.due_categories()
        self.assertNotIn("news", cats0)          # 禁用域跳过
        self.assertIn("biz_change", cats0)       # 计数 0：0 % 2 == 0，执行
        self.assertIn("social", cats0)

        w._refresh_counter = 1
        cats1 = w.due_categories()
        self.assertNotIn("news", cats1)          # 禁用域始终跳过
        self.assertNotIn("biz_change", cats1)    # 0.5x 隔次执行
        self.assertIn("lawsuit", cats1)          # 2x 每轮执行
        self.assertIn("social", cats1)
        w.close()
        w.deleteLater()

    def test_due_categories_default_all_enabled(self):
        """无 category_frequency 记录时全部信息域默认参与。"""
        w = MainWindow()
        self.assertEqual(w.due_categories(), list(CATEGORIES))
        w.close()
        w.deleteLater()

    def test_run_refresh_cycle_skips_disabled_domains(self):
        """run_refresh_cycle 只查询 active_cats 内的域（news 未被查询）。"""
        ent = Enterprise(id=1, name="测试公司")
        calls = []

        def fake_rwf(cat, e):
            calls.append(cat)
            return [], f"{cat}:ok"

        with mock.patch.object(sync_mod, "run_with_fallback", side_effect=fake_rwf):
            result = run_refresh_cycle([ent], ["biz_change", "social"])
        self.assertEqual(result, {"ok": 1, "fail": 0})
        self.assertEqual(sorted(calls), ["biz_change", "social"])
        self.assertNotIn("news", calls)
        self.assertNotIn("lawsuit", calls)

    def test_run_refresh_cycle_empty_active_skips_all(self):
        """本轮无参与域时整体跳过，不发起任何查询。"""
        with mock.patch.object(sync_mod, "run_with_fallback") as fake_rwf:
            result = run_refresh_cycle([Enterprise(id=1, name="测试公司")], [])
        self.assertEqual(result, {"ok": 0, "fail": 0})
        fake_rwf.assert_not_called()

    def test_refresh_worker_defaults_to_all_categories(self):
        """未指定域时 RefreshWorker 按全部 CATEGORIES 刷新（兼容旧行为）。"""
        worker = mw_mod.RefreshWorker([Enterprise(id=1, name="x")])
        self.assertEqual(worker._cats, set(CATEGORIES))


if __name__ == "__main__":
    unittest.main()

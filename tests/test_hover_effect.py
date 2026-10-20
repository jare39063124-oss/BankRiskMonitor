"""悬停缩放动效与社媒登录态相关的 GUI 测试（离屏）。"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog,  # noqa: E402
                               QPushButton, QToolBar, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QWidget)

from src.gui import hover_effect  # noqa: E402

_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class ScaledRectTest(unittest.TestCase):
    def test_scale_up_keeps_center(self):
        from PySide6.QtCore import QRect
        r = hover_effect.scaled_rect(QRect(0, 0, 100, 40), 1.5)
        self.assertEqual(r.width(), 150)
        self.assertEqual(r.height(), 60)
        # 中心保持不变（奇数尺寸取整允许 1px 偏差）
        self.assertLessEqual(abs(r.center().x() - 50), 1)
        self.assertLessEqual(abs(r.center().y() - 20), 1)

    def test_factor_le_one_returns_copy(self):
        from PySide6.QtCore import QRect
        r = hover_effect.scaled_rect(QRect(0, 0, 100, 40), 1.0)
        self.assertEqual(r, QRect(0, 0, 100, 40))


class InstallTest(unittest.TestCase):
    def setUp(self):
        _app()

    def test_button_gets_controller(self):
        btn = QPushButton("x")
        ctrl = hover_effect.install_hover_scale(btn)
        self.assertIsNotNone(ctrl)
        self.assertTrue(hover_effect.hover_scale_installed(btn))

    def test_combo_gets_controller(self):
        combo = QComboBox()
        self.assertIsNotNone(hover_effect.install_hover_scale(combo))

    def test_non_button_ignored(self):
        tree = QTreeWidget()
        self.assertIsNone(hover_effect.install_hover_scale(tree))

    def test_idempotent(self):
        btn = QPushButton("x")
        a = hover_effect.install_hover_scale(btn)
        b = hover_effect.install_hover_scale(btn)
        self.assertIs(a, b)

    def test_toolbar_action_button_scaled(self):
        """工具栏 QAction 渲染成的 QToolButton 也要有缩放动效。"""
        bar = QToolBar()
        bar.addAction("动作")
        hover_effect.install_hover_scale_recursive(bar)
        tool_buttons = bar.findChildren(QToolButton)
        self.assertTrue(tool_buttons)
        self.assertTrue(any(hover_effect.hover_scale_installed(b) for b in tool_buttons))

    def test_recursive_covers_combo(self):
        w = QWidget()
        combo = QComboBox(w)
        n = hover_effect.install_hover_scale_recursive(w)
        self.assertGreaterEqual(n, 1)
        self.assertTrue(hover_effect.hover_scale_installed(combo))

    def test_refresh_covers_dynamic_child(self):
        """动态新增的子控件通过 refresh_hover_effects 补装。"""
        holder = QWidget()
        holder.show()
        _app().processEvents()
        btn = QPushButton("动态", holder)
        _app().processEvents()
        self.assertFalse(hover_effect.hover_scale_installed(btn))
        hover_effect.refresh_hover_effects()
        self.assertTrue(hover_effect.hover_scale_installed(btn))
        holder.close()

    def test_dialog_helper_installs(self):
        dlg = QDialog()
        btn = QPushButton("x", dlg)
        n = hover_effect.setup_dialog_hover(dlg)
        self.assertGreaterEqual(n, 1)
        self.assertTrue(hover_effect.hover_scale_installed(btn))


class HoverEventTest(unittest.TestCase):
    """悬停进入/离开触发缩放动画。"""

    def setUp(self):
        _app()

    @staticmethod
    def _pump(seconds=0.4):
        """推进真实时钟（QPropertyAnimation 依赖真实时间，非 processEvents 次数）。"""
        import time
        app = _app()
        t0 = time.time()
        while time.time() - t0 < seconds:
            app.processEvents()
            time.sleep(0.005)

    def test_enter_expands_geometry(self):
        """悬停进入后，控件的目标几何应为放大后的矩形。"""
        w = QWidget()
        w.resize(200, 100)
        btn = QPushButton("放大我", w)
        btn.setGeometry(10, 10, 100, 30)
        ctrl = hover_effect.install_hover_scale(btn, scale=1.5, duration=10)
        ctrl.play_in()
        target = ctrl.target_rect()
        self.assertEqual(target.width(), 150)
        self.assertEqual(target.height(), 45)
        self._pump()
        self.assertGreaterEqual(btn.width(), 100)

    def test_leave_restores_geometry(self):
        """悬停离开后回到原尺寸。"""
        w = QWidget()
        w.resize(200, 100)
        btn = QPushButton("复原", w)
        btn.setGeometry(10, 10, 100, 30)
        ctrl = hover_effect.install_hover_scale(btn, scale=1.5, duration=10)
        ctrl.play_in()
        self._pump()
        ctrl.play_out()
        self._pump()
        self.assertLessEqual(abs(btn.width() - 100), 1)


class TreeItemHoverQssTest(unittest.TestCase):
    """列表/树条目的悬停与选中视觉由 QSS 提供。"""

    def test_qss_has_tree_hover_and_selected(self):
        from src.gui import theme
        qss = theme.get_app_qss()
        self.assertIn("QTreeWidget::item:hover", qss)
        self.assertIn("QTreeWidget::item:selected", qss)
        self.assertIn("QTableView::item:selected", qss)

    def test_unselected_text_contrast(self):
        """未选中条目正文对比度需达 WCAG AA（4.5:1）。"""
        from src.gui import theme
        ratio = theme.contrast_ratio(theme.ROW_BASELINE_FG, theme.BG_CARD)
        self.assertGreaterEqual(ratio, 4.5)


if __name__ == "__main__":
    unittest.main()

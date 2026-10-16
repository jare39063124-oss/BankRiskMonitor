"""GUI 社媒适配离线测试（QT_QPA_PLATFORM=offscreen）：

- EventFeedPanel 类型下拉含“社媒”、来源下拉含 config.yaml 社媒五源；
- AlertPanel 对 social 事件正常展示（预警按 severity 过滤，社媒自动生效）；
- MapView 传入含 social 类别的 spots 数据冒烟不报错（v2 纯 Qt 自绘，无 WebEngine）。

运行：python -m unittest tests.test_gui_social -v
"""
import gc
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import yaml

from src.core import db, settings  # noqa: E402

# 把真实配置预热进缓存（同 test_social 做法，避免测试触发配置文件自举）
with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as _f:
    settings._config = yaml.safe_load(_f) or {}

# v2 MapView 为纯 Qt 自绘，无 QtWebEngine/QtWebChannel 依赖，导入顺序不再受限
from src.gui.map_view import MapView  # noqa: E402
from src.gui.panels import AlertPanel, EventFeedPanel  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from src.core.models import Event  # noqa: E402

SOCIAL_SOURCES = ("weibo", "wechat_sogou", "douyin", "kuaishou", "xiaohongshu")


class GuiTestBase(unittest.TestCase):
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


class EventFeedSocialTest(GuiTestBase):
    def test_category_combo_contains_social(self):
        """类型下拉由 CATEGORY_LABELS 动态生成，含“社媒”。"""
        panel = EventFeedPanel()
        labels = [panel.cat_combo.itemText(i) for i in range(panel.cat_combo.count())]
        datas = [panel.cat_combo.itemData(i) for i in range(panel.cat_combo.count())]
        self.assertIn("social", datas)
        self.assertIn("社媒", labels)
        self.assertEqual(labels[datas.index("social")], "社媒")
        panel.deleteLater()

    def test_source_combo_lists_five_social_sources(self):
        """来源下拉从 config.yaml sources 各段动态聚合，无事件时也列出社媒五源。"""
        panel = EventFeedPanel()
        panel.set_events([], {})
        datas = [panel.source_combo.itemData(i) for i in range(panel.source_combo.count())]
        for s in SOCIAL_SOURCES:
            self.assertIn(s, datas)
        self.assertEqual(datas[0], "")  # 首位保持“全部来源”
        panel.deleteLater()

    def test_source_combo_merges_event_sources(self):
        """事件中出现但未登记的来源也会并入下拉，且不重复。"""
        panel = EventFeedPanel()
        ev = Event(enterprise_id=1, category="social", title="微博爆料",
                   source="weibo", severity="low")
        panel.set_events([ev], {1: "测试公司"})
        datas = [panel.source_combo.itemData(i) for i in range(panel.source_combo.count())]
        self.assertEqual(datas.count("weibo"), 1)
        self.assertIn("xiaohongshu", datas)
        panel.deleteLater()


class AlertSocialTest(GuiTestBase):
    def test_social_event_alerted(self):
        """预警列表按 severity 过滤：social 高危事件正常进入并展示中文类型。"""
        panel = AlertPanel()
        ev = Event(enterprise_id=1, category="social", title="微博被执行爆料",
                   source="weibo", severity="high")
        panel.set_alerts([ev], {1: "测试公司"})
        self.assertEqual(panel.tree.topLevelItemCount(), 1)
        item = panel.tree.topLevelItem(0)
        self.assertEqual(item.text(2), "社媒")
        panel.deleteLater()


class MapViewSocialSmokeTest(GuiTestBase):
    def test_set_data_with_social_spot_no_error(self):
        """MapView（纯 Qt）传入含 social 类别的 spots 冒烟不报错，就绪即无待渲染数据。"""
        mv = MapView()
        mv.show()
        QApplication.processEvents()
        spots = [{"name": "社媒测试公司", "province": "浙江省", "city": "杭州市",
                  "lng": 120.15, "lat": 30.28, "event_count": 2,
                  "severity": "medium", "categories": ["social", "news"]}]
        mv.set_data(spots, "category")
        for _ in range(10):
            QApplication.processEvents()
        self.assertIsNone(mv._pending)  # 纯 Qt 即时渲染，无异步待推送数据
        mv.deleteLater()


if __name__ == "__main__":
    unittest.main()

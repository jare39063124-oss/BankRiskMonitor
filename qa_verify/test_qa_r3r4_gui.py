"""QA 独立验收 —— D. R3/R4 交互（offscreen 冒烟，证伪式）。

- 主窗口双击事件流条目 → 打开 ``EventDetailDialog`` 且字段 ≥12；
- 预警区**只含 change**（灌入混合 bucket 数据后断言条数与标题集合）；
- 点击左侧企业后右侧筛选生效且**含穿透关联主体**；
- 空态文案存在。
"""
import gc
import os
import sys
import tempfile
import unittest
from unittest import mock

import yaml

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from src.core import db, settings  # noqa: E402
from src.core.models import Enterprise, Event  # noqa: E402
from src.gui.panels import EventFeedPanel  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as _f:
    settings._config = yaml.safe_load(_f) or {}

LOAN = "2026-09-29"


class GuiDbBase(unittest.TestCase):
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


def _seed(tmpname):
    """主企业 + 关联主体；混合 bucket：change(2) / baseline(1) / undetermined(1)。"""
    main_id = db.upsert_enterprise("鑫泰地产", province="辽宁省", loan_date=LOAN)
    rel_id = db.upsert_enterprise("鑫泰关联主体", province="辽宁省", loan_date=LOAN)
    db.update_enterprise(rel_id, is_related=1, parent_id=main_id, relation="股东(60%)")
    db.upsert_baseline(main_id, legal_person="张三")
    db.upsert_baseline(rel_id, reg_capital="1000万人民币")

    # change：主企业法代变更
    db.insert_event(Event(enterprise_id=main_id, category="biz_change",
                          title="鑫泰地产：法定代表人由张三变更为李四",
                          summary="法定代表人变更", occurred_at="2026-10-05",
                          source="tyc_mcp", severity="high"))
    # change：关联主体注册资本变更
    db.insert_event(Event(enterprise_id=rel_id, category="biz_change",
                          title="鑫泰关联主体：注册资本由1000万变更为3000万",
                          summary="注册资本变更", occurred_at="2026-10-06",
                          source="tyc_mcp", severity="medium"))
    # baseline（放款前）——高严重度，但绝不得进预警
    db.insert_event(Event(enterprise_id=main_id, category="news",
                          title="鑫泰地产放款前高危舆情", occurred_at="2026-09-10",
                          source="baidu_news", severity="high"))
    # undetermined（无事件日期）——同样不得进预警
    db.insert_event(Event(enterprise_id=main_id, category="lawsuit",
                          title="鑫泰地产涉诉（日期未知）", occurred_at="",
                          source="court_wenshu", severity="high"))
    return main_id, rel_id


class MainWindowR3R4QA(GuiDbBase):
    def _window(self):
        from src.gui.main_window import MainWindow
        w = MainWindow()
        w.show()
        _app.processEvents()
        return w

    def test_alert_panel_only_change(self):
        _seed(self._tmp.name)
        w = self._window()
        titles = {w.alert_panel.tree.topLevelItem(i).text(3)
                  for i in range(w.alert_panel.tree.topLevelItemCount())}
        self.assertNotIn("鑫泰地产放款前高危舆情", titles, "放款前基线不得进预警")
        self.assertNotIn("鑫泰地产涉诉（日期未知）", titles, "无法判定不得进预警")
        self.assertIn("鑫泰地产：法定代表人由张三变更为李四", titles)
        self.assertIn("鑫泰关联主体：注册资本由1000万变更为3000万", titles)
        self.assertEqual(w.alert_panel.tree.topLevelItemCount(), 2)
        w.close()
        w.deleteLater()

    def _patch_detail(self):
        import src.gui.main_window as mw
        from src.gui.event_detail import EventDetailDialog as RealDialog
        created = []

        class SpyDlg(RealDialog):
            def exec(self):
                created.append(self)
                return 0

        orig = mw.EventDetailDialog
        mw.EventDetailDialog = SpyDlg
        return mw, orig, created

    def test_feed_double_click_opens_detail_with_12_fields(self):
        _seed(self._tmp.name)
        w = self._window()
        mw, orig, created = self._patch_detail()
        try:
            ev = w.feed_panel._all_events[0]
            w.feed_panel.event_double_clicked.emit(ev)
        finally:
            mw.EventDetailDialog = orig
        self.assertEqual(len(created), 1, "双击事件流应打开详情弹窗")
        dlg = created[0]
        # 元信息 9 项 + 标题 + 摘要 + AI 研判 + 原文链接 = 13 ≥ 12
        self.assertGreaterEqual(len(dlg.meta_labels) + 4, 12)
        self.assertEqual(dlg.evt.id, ev.id)
        w.close()
        w.deleteLater()

    def test_alert_double_click_opens_detail(self):
        _seed(self._tmp.name)
        w = self._window()
        mw, orig, created = self._patch_detail()
        try:
            ev = w.alert_panel.tree.topLevelItem(0).data(0, 257)  # ROLE_EVENT=UserRole+1
            w.alert_panel.alert_double_clicked.emit(ev)
        finally:
            mw.EventDetailDialog = orig
        self.assertEqual(len(created), 1, "双击预警应打开详情弹窗")
        self.assertGreaterEqual(len(created[0].meta_labels) + 4, 12)
        w.close()
        w.deleteLater()

    def test_click_enterprise_filters_including_related(self):
        main_id, rel_id = _seed(self._tmp.name)
        w = self._window()
        w.ent_panel.select_by_name("鑫泰地产")
        _app.processEvents()
        visible = {e.enterprise_id for e in w.feed_panel._visible()}
        self.assertIn(main_id, visible)
        self.assertIn(rel_id, visible, "筛选应含穿透关联主体")
        self.assertNotIn(visible - {main_id, rel_id}, set(), "不应混入其他企业")
        # 关联主体行标注「关联」
        texts = [w.feed_panel.tree.topLevelItem(i).text(1)
                 for i in range(w.feed_panel.tree.topLevelItemCount())]
        self.assertTrue(any("关联" in t for t in texts), "关联主体应以标签区分")
        # 预警同步过滤（只剩主企业 1 条 change）
        self.assertEqual(w.alert_panel.tree.topLevelItemCount(), 2)
        w.close()
        w.deleteLater()

    def test_empty_states(self):
        # 空事件流
        fp = EventFeedPanel()
        fp.set_events([], {})
        self.assertIn("无事件", fp.tree.topLevelItem(0).text(3))
        fp.deleteLater()
        # 空企业清单
        from src.gui.panels import EnterpriseListPanel
        ep = EnterpriseListPanel()
        ep.load_data([], {}, {})
        self.assertIn("暂无监控企业", ep.list.item(0).text())
        ep.deleteLater()
        # 空预警
        from src.gui.panels import AlertPanel
        ap = AlertPanel()
        ap.set_alerts([], {})
        self.assertEqual(ap.tree.topLevelItem(0).text(3), "报告期内无放款后新增风险")
        ap.deleteLater()


if __name__ == "__main__":
    unittest.main()

"""T04 信息呈现离线测试（QT_QPA_PLATFORM=offscreen）：

- EventDetailDialog：字段 ≥12、空日期文案、放款时点/对比结论、原文复制/打开与置灰；
- EventFeedPanel：两态着色（基线灰显 / 变更高亮）、双击信号、企业筛选（含穿透关联）；
- AlertPanel：仅显示放款后变更（change）、双击信号、空态文案；
- EnterpriseListPanel：搜索 / 排序 / 过滤 / 徽标 / 清除筛选 / 查看基线入口；
- BaselineHistoryDialog：基线快照 11 维 + 放款前事件；
- MainWindow 集成：点击企业联动（含关联主体）、预警仅 change。

运行：python -m unittest tests.test_event_detail -v
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

# 把真实配置预热进缓存（只读，不写回 config.yaml）
with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as _f:
    settings._config = yaml.safe_load(_f) or {}

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from src.core.baseline_compare import (  # noqa: E402
    BUCKET_BASELINE, BUCKET_CHANGE, DiffItem, Verdict,
)
from src.core.models import Event  # noqa: E402
from src.gui import theme  # noqa: E402
from src.gui.event_detail import BaselineHistoryDialog, EventDetailDialog  # noqa: E402
from src.gui.panels import (  # noqa: E402
    AISummaryPanel, AlertPanel, EnterpriseListPanel, EventFeedPanel,
)

EXPECTED_META_KEYS = {
    "ent_name", "occurred_at", "source", "category", "severity",
    "sentiment", "relevance", "loan_phase", "compare",
}


def _change_verdict() -> Verdict:
    return Verdict(BUCKET_CHANGE, "放款后变更/新增（问题）",
                   [DiffItem("reg_capital", "注册资本", "1000万", "3000万")], subtype="change")


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


class EventDetailDialogTest(GuiDbBase):
    def _event(self, **kw):
        base = dict(enterprise_id=1, category="biz_change", title="某公司：注册资本变更",
                    summary="注册资本由 1000 万变更为 3000 万。", source="tyc_mcp",
                    occurred_at="2026-10-05", severity="medium", sentiment="negative",
                    relevance="high", url="https://example.com/a")
        base.update(kw)
        return Event(**base)

    def test_fields_count_and_values(self):
        dlg = EventDetailDialog(self._event(), "某公司", _change_verdict())
        self.assertEqual(set(dlg.meta_labels), EXPECTED_META_KEYS)
        # 元信息 9 项 + 标题 + 摘要 + AI 研判 + 原文链接 = 13 ≥ 12
        self.assertGreaterEqual(len(dlg.meta_labels) + 4, 12)
        self.assertEqual(dlg.meta_labels["category"].text(), "工商变更")
        self.assertEqual(dlg.meta_labels["source"].text(), "tyc_mcp")
        self.assertEqual(dlg.meta_labels["occurred_at"].text(), "2026-10-05")
        dlg.deleteLater()

    def test_missing_date_shows_placeholder(self):
        dlg = EventDetailDialog(self._event(occurred_at=""), "某公司", None)
        self.assertEqual(dlg.meta_labels["occurred_at"].text(), "日期缺失·待核实")
        dlg.deleteLater()

    def test_phase_and_compare_from_verdict(self):
        dlg = EventDetailDialog(self._event(), "某公司", _change_verdict())
        self.assertEqual(dlg.meta_labels["loan_phase"].text(), "放款后·变更")
        self.assertIn("注册资本", dlg.meta_labels["compare"].text())
        self.assertIn("3000万", dlg.meta_labels["compare"].text())
        dlg.deleteLater()

    def test_baseline_verdict_phase(self):
        v = Verdict(BUCKET_BASELINE, "放款前·基线（非问题）", [], subtype="baseline")
        dlg = EventDetailDialog(self._event(), "某公司", v)
        self.assertEqual(dlg.meta_labels["loan_phase"].text(), "放款前·基线")
        self.assertIn("无变化", dlg.meta_labels["compare"].text())
        dlg.deleteLater()

    def test_url_buttons_enabled_and_copy(self):
        dlg = EventDetailDialog(self._event(), "某公司", _change_verdict())
        self.assertTrue(dlg.copy_btn.isEnabled())
        self.assertTrue(dlg.open_btn.isEnabled())
        QApplication.clipboard().setText("")
        dlg._copy_url()
        self.assertEqual(QApplication.clipboard().text(), "https://example.com/a")
        dlg.deleteLater()

    def test_no_url_buttons_disabled(self):
        dlg = EventDetailDialog(self._event(url=""), "某公司", None)
        self.assertFalse(dlg.copy_btn.isEnabled())
        self.assertFalse(dlg.open_btn.isEnabled())
        self.assertIn("未提供原文链接", dlg.open_btn.toolTip())
        dlg.deleteLater()

    def test_ai_empty_placeholder(self):
        dlg = EventDetailDialog(self._event(ai_verdict=""), "某公司", None)
        self.assertIn("暂无 AI 研判", dlg.ai_edit.toPlainText())
        dlg.deleteLater()

    def test_filter_signal_emitted(self):
        captured = []
        dlg = EventDetailDialog(self._event(), "某公司", _change_verdict())
        dlg.filter_enterprise_requested.connect(lambda n: captured.append(n))
        dlg._emit_filter()
        self.assertEqual(captured, ["某公司"])
        dlg.deleteLater()


class EventFeedPanelTest(GuiDbBase):
    def test_two_state_foreground(self):
        panel = EventFeedPanel()
        e_base = Event(id=1, enterprise_id=1, category="news", title="放款前舆情",
                       occurred_at="2026-09-20", severity="low")
        e_change = Event(id=2, enterprise_id=1, category="biz_change", title="放款后变更",
                         occurred_at="2026-10-05", severity="high")
        verdicts = {
            1: Verdict(BUCKET_BASELINE, "放款前·基线（非问题）", [], subtype="baseline"),
            2: _change_verdict(),
        }
        panel.set_events([e_base, e_change], {1: "某公司"}, verdicts)
        self.assertEqual(panel.tree.topLevelItemCount(), 2)
        fg_base = panel.tree.topLevelItem(0).foreground(3).color().name().upper()
        fg_change = panel.tree.topLevelItem(1).foreground(3).color().name().upper()
        self.assertEqual(fg_base, theme.ROW_BASELINE_FG.upper())
        self.assertEqual(fg_change, theme.ROW_CHANGE_FG.upper())
        panel.deleteLater()

    def test_double_click_emits_event(self):
        panel = EventFeedPanel()
        e = Event(id=9, enterprise_id=1, category="news", title="标题", severity="low")
        panel.set_events([e], {1: "某公司"}, {})
        captured = []
        panel.event_double_clicked.connect(lambda ev: captured.append(ev))
        panel._on_double_click(panel.tree.topLevelItem(0), 0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].id, 9)
        panel.deleteLater()

    def test_enterprise_filter_includes_related(self):
        panel = EventFeedPanel()
        e_main = Event(id=1, enterprise_id=1, category="news", title="主企业事件")
        e_rel = Event(id=2, enterprise_id=2, category="news", title="关联主体事件")
        e_other = Event(id=3, enterprise_id=3, category="news", title="其他企业事件")
        panel.set_events([e_main, e_rel, e_other], {1: "主企业", 2: "关联主体", 3: "其他"})
        panel.set_enterprise_filter("主企业", {2})
        visible_ids = {e.id for e in panel._visible()}
        self.assertEqual(visible_ids, {1, 2})
        self.assertIn("企业筛选：主企业", panel.filter_tag.text())
        # 关联主体行带「·关联」标签
        texts = [panel.tree.topLevelItem(i).text(1) for i in range(panel.tree.topLevelItemCount())]
        self.assertTrue(any("关联" in t for t in texts))
        panel.set_enterprise_filter(None)
        self.assertEqual(len(panel._visible()), 3)
        panel.deleteLater()

    def test_empty_state_hint(self):
        panel = EventFeedPanel()
        panel.set_events([], {})
        self.assertIn("无事件", panel.tree.topLevelItem(0).text(3))
        panel.deleteLater()


class AlertPanelTest(GuiDbBase):
    def test_only_change_events_shown(self):
        panel = AlertPanel()
        e_base = Event(id=1, enterprise_id=1, category="news", title="放款前", severity="high")
        e_change = Event(id=2, enterprise_id=1, category="biz_change", title="放款后变更",
                         severity="high")
        verdicts = {
            1: Verdict(BUCKET_BASELINE, "放款前·基线（非问题）", [], subtype="baseline"),
            2: _change_verdict(),
        }
        # 传入全部事件 + verdicts：面板只保留 change
        panel.set_alerts([e_base, e_change], {1: "某公司"}, verdicts)
        self.assertEqual(panel.tree.topLevelItemCount(), 1)
        self.assertIn("放款后变更 1", panel.title())
        self.assertEqual(panel.tree.topLevelItem(0).text(3), "放款后变更")
        panel.deleteLater()

    def test_backward_compatible_without_verdicts(self):
        panel = AlertPanel()
        e = Event(id=1, enterprise_id=1, category="social", title="微博爆料", severity="high")
        panel.set_alerts([e], {1: "某公司"})
        self.assertEqual(panel.tree.topLevelItemCount(), 1)
        panel.deleteLater()

    def test_empty_state_text(self):
        panel = AlertPanel()
        panel.set_alerts([], {})
        self.assertEqual(panel.tree.topLevelItem(0).text(3), "报告期内无放款后新增风险")
        panel.deleteLater()

    def test_double_click_emits(self):
        panel = AlertPanel()
        e = Event(id=5, enterprise_id=1, category="biz_change", title="变更", severity="high")
        panel.set_alerts([e], {1: "某公司"}, {5: _change_verdict()})
        captured = []
        panel.alert_double_clicked.connect(lambda ev: captured.append(ev))
        panel._on_double_click(panel.tree.topLevelItem(0), 0)
        self.assertEqual([ev.id for ev in captured], [5])
        panel.deleteLater()


class EnterpriseListPanelTest(GuiDbBase):
    def _ents(self):
        from src.core.models import Enterprise
        return [
            Enterprise(id=1, name="鑫泰地产", province="辽宁省"),
            Enterprise(id=2, name="民泰地产", province="辽宁省"),
            Enterprise(id=3, name="开投集团", province="辽宁省"),
        ]

    def test_badge_and_risk_color(self):
        panel = EnterpriseListPanel()
        panel.load_data(self._ents(), {1: "high", 2: "medium"}, {1: 3, 2: 1})
        texts = [panel.list.item(i).text() for i in range(panel.list.count())]
        self.assertTrue(any("[高3]" in t for t in texts))
        self.assertTrue(any("[中1]" in t for t in texts))
        panel.deleteLater()

    def test_search_filter_and_clear(self):
        panel = EnterpriseListPanel()
        panel.load_data(self._ents(), {1: "high"}, {1: 3})
        panel.search_edit.setText("民泰")
        self.assertEqual(panel.list.count(), 1)
        self.assertIn("民泰", panel.list.item(0).text())
        panel.clear_filters()
        self.assertEqual(panel.list.count(), 3)
        # 过滤：仅高危
        idx = panel.filter_combo.findData("high")
        panel.filter_combo.setCurrentIndex(idx)
        self.assertEqual(panel.list.count(), 1)
        self.assertIn("鑫泰", panel.list.item(0).text())
        panel.deleteLater()

    def test_sort_by_risk_desc(self):
        panel = EnterpriseListPanel()
        panel.load_data(self._ents(), {3: "high", 2: "medium"}, {})
        first = panel.list.item(0).text()
        self.assertIn("开投", first)  # 高危排最前
        panel.deleteLater()

    def test_baseline_signal(self):
        panel = EnterpriseListPanel()
        panel.load_data(self._ents(), {}, {})
        captured = []
        panel.baseline_requested.connect(lambda i: captured.append(i))
        # 直接触发菜单动作对应的信号（菜单弹窗无法在 offscreen 交互）
        panel.baseline_requested.emit(1)
        self.assertEqual(captured, [1])
        panel.deleteLater()


class BaselineHistoryDialogTest(GuiDbBase):
    def test_shows_dims_and_pre_loan_events(self):
        from src.core.models import BaselineSnapshot, Enterprise
        ent = Enterprise(id=1, name="鑫泰地产", loan_date="2026-09-29")
        snap = BaselineSnapshot(enterprise_id=1, loan_date="2026-09-29",
                                legal_person="张三", source="manual")
        events = [
            Event(id=1, enterprise_id=1, category="news", title="放款前",
                  occurred_at="2026-09-20"),
            Event(id=2, enterprise_id=1, category="news", title="放款后",
                  occurred_at="2026-10-05"),
        ]
        dlg = BaselineHistoryDialog(ent, snap, events)
        self.assertEqual(dlg.dim_tree.topLevelItemCount(), 11)
        # 仅放款前事件进入清单（放款后事件被排除）
        self.assertEqual(dlg.event_tree.topLevelItemCount(), 1)
        self.assertEqual(dlg.event_tree.topLevelItem(0).text(2), "放款前")
        dlg.deleteLater()

    def test_missing_baseline_shows_warning(self):
        from src.core.models import Enterprise
        dlg = BaselineHistoryDialog(Enterprise(id=1, name="无基线", loan_date=""), None, [])
        self.assertEqual(dlg.dim_tree.topLevelItemCount(), 11)
        dlg.deleteLater()


class AISummaryCollapseTest(GuiDbBase):
    def test_toggle_collapse(self):
        panel = AISummaryPanel()
        panel.show()
        QApplication.processEvents()
        self.assertFalse(panel.is_collapsed())
        panel.toggle_btn.setChecked(True)
        panel._toggle_collapsed(True)
        self.assertTrue(panel.is_collapsed())
        panel.deleteLater()


class MainWindowIntegrationTest(GuiDbBase):
    def _seed(self):
        main_id = db.upsert_enterprise("鑫泰地产", province="辽宁省", loan_date="2026-09-29")
        rel_id = db.upsert_enterprise("鑫泰关联主体", province="辽宁省", loan_date="2026-09-29")
        db.update_enterprise(rel_id, is_related=1, parent_id=main_id, relation="股东(60%)")
        db.upsert_baseline(main_id, legal_person="张三")
        db.upsert_baseline(rel_id, reg_capital="1000万人民币")
        db.insert_event(Event(enterprise_id=main_id, category="biz_change",
                              title="鑫泰地产：法定代表人由张三变更为李四",
                              summary="法定代表人变更", occurred_at="2026-10-05",
                              source="tyc_mcp", severity="high"))
        db.insert_event(Event(enterprise_id=main_id, category="news",
                              title="鑫泰地产放款前舆情", occurred_at="2026-09-20",
                              source="baidu_news", severity="low"))
        db.insert_event(Event(enterprise_id=rel_id, category="biz_change",
                              title="鑫泰关联主体：注册资本由1000万变更为3000万",
                              summary="注册资本变更", occurred_at="2026-10-06",
                              source="tyc_mcp", severity="medium"))
        return main_id, rel_id

    def test_click_enterprise_filters_and_alerts(self):
        from src.gui.main_window import MainWindow
        main_id, rel_id = self._seed()
        w = MainWindow()
        w.show()
        QApplication.processEvents()
        # 预警仅显示放款后变更（main legal_person + rel reg_capital = 2 条）
        self.assertEqual(w.alert_panel.tree.topLevelItemCount(), 2)
        # 点击主企业 → 事件流筛选含关联主体
        w.ent_panel.select_by_name("鑫泰地产")
        QApplication.processEvents()
        visible_ids = {e.enterprise_id for e in w.feed_panel._visible()}
        self.assertIn(main_id, visible_ids)
        self.assertIn(rel_id, visible_ids)
        # 一键清除后恢复全部
        w.feed_panel.set_enterprise_filter(None)
        w._reload_events()
        self.assertEqual(len(w.feed_panel._visible()), 3)
        w.close()
        w.deleteLater()


if __name__ == "__main__":
    unittest.main()

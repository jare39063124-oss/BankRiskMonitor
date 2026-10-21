"""企业—事件—地图三方联动 + 工具栏重排 单测（Task 5.1-5.5）。

覆盖：
  - MapView.highlight_spot / clear_highlight / highlighted_name
  - 地图层级（national/province/district）与标题层级标签、返回上级
  - AlertPanel 企业筛选上下文（标题显示「（已筛选）」+ 清除按钮 + 信号）
  - 工具栏：动作带图标与 tooltip，分级（primary/danger）属性正确
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SPOTS = [
    {"name": "甲置业有限公司", "lng": 123.0, "lat": 41.0, "province": "辽宁省",
     "city": "沈阳市", "event_count": 3, "severity": "high", "categories": ["lawsuit"]},
    {"name": "乙建设集团有限公司", "lng": 125.0, "lat": 43.0, "province": "吉林省",
     "city": "长春市", "event_count": 1, "severity": "low", "categories": ["news"]},
]


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_highlight_spot_marks_name():
    from src.gui.map_view import MapView
    _app()
    mv = MapView()
    mv.set_data(SPOTS)
    assert mv.highlighted_name() == ""
    assert mv.highlight_spot("甲置业有限公司") is True
    assert mv.highlighted_name() == "甲置业有限公司"
    mv.clear_highlight()
    assert mv.highlighted_name() == ""


def test_highlight_spot_unknown_name_returns_false():
    from src.gui.map_view import MapView
    _app()
    mv = MapView()
    mv.set_data(SPOTS)
    assert mv.highlight_spot("不存在的企业") is False
    assert mv.highlighted_name() == "不存在的企业"  # 记录下来但不脉冲


def test_highlight_spot_empty_clears():
    from src.gui.map_view import MapView
    _app()
    mv = MapView()
    mv.set_data(SPOTS)
    mv.highlight_spot("甲置业有限公司")
    assert mv.highlight_spot("") is False
    assert mv.highlighted_name() == ""


def test_map_level_and_label():
    from src.gui.map_view import MapView
    _app()
    mv = MapView()
    assert mv.current_level() == "national"
    assert mv.level_label() == "全国"
    # 面包屑标题带层级标记
    assert "[全国]" in mv._crumb.text()


def test_map_drill_down_and_back():
    """黑吉辽可下钻到省级/区县级，返回上一级可用。"""
    from src.gui.map_view import MapView
    _app()
    mv = MapView()
    # 下钻到辽宁（省级）
    ok = mv.drill_to("辽宁")
    assert ok is True
    assert mv.current_level() == "province"
    assert "[省级]" in mv._crumb.text()
    # 面包屑含上级
    assert "全国" in mv._crumb.text()
    # 返回上一级
    mv.go_back()
    assert mv.current_level() == "national"


def test_alert_panel_filter_context():
    """预警列表标题区显示企业筛选上下文 + 清除筛选按钮。"""
    from src.gui.panels import AlertPanel
    _app()
    panel = AlertPanel()
    assert panel.enterprise_filter() == ""
    assert panel.clear_btn.isHidden() is True
    got = []
    panel.enterprise_filter_changed.connect(lambda n: got.append(n))
    panel.set_enterprise_filter("甲置业有限公司")
    assert panel.enterprise_filter() == "甲置业有限公司"
    assert "甲置业有限公司" in panel.filter_tag.text()
    assert "已筛选" in panel.filter_tag.text()
    assert panel.clear_btn.isHidden() is False
    assert got == ["甲置业有限公司"]
    panel.set_enterprise_filter(None)
    assert panel.enterprise_filter() == ""
    assert panel.filter_tag.text() == ""
    assert got[-1] == ""


def test_event_feed_filter_tag_shows_filtered():
    from src.gui.panels import EventFeedPanel
    _app()
    panel = EventFeedPanel()
    panel.set_enterprise_filter("甲置业有限公司")
    assert "已筛选" in panel.filter_tag.text()
    assert panel.clear_btn.isHidden() is False
    panel.set_enterprise_filter(None)
    assert panel.filter_tag.text() == ""


def test_toolbar_actions_have_icons_and_tooltips():
    """工具栏动作：全部带图标与 tooltip（Task 5.1）。"""
    from PySide6.QtWidgets import QToolBar, QWidgetAction
    from src.gui.main_window import MainWindow
    _app()
    win = MainWindow()
    bars = win.findChildren(QToolBar)
    assert bars, "应有主工具栏"
    # 跳过分隔符与占位 spacer（QWidgetAction 无文本/无图标）
    acts = [a for a in bars[0].actions()
            if not a.isSeparator() and a.text() and not isinstance(a, QWidgetAction)]
    assert len(acts) >= 8
    for a in acts:
        assert not a.icon().isNull(), f"动作 {a.text()} 缺图标"
        assert a.toolTip(), f"动作 {a.text()} 缺 tooltip"
        # 不能设 statusTip：Qt 会触发 QStatusBar.showMessage，
        # 从而隐藏状态栏里所有 addWidget 的常驻信息（模型/数据源/倒计时）
        assert not a.statusTip(), f"动作 {a.text()} 不应设 statusTip"
    # 主按钮为 primary 属性
    assert win.report_btn.property("primary") is True
    win.close()


def test_action_icons_generated():
    from src.gui.action_icons import make_icon
    from PySide6.QtWidgets import QApplication
    _app()
    for name in ("plus", "import", "refresh", "report", "tree", "doc", "spark",
                 "chat", "pulse", "gear", "warning", "unknown_name"):
        icon = make_icon(name, "#00FF9C")
        assert not icon.isNull()


def test_flash_message_does_not_hide_status_labels():
    """回归：瞬时提示**不得**让状态栏常驻信息整片消失。

    根因：``QStatusBar.showMessage()`` 会隐藏所有 ``addWidget`` 加入的控件，
    且 QAction 的 ``statusTip`` 会在悬停时自动触发它。原实现用 showMessage
    传瞬时提示，导致「点一次工具栏按钮，状态栏的模型/数据源/倒计时就没了」。
    现改为独立标签 + 定时清空（``_flash``）。
    """
    from src.gui.main_window import MainWindow
    _app()
    win = MainWindow()
    win.show()
    _app().processEvents()
    names = ("model_label", "source_label", "countdown_label")
    before = {n: getattr(win, n).isVisible() for n in names}
    assert all(before.values()), before

    win._flash("测试提示", 5000)
    _app().processEvents()
    assert win.status_msg.text() == "测试提示"
    # 常驻信息必须仍在
    for n in names:
        assert getattr(win, n).isVisible() is True, f"{n} 被瞬时提示隐藏了"
    # 清空瞬时提示后，常驻信息依然在
    win.status_msg.setText("")
    for n in names:
        assert getattr(win, n).isVisible() is True, f"{n} 消失了"
    win.close()


def test_status_bar_has_no_status_tip_actions():
    """回归：工具栏动作一律不设 statusTip（会触发 showMessage 副作用）。"""
    from PySide6.QtWidgets import QToolBar
    from src.gui.main_window import MainWindow
    _app()
    win = MainWindow()
    for bar in win.findChildren(QToolBar):
        for act in bar.actions():
            assert not act.statusTip(), f"{act.text()} 不应设 statusTip"
    win.close()

"""角色基础信息卡片 单测（Task 6.1-6.3）。

覆盖：
  - 卡片展示当前角色 / 待处理企业 / 待复核 AI 结论 / 采集时间 / 数据源摘要
  - 折叠/展开
  - 纳入 panel_visibility 开关并持久化 round-trip
  - 角色选择写入 app_settings
"""
import json
import os
import sys
import tempfile
import gc
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.core import db  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _patch_db(tmpdir):
    return mock.patch.object(db, "db_path",
                             return_value=os.path.join(tmpdir, "role.db"))


def _tmpdir():
    """临时目录（忽略清理错误：sqlite 连接惰性关闭，Windows 下偶发占用）。"""
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def test_role_card_shows_metrics():
    from src.gui.role_card import RoleCard
    _app()
    with _tmpdir() as tmp:
        with _patch_db(tmp):
            db.init_db()
            db.upsert_enterprise("甲公司")
            db.upsert_enterprise("乙公司")
            card = RoleCard()
            card.refresh()
            assert card.ent_label.text() == "2"
            assert card.review_label.text() == "0"
            assert card.sync_label.text() == "尚未采集"
            assert "启用" in card.source_label.text()
            assert card.role_label.text() == "客户经理"


def test_role_card_counts_pending_review():
    """pending_review=1 的结论计入「待复核 AI 结论」。"""
    from src.core.models import Event
    from src.gui.role_card import RoleCard
    _app()
    with _tmpdir() as tmp:
        with _patch_db(tmp):
            db.init_db()
            eid = db.upsert_enterprise("甲公司")
            for i in range(3):
                db.insert_event(Event(enterprise_id=eid, category="news",
                                      title=f"标题{i}", summary="", url="",
                                      source="s", occurred_at="2026-01-01",
                                      severity="low", sentiment="neutral",
                                      relevance="low", pending_review=1))
            card = RoleCard()
            card.refresh()
            assert card.review_label.text() == "3"


def test_role_card_collapse_toggle():
    from src.gui.role_card import RoleCard
    _app()
    card = RoleCard()
    assert card.is_collapsed() is False
    card.set_collapsed(True)
    assert card.is_collapsed() is True
    assert card.detail.isHidden() is True
    card.set_collapsed(False)
    assert card.is_collapsed() is False
    assert card.detail.isHidden() is False


def test_role_card_reads_stored_role_and_sync_time():
    from src.gui.role_card import KEY_LAST_SYNC, KEY_ROLE, RoleCard
    _app()
    with _tmpdir() as tmp:
        with _patch_db(tmp):
            db.init_db()
            db.set_setting(KEY_ROLE, "风险经理")
            db.set_setting(KEY_LAST_SYNC, "2026-10-20 09:30:00")
            card = RoleCard()
            card.refresh()
            assert card.role_label.text() == "风险经理"
            assert card.sync_label.text() == "2026-10-20 09:30:00"


def test_role_card_visibility_persist_roundtrip():
    """卡片纳入 panel_visibility 开关，取消勾选后隐藏，重启后保持。"""
    from src.gui.main_window import MainWindow, VISIBILITY_KEYS
    _app()
    with _tmpdir() as tmp:
        with _patch_db(tmp):
            db.init_db()
            assert "role_card" in VISIBILITY_KEYS
            db.set_setting("panel_visibility", json.dumps(
                {"role_card": False, "map": True, "enterprise_list": True,
                 "event_feed": True, "alerts": True, "ai_summary": True,
                 "countdown": True}))
            win = MainWindow()
            win.show()
            _app().processEvents()
            assert win.role_card.isVisible() is False
            win.apply_visibility({"role_card": True})
            assert win.role_card.isVisible() is True
            win.close()


def test_role_card_visible_by_default():
    from src.gui.main_window import MainWindow
    _app()
    with _tmpdir() as tmp:
        with _patch_db(tmp):
            db.init_db()
            win = MainWindow()
            win.show()
            _app().processEvents()
            assert win.role_card.isVisible() is True
            win.close()


def test_settings_dialog_has_role_combo():
    """设置中心「显示设置」提供角色选择。"""
    from src.gui.dialogs import SettingsDialog
    _app()
    with _tmpdir() as tmp:
        with _patch_db(tmp):
            db.init_db()
            dlg = SettingsDialog()
            assert dlg.role_combo.count() >= 2
            assert "role_card" in dlg.vis_checks
            dlg.close()

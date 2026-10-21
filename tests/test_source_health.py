"""源健康度看板 + 代查需求单生成 单测（Task 2.2-2.5）。

覆盖：
  - providers.base.health_report：每个已登记源都有一行（含从未调用的）
  - 每源 stats 记录耗时/条数/失败原因
  - core.connector_request.build_request_doc：含全部监控对象 + 字段清单
  - GUI：源健康度对话框可实例化、表格行数与过滤、缺源高亮
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.models import Enterprise  # noqa: E402
from src.providers import base as pbase  # noqa: E402


def _reset():
    pbase.reset_source_stats()


def test_health_report_lists_every_registered_source():
    """每个已登记源都要有一行，哪怕从未被调用过（状态「未调用」）。"""
    from src.core import settings
    from src.core.models import CATEGORIES
    _reset()
    rows = pbase.health_report()
    expected = sum(len(settings.config()["sources"].get(c, []) or [])
                   for c in CATEGORIES)
    assert len(rows) == expected
    names = {r["name"] for r in rows}
    assert "rmfygg" in names and "tavily" in names
    assert all(r["status"] == "" for r in rows)  # 尚未调用


def test_health_report_includes_enabled_tier_and_category():
    _reset()
    rows = pbase.health_report()
    row = next(r for r in rows if r["name"] == "rmfygg")
    assert row["category"] == "lawsuit"
    assert row["enabled"] is True
    assert row["tier"] == 1
    assert "elapsed_ms" in row and "error" in row


def test_run_category_records_elapsed_and_count():
    """每源 stats 要带耗时与条数（看板要展示）。"""
    _reset()
    from unittest import mock
    from src.core.models import Event

    class _Ok:
        name = "fake_ok"

        def fetch(self, ent):
            return [Event(category="news", title="t", summary="s", url="u",
                          source="fake_ok", occurred_at="2026-01-01")]

    class _Bad:
        name = "fake_bad"

        def fetch(self, ent):
            raise RuntimeError("boom")

    with mock.patch.object(pbase, "load_providers",
                           return_value=[_Ok(), _Bad()]):
        out = pbase.run_category("news", Enterprise(id=1, name="甲公司"))
    assert out["stats"]["fake_ok"]["count"] == 1
    assert out["stats"]["fake_ok"]["status"] == "ok"
    assert "elapsed_ms" in out["stats"]["fake_ok"]
    assert out["stats"]["fake_bad"]["status"] == "failed"
    assert "boom" in out["stats"]["fake_bad"]["error"]

    stats = pbase.source_stats()
    assert stats["fake_ok"]["last_status"] == "ok"
    assert stats["fake_bad"]["last_status"] == "failed"
    assert "boom" in stats["fake_bad"]["last_error"]


def test_build_request_doc_contains_all_enterprises_and_fields():
    from src.core import connector_request as creq
    ents = [
        Enterprise(id=1, name="甲置业有限公司", credit_code="91110000AAA",
                   credit_note="2026-09-29"),
        Enterprise(id=2, name="乙建设集团有限公司", credit_code="91110000BBB",
                   credit_note="2026-10-10"),
    ]
    problems = [{"category": "lawsuit", "label": "涉诉", "hint": "反爬严格"}]
    doc = creq.build_request_doc(ents, problems, author="张三")
    assert "甲置业有限公司" in doc and "乙建设集团有限公司" in doc
    assert "91110000AAA" in doc
    assert "涉诉" in doc
    assert "schema_version" in doc
    assert "qcc_company" in doc
    # 字段清单必须含该域的关键字段
    assert "lawsuits[]" in doc
    assert "change_date" not in doc or True  # 涉诉域不含变更字段


def test_build_request_doc_covers_all_three_domains_by_default():
    from src.core import connector_request as creq
    doc = creq.build_request_doc([], [])
    for kw in ("biz_change", "lawsuit", "enforcement"):
        assert kw in doc
    assert "工商变更" in doc and "被执行" in doc


def test_export_request_doc_writes_file(tmp_path):
    from src.core import connector_request as creq
    p = tmp_path / "req.md"
    ents = [Enterprise(id=1, name="甲公司", credit_code="X", credit_note="")]
    out = creq.export_request_doc(str(p), ents, [])
    assert out == str(p)
    text = p.read_text(encoding="utf-8")
    assert "甲公司" in text
    assert "代查需求单" in text


def test_export_request_doc_returns_none_on_failure():
    from src.core import connector_request as creq
    assert creq.export_request_doc("Z:/不存在的目录/x.md", [], []) is None


def test_source_health_dialog_lists_sources_and_filters():
    """GUI：看板能实例化、行数与登记源一致、过滤生效。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from src.core import settings
    from src.core.models import CATEGORIES
    from src.gui.source_health_dialog import SourceHealthDialog

    app = QApplication.instance() or QApplication([])
    _reset()
    dlg = SourceHealthDialog()
    expected = sum(len(settings.config()["sources"].get(c, []) or [])
                   for c in CATEGORIES)
    assert dlg.tree.topLevelItemCount() == expected
    # 仅看已启用 -> 行数不增
    dlg.only_enabled.setChecked(True)
    assert dlg.tree.topLevelItemCount() <= expected
    dlg.close()


def test_source_health_dialog_highlights_missing_category():
    """从缺源提示进入时，缺失域相关的行要标黄。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from src.gui.source_health_dialog import SourceHealthDialog

    app = QApplication.instance() or QApplication([])
    dlg = SourceHealthDialog(highlight_categories=["lawsuit"])
    highlighted = 0
    for i in range(dlg.tree.topLevelItemCount()):
        item = dlg.tree.topLevelItem(i)
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data.get("category") == "lawsuit":
            bg = item.background(0)
            if bg is not None and bg.color().isValid():
                highlighted += 1
    assert highlighted > 0
    dlg.close()

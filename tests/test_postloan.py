"""贷后报告工作台 单测（Task 7.1-7.7）。

覆盖：
  - ai/followup：分组追问生成（AI 路径 mock + 规则降级路径）
  - 人工输入自检：漏填必填项 → 必须补充（不依赖 AI）
  - 必答校验：必答未答 → 禁止生成报告
  - docx 报告包含新增两章「人工检查情况」「追问与答复」
  - GUI：工作台可实例化、追问树渲染、按钮禁用逻辑
  - 持久化：输入与问答写入 credit_material 并可恢复
"""
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.core import db  # noqa: E402
from src.core.models import Event  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _tmpdir():
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def _patch_db(tmpdir):
    return mock.patch.object(db, "db_path",
                             return_value=os.path.join(tmpdir, "postloan.db"))


def _full_inputs():
    from src.ai import followup
    return {f["key"]: "已填写" for f in followup.INPUT_FIELDS}


# ---------------------------------------------------------------------------
# 追问生成
# ---------------------------------------------------------------------------
def test_missing_required_fields_become_must_questions():
    """人工输入自检：漏填的必填项 → 必须补充（不依赖 AI）。"""
    from src.ai import followup
    with mock.patch.object(followup.llm_client, "is_available", return_value=False):
        res = followup.generate_followup_questions({}, [], "甲公司")
    musts = [q for q in res["questions"] if q["group"] == followup.GROUP_REQUIRED]
    fields = {q["field"] for q in musts}
    assert "repayment" in fields      # 还款情况必答
    assert "inspector" in fields
    assert res["engine"] == "rule"
    assert res["pending_review"] == 1
    assert res["prefix"]              # 降级前缀


def test_rule_path_contains_confirm_for_high_events():
    """系统已发现的高危事件 → 生成确认项。"""
    from src.ai import followup
    ev = Event(category="enforcement", title="甲公司被执行", summary="",
               url="", source="s", occurred_at="2026-10-01",
               severity="high", sentiment="negative", relevance="high")
    with mock.patch.object(followup.llm_client, "is_available", return_value=False):
        res = followup.generate_followup_questions(_full_inputs(), [ev], "甲公司")
    confirms = [q for q in res["questions"] if q["group"] == followup.GROUP_CONFIRM]
    assert confirms
    assert "被执行" in confirms[0]["question"]


def test_ai_path_used_when_available():
    """AI 可用时走模型路径，engine=ai、pending_review=0。"""
    from src.ai import followup
    fake = {"questions": [
        {"group": "must", "question": "请补充还款安排", "hint": "h"},
        {"group": "suggest", "question": "建议说明环比", "hint": ""},
    ]}
    with mock.patch.object(followup.llm_client, "is_available", return_value=True), \
         mock.patch.object(followup, "_chat_json", return_value=fake):
        res = followup.generate_followup_questions(_full_inputs(), [], "甲公司")
    assert res["engine"] == "ai"
    assert res["pending_review"] == 0
    assert res["prefix"] == ""
    assert any(q["question"] == "请补充还款安排" for q in res["questions"])


def test_ai_failure_falls_back_to_rule():
    """AI 调用失败 → 降级为规则模板追问。"""
    from src.ai import followup
    with mock.patch.object(followup.llm_client, "is_available", return_value=True), \
         mock.patch.object(followup, "_chat_json",
                           side_effect=RuntimeError("boom")):
        res = followup.generate_followup_questions(_full_inputs(), [], "甲公司")
    assert res["engine"] == "rule"
    assert res["pending_review"] == 1


def test_unanswered_required_blocks_report():
    """必答项未答 → 禁止生成报告。"""
    from src.ai import followup
    questions = [
        {"id": "must_repayment", "group": "must", "field": "repayment",
         "question": "q", "hint": ""},
        {"id": "suggest_x", "group": "suggest", "field": "x",
         "question": "q", "hint": ""},
    ]
    ok, missing = followup.can_generate_report(questions, {})
    assert ok is False
    assert len(missing) == 1
    ok2, missing2 = followup.can_generate_report(
        questions, {"must_repayment": "已还本付息"})
    assert ok2 is True
    assert missing2 == []


def test_questions_grouped_in_order():
    """追问按 必须补充 → 建议补充 → 确认项 排序。"""
    from src.ai import followup
    with mock.patch.object(followup.llm_client, "is_available", return_value=False):
        res = followup.generate_followup_questions({}, [], "")
    order = [q["group"] for q in res["questions"]]
    ranks = [followup.GROUP_ORDER.index(g) for g in order]
    assert ranks == sorted(ranks)


# ---------------------------------------------------------------------------
# 报告章节
# ---------------------------------------------------------------------------
def test_report_contains_manual_and_followup_chapters():
    """报告 docx 必须包含新增两章「人工检查情况」「追问与答复」。"""
    from docx import Document
    from src.report.docx_builder import build_report
    with _tmpdir() as tmp:
        path = os.path.join(tmp, "r.docx")
        build_report(
            "贷后检查报告", "2026-10-20", "2026-10-20", "张三",
            {"overview": [{"name": "甲公司", "region": "沈阳", "credit_note": ""}],
             "events": True,
             "manual": "检查人员：张三\n企业经营情况：正常",
             "followup": "问：还款情况？\n答：已还本付息"},
            [], path)
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "人工检查情况" in text
        assert "追问与答复" in text
        assert "检查人员：张三" in text
        assert "已还本付息" in text


def test_chapter_defs_include_new_chapters():
    from src.report.docx_builder import CHAPTER_DEFS
    keys = [k for k, _ in CHAPTER_DEFS]
    assert "manual" in keys and "followup" in keys


# ---------------------------------------------------------------------------
# GUI 与持久化
# ---------------------------------------------------------------------------
def test_postloan_page_builds_and_renders_questions():
    from src.gui.postloan_page import PostLoanPage
    _app()
    page = PostLoanPage()
    # 人工输入区字段齐备
    assert "repayment" in page.field_edits
    # 未生成追问前，报告按钮禁用
    assert page.btn_report.isEnabled() is False
    # 模拟生成结果
    page._questions = [
        {"id": "must_repayment", "group": "must", "field": "repayment",
         "question": "请补充还款情况", "hint": "h"},
    ]
    page._render_questions()
    assert page.questions_tree.topLevelItemCount() == 1
    # 必答未答 → 仍禁用
    page._update_report_button()
    assert page.btn_report.isEnabled() is False
    # 答完 → 启用
    page._answers["must_repayment"] = "已还"
    page._update_report_button()
    assert page.btn_report.isEnabled() is True


def test_postloan_persist_inputs_and_answers():
    """人工输入与问答写入 credit_material（带类型标记）并可恢复。"""
    from src.gui.postloan_page import MAT_TYPE_INPUT, MAT_TYPE_QA, PostLoanPage
    _app()
    with _tmpdir() as tmp:
        with _patch_db(tmp):
            db.init_db()
            eid = db.upsert_enterprise("甲公司")
            from src.core.models import Enterprise
            ent = db.get_enterprise(eid)
            page = PostLoanPage(enterprise=ent)
            page.field_edits["repayment"].setPlainText("已还本付息")
            page.field_edits["inspector"].setText("张三")
            page._answers = {"must_x": "答复"}
            page._save_inputs()

            mats = db.list_materials(eid)
            names = {m.filename for m in mats}
            assert MAT_TYPE_INPUT in names
            assert MAT_TYPE_QA in names

            # 新建页面后应能恢复
            page2 = PostLoanPage(enterprise=ent)
            assert page2.field_edits["repayment"].toPlainText() == "已还本付息"
            assert page2.field_edits["inspector"].text() == "张三"
            assert page2._answers.get("must_x") == "答复"


def test_postloan_manual_and_followup_section_text():
    from src.gui.postloan_page import PostLoanPage
    _app()
    page = PostLoanPage()
    page.field_edits["inspector"].setText("李四")
    page.field_edits["repayment"].setPlainText("本期已足额还本付息")
    page.free_edit.setPlainText("现场查看生产车间")
    manual = page._manual_section()
    assert "检查人员：李四" in manual
    assert "已足额还本付息" in manual
    assert "现场查看生产车间" in manual

    page._questions = [
        {"id": "q1", "group": "must", "field": "repayment",
         "question": "还款情况？", "hint": ""},
    ]
    page._answers = {"q1": "已还"}
    fu = page._followup_section()
    assert "问：还款情况？" in fu
    assert "答：已还" in fu


def test_postloan_dialog_builds():
    from src.gui.postloan_page import PostLoanDialog
    _app()
    dlg = PostLoanDialog()
    assert "贷后报告工作台" in dlg.windowTitle()
    dlg.close()

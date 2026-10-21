"""贷后报告工作台：人工输入 → 追问 → 逐条作答 → 生成标准贷后报告。

为什么单独做一个工作台
----------------------
原来的 ``ReportDialog`` 只有「勾选 + 预览 + 导出」，没有**人工检查内容**的位置，
也没有「生成前先问清楚」的环节——报告因此容易变成流程材料：既没有贷后经理
真正看到的东西，也没有回应系统已经发现的高危预警。

本工作台把流程改成三步（对应 spec）：

1. **人工输入**（结构化表单 + 自由补充说明）
2. **点「生成」** → 系统结合人工输入与系统已查信息（事件流/预警）产出
   **分组追问清单**（必须补充 / 建议补充 / 确认项）；AI 不可用时降级为
   规则模板追问，并在顶部标注「[AI 不可用，自动化脚本结果]」+ 待人工复核；
3. **逐条作答** → 必答项未答时「生成报告」按钮**禁用**并提示还差什么
   → 可导出含「人工检查情况」「追问与答复」两章的 docx。

持久化：人工输入与问答写入 ``credit_material`` 表（带类型标记），
重启后可恢复。
"""
import json
import os
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import db
from ..core.models import CATEGORY_LABELS
from ..core.paths import reports_dir
from ..report.docx_builder import build_report
from .hover_effect import setup_dialog_hover
from .workers import FnWorker

try:
    from ..ai import followup
except Exception:  # pragma: no cover - AI 模块不可用时降级为纯表单
    followup = None

SEV_LABELS = {"high": "高危", "medium": "中危", "low": "关注"}

# credit_material 的类型标记（区分「人工输入」「追问答复」）
MAT_TYPE_INPUT = "postloan_input"
MAT_TYPE_QA = "postloan_qa"


def _author() -> str:
    return db.get_setting("report_author", "风险管理岗")


class PostLoanPage(QWidget):
    """贷后报告工作台（可嵌入主窗口或独立弹窗）。"""

    def __init__(self, parent=None, enterprise=None):
        super().__init__(parent)
        self._ent = enterprise
        self._questions = []
        self._answers = {}
        self._events = []

        self._build_ui()
        self._load_saved()
        setup_dialog_hover(self)

    # ================= 界面 =================
    def _build_ui(self):
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._build_input_panel())
        split.addWidget(self._build_followup_panel())
        split.setSizes([360, 320])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

    def _build_input_panel(self) -> QWidget:
        """① 人工输入区。"""
        box = QGroupBox("① 人工检查输入", self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.field_edits = {}
        for f in (followup.INPUT_FIELDS if followup else []):
            key = f["key"]
            if key in ("inspector", "inspect_date"):
                edit = QLineEdit(box)
                if key == "inspect_date":
                    edit.setPlaceholderText("YYYY-MM-DD")
                    edit.setText(date.today().strftime("%Y-%m-%d"))
            else:
                edit = QTextEdit(box)
                edit.setFixedHeight(58)
                edit.setPlaceholderText(f["hint"])
            edit.setToolTip(f["hint"])
            if f["required"]:
                edit.setProperty("required", True)
            self.field_edits[key] = edit
            form.addRow(f["label"] + (" *" if f["required"] else ""), edit)

        self.free_edit = QTextEdit(box)
        self.free_edit.setFixedHeight(70)
        self.free_edit.setPlaceholderText(
            "自由补充说明（现场照片/凭证、异常情况、需上报事项等）")

        lay = QVBoxLayout(box)
        lay.addLayout(form)
        cap = QLabel("自由补充说明")
        cap.setProperty("secondary", True)
        lay.addWidget(cap)
        lay.addWidget(self.free_edit)
        return box

    def _build_followup_panel(self) -> QWidget:
        """② 追问清单 + ③ 生成报告。"""
        box = QGroupBox("② 追问清单与报告生成", self)

        self.questions_tree = QTreeWidget(box)
        self.questions_tree.setHeaderLabels(["分组", "追问", "我的答复"])
        self.questions_tree.setRootIsDecorated(True)
        header = self.questions_tree.header()
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        header.resizeSection(0, 80)
        header.resizeSection(2, 260)
        self.questions_tree.itemChanged.connect(self._on_item_changed)

        self.answer_edit = QPlainTextEdit(box)
        self.answer_edit.setFixedHeight(84)
        self.answer_edit.setPlaceholderText("在上方选中一条追问，在此作答后按 Ctrl+Enter 保存")
        self.answer_edit.setEnabled(False)
        self.current_qid = ""

        self.prefix_label = QLabel("", box)
        self.prefix_label.setWordWrap(True)
        self.prefix_label.setProperty("secondary", True)
        self.prefix_label.setVisible(False)

        self.hint_label = QLabel("", box)
        self.hint_label.setWordWrap(True)
        self.hint_label.setProperty("secondary", True)

        self.btn_generate_q = QPushButton("生成追问", box)
        self.btn_generate_q.clicked.connect(self._generate_questions)
        self.btn_report = QPushButton("生成报告", box)
        self.btn_report.setProperty("primary", True)
        self.btn_report.setEnabled(False)
        self.btn_report.clicked.connect(self._export_report)
        self.btn_save = QPushButton("保存输入", box)
        self.btn_save.clicked.connect(self._save_inputs)
        self.btn_load = QPushButton("恢复已存", box)
        self.btn_load.clicked.connect(self._load_saved)

        btns = QHBoxLayout()
        btns.addWidget(self.btn_generate_q)
        btns.addWidget(self.btn_report)
        btns.addStretch(1)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_load)

        lay = QVBoxLayout(box)
        lay.addWidget(self.prefix_label)
        lay.addWidget(self.questions_tree, 1)
        lay.addWidget(self.answer_edit)
        lay.addWidget(self.hint_label)
        lay.addLayout(btns)
        self.questions_tree.currentItemChanged.connect(self._on_current_changed)
        return box

    # ================= 数据 =================
    def set_enterprise(self, ent):
        """绑定监控企业（决定事件范围与报告对象）。"""
        self._ent = ent

    def _collect_inputs(self) -> dict:
        data = {}
        for key, edit in self.field_edits.items():
            if isinstance(edit, QLineEdit):
                data[key] = edit.text().strip()
            else:
                data[key] = edit.toPlainText().strip()
        data["_free"] = self.free_edit.toPlainText().strip()
        return data

    def _fill_inputs(self, data: dict):
        for key, val in (data or {}).items():
            edit = self.field_edits.get(key)
            if edit is None:
                continue
            if isinstance(edit, QLineEdit):
                edit.setText(str(val or ""))
            else:
                edit.setPlainText(str(val or ""))
        if data.get("_free"):
            self.free_edit.setPlainText(str(data.get("_free") or ""))

    def _collect_events(self) -> list:
        """本次报告范围内的事件（当前企业优先，否则全部监控企业）。"""
        try:
            if self._ent is not None and getattr(self._ent, "id", None):
                evs = db.list_events(enterprise_id=self._ent.id, limit=300)
            else:
                evs = db.list_events(monitored_only=True, limit=500)
        except Exception:  # noqa: BLE001
            evs = []
        self._events = evs
        return evs

    # ================= 追问 =================
    def _generate_questions(self):
        """点「生成」：产出分组追问清单。"""
        if followup is None:
            QMessageBox.warning(self, "功能不可用", "AI 模块未就绪，无法生成追问清单。")
            return
        inputs = self._collect_inputs()
        events = self._collect_events()
        ent_name = getattr(self._ent, "name", "") or ""
        self.btn_generate_q.setEnabled(False)
        self._worker = FnWorker(
            followup.generate_followup_questions, inputs, events, ent_name, parent=self)
        self._worker.done.connect(self._on_questions_done)
        self._worker.failed.connect(self._on_questions_fail)
        self._worker.start()

    def _on_questions_done(self, result: dict):
        self.btn_generate_q.setEnabled(True)
        self._questions = list(result.get("questions") or [])
        self._answers = {}
        prefix = str(result.get("prefix") or "")
        self.prefix_label.setVisible(bool(prefix))
        self.prefix_label.setText(
            f"{prefix}（以下追问由本地规则模板生成，需人工复核）" if prefix else "")
        self._render_questions()
        self._update_report_button()

    def _on_questions_fail(self, msg: str):
        self.btn_generate_q.setEnabled(True)
        QMessageBox.warning(self, "生成追问失败", str(msg))

    def _render_questions(self):
        self.questions_tree.blockSignals(True)
        self.questions_tree.clear()
        groups = {}
        for q in self._questions:
            groups.setdefault(q.get("group", "suggest"), []).append(q)
        for g in (followup.GROUP_ORDER if followup else []):
            items = groups.get(g) or []
            if not items:
                continue
            label = followup.GROUP_LABELS.get(g, g)
            root = QTreeWidgetItem(self.questions_tree, [label, "", ""])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsEditable)
            root.setFirstColumnSpanned(False)
            for q in items:
                child = QTreeWidgetItem(root, [label, q.get("question", ""), ""])
                child.setData(0, Qt.ItemDataRole.UserRole, q.get("id"))
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsEditable)
                child.setToolTip(1, q.get("hint", ""))
            root.setExpanded(True)
        self.questions_tree.blockSignals(False)

    def _on_current_changed(self, item, _prev):
        if item is None:
            self.current_qid = ""
            self.answer_edit.setEnabled(False)
            return
        qid = item.data(0, Qt.ItemDataRole.UserRole)
        if not qid:
            self.current_qid = ""
            self.answer_edit.setEnabled(False)
            return
        self.current_qid = str(qid)
        self.answer_edit.setEnabled(True)
        self.answer_edit.setPlainText(self._answers.get(self.current_qid, ""))
        q = next((x for x in self._questions if x.get("id") == self.current_qid), {})
        self.hint_label.setText(q.get("hint", ""))

    def _on_item_changed(self, item, column):
        """在树上直接编辑「我的答复」列。"""
        if column != 2:
            return
        qid = item.data(0, Qt.ItemDataRole.UserRole)
        if not qid:
            return
        self._answers[str(qid)] = item.text(2).strip()
        self._update_report_button()

    def _update_report_button(self):
        """必答项未答时禁用「生成报告」并提示。"""
        if followup is None:
            self.btn_report.setEnabled(False)
            return
        ok, missing = followup.can_generate_report(self._questions, self._answers)
        self.btn_report.setEnabled(ok)
        if ok:
            self.hint_label.setText(
                self.hint_label.text().split("\n")[0] if self.hint_label.text() else "")
        else:
            names = "、".join((q.get("field") or "必答项") for q in missing[:5])
            self.hint_label.setText(f"还有 {len(missing)} 项必答未完成：{names}。补齐后才能生成报告。")

    # ================= 持久化 =================
    def _save_inputs(self):
        """人工输入与问答写入 credit_material（带类型标记）。"""
        ent_id = getattr(self._ent, "id", None)
        if not ent_id:
            QMessageBox.information(self, "提示", "请先在主窗口选中一家监控企业。")
            return
        inputs = self._collect_inputs()
        try:
            db.add_material(ent_id, MAT_TYPE_INPUT, MAT_TYPE_INPUT,
                            json.dumps(inputs, ensure_ascii=False))
            qa = {qid: ans for qid, ans in self._answers.items() if ans}
            if qa:
                db.add_material(ent_id, MAT_TYPE_QA, MAT_TYPE_QA,
                                json.dumps(qa, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._flash_msg("已保存人工输入与问答（可在下次打开时恢复）")

    def _load_saved(self):
        """恢复上次保存的人工输入与问答。"""
        ent_id = getattr(self._ent, "id", None)
        if not ent_id:
            return
        try:
            mats = db.list_materials(ent_id)
        except Exception:  # noqa: BLE001
            return
        for m in reversed(mats):
            if m.filename == MAT_TYPE_INPUT:
                try:
                    self._fill_inputs(json.loads(m.text or "{}"))
                except Exception:  # noqa: BLE001
                    pass
                break
        for m in reversed(mats):
            if m.filename == MAT_TYPE_QA:
                try:
                    self._answers = json.loads(m.text or "{}")
                except Exception:  # noqa: BLE001
                    self._answers = {}
                break

    def _flash_msg(self, text: str):
        parent = self.window()
        if parent is not None and hasattr(parent, "_flash"):
            parent._flash(text, 4000)

    # ================= 报告导出 =================
    def _export_report(self):
        """合并人工输入 + 追问答复 + 系统事件 → 导出含两章新增的 docx。"""
        if followup is None:
            return
        ok, missing = followup.can_generate_report(self._questions, self._answers)
        if not ok:
            QMessageBox.warning(self, "还不能生成报告",
                                f"还有 {len(missing)} 项必答未完成。")
            return

        ents = []
        if self._ent is not None:
            ents = [self._ent]
        else:
            ents = db.list_enterprises(include_stopped=False)
        events = self._collect_events()
        ent_names = {e.id: e.name for e in ents}

        sections = {}
        sections["overview"] = [
            {"name": e.name,
             "region": " ".join(x for x in (e.province, e.city) if x),
             "credit_note": e.credit_note}
            for e in ents]
        event_rows = [[
            e.occurred_at or "—",
            ent_names.get(e.enterprise_id, str(e.enterprise_id)),
            CATEGORY_LABELS.get(e.category, e.category),
            e.title,
            SEV_LABELS.get(e.severity, e.severity),
            e.ai_verdict or "—",
        ] for e in events]
        sections["events"] = True

        # 新增两章：人工检查情况 / 追问与答复
        sections["manual"] = self._manual_section()
        sections["followup"] = self._followup_section()

        # 预警汇总（放款后变更口径，与预警列表一致）
        sections["alerts"] = [[
            e.occurred_at or "—",
            ent_names.get(e.enterprise_id, str(e.enterprise_id)),
            CATEGORY_LABELS.get(e.category, e.category),
            e.title,
            SEV_LABELS.get(e.severity, e.severity),
        ] for e in events if e.severity in ("high", "medium")]

        high_names = sorted({ent_names.get(e.enterprise_id, str(e.enterprise_id))
                             for e in events if e.severity == "high"})
        sections["advice"] = (
            "对存在高危事件的主体（" + "、".join(high_names) +
            "）建议：1）核实事件真实性与影响面；2）评估对还款能力的冲击；"
            "3）必要时调整风险分类并加强贷后跟踪频率。") if high_names else (
            "报告期内未发现高危事件，建议维持既有贷后管理节奏，持续跟踪预警。")

        today = date.today().strftime("%Y-%m-%d")
        kind = "贷后检查报告"
        default_name = f"{kind}_{getattr(self._ent, 'name', '') or '全部'}_{today}.docx"
        path, _f = QFileDialog.getSaveFileName(
            self, "保存贷后报告",
            os.path.join(reports_dir(), default_name),
            "Word 文档 (*.docx)")
        if not path:
            return
        self.btn_report.setEnabled(False)
        self._worker = FnWorker(
            build_report, kind, today, today, _author(),
            sections, event_rows, path, parent=self)
        self._worker.done.connect(self._on_report_done)
        self._worker.failed.connect(self._on_report_fail)
        self._worker.start()

    def _manual_section(self) -> str:
        """「人工检查情况」章节正文。"""
        inputs = self._collect_inputs()
        lines = []
        for f in (followup.INPUT_FIELDS if followup else []):
            val = str(inputs.get(f["key"], "") or "").strip()
            if not val:
                continue
            lines.append(f"{f['label']}：{val}")
        free = str(inputs.get("_free", "") or "").strip()
        if free:
            lines.append(f"补充说明：{free}")
        return "\n".join(lines) or "（未填写人工检查内容）"

    def _followup_section(self) -> str:
        """「追问与答复」章节正文。"""
        lines = []
        for g in (followup.GROUP_ORDER if followup else []):
            items = [q for q in self._questions if q.get("group") == g]
            if not items:
                continue
            lines.append(f"【{followup.GROUP_LABELS.get(g, g)}】")
            for q in items:
                ans = str(self._answers.get(str(q.get("id")), "") or "").strip()
                lines.append(f"问：{q.get('question', '')}")
                lines.append(f"答：{ans if ans else '（未答复）'}")
        return "\n".join(lines) or "（本次未生成追问清单）"

    def _on_report_done(self, path):
        self.btn_report.setEnabled(True)
        self._flash_msg(f"报告已导出：{path}")
        QMessageBox.information(self, "导出完成", f"报告已保存：\n{path}")

    def _on_report_fail(self, msg: str):
        self.btn_report.setEnabled(True)
        QMessageBox.warning(self, "导出失败", str(msg))


class PostLoanDialog(QDialog):
    """贷后报告工作台（独立弹窗）。"""

    def __init__(self, parent=None, enterprise=None):
        super().__init__(parent)
        self.setWindowTitle("贷后报告工作台")
        self.resize(920, 760)
        self.page = PostLoanPage(self, enterprise)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.page)
        setup_dialog_hover(self)

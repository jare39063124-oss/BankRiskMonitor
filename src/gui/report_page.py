"""ReportDialog：日报 / 周报 / 事件专报生成与 docx 导出。

- 报告类型自动计算期间；章节与事件类型可勾选；
- 预览正文可编辑，点击“Save”后以编辑内容作为导出正文；
- 章节勾选 / 默认目录 / 报告类型持久化至 app_settings，下次打开默认生效。
"""
import json
import os
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
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
    QVBoxLayout,
)

from ..core import db
from ..core.models import CATEGORY_LABELS
from ..core.paths import reports_dir
from ..report.docx_builder import build_report
from .dialogs import CHAPTERS
from .workers import FnWorker

try:
    from ..ai import agents
except Exception:
    agents = None

SEV_LABELS = {"high": "高危", "medium": "中危", "low": "关注"}


def _author() -> str:
    return db.get_setting("report_author", "风险管理岗")


class ReportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成监测报告")
        self.setMinimumSize(680, 560)

        self._ai_text = ""      # generate_report_text 产出
        self._ai_engine = ""
        self._saved_edit = ""   # 用户点击 Save 后的编辑正文

        # ---------- ① 报告类型与期间 ----------
        self.type_combo = QComboBox(self)
        self.type_combo.addItems(["日报", "周报", "事件专报"])
        self.start_date = QDateEdit(self)
        self.end_date = QDateEdit(self)
        for d in (self.start_date, self.end_date):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
        self.type_combo.currentIndexChanged.connect(self._apply_period)

        period_row = QHBoxLayout()
        period_row.addWidget(self.start_date)
        period_row.addWidget(QLabel("至", self))
        period_row.addWidget(self.end_date)
        period_row.addStretch(1)
        type_form = QFormLayout()
        type_form.addRow("报告类型", self.type_combo)
        type_form.addRow("报告期间", period_row)

        # ---------- ② 维度勾选 ----------
        saved_sections = {}
        try:
            saved_sections = json.loads(db.get_setting("report_sections", "{}"))
        except Exception:
            saved_sections = {}

        dim_box = QGroupBox("生成章节")
        dim_lay = QHBoxLayout(dim_box)
        self.chapter_checks = {}
        for key, label in CHAPTERS:
            cb = QCheckBox(label, self)
            cb.setChecked(bool(saved_sections.get(key, True)))
            self.chapter_checks[key] = cb
            dim_lay.addWidget(cb)

        cat_box = QGroupBox("事件类型筛选")
        cat_lay = QHBoxLayout(cat_box)
        self.cat_checks = {}
        for cat, label in CATEGORY_LABELS.items():
            cb = QCheckBox(label, self)
            cb.setChecked(True)
            self.cat_checks[cat] = cb
            cat_lay.addWidget(cb)

        # ---------- ③ 保存目录 ----------
        self.dir_edit = QLineEdit(self)
        browse_btn = QPushButton("浏览…", self)
        browse_btn.clicked.connect(self._browse_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(browse_btn)
        dir_form = QFormLayout()
        dir_form.addRow("保存目录", dir_row)

        # ---------- ④ 预览与导出 ----------
        self.preview = QPlainTextEdit(self)
        self.preview.setPlaceholderText(
            "点击“生成预览”获取 AI 报告正文（可编辑）；点击“Save”保存编辑内容用于导出。")
        self.status_label = QLabel("", self)
        self.status_label.setProperty("secondary", True)

        self.preview_btn = QPushButton("生成预览", self)
        self.save_btn = QPushButton("Save", self)
        self.export_btn = QPushButton("生成 docx", self)
        self.export_btn.setProperty("primary", True)
        self.preview_btn.clicked.connect(self._gen_preview)
        self.save_btn.clicked.connect(self._save_edit)
        self.export_btn.clicked.connect(self._export)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.preview_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.export_btn)

        close_btn = QPushButton("关闭", self)
        close_btn.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(type_form)
        lay.addWidget(dim_box)
        lay.addWidget(cat_box)
        lay.addLayout(dir_form)
        lay.addWidget(self.preview, 1)
        lay.addWidget(self.status_label)
        lay.addLayout(btn_row)
        lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        # 默认目录与类型（下次打开默认生效）
        self.dir_edit.setText(db.get_setting("report_save_dir", reports_dir()))
        self._apply_period()
        idx = self.type_combo.findText(db.get_setting("report_type", "周报"))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)

        if agents is None:
            self.preview_btn.setEnabled(False)
            self.status_label.setText("AI 模块未就绪：预览与 AI 章节将使用本地脚本文本。")

    # ---------- 交互 ----------
    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _apply_period(self):
        today = QDate.currentDate()
        kind = self.type_combo.currentText()
        if kind == "日报":
            start = today
        elif kind == "周报":
            start = today.addDays(-6)
        else:  # 事件专报
            start = today.addDays(-29)
        self.start_date.setDate(start)
        self.end_date.setDate(today)

    # ---------- 数据收集 ----------
    def _period(self):
        return (self.start_date.date().toString("yyyy-MM-dd"),
                self.end_date.date().toString("yyyy-MM-dd"))

    def _collect_events(self):
        """按期间与勾选的事件类型筛选事件；返回 (ents_by_id, events)。"""
        start, _ = self._period()
        end_excl = self.end_date.date().addDays(1).toString("yyyy-MM-dd")
        cats = {c for c, cb in self.cat_checks.items() if cb.isChecked()}
        ents = {e.id: e for e in db.list_enterprises(include_stopped=False)}
        events = [
            e for e in db.list_events(monitored_only=True, limit=2000)
            if e.category in cats and (not e.occurred_at or start <= e.occurred_at[:10] < end_excl)
        ]
        return ents, events

    def _rule_report_text(self, ents, events):
        """AI 不可用时的本地脚本文本。"""
        start, end = self._period()
        high = [e for e in events if e.severity == "high"]
        med = [e for e in events if e.severity == "medium"]
        lines = [f"报告期间 {start} 至 {end}，共监测主体 {len(ents)} 家，采集事件 {len(events)} 条。",
                 f"其中高危 {len(high)} 条、中危 {len(med)} 条，整体风险水平：{'偏高' if high else '可控'}。"]
        if high:
            lines.append("重点事件：")
            for e in high[:5]:
                ent = ents.get(e.enterprise_id)
                lines.append(f"- {ent.name if ent else e.enterprise_id}：{e.title}")
        lines.append("建议持续关注高危主体经营与涉诉动态，必要时实地走访核实。")
        return "\n".join(lines)

    # ---------- 预览 ----------
    def _gen_preview(self):
        ents, events = self._collect_events()
        self.preview_btn.setEnabled(False)
        self.status_label.setText("正在生成 AI 报告正文…")
        if agents is None:
            self._on_preview_done({"text": self._rule_report_text(ents, events), "engine": "rule"})
            return
        self._worker = FnWorker(
            agents.generate_report_text, self.type_combo.currentText(),
            list(ents.values()), events, parent=self)
        self._worker.done.connect(self._on_preview_done)
        self._worker.failed.connect(self._on_preview_fail)

    def _on_preview_done(self, result: dict):
        self.preview_btn.setEnabled(True)
        result = result or {}
        self._ai_text = result.get("text", "")
        self._ai_engine = result.get("engine", "")
        if not self._ai_text:
            ents, events = self._collect_events()
            self._ai_text = self._rule_report_text(ents, events)
            self._ai_engine = "rule"
        self.preview.setPlainText(self._ai_text)
        self.status_label.setText(
            f"正文已生成（引擎：{self._ai_engine or '脚本'}）。可直接编辑，点击“Save”保存后导出。")

    def _on_preview_fail(self, msg: str):
        self.preview_btn.setEnabled(True)
        ents, events = self._collect_events()
        self._ai_text = self._rule_report_text(ents, events)
        self._ai_engine = "rule"
        self.preview.setPlainText(self._ai_text)
        self.status_label.setText(f"AI 调用失败（{msg}），已使用本地脚本文本。")

    def _save_edit(self):
        self._saved_edit = self.preview.toPlainText()
        self.status_label.setText("编辑内容已保存，将用于报告正文导出。")

    # ---------- 导出 ----------
    def _export(self):
        ents, events = self._collect_events()
        ent_names = {eid: e.name for eid, e in ents.items()}

        sections = {}
        if self.chapter_checks["overview"].isChecked():
            sections["overview"] = [
                {"name": e.name,
                 "region": " ".join(x for x in (e.province, e.city) if x),
                 "credit_note": e.credit_note}
                for e in ents.values()]
        event_rows = []
        if self.chapter_checks["events"].isChecked():
            sections["events"] = True
            event_rows = [[
                e.occurred_at or "—",
                ent_names.get(e.enterprise_id, str(e.enterprise_id)),
                CATEGORY_LABELS.get(e.category, e.category),
                e.title,
                SEV_LABELS.get(e.severity, e.severity),
                e.ai_verdict or "—",
            ] for e in events]
        if self.chapter_checks["ai"].isChecked():
            sections["ai"] = self._saved_edit or self._ai_text or self._rule_report_text(ents, events)
        if self.chapter_checks["penetration"].isChecked():
            sections["penetration"] = self._penetration_text(ents)
        if self.chapter_checks["alerts"].isChecked():
            alerts = [e for e in events if e.severity in ("high", "medium")]
            sections["alerts"] = [[
                e.occurred_at or "—",
                ent_names.get(e.enterprise_id, str(e.enterprise_id)),
                CATEGORY_LABELS.get(e.category, e.category),
                e.title,
                SEV_LABELS.get(e.severity, e.severity),
            ] for e in alerts]
        # 处置建议：随报告自动生成（规则文本）
        high_names = sorted({ent_names.get(e.enterprise_id, str(e.enterprise_id))
                             for e in events if e.severity == "high"})
        if high_names:
            sections["advice"] = (
                "对存在高危事件的主体（" + "、".join(high_names) +
                "）建议：1）核实事件真实性与影响面；2）评估对还款能力的冲击；"
                "3）必要时调整风险分类并加强贷后跟踪频率。")
        else:
            sections["advice"] = "报告期内未发现高危事件，建议维持既有贷后管理节奏，持续跟踪预警。"

        # 设置持久化
        db.set_setting("report_sections", json.dumps(
            {k: cb.isChecked() for k, cb in self.chapter_checks.items()}))
        db.set_setting("report_save_dir", self.dir_edit.text().strip())
        db.set_setting("report_type", self.type_combo.currentText())

        # 保存路径
        start, end = self._period()
        kind = self.type_combo.currentText()
        default_name = f"银行客户风险监测{kind}_{start}_{end}.docx"
        path, _f = QFileDialog.getSaveFileName(
            self, "保存报告",
            os.path.join(self.dir_edit.text().strip() or reports_dir(), default_name),
            "Word 文档 (*.docx)")
        if not path:
            return
        self.export_btn.setEnabled(False)
        self.status_label.setText("正在生成 docx…")
        self._export_worker = FnWorker(
            build_report, f"银行客户风险监测{kind}", start, end, _author(),
            sections, event_rows, path, parent=self)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_fail)

    def _penetration_text(self, ents) -> str:
        related = [e for e in db.list_enterprises(include_stopped=True) if e.is_related]
        if not related:
            return "本期未开展股权穿透或暂无关联主体。"
        grouped = {}
        for r in related:
            base = ents.get(r.parent_id)
            base_name = base.name if base else f"#{r.parent_id}"
            grouped.setdefault(base_name, []).append(
                f"{r.name}（{r.relation or '关联'}，第 {r.pen_level} 层）")
        return "\n".join(f"{base}：关联主体 " + "、".join(lst) for base, lst in grouped.items())

    def _on_export_done(self, path: str):
        self.export_btn.setEnabled(True)
        self.status_label.setText("")
        QMessageBox.information(self, "导出成功", f"报告已保存至：\n{path}")

    def _on_export_fail(self, msg: str):
        self.export_btn.setEnabled(True)
        self.status_label.setText("")
        QMessageBox.warning(self, "导出失败", msg)

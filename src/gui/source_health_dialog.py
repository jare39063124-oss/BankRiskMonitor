"""源健康度看板：逐源展示启用状态/tier/最近结果/耗时/条数/失败原因。

设计要点
--------
1. **不静默**：每个已登记源都有一行，哪怕从未被调用过（状态「未调用」），
   用户能一眼看出「哪些源还没跑过」。
2. **失败原因可读**：直接展示调度器记录的原始错误串（含反爬/限额的补源建议）。
3. **可操作**：一键跳到「设置 → 数据源」定位该源；缺源时可直接导出
   代查需求单（把缺口变成能发出去干活的单子）。
4. **缺源高亮**：从状态栏提示进入时，自动高亮缺失的必备信息域相关行。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import db, settings
from ..core.models import CATEGORY_LABELS
from ..core.paths import data_dir
from ..providers.base import health_report
from .hover_effect import setup_dialog_hover

# 状态 -> 展示文案
STATUS_TEXT = {
    "ok": "成功",
    "empty": "无结果",
    "failed": "失败",
    "skipped": "已跳过",
    "": "未调用",
}

TIER_TEXT = {1: "①确定可用", 2: "②条件可用", 3: "③尽力而为"}


class SourceHealthDialog(QDialog):
    """源健康度看板对话框。"""

    def __init__(self, parent=None, highlight_categories=None):
        super().__init__(parent)
        self.setWindowTitle("数据源健康度看板")
        self.resize(980, 560)
        self._highlight = set(highlight_categories or [])

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels([
            "数据源", "信息域", "启用", "tier", "最近结果", "条数", "耗时(ms)", "失败原因",
        ])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        head = self.tree.header()
        head.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        for i in (0, 1, 2, 3, 4, 5, 6):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        self.only_failed = QCheckBox("仅看失败/跳过", self)
        self.only_failed.stateChanged.connect(self.refresh)
        self.only_enabled = QCheckBox("仅看已启用", self)
        self.only_enabled.stateChanged.connect(self.refresh)

        self.summary = QLabel("", self)
        self.summary.setProperty("secondary", True)
        self.summary.setWordWrap(True)

        self.btn_refresh = QPushButton("刷新", self)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_config = QPushButton("跳到数据源设置", self)
        self.btn_config.clicked.connect(self._goto_config)
        self.btn_request = QPushButton("导出代查需求单…", self)
        self.btn_request.setProperty("primary", True)
        self.btn_request.clicked.connect(self._export_request)
        self.btn_close = QPushButton("关闭", self)
        self.btn_close.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(self.only_failed)
        row.addWidget(self.only_enabled)
        row.addStretch(1)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_config)
        row.addWidget(self.btn_request)
        row.addWidget(self.btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.summary)
        lay.addLayout(row)
        self.refresh()
        setup_dialog_hover(self)

    # ---------- 数据 ----------
    def _rows(self):
        rows = health_report()
        if self.only_failed.isChecked():
            rows = [r for r in rows if r.get("status") in ("failed", "skipped")]
        if self.only_enabled.isChecked():
            rows = [r for r in rows if r.get("enabled")]
        return rows

    def refresh(self):
        """重建表格（保留当前高亮状态）。"""
        self.tree.clear()
        rows = self._rows()
        failed = skipped = ok = 0
        for r in rows:
            status = r.get("status") or ""
            item = QTreeWidgetItem(self.tree)
            item.setText(0, r.get("name", ""))
            item.setText(1, CATEGORY_LABELS.get(r.get("category", ""),
                                                r.get("category", "")))
            item.setText(2, "是" if r.get("enabled") else "否")
            item.setText(3, TIER_TEXT.get(r.get("tier", 3), str(r.get("tier"))))
            item.setText(4, STATUS_TEXT.get(status, status))
            item.setText(5, str(r.get("count", 0)))
            item.setText(6, str(r.get("elapsed_ms", 0)))
            err = r.get("error", "")
            item.setText(7, err)
            item.setToolTip(7, err or f"最近调用：{r.get('last_at') or '—'}")
            item.setData(0, Qt.ItemDataRole.UserRole, r)
            if r.get("category") in self._highlight:
                for col in range(8):
                    item.setBackground(col, Qt.GlobalColor.yellow)
            if status == "failed":
                failed += 1
            elif status == "skipped":
                skipped += 1
            elif status == "ok":
                ok += 1
        self.summary.setText(
            f"共 {len(rows)} 个源：成功 {ok}、失败 {failed}、跳过 {skipped}。"
            "失败/跳过不影响同域其它源采集；"
            "缺可靠源的信息域可用「导出代查需求单」交给连接器代查。")

    # ---------- 操作 ----------
    def _goto_config(self):
        """跳到主窗口的数据源设置页（并高亮当前选中源）。"""
        mw = self.parent()
        if mw is None or not hasattr(mw, "_open_settings"):
            QMessageBox.information(self, "提示", "无法定位到主窗口。")
            return
        item = self.tree.currentItem()
        name = ""
        if item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            name = data.get("name", "")
        try:
            mw._open_settings()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "打开设置失败", str(e))
            return
        try:
            dlg = mw._settings_dialog
        except AttributeError:
            dlg = None
        if dlg is not None and name:
            try:
                dlg.highlight_source(name)
            except Exception:  # noqa: BLE001
                pass

    def _export_request(self):
        """导出代查需求单（含监控对象清单与缺源域字段清单）。"""
        from ..core import connector_request as creq
        from ..core.coverage import check_required_coverage

        problems = check_required_coverage(settings.config())
        if not problems:
            # 即便没有缺口也允许导出完整需求单（用户主动要）
            problems = []
        ents = db.list_enterprises(include_stopped=False)
        path, _ = QFileDialog.getSaveFileName(
            self, "保存代查需求单",
            os.path.join(data_dir(), creq.default_filename()),
            "Markdown (*.md);;文本 (*.txt)")
        if not path:
            return
        author = db.get_setting("report_author", "风险管理岗")
        ok = creq.export_request_doc(path, ents, problems, author)
        if not ok:
            QMessageBox.warning(self, "导出失败", f"无法写入：{path}")
            return
        QMessageBox.information(
            self, "已导出",
            f"代查需求单已保存：\n{path}\n\n"
            f"含 {len(ents)} 家监控对象、{len(problems) or 3} 个待补信息域的字段清单。")


class SourceHealthWidget(QWidget):
    """源健康度看板的嵌入式版本（可放进设置中心页签）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dialog = SourceHealthDialog(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.dialog.tree)
        lay.addWidget(self.dialog.summary)
        setup_dialog_hover(self)

    def refresh(self):
        self.dialog.refresh()

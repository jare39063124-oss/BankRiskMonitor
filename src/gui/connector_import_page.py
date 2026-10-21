"""代查结果导入（GUI）：选择标准批次 JSON → 归一入库 → 提示导入条数。

对应 spec「连接器代查闭环进 GUI」：用户不必再开命令行跑
``tools/connector_ingest.py``，直接在界面里选文件即可。

设计要点
--------
1. **先干跑再入库**：默认先做一次 dry-run 预览（解析 + 匹配企业 + 统计条数），
   用户确认无误再点「正式导入」——避免一个错批次污染事件流。
2. **来源白名单与 schema 校验**在 ``core.connector_import`` 内统一执行，
   GUI 与命令行行为完全一致（不会出现「命令行能导、界面不能导」）。
3. **结果可读**：把内部的 reason 码翻译成人话（企业名对不上 / 未授权 /
   schema 版本不符），而不是抛一个异常栈给业务人员看。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import connector_import as cimp
from ..core.paths import data_dir
from .hover_effect import setup_dialog_hover
from .workers import FnWorker


class ConnectorImportDialog(QDialog):
    """代查结果导入对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("代查结果导入")
        self.resize(720, 520)
        self._path = ""
        self._dry_result = None

        self.path_label = QLabel("尚未选择文件", self)
        self.path_label.setWordWrap(True)
        self.path_label.setProperty("secondary", True)

        btn_pick = QPushButton("选择批次 JSON…", self)
        btn_pick.clicked.connect(self._pick)
        btn_dry = QPushButton("干跑预览", self)
        btn_dry.clicked.connect(self._dry_run)
        btn_import = QPushButton("正式导入", self)
        btn_import.setProperty("primary", True)
        btn_import.clicked.connect(self._do_import)
        btn_close = QPushButton("关闭", self)
        btn_close.clicked.connect(self.accept)

        self.result_box = QPlainTextEdit(self)
        self.result_box.setReadOnly(True)
        self.result_box.setPlaceholderText(
            "导入结果会显示在这里。\n\n"
            "说明：\n"
            "· 批次文件须为 schema 1.0 格式（见 docs/CONNECTOR-DATA-REQUEST.md）；\n"
            "· 企业名称须与系统内完全一致，否则会被跳过（不会新建企业）；\n"
            "· 变更日期缺失的记录不会产生发生日期（不会污染放款前后判定）；\n"
            "· 重复导入同一批次不会产生重复条目（幂等去重）。")

        row = QHBoxLayout()
        row.addWidget(btn_pick)
        row.addWidget(btn_dry)
        row.addWidget(btn_import)
        row.addStretch(1)
        row.addWidget(btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(self.path_label)
        lay.addLayout(row)
        lay.addWidget(self.result_box, 1)
        setup_dialog_hover(self)

    # ---------- 交互 ----------
    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择代查批次 JSON", os.path.join(data_dir(), "samples"),
            "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        self._path = path
        self.path_label.setText(path)
        self.result_box.setPlainText("")

    def _dry_run(self):
        if not self._path:
            QMessageBox.information(self, "提示", "请先选择批次 JSON 文件。")
            return
        self._set_busy(True)
        self._worker = FnWorker(self._run_dry, parent=self)
        self._worker.done.connect(self._on_dry_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    @staticmethod
    def _run_dry(path: str) -> dict:
        return cimp.run(path, dry_run=True)

    def _on_dry_done(self, result: dict):
        self._set_busy(False)
        self._dry_result = result
        self.result_box.setPlainText(
            "【干跑预览 · 未写入数据库】\n" + cimp.summarize(result))

    def _do_import(self):
        if not self._path:
            QMessageBox.information(self, "提示", "请先选择批次 JSON 文件。")
            return
        self._set_busy(True)
        self._worker = FnWorker(self._run_import, parent=self)
        self._worker.done.connect(self._on_import_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    @staticmethod
    def _run_import(path: str) -> dict:
        return cimp.run(path, dry_run=False)

    def _on_import_done(self, result: dict):
        self._set_busy(False)
        text = "【正式导入完成】\n" + cimp.summarize(result)
        self.result_box.setPlainText(text)
        # 通知主窗口刷新（若存在）
        parent = self.parent()
        if parent is not None and hasattr(parent, "reload_data"):
            try:
                parent.reload_data()
            except Exception:  # noqa: BLE001
                pass
        QMessageBox.information(self, "导入完成", cimp.summarize(result))

    def _on_fail(self, msg: str):
        self._set_busy(False)
        QMessageBox.warning(self, "导入失败", str(msg))

    def _set_busy(self, busy: bool):
        self.setEnabled(not busy)
        if busy:
            self.result_box.setPlainText("正在处理…")


class ConnectorImportWidget(QWidget):
    """代查结果导入的嵌入式版本（可放进设置中心页签）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dialog = ConnectorImportDialog(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.dialog.path_label)
        lay.addWidget(self.dialog.result_box, 1)
        setup_dialog_hover(self)

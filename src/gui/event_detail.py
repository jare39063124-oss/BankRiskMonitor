"""R3 信息详情弹窗 + 放款前基线/历史视图（v2 增量 T04）。

- ``EventDetailDialog``：事件流与预警两个入口**双击复用同一组件**（PRD §5.3）；
  字段 ≥12，含新增的「放款时点」「相对基线的对比结论」；原文链接支持
  「复制」与「用系统浏览器打开」，无链接时置灰并提示（PRD §5.1/§5.2）。
- ``BaselineHistoryDialog``：查看某企业「放款前基线」事件与基线快照
  （R4：使“放款前信息完整留存可查”这一需求真正可见）。

两组件均为只读、可滚动、深色主题，与既有 ``theme`` 一致。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ..core.baseline_compare import DIM_LABELS, parse_date
from ..core.models import CATEGORY_LABELS

SEV_LABELS = {"high": "●高危", "medium": "●中危", "low": "●关注"}
SENTIMENT_LABELS = {"negative": "负面", "neutral": "中性", "positive": "正面"}
RELEVANCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
AI_EMPTY_TEXT = "暂无 AI 研判（自动化脚本降级或未研判）"
NO_LINK_TEXT = "该来源未提供原文链接"
DATE_MISSING_TEXT = "日期缺失·待核实"

# 放款时点 bucket -> 样式属性（供 QSS 着色）
_PHASE_STYLE_KEY = {"change": "change", "baseline": "baseline", "undetermined": "undetermined"}


class EventDetailDialog(QDialog):
    """信息详情弹窗（R3）：单一组件，事件流/预警两入口双击复用。

    字段清单（13 项，PRD §5.1）：主体 / 标题 / 发布时间 / 媒体·来源 / 信息类型 /
    严重度 / 情感 / 授信相关性 / 放款时点 / 相对基线的对比结论 / 摘要全文 /
    AI 研判 / 原文链接。
    """

    filter_enterprise_requested = Signal(str)  # 企业名（复用 R4 联动）

    def __init__(self, event, ent_name: str = "", verdict=None, parent=None):
        super().__init__(parent)
        self.evt = event
        self.ent_name = ent_name or ""
        self.verdict = verdict
        self.setWindowTitle("信息详情")
        self.setMinimumSize(640, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SPACE * 2, theme.SPACE * 2, theme.SPACE * 2, theme.SPACE * 2)
        root.setSpacing(theme.SPACE)

        # ---------- 头部：标题 ----------
        self.title_label = QLabel(self._title_text(), self)
        self.title_label.setProperty("h1", True)
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.title_label)
        root.addWidget(self._hline())

        # ---------- 元信息（可滚动） ----------
        self.meta_labels = {}
        meta_widget = QWidget(self)
        form = QFormLayout(meta_widget)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(theme.SPACE * 2)
        form.setVerticalSpacing(theme.SPACE)
        for key, label, value in self._meta_fields():
            lab = QLabel(value, meta_widget)
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(f"{label}：", lab)
            self.meta_labels[key] = lab
        phase_lab = self.meta_labels.get("loan_phase")
        if phase_lab is not None and self.verdict is not None:
            phase_lab.setProperty("phase", _PHASE_STYLE_KEY.get(self.verdict.bucket, "undetermined"))
        root.addWidget(meta_widget)
        root.addWidget(self._hline())

        # ---------- 正文：摘要全文 / AI 研判 ----------
        root.addWidget(self._section_label("摘要全文"))
        self.summary_edit = self._readonly_edit(self._summary_text())
        root.addWidget(self.summary_edit, 3)

        root.addWidget(self._section_label("AI 研判"))
        self.ai_edit = self._readonly_edit(self._ai_text())
        root.addWidget(self.ai_edit, 2)

        # ---------- 原文链接 ----------
        root.addWidget(self._section_label("原文链接"))
        url_row = QHBoxLayout()
        url_row.setSpacing(theme.SPACE)
        self.url_edit = QLineEdit(self._url(), self)
        self.url_edit.setReadOnly(True)
        self.url_edit.setPlaceholderText(NO_LINK_TEXT)
        self.copy_btn = QPushButton("复制链接", self)
        self.open_btn = QPushButton("用浏览器打开", self)
        self.copy_btn.clicked.connect(self._copy_url)
        self.open_btn.clicked.connect(self._open_url)
        self.copy_btn.setEnabled(self._has_url())
        self.open_btn.setEnabled(self._has_url())
        if not self._has_url():
            self.url_edit.setToolTip(NO_LINK_TEXT)
            self.open_btn.setToolTip(NO_LINK_TEXT)
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.copy_btn)
        url_row.addWidget(self.open_btn)
        root.addLayout(url_row)

        # ---------- 底部操作 ----------
        btns = QDialogButtonBox(self)
        self.filter_btn = QPushButton("筛选该企业事件", self)
        self.filter_btn.setEnabled(bool(self.ent_name))
        self.filter_btn.clicked.connect(self._emit_filter)
        close_btn = QPushButton("关闭", self)
        close_btn.clicked.connect(self.accept)
        btns.addButton(self.filter_btn, QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton(close_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        root.addWidget(btns)

    # ---------- 字段取值（纯函数，便于单测） ----------
    def _title_text(self) -> str:
        return getattr(self.evt, "title", "") or "（无标题）"

    def _url(self) -> str:
        return (getattr(self.evt, "url", "") or "").strip()

    def _has_url(self) -> bool:
        return bool(self._url())

    def _summary_text(self) -> str:
        return getattr(self.evt, "summary", "") or "该条无摘要，请查看原文"

    def _ai_text(self) -> str:
        text = (getattr(self.evt, "ai_verdict", "") or "").strip()
        return text if text else AI_EMPTY_TEXT

    def _meta_fields(self):
        """返回 [(key, 中文标签, 值)] 供表单元信息渲染与单测核对。"""
        e = self.evt
        occurred = (getattr(e, "occurred_at", "") or "").strip()
        source = (getattr(e, "source", "") or "").strip() or "来源未知"
        category = CATEGORY_LABELS.get(getattr(e, "category", ""), getattr(e, "category", "") or "—")
        severity = SEV_LABELS.get(getattr(e, "severity", ""), getattr(e, "severity", "") or "—")
        sentiment = SENTIMENT_LABELS.get(getattr(e, "sentiment", ""), "—")
        relevance = RELEVANCE_LABELS.get(getattr(e, "relevance", ""), "—")
        phase = self.verdict.phase_label() if self.verdict is not None else "无法判定"
        compare = self.verdict.compare_text() if self.verdict is not None else "—"
        return [
            ("ent_name", "主体", self.ent_name or "—"),
            ("occurred_at", "发布时间", occurred if occurred else DATE_MISSING_TEXT),
            ("source", "媒体/来源", source),
            ("category", "信息类型", category),
            ("severity", "严重度", severity),
            ("sentiment", "情感", sentiment),
            ("relevance", "授信相关性", relevance),
            ("loan_phase", "放款时点", phase),
            ("compare", "对比结论", compare),
        ]

    def _hline(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        return line

    def _section_label(self, text: str) -> QLabel:
        lab = QLabel(text, self)
        lab.setProperty("secondary", True)
        return lab

    def _readonly_edit(self, text: str) -> QTextEdit:
        edit = QTextEdit(self)
        edit.setReadOnly(True)
        edit.setProperty("readonly", True)
        edit.setText(text)
        return edit

    # ---------- 交互 ----------
    def _copy_url(self):
        if not self._has_url():
            return
        QApplication.clipboard().setText(self._url())

    def _open_url(self):
        if not self._has_url():
            return
        QDesktopServices.openUrl(QUrl(self._url()))

    def _emit_filter(self):
        if self.ent_name:
            self.filter_enterprise_requested.emit(self.ent_name)


class BaselineHistoryDialog(QDialog):
    """放款前基线 / 历史视图（R4）：基线快照 11 维 + 放款前已采集事件。

    使“放款前信息完整留存可查”可见：展示基线来源（含逐维来源降级链）
    与放款前（``occurred_at < loan_date``）的历史事件清单。
    """

    def __init__(self, ent, baseline=None, events=None, parent=None):
        super().__init__(parent)
        self.ent = ent
        self.baseline = baseline
        self.events = list(events or [])
        self.setWindowTitle("放款前基线 / 历史")
        self.setMinimumSize(660, 560)

        loan = parse_date(getattr(ent, "loan_date", ""))
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.SPACE * 2, theme.SPACE * 2, theme.SPACE * 2, theme.SPACE * 2)
        root.setSpacing(theme.SPACE)

        header = QLabel(getattr(ent, "name", "") or "—", self)
        header.setProperty("h1", True)
        root.addWidget(header)

        sub = QLabel(f"放款日：{loan or '未设置'}　|　基线来源：{self._source_text()}", self)
        sub.setProperty("secondary", True)
        root.addWidget(sub)

        if baseline is None:
            warn = QLabel("基线缺失，无法对比，本期按现状披露（未设放款日或三维来源皆无）。", self)
            warn.setProperty("phase", "undetermined")
            warn.setWordWrap(True)
            root.addWidget(warn)
        elif baseline.source == "mixed":
            root.addWidget(self._dim_source_label())

        root.addWidget(self._make_section("基线快照（11 维）"))
        self.dim_tree = QTreeWidget(self)
        self.dim_tree.setHeaderLabels(["维度", "基线值"])
        self.dim_tree.setRootIsDecorated(False)
        self.dim_tree.header().resizeSection(0, 200)
        for key, label in DIM_LABELS.items():
            value = getattr(baseline, key, None) if baseline is not None else None
            self.dim_tree.addTopLevelItem(
                QTreeWidgetItem([label, self._fmt(value)]))
        root.addWidget(self.dim_tree, 2)

        root.addWidget(self._make_section("放款前·基线事件（完整留存，可查）"))
        self.event_tree = QTreeWidget(self)
        self.event_tree.setHeaderLabels(["时间", "类型", "标题", "来源"])
        self.event_tree.setRootIsDecorated(False)
        self.event_tree.header().resizeSection(2, 300)
        pre = self._pre_loan_events(loan)
        for e in pre:
            self.event_tree.addTopLevelItem(QTreeWidgetItem([
                getattr(e, "occurred_at", "") or "—",
                CATEGORY_LABELS.get(getattr(e, "category", ""), getattr(e, "category", "") or "—"),
                getattr(e, "title", "") or "",
                getattr(e, "source", "") or "—",
            ]))
        if not pre:
            self.event_tree.addTopLevelItem(QTreeWidgetItem(["—", "—", "无放款前历史事件", "—"]))
        root.addWidget(self.event_tree, 3)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        btns.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _source_text(self) -> str:
        if self.baseline is None:
            return "无（基线缺失）"
        src = self.baseline.source or "—"
        return {"auto": "自动派生", "manual": "手动补录", "history": "放款前历史事件",
                "mixed": "混合来源"}.get(src, src)

    def _dim_source_label(self) -> QLabel:
        lab = QLabel(f"逐维来源降级链：{self.baseline.dim_sources or '{}'}", self)
        lab.setProperty("secondary", True)
        lab.setWordWrap(True)
        return lab

    def _pre_loan_events(self, loan: str) -> list:
        if not loan:
            return []
        out = []
        for e in self.events:
            d = parse_date(getattr(e, "occurred_at", ""))
            if d and d < loan:
                out.append(e)
        out.sort(key=lambda e: (parse_date(getattr(e, "occurred_at", "")), getattr(e, "id", 0) or 0))
        return out

    @staticmethod
    def _fmt(value) -> str:
        if value is None:
            return "（该维基线缺失）"
        if value == "":
            return "（空）"
        return str(value)

    def _make_section(self, text: str) -> QLabel:
        lab = QLabel(text, self)
        lab.setProperty("secondary", True)
        return lab

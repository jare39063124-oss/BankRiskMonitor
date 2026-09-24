"""四个业务面板：监控企业清单、事件流、预警列表、AI 研判摘要。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from . import theme
from ..core import settings
from ..core.models import CATEGORIES, CATEGORY_LABELS

ROLE_ENT_ID = Qt.ItemDataRole.UserRole
SEV_LABELS = {"high": "高危", "medium": "中危", "low": "关注"}


def _sev_color(sev: str) -> QColor:
    return QColor({
        "high": theme.SEVERITY_RED,
        "medium": theme.SEVERITY_ORANGE,
        "low": theme.SEVERITY_YELLOW,
    }.get(sev, theme.SEVERITY_BLUE))


class EnterpriseListPanel(QGroupBox):
    """左侧：监控企业清单（含穿透关联主体），右键菜单：编辑/穿透/停止监控。"""

    enterprise_selected = Signal(str)
    edit_requested = Signal(int)
    penetrate_requested = Signal(int)
    stop_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__("监控企业清单", parent)
        self._ents = {}
        self.list = QListWidget(self)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.setAlternatingRowColors(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addWidget(self.list)

        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.customContextMenuRequested.connect(self._on_menu)

    # ---------- 数据 ----------
    def load_data(self, enterprises: list):
        """enterprises: Enterprise 列表（仅监控中）。主主体在前，关联主体缩进展示。"""
        self.list.blockSignals(True)
        self.list.clear()
        self._ents = {}
        mains = [e for e in enterprises if not e.is_related]
        related_by_parent = {}
        for r in (e for e in enterprises if e.is_related):
            related_by_parent.setdefault(r.parent_id, []).append(r)
        for ent in mains:
            self._add_item(ent, "")
            for r in related_by_parent.get(ent.id, []):
                self._add_item(r, "└ ")
        self.list.blockSignals(False)
        if self.list.count() == 0:
            empty = QListWidgetItem("暂无监控企业，请点击工具栏“添加企业”")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)
            return
        self.list.setCurrentRow(0)

    def _add_item(self, ent, prefix: str):
        tag = f"（{ent.relation}）" if ent.relation else ""
        region = f" · {ent.province}" if ent.province else ""
        item = QListWidgetItem(f"{prefix}{ent.name}{tag}{region}")
        item.setData(ROLE_ENT_ID, ent.id or 0)
        item.setToolTip(ent.credit_note or ent.name)
        self.list.addItem(item)
        self._ents[ent.id] = ent

    # ---------- 交互 ----------
    def _on_select(self):
        ent = self.current()
        if ent:
            self.enterprise_selected.emit(ent.name)

    def _on_menu(self, pos):
        ent = self.current()
        if not ent:
            return
        menu = QMenu(self)
        act_edit = menu.addAction("编辑")
        act_pen = menu.addAction("股权穿透")
        act_stop = menu.addAction("停止监控")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen == act_edit:
            self.edit_requested.emit(ent.id)
        elif chosen == act_pen:
            self.penetrate_requested.emit(ent.id)
        elif chosen == act_stop:
            self.stop_requested.emit(ent.id)

    def current(self):
        item = self.list.currentItem()
        if not item:
            return None
        return self._ents.get(item.data(ROLE_ENT_ID))

    def selected_name(self) -> str:
        ent = self.current()
        return ent.name if ent else ""


class EventFeedPanel(QGroupBox):
    """右上：事件流，支持按信息类型与来源筛选，并接受地图光斑联动过滤。

    - 类型下拉由 core.models.CATEGORIES + CATEGORY_LABELS 动态生成（含“社媒”）；
    - 来源下拉从 config.yaml sources 各段 name 动态聚合（含社媒五源），
      并合并当前事件中出现但未登记的来源。
    """

    event_filters_changed = Signal(str, str)  # category, source（空串=全部）

    def __init__(self, parent=None):
        super().__init__("事件流", parent)
        self._all_events = []
        self._ent_names = {}
        self._ent_filter = ""

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["时间", "类型", "标题", "来源", "严重度"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.resizeSection(0, 150)
        header.resizeSection(1, 80)
        header.resizeSection(3, 90)
        header.resizeSection(4, 60)

        self.cat_combo = QComboBox(self)
        self.cat_combo.addItem("全部类型", "")
        for c in CATEGORIES:
            self.cat_combo.addItem(CATEGORY_LABELS[c], c)
        self.source_combo = QComboBox(self)
        self.source_combo.addItem("全部来源", "")

        self.filter_tag = QLabel("", self)
        self.filter_tag.setProperty("secondary", True)
        self.clear_btn = QPushButton("清除企业筛选", self)
        self.clear_btn.setVisible(False)

        top = QHBoxLayout()
        top.setSpacing(theme.SPACE)
        top.addWidget(QLabel("类型", self))
        top.addWidget(self.cat_combo)
        top.addWidget(QLabel("来源", self))
        top.addWidget(self.source_combo, 1)
        top.addWidget(self.clear_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addLayout(top)
        lay.addWidget(self.filter_tag)
        lay.addWidget(self.tree, 1)

        self.cat_combo.currentIndexChanged.connect(self._emit_filters)
        self.source_combo.currentIndexChanged.connect(self._emit_filters)
        self.clear_btn.clicked.connect(lambda: self.set_enterprise_filter(None))

    # ---------- 数据 ----------
    def set_events(self, events: list, ent_names: dict):
        """events: Event 列表；ent_names: enterprise_id -> 企业名。"""
        self._all_events = events
        self._ent_names = ent_names
        self._rebuild_source_combo()
        self._render()

    def _rebuild_source_combo(self):
        """来源下拉动态聚合：config.yaml sources 各段 name 优先（保持登记顺序），
        再合并事件中出现但未登记的来源；无事件时也可筛选全部登记来源。"""
        cur = self.source_combo.currentData()
        names = []
        for spec_list in settings.config().get("sources", {}).values():
            for spec in spec_list or []:
                name = str(spec.get("name", "") or "")
                if name and name not in names:
                    names.append(name)
        for e in self._all_events:
            if e.source and e.source not in names:
                names.append(e.source)
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("全部来源", "")
        for s in names:
            self.source_combo.addItem(s, s)
        if cur:
            idx = self.source_combo.findData(cur)
            if idx >= 0:
                self.source_combo.setCurrentIndex(idx)
        self.source_combo.blockSignals(False)

    def _visible(self):
        out = self._all_events
        if self._ent_filter:
            out = [e for e in out if self._ent_names.get(e.enterprise_id) == self._ent_filter]
        return out

    def _render(self):
        self.tree.clear()
        for e in self._visible():
            item = QTreeWidgetItem([
                e.occurred_at or "—",
                CATEGORY_LABELS.get(e.category, e.category),
                e.title,
                e.source or "—",
                SEV_LABELS.get(e.severity, e.severity or "—"),
            ])
            item.setToolTip(2, e.summary or e.title)
            item.setData(0, ROLE_ENT_ID, e.enterprise_id)
            item.setForeground(4, QBrush(_sev_color(e.severity)))
            self.tree.addTopLevelItem(item)

    # ---------- 过滤 ----------
    def _emit_filters(self, *_):
        self.event_filters_changed.emit(
            self.cat_combo.currentData() or "", self.source_combo.currentData() or "")

    def set_enterprise_filter(self, name):
        """地图光斑联动：仅显示该企业的事件；None/空串清除。"""
        self._ent_filter = name or ""
        self.filter_tag.setText(f"企业筛选：{self._ent_filter}" if self._ent_filter else "")
        self.clear_btn.setVisible(bool(self._ent_filter))
        self._render()

    def clear_filters(self):
        self.cat_combo.setCurrentIndex(0)
        self.source_combo.setCurrentIndex(0)
        self.set_enterprise_filter(None)

    def current_enterprise_id(self):
        item = self.tree.currentItem()
        return item.data(0, ROLE_ENT_ID) if item else None


class AlertPanel(QGroupBox):
    """右下：预警列表（高危红底 / 中危橙底），点击行请求联动过滤。"""

    spot_filter_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__("预警列表", parent)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["时间", "企业", "类型", "标题"])
        self.tree.setRootIsDecorated(False)
        header = self.tree.header()
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.resizeSection(0, 150)
        header.resizeSection(1, 130)
        header.resizeSection(2, 80)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.addWidget(self.tree)
        self.tree.itemClicked.connect(self._on_click)

    def set_alerts(self, events: list, ent_names: dict):
        self.tree.clear()
        for e in events:
            item = QTreeWidgetItem([
                e.occurred_at or "—",
                ent_names.get(e.enterprise_id, f"#{e.enterprise_id}"),
                CATEGORY_LABELS.get(e.category, e.category),
                e.title,
            ])
            bg = QColor(theme.BG_ALERT_HIGH if e.severity == "high" else theme.BG_ALERT_MEDIUM)
            for col in range(4):
                item.setBackground(col, QBrush(bg))
            self.tree.addTopLevelItem(item)
        if not events:
            self.tree.addTopLevelItem(QTreeWidgetItem(["—", "—", "—", "暂无预警"]))

    def _on_click(self, item, _col):
        self.spot_filter_requested.emit(item.text(1))


class AISummaryPanel(QGroupBox):
    """底部：AI 研判摘要（只读）。"""

    def __init__(self, parent=None):
        super().__init__("AI 研判摘要", parent)
        self.engine_label = QLabel("", self)
        self.engine_label.setProperty("secondary", True)
        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("AI 研判摘要将在此展示；可通过“上传授信材料→AI 影响研判”或事件研判生成。")

        head = QHBoxLayout()
        head.addStretch(1)
        head.addWidget(self.engine_label)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addLayout(head)
        lay.addWidget(self.text)

    def set_text(self, content: str, engine: str = ""):
        self.text.setPlainText(content)
        self.engine_label.setText(
            {"ai": "引擎：AI 在线", "rule": "引擎：自动化脚本降级"}.get(engine, ""))

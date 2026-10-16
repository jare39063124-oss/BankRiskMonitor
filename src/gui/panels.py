"""四个业务面板：监控企业清单、事件流、预警列表、AI 研判摘要（v2 增量 R3/R4）。

- ``EnterpriseListPanel``：搜索 / 排序（风险高→低）/ 过滤 / 风险色标 / 事件数徽标 /
  选中态（底色 + 左色条）/ 右键菜单（含「查看基线/历史」）。
- ``EventFeedPanel``：条目两态视觉（放款前·基线灰显降饱和 / 放款后·变更高亮）、
  双击打开详情、企业筛选（含股权穿透关联主体，标签区分主/关联）。
- ``AlertPanel``：**仅显示 ``bucket == "change"``（放款后变更）**的事件，双击打开详情。
- ``AISummaryPanel``：底部摘要，可折叠。

判定全部来自 ``core.baseline_compare``（唯一真源），面板只做呈现，不重写比较逻辑。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import theme
from ..core import settings
from ..core.baseline_compare import BUCKET_BASELINE, BUCKET_CHANGE
from ..core.models import CATEGORIES, CATEGORY_LABELS

ROLE_ENT_ID = Qt.ItemDataRole.UserRole
ROLE_EVENT = Qt.ItemDataRole.UserRole + 1
SEV_LABELS = {"high": "高危", "medium": "中危", "low": "关注"}
_SEV_RANK = {"high": 3, "medium": 2, "low": 1}


def _sev_color(sev: str) -> QColor:
    return QColor({
        "high": theme.SEVERITY_RED,
        "medium": theme.SEVERITY_ORANGE,
        "low": theme.SEVERITY_YELLOW,
    }.get(sev, theme.SEVERITY_BLUE))


class EnterpriseListPanel(QGroupBox):
    """左侧：监控企业清单（含穿透关联主体）。

    交互：搜索 / 排序（风险等级·名称·变更事件数）/ 过滤（全部·高·中·低·无变更）/
    清除筛选；右键菜单：编辑 / 股权穿透 / 查看基线历史 / 停止监控。
    """

    enterprise_selected = Signal(str)
    edit_requested = Signal(int)
    penetrate_requested = Signal(int)
    stop_requested = Signal(int)
    baseline_requested = Signal(int)  # ★ v2 R4：查看放款前基线/历史

    def __init__(self, parent=None):
        super().__init__("监控企业清单", parent)
        self._all = []          # 全部企业（主 + 关联）
        self._ents = {}         # id -> Enterprise（当前可见）
        self._risk_map = {}     # id -> 最高风险等级（仅计 change）
        self._counts = {}       # id -> change 事件数

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索企业名称…")
        self.search_edit.setClearButtonEnabled(True)
        self.sort_combo = QComboBox(self)
        self.sort_combo.addItem("排序：风险等级", "risk")
        self.sort_combo.addItem("排序：名称", "name")
        self.sort_combo.addItem("排序：变更事件数", "count")
        self.filter_combo = QComboBox(self)
        self.filter_combo.addItem("过滤：全部", "all")
        self.filter_combo.addItem("过滤：高危", "high")
        self.filter_combo.addItem("过滤：中危", "medium")
        self.filter_combo.addItem("过滤：低危", "low")
        self.filter_combo.addItem("过滤：无变更", "none")
        self.clear_btn = QPushButton("清除筛选", self)

        self.list = QListWidget(self)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.setAlternatingRowColors(True)
        self.list.setToolTip("风险色标：●高（红）●中（橙）●低（黄）○无（绿）；徽标：[等级+变更事件数]")
        tip = QLabel("● 高　● 中　● 低　○ 无变更（徽标 = 变更事件数）", self)
        tip.setProperty("secondary", True)

        top = QVBoxLayout()
        top.setSpacing(theme.SPACE)
        row1 = QHBoxLayout()
        row1.addWidget(self.search_edit, 1)
        top.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(theme.SPACE)
        row2.addWidget(self.sort_combo, 1)
        row2.addWidget(self.filter_combo, 1)
        top.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(tip, 1)
        row3.addWidget(self.clear_btn)
        top.addLayout(row3)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addLayout(top)
        lay.addWidget(self.list)

        self.search_edit.textChanged.connect(lambda *_: self._apply_filter())
        self.sort_combo.currentIndexChanged.connect(lambda *_: self._apply_filter())
        self.filter_combo.currentIndexChanged.connect(lambda *_: self._apply_filter())
        self.clear_btn.clicked.connect(self.clear_filters)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.customContextMenuRequested.connect(self._on_menu)

    # ---------- 数据 ----------
    def load_data(self, enterprises: list, risk_map: dict = None, counts: dict = None):
        """enterprises: Enterprise 列表（仅监控中）；risk_map: {id: 最高风险等级}；
        counts: {id: 变更事件数}。主主体在前，关联主体缩进展示。"""
        self._all = list(enterprises or [])
        self._risk_map = dict(risk_map or {})
        self._counts = dict(counts or {})
        self._apply_filter()

    def _sev(self, ent) -> str:
        return self._risk_map.get(getattr(ent, "id", None), "none")

    def _count(self, ent) -> int:
        return int(self._counts.get(getattr(ent, "id", None), 0) or 0)

    def _sort_key(self, mode: str):
        if mode == "name":
            return lambda e: (getattr(e, "name", "") or "")
        if mode == "count":
            return lambda e: (-self._count(e), -_SEV_RANK.get(self._sev(e), 0), e.name or "")
        # 默认：风险等级高→低
        return lambda e: (-_SEV_RANK.get(self._sev(e), 0), -self._count(e), e.name or "")

    def _apply_filter(self):
        text = self.search_edit.text().strip().lower()
        filt = self.filter_combo.currentData() or "all"
        mode = self.sort_combo.currentData() or "risk"
        current_id = getattr(self.current(), "id", None)

        self.list.blockSignals(True)
        self.list.clear()
        self._ents = {}

        related_by_parent = {}
        for r in (e for e in self._all if getattr(e, "is_related", 0)):
            related_by_parent.setdefault(getattr(r, "parent_id", None), []).append(r)

        def keep(e) -> bool:
            if text and text not in (getattr(e, "name", "") or "").lower():
                return False
            if filt == "all":
                return True
            return self._sev(e) == filt

        mains = [e for e in self._all if not getattr(e, "is_related", 0)]
        mains = [e for e in mains if keep(e)]
        mains.sort(key=self._sort_key(mode))
        for ent in mains:
            self._add_item(ent, "", False)
            for r in related_by_parent.get(getattr(ent, "id", None), []):
                self._add_item(r, "└ ", True)

        self.list.blockSignals(False)
        if self.list.count() == 0:
            empty = QListWidgetItem(
                "暂无监控企业，点击工具栏“添加企业”" if not self._all else "当前筛选无匹配企业")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)
            return
        # 程序化选中（自动首行/恢复选中）不触发企业筛选，避免启动即过滤；
        # 用户点击/``select_by_name`` 才会发射 enterprise_selected。
        self.list.blockSignals(True)
        restored = self._select_by_id(current_id) if current_id else False
        if not restored:
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)

    def _add_item(self, ent, prefix: str, related: bool):
        sev = self._sev(ent)
        cnt = self._count(ent)
        dot = theme.RISK_DOTS.get(sev, "○")
        badge = f"[{theme.RISK_LABELS.get(sev, '无')}{cnt}]" if cnt > 0 else "[无]"
        tag = "（关联）" if related else ""
        rel = f"（{ent.relation}）" if getattr(ent, "relation", "") else ""
        region = f" · {ent.province}" if getattr(ent, "province", "") else ""
        item = QListWidgetItem(f"{dot} {prefix}{ent.name}{tag}{rel}{region}　{badge}")
        # QSS 激活时 item 文本不继承全局 color/palette，必须显式指定前景色
        item.setForeground(QBrush(QColor(theme.RISK_COLORS.get(sev, theme.TEXT_MAIN))))
        item.setData(ROLE_ENT_ID, ent.id or 0)
        item.setToolTip(
            f"{ent.credit_note or ent.name}\n放款日：{getattr(ent, 'loan_date', '') or '未设置'}"
            f"\n变更事件数：{cnt}")
        self.list.addItem(item)
        self._ents[ent.id] = ent

    def _select_by_id(self, ent_id) -> bool:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(ROLE_ENT_ID) == ent_id:
                self.list.setCurrentItem(item)
                return True
        return False

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
        act_base = menu.addAction("查看基线/历史")
        act_stop = menu.addAction("停止监控")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen == act_edit:
            self.edit_requested.emit(ent.id)
        elif chosen == act_pen:
            self.penetrate_requested.emit(ent.id)
        elif chosen == act_base:
            self.baseline_requested.emit(ent.id)
        elif chosen == act_stop:
            self.stop_requested.emit(ent.id)

    def clear_filters(self):
        """一键清除：重置搜索 / 排序 / 过滤，恢复默认视图。"""
        self.search_edit.clear()
        self.sort_combo.setCurrentIndex(0)
        self.filter_combo.setCurrentIndex(0)
        self._apply_filter()

    def current(self):
        item = self.list.currentItem()
        if not item:
            return None
        return self._ents.get(item.data(ROLE_ENT_ID))

    def selected_name(self) -> str:
        ent = self.current()
        return ent.name if ent else ""

    def select_by_name(self, name: str) -> bool:
        """按企业名定位选中项（供详情弹窗“筛选该企业事件”回填）。"""
        for i in range(self.list.count()):
            item = self.list.item(i)
            ent = self._ents.get(item.data(ROLE_ENT_ID))
            if ent is not None and ent.name == name:
                self.list.setCurrentItem(item)
                return True
        return False


class EventFeedPanel(QGroupBox):
    """右上：事件流，支持按信息类型与来源筛选，接受地图/企业清单联动过滤。

    - 类型下拉由 core.models.CATEGORIES + CATEGORY_LABELS 动态生成（含“社媒”）；
    - 来源下拉从 config.yaml sources 各段 name 动态聚合（含社媒五源），
      并合并当前事件中出现但未登记的来源；
    - 条目两态视觉：放款前·基线灰显降饱和 / 放款后·变更高亮（ARCH §8.3）；
    - 双击打开详情弹窗（R3）。
    """

    event_filters_changed = Signal(str, str)   # category, source（空串=全部）
    event_double_clicked = Signal(object)      # ★ v2 R3：双击 → 详情弹窗

    def __init__(self, parent=None):
        super().__init__("事件流", parent)
        self._all_events = []
        self._ent_names = {}
        self._verdicts = {}       # event_id -> Verdict
        self._ent_filter = ""
        self._allowed_ids = None  # 企业筛选生效时的允许 enterprise_id 集合（含关联）
        self._related_ids = set()

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["时间", "主体", "类型", "标题", "来源", "严重度"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.resizeSection(0, 130)
        header.resizeSection(1, 140)
        header.resizeSection(2, 80)
        header.resizeSection(4, 90)
        header.resizeSection(5, 60)

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

        legend = QLabel("图例：● 放款后·变更（高亮）　○ 放款前·基线（灰显）", self)
        legend.setProperty("secondary", True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addLayout(top)
        lay.addWidget(self.filter_tag)
        lay.addWidget(self.tree, 1)
        lay.addWidget(legend)

        self.cat_combo.currentIndexChanged.connect(self._emit_filters)
        self.source_combo.currentIndexChanged.connect(self._emit_filters)
        self.clear_btn.clicked.connect(lambda: self.set_enterprise_filter(None))
        self.tree.itemDoubleClicked.connect(self._on_double_click)

    # ---------- 数据 ----------
    def set_events(self, events: list, ent_names: dict, verdicts: dict = None):
        """events: Event 列表；ent_names: id -> 企业名；verdicts: event_id -> Verdict。"""
        self._all_events = list(events or [])
        self._ent_names = dict(ent_names or {})
        self._verdicts = dict(verdicts or {})
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

    def allowed_enterprise_ids(self):
        """当前企业筛选允许的 enterprise_id 集合；未筛选返回 ``None``。"""
        return self._allowed_ids

    def _visible(self):
        out = self._all_events
        if self._ent_filter:
            if self._allowed_ids is not None:
                out = [e for e in out if e.enterprise_id in self._allowed_ids]
            else:
                out = [e for e in out if self._ent_names.get(e.enterprise_id) == self._ent_filter]
        return out

    def _render(self):
        self.tree.clear()
        for e in self._visible():
            name = self._ent_names.get(e.enterprise_id, f"#{e.enterprise_id}")
            if self._ent_filter and e.enterprise_id in self._related_ids:
                name = f"{name} ·关联"
            elif self._ent_filter:
                name = f"{name} ·主"
            item = QTreeWidgetItem([
                e.occurred_at or "—",
                name,
                CATEGORY_LABELS.get(e.category, e.category),
                e.title,
                e.source or "—",
                SEV_LABELS.get(e.severity, e.severity or "—"),
            ])
            item.setData(0, ROLE_ENT_ID, e.enterprise_id)
            item.setData(0, ROLE_EVENT, e)
            item.setToolTip(3, e.summary or e.title)
            verdict = self._verdicts.get(e.id)
            if verdict is not None:
                item.setToolTip(0, verdict.phase_label())
            fg = theme.TEXT_MAIN
            if verdict is not None and verdict.bucket == BUCKET_BASELINE:
                fg = theme.ROW_BASELINE_FG
            elif verdict is not None and verdict.bucket == BUCKET_CHANGE:
                fg = theme.ROW_CHANGE_FG
            for col in range(6):
                item.setForeground(col, QBrush(QColor(fg)))
            item.setForeground(5, QBrush(_sev_color(e.severity)))
            self.tree.addTopLevelItem(item)
        if self.tree.topLevelItemCount() == 0:
            hint = QTreeWidgetItem(["—", "—", "—", "当前筛选无事件，试试放宽筛选条件", "—", "—"])
            hint.setFlags(hint.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(hint)

    # ---------- 过滤 ----------
    def _emit_filters(self, *_):
        self.event_filters_changed.emit(
            self.cat_combo.currentData() or "", self.source_combo.currentData() or "")

    def set_enterprise_filter(self, name, related_ids=None):
        """企业联动筛选：显示该企业事件（可选含其股权穿透关联主体）。

        - ``name`` 为空 → 清除筛选；
        - 传 ``related_ids`` 时一并纳入关联主体事件并以「主/关联」标签区分；
        - 不传 ``related_ids`` 时按企业名精确匹配（兼容地图光斑联动）。
        """
        self._ent_filter = name or ""
        if not self._ent_filter:
            self._allowed_ids = None
            self._related_ids = set()
        elif related_ids:
            ids = {i for i, n in self._ent_names.items() if n == self._ent_filter}
            ids |= set(related_ids)
            self._allowed_ids = ids
            self._related_ids = set(related_ids)
        else:
            self._allowed_ids = None
            self._related_ids = set()
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

    def _on_double_click(self, item, _col):
        event = item.data(0, ROLE_EVENT)
        if event is not None:
            self.event_double_clicked.emit(event)


class AlertPanel(QGroupBox):
    """右下：预警列表，**仅显示放款后变更（bucket == "change"）**的事件。

    高危红底 / 中危橙底；单击行请求联动过滤；双击打开详情弹窗（R3）。
    """

    spot_filter_requested = Signal(str)
    alert_double_clicked = Signal(object)   # ★ v2 R3：双击 → 详情弹窗

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
        self.tree.itemDoubleClicked.connect(self._on_double_click)

    def set_alerts(self, events: list, ent_names: dict, verdicts: dict = None):
        """events 应仅含放款后变更事件；若传入 verdicts 则再次按 ``change`` 兜底过滤。"""
        shown = list(events or [])
        if verdicts:
            shown = [e for e in shown
                     if getattr(verdicts.get(e.id), "bucket", BUCKET_CHANGE) == BUCKET_CHANGE]
        self.setTitle(f"预警列表（放款后变更 {len(shown)}）")
        self.tree.clear()
        for e in shown:
            item = QTreeWidgetItem([
                e.occurred_at or "—",
                ent_names.get(e.enterprise_id, f"#{e.enterprise_id}"),
                CATEGORY_LABELS.get(e.category, e.category),
                e.title,
            ])
            item.setData(0, ROLE_EVENT, e)
            bg = QColor(theme.BG_ALERT_HIGH if e.severity == "high" else theme.BG_ALERT_MEDIUM)
            for col in range(4):
                item.setBackground(col, QBrush(bg))
                item.setForeground(col, QBrush(QColor(theme.TEXT_MAIN)))
            self.tree.addTopLevelItem(item)
        if not shown:
            empty = QTreeWidgetItem(["—", "—", "—", "报告期内无放款后新增风险"])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(empty)

    def _on_click(self, item, _col):
        self.spot_filter_requested.emit(item.text(1))

    def _on_double_click(self, item, _col):
        event = item.data(0, ROLE_EVENT)
        if event is not None:
            self.alert_double_clicked.emit(event)


class AISummaryPanel(QGroupBox):
    """底部：AI 研判摘要（只读，可折叠）。"""

    def __init__(self, parent=None):
        super().__init__("AI 研判摘要", parent)
        self.engine_label = QLabel("", self)
        self.engine_label.setProperty("secondary", True)
        self.toggle_btn = QToolButton(self)
        self.toggle_btn.setText("收起")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.clicked.connect(self._toggle_collapsed)
        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("AI 研判摘要将在此展示；可通过“上传授信材料→AI 影响研判”或事件研判生成。")

        head = QHBoxLayout()
        head.addWidget(self.toggle_btn)
        head.addStretch(1)
        head.addWidget(self.engine_label)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addLayout(head)
        lay.addWidget(self.text)

    def _toggle_collapsed(self, collapsed: bool):
        self.text.setVisible(not collapsed)
        self.toggle_btn.setText("展开" if collapsed else "收起")

    def is_collapsed(self) -> bool:
        return not self.text.isVisible()

    def set_text(self, content: str, engine: str = ""):
        self.text.setPlainText(content)
        self.engine_label.setText(
            {"ai": "引擎：AI 在线", "rule": "引擎：自动化脚本降级"}.get(engine, ""))

"""角色基础信息卡片：当前角色 / 待处理企业 / 待复核 AI 结论 / 采集时间 / 数据源摘要。

设计意图
--------
业务人员（客户经理 / 风险经理）打开系统第一眼要知道的不是"程序启动了"，
而是「**我要处理多少家、还剩多少条要人工复核、上次采到什么时候、数据源好不好使**」。
这些信息原先散落在状态栏、摘要面板与设置里，需要来回切换才能拼出来。

本卡片把这些聚到主窗口顶部，**可折叠**（默认展开），并纳入「显示设置」
的 ``panel_visibility`` 开关（键 ``role_card``），重启后保持。

数据来源（全部为只读派生，不新增表）
----------------------------------
* 当前角色：``app_settings.user_role``（设置中心可改），默认「客户经理」；
* 待处理企业：仍在监控中的企业数；
* 待复核 AI 结论：``event.pending_review = 1`` 的条数（AI 降级需人工复核）；
* 本轮采集时间：``app_settings.last_sync_at``（刷新结束时写入）；
* 数据源摘要：config.yaml 中启用/总源数 + 必备信息域是否有可靠源。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import db, settings
from . import theme

# 角色候选
ROLES = ("客户经理", "风险经理", "信贷审批", "贷后管理")

# app_settings 键
KEY_ROLE = "user_role"
KEY_LAST_SYNC = "last_sync_at"


def _stat_value(text: str, tip: str = "") -> QLabel:
    """构造一个统计值标签（数值醒目 + 说明次要）。"""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {theme.PRIMARY}; font-size: 14px; font-weight: bold;")
    if tip:
        lbl.setToolTip(tip)
    return lbl


class RoleCard(QFrame):
    """角色基础信息卡片（可折叠）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("roleCard")
        self.setStyleSheet(
            f"QFrame#roleCard {{ background: {theme.BG_CARD}; "
            f"border: 1px solid {theme.BORDER}; border-radius: {theme.RADIUS}px; }}")

        self._collapsed = False

        self.role_label = QLabel("客户经理")
        self.role_label.setStyleSheet(
            f"color: {theme.PRIMARY}; font-size: 15px; font-weight: bold;")
        self.role_label.setToolTip("当前登录角色（设置中心可切换）")

        self.toggle_btn = QToolButton(self)
        self.toggle_btn.setText("收起 ▲")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setToolTip("折叠/展开卡片")
        self.toggle_btn.clicked.connect(self._toggle)

        # 标题行
        head = QHBoxLayout()
        head.setSpacing(theme.SPACE)
        title = QLabel("我的工作台")
        title.setProperty("h2", True)
        head.addWidget(title)
        head.addWidget(self.role_label)
        head.addStretch(1)
        head.addWidget(self.toggle_btn)

        # 指标行
        self.ent_label = _stat_value("0", "在监控中的企业数量")
        self.review_label = _stat_value("0", "AI 降级待人工复核的结论条数")
        self.sync_label = _stat_value("—", "最近一次采集完成时间")
        self.source_label = _stat_value("—", "数据源启用情况与必备信息域覆盖")

        self.detail = QWidget(self)
        row = QHBoxLayout(self.detail)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE * 2)
        row.addWidget(self._block("待处理企业", self.ent_label))
        row.addWidget(self._block("待复核 AI 结论", self.review_label))
        row.addWidget(self._block("本轮采集时间", self.sync_label))
        row.addWidget(self._block("数据源可用摘要", self.source_label))
        row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addLayout(head)
        lay.addWidget(self.detail)

    def _block(self, caption: str, value: QLabel) -> QWidget:
        """一个「标题 + 数值」小块。"""
        box = QWidget(self)
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        cap = QLabel(caption)
        cap.setProperty("secondary", True)
        v.addWidget(cap)
        v.addWidget(value)
        return box

    # ---------- 折叠 ----------
    def _toggle(self, collapsed: bool):
        self.set_collapsed(collapsed)

    def set_collapsed(self, collapsed: bool):
        """折叠/展开（收起时只留标题行）。"""
        self._collapsed = bool(collapsed)
        self.detail.setVisible(not self._collapsed)
        self.toggle_btn.setText("展开 ▼" if self._collapsed else "收起 ▲")

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ---------- 数据 ----------
    def refresh(self):
        """重新读取各指标并刷新显示（只读派生，无副作用）。"""
        role = (db.get_setting(KEY_ROLE, "") or "").strip() or ROLES[0]
        self.role_label.setText(role)

        try:
            ents = db.list_enterprises(include_stopped=False)
            self.ent_label.setText(str(len(ents)))
        except Exception:  # noqa: BLE001 - 卡片永不打断主流程
            self.ent_label.setText("—")

        try:
            self.review_label.setText(str(_pending_review_count()))
        except Exception:  # noqa: BLE001
            self.review_label.setText("—")

        last = (db.get_setting(KEY_LAST_SYNC, "") or "").strip()
        self.sync_label.setText(last or "尚未采集")

        try:
            enabled, total, cov = _source_summary()
            self.source_label.setText(f"{enabled}/{total} 启用 · {cov}")
        except Exception:  # noqa: BLE001
            self.source_label.setText("—")


def _pending_review_count() -> int:
    """待人工复核的 AI 结论条数（``event.pending_review = 1``）。"""
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM event WHERE pending_review = 1")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _source_summary() -> tuple:
    """数据源摘要：``(启用数, 总数, 必备信息域覆盖文案)``。"""
    from ..core.coverage import check_required_coverage

    cfg = settings.config()
    sources = cfg.get("sources", {}) or {}
    total = sum(len(v or []) for v in sources.values())
    enabled = sum(1 for v in sources.values() for s in (v or []) if s.get("enabled"))
    problems = check_required_coverage(cfg)
    if problems:
        cov = "必备域缺源：" + "、".join(p["label"] for p in problems)
    else:
        cov = "必备域齐备"
    return enabled, total, cov

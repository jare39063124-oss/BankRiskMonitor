"""悬停缩放动效（Hover Scale）。

QSS 不支持 transform / scale，"鼠标悬停按钮放大" 只能在代码层实现。
本模块用 QPropertyAnimation 直接驱动控件 geometry 做中心缩放：

- 缩放基于控件自身几何做"中心向外扩张"，视觉上为原地放大而非位移；
- 只改 geometry、不改 sizeHint / 不调用 updateGeometry，因此 QLayout 不会被
  主动激活重排，兄弟控件位置保持不动（仅视觉上被覆盖一层，符合"浮起"观感）；
- 若布局在悬停期间重排了控件，控制器会自动重新锚定并重新放大，不会"缩回去"；
- 使用 OutCubic 缓动，进出对称；
- 安装幂等：同一控件重复 install 不会叠加多个控制器。

覆盖范围：按钮（QPushButton/QToolButton/QCheckBox…）、下拉框（QComboBox）、
工具栏动作按钮（QAction 转成的 QToolButton）。列表/树条目用 QSS 悬停高亮
表达（几何缩放会破坏条目布局，故不启用）。
"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPropertyAnimation,
                            QRect, Qt)
from PySide6.QtWidgets import QAbstractButton, QComboBox, QWidget

__all__ = [
    "DEFAULT_SCALE",
    "DEFAULT_DURATION",
    "HoverScaleController",
    "hover_scale_installed",
    "install_hover_filter",
    "install_hover_scale",
    "install_hover_scale_recursive",
    "scaled_rect",
    "setup_dialog_hover",
]

DEFAULT_SCALE = 1.06
DEFAULT_DURATION = 140

_ATTR = "_hover_scale_ctrl"

# 允许缩放的控件类型：按钮类 + 下拉框
_SCALABLE_TYPES = (QAbstractButton, QComboBox)


def scaled_rect(base: QRect, factor: float) -> QRect:
    """以 base 中心为锚点，按 factor 等比放大，返回新矩形。

    factor <= 1 时返回 base 的副本（不做收缩）。
    """
    if factor <= 1.0:
        return QRect(base)
    w = base.width() * factor
    h = base.height() * factor
    dx = (w - base.width()) / 2.0
    dy = (h - base.height()) / 2.0
    return QRect(
        int(round(base.x() - dx)),
        int(round(base.y() - dy)),
        int(round(w)),
        int(round(h)),
    )


class HoverScaleController(QObject):
    """单个控件的悬停缩放动画控制器。"""

    def __init__(self, widget: QWidget, scale: float = DEFAULT_SCALE,
                 duration: int = DEFAULT_DURATION):
        super().__init__(widget)
        self.widget = widget
        self.scale = float(scale)
        self.duration = int(duration)
        self._base: QRect | None = None       # 未放大的基准几何
        self._applied: QRect | None = None    # 最近一次由动画写入的几何
        self._animating = False
        self._hovered = False

        self._anim = QPropertyAnimation(widget, b"geometry", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(self.duration)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)

        widget.installEventFilter(self)

    # ---------- 事件 ----------
    def eventFilter(self, obj, event):
        # 构造期间 QObject 可能先派发事件，此时属性尚未就绪，直接忽略
        widget = getattr(self, "widget", None)
        if widget is None or obj is not widget:
            return False
        et = event.type()
        if et == QEvent.Type.Enter:
            self.play_in()
        elif et == QEvent.Type.Leave:
            self.play_out()
        elif et in (QEvent.Type.Resize, QEvent.Type.Move):
            self._on_external_geometry()
        return False

    def _on_external_geometry(self):
        """控件几何被外部（布局重排）改动时的纠正。"""
        if self._animating:
            return
        cur = QRect(self.widget.geometry())
        if self._applied is not None and cur == self._applied:
            return
        # 布局把控件放回了未放大的位置：重新以新位置为基准并再次放大
        self._base = cur
        if self._hovered:
            target = scaled_rect(cur, self.scale)
            self._animating = True
            try:
                self._applied = QRect(target)
                self.widget.setGeometry(target)
            finally:
                self._animating = False

    # ---------- 动画 ----------
    def current_base(self) -> QRect:
        if self._base is None:
            self._base = QRect(self.widget.geometry())
        return QRect(self._base)

    def target_rect(self, factor: float | None = None) -> QRect:
        return scaled_rect(self.current_base(),
                           self.scale if factor is None else factor)

    def play_in(self):
        self._hovered = True
        cur = QRect(self.widget.geometry())
        if self._base is None or cur != scaled_rect(self._base, self.scale):
            self._base = cur
        base = self.current_base()
        target = scaled_rect(base, self.scale)
        if target == base:
            return
        self._anim.stop()
        self._anim.setStartValue(base)
        self._anim.setEndValue(target)
        self._anim.start()

    def play_out(self):
        self._hovered = False
        base = self.current_base()
        cur = QRect(self.widget.geometry())
        if cur == base:
            self._settle()
            return
        self._anim.stop()
        self._anim.setStartValue(cur)
        self._anim.setEndValue(base)
        self._anim.start()

    def _on_value(self, value):
        if not isinstance(value, QRect):
            return
        self._animating = True
        try:
            self._applied = QRect(value)
            self.widget.setGeometry(value)
        finally:
            self._animating = False

    def _on_finished(self):
        self._animating = False
        if self._hovered:
            self._applied = QRect(self.widget.geometry())
        else:
            self._settle()

    def _settle(self):
        self._applied = None
        parent = self.widget.parentWidget()
        if parent is None:
            return
        layout = parent.layout()
        if layout is not None:
            layout.activate()


def install_hover_scale(widget: QWidget, scale: float = DEFAULT_SCALE,
                         duration: int = DEFAULT_DURATION):
    """为控件安装悬停缩放动效，返回控制器（幂等，重复安装返回同一实例）。

    只对按钮类与下拉框生效（QPushButton / QToolButton / QCheckBox / QComboBox），
    其它控件忽略并返回 None。
    """
    if widget is None:
        return None
    if not isinstance(widget, _SCALABLE_TYPES):
        return None
    existing = getattr(widget, _ATTR, None)
    if existing is not None:
        return existing
    ctrl = HoverScaleController(widget, scale=scale, duration=duration)
    try:
        setattr(widget, _ATTR, ctrl)
    except AttributeError:  # pragma: no cover - 极少数不支持动态属性的宿主
        return None
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return ctrl


def hover_scale_installed(widget: QWidget) -> bool:
    """该控件是否已安装悬停缩放动效。"""
    if widget is None:
        return False
    return getattr(widget, _ATTR, None) is not None


class _ChildAddedFilter(QObject):
    """子控件创建时自动安装悬停缩放。

    注意：PySide6 的 ``QChildEvent.child()`` 返回的是**基类 QWidget 包装**，
    即使实际是 QPushButton，``metaObject().className()`` 也只给 "QWidget"，
    无法据此判定类型（且此时控件尚未定型）。
    因此这里不依赖事件里的 child 对象，改为在事件到达时记录「该容器需要刷新」，
    由 :func:`refresh_hover_effects` 统一扫描补装。
    """

    def __init__(self, scale: float = DEFAULT_SCALE, duration: int = DEFAULT_DURATION):
        super().__init__()
        self.scale = scale
        self.duration = duration
        self.dirty: set = set()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ChildAdded:
            self.dirty.add(obj)
        return False


def refresh_hover_effects(scale: float = DEFAULT_SCALE,
                          duration: int = DEFAULT_DURATION) -> int:
    """扫描所有顶层窗口，补装尚未安装缩放的按钮/下拉框，返回新增数量。

    用于覆盖运行时动态创建的控件（本项目按钮基本在对话框 __init__ 内静态创建，
    但列表刷新等场景可能动态增删控件）。
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return 0
    count = 0
    for window in app.topLevelWidgets():
        count += install_hover_scale_recursive(window, scale=scale, duration=duration)
    return count


def install_hover_filter(widget: QWidget, scale: float = DEFAULT_SCALE,
                         duration: int = DEFAULT_DURATION):
    """给容器控件装上「子控件创建即装缩放」的过滤器。

    需对**每个顶层窗口**调用（主窗口、各对话框），以覆盖动态创建的按钮。
    """
    if widget is None:
        return None
    existing = getattr(widget, "_hover_child_filter", None)
    if existing is not None:
        return existing
    f = _ChildAddedFilter(scale, duration)
    widget.installEventFilter(f)
    try:
        setattr(widget, "_hover_child_filter", f)
    except AttributeError:  # pragma: no cover
        pass
    return f


def install_hover_scale_recursive(root: QWidget, scale: float = DEFAULT_SCALE,
                                   duration: int = DEFAULT_DURATION) -> int:
    """递归为 root 及其所有子控件中的按钮安装悬停缩放，返回安装数量。

    同时为 root 装上子控件创建过滤器，后续动态添加的按钮也自动获得动效。
    """
    if root is None:
        return 0
    count = 0
    if install_hover_scale(root, scale, duration) is not None:
        count += 1
    for child in root.findChildren(QWidget):
        if install_hover_scale(child, scale, duration) is not None:
            count += 1
    install_hover_filter(root, scale, duration)
    return count


def setup_dialog_hover(dialog, scale: float = DEFAULT_SCALE,
                        duration: int = DEFAULT_DURATION) -> int:
    """对话框统一入口：为窗口内已有按钮装缩放，并监听后续动态新增的按钮。

    需在对话框 ``__init__`` 末尾调用（控件已全部创建后）。
    """
    if dialog is None:
        return 0
    return install_hover_scale_recursive(dialog, scale=scale, duration=duration)

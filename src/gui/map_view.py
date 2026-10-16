"""MapView：纯 Qt（QPainter 自绘）中国地图，展示监控主体风险分布与四级下钻。

设计要点（ARCH-v2 §1.2 决策 A / §3.1）：

- **无浏览器内核**：完全移除 Qt 浏览器内核相关依赖，改用
  :mod:`src.gui.map_engine` 的投影 + ``QPainter`` 自绘。
- **几何只投影一次**：``RegionFeature.path`` 常驻世界坐标；平移/缩放只改
  ``QTransform`` 视图矩阵，不重建路径。
- **命中**：``Projection.hit_test`` 把像素逆变换回世界坐标后用
  ``QPainterPath.contains`` 判定（与渲染共用同一几何）。
- **对外契约不变**：``set_data(spots, mode=None)`` 与 ``spot_clicked = Signal(str)``
  保持与 v1 一致；``self._loaded=True`` / ``self._pending=None`` 为测试兼容保留。
- **下钻状态机**：全国 → 省 → 市 → 区县；区县文件不存在则不下钻并显示降级文案。
"""
from __future__ import annotations

import math
from typing import List, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import geo, settings
from . import theme
from .map_engine import (
    NO_CHILD_TEXT,
    GeoDataLoader,
    Projection,
    RegionFeature,
    union_bbox,
)

# ---------------- 配色（ARCH-v2 §7.2，与 v1 地图配色语义一致） ----------------
MAP_BG = "#0D1420"
NEON = theme.PRIMARY  # #00FF9C
SEV_COLORS = {"high": "#FF4D4D", "medium": "#FFA033", "low": "#FFD84D", "none": "#00FF9C"}
SEV_LABELS = {"high": "高危", "medium": "中危", "low": "关注", "none": "平稳"}
CAT_COLORS = {
    "enforcement": "#FF4D4D",
    "lawsuit": "#FFD84D",
    "biz_change": "#FFA033",
    "news": "#4DA3FF",
    "social": "#C77DFF",
}
CAT_ORDER = ["enforcement", "lawsuit", "biz_change", "news", "social"]
CAT_LABELS = {
    "news": "舆情新闻",
    "biz_change": "工商变更",
    "lawsuit": "涉诉",
    "enforcement": "被执行/查封",
    "social": "社媒",
}

PADDING = 28          # 地图留白（像素）
ANIM_INTERVAL_MS = 40  # 涟漪动画间隔（约 25fps）
PAN_THRESHOLD = 4      # 拖拽判定阈值（像素）

FRAME_NATIONAL = "national"
FRAME_PROVINCE = "province"
FRAME_DISTRICT = "district"


class MapView(QWidget):
    """中央地图面板（纯 Qt 自绘）。

    信号
    ----
    - ``spot_clicked(str)``：点击光点回传企业名（``main_window._on_spot_clicked`` 消费）。
    - ``region_clicked(object)``：点击区域下钻成功回传 ``RegionFeature``（供 T04 扩展）。
    - ``level_changed(str)``：层级变化回传 ``"national" | "province" | "district"``。
    """

    spot_clicked = Signal(str)
    region_clicked = Signal(object)
    level_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # —— 测试兼容：构造即视为就绪，无待推送数据 ——
        self._loaded = True
        self._pending = None

        # —— 状态 ——
        self._loader = GeoDataLoader()
        self.proj = Projection()
        self._features: List[RegionFeature] = []
        self._spots: List[dict] = []
        self._mode: str = "severity"
        self._hover: Optional[RegionFeature] = None
        self._bbox = union_bbox([])
        self._stack: List[dict] = [{"level": FRAME_NATIONAL, "adcode": None, "name": "全国"}]

        # 交互临时态
        self._press_pos: Optional[QPoint] = None
        self._last_pos: Optional[QPoint] = None
        self._moved: bool = False
        self._pressed_spot: Optional[dict] = None

        self._ripple_phase: float = 0.0
        self._detail_spot: Optional[dict] = None

        self.setMouseTracking(True)
        self.setMinimumSize(240, 180)
        self.setAutoFillBackground(False)

        self._build_overlays()

        # 涟漪动画
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_anim)
        self._timer.start(ANIM_INTERVAL_MS)

        # 初始化：加载全国省级边界
        self._reset_to_national()

    # ================= 覆盖控件（面包屑 / 返回 / 降级文案 / 详情卡） =================
    def _build_overlays(self):
        self._crumb = QLabel("全国", self)
        self._crumb.setStyleSheet(
            f"color: {theme.TEXT_SECOND}; background: transparent; "
            "font-size: 13px; letter-spacing: 1px;")

        self._back = QToolButton(self)
        self._back.setText("⌫  返回")
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.setStyleSheet(
            f"QToolButton {{ background: {theme.PRIMARY_BG}; color: {NEON}; "
            f"border: 1px solid {NEON}; border-radius: 4px; padding: 2px 12px; }}"
            f"QToolButton:hover {{ background: {theme.BG_HOVER}; }}"
            f"QToolButton:pressed {{ background: {NEON}; color: {theme.BG_WINDOW}; }}")
        self._back.clicked.connect(self.go_back)
        self._back.hide()

        self._msg = QLabel("", self)
        self._msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg.setStyleSheet(f"color: {theme.TEXT_SECOND}; background: transparent;")
        self._msg.hide()

        self._card = self._build_card()

        self._layout_overlays()

    def _build_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("mapDetailCard")
        card.setStyleSheet(
            f"QFrame#mapDetailCard {{ background: rgba(13,19,28,0.97); "
            f"border: 1px solid {NEON}; border-radius: 6px; }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._card_title = QLabel("", card)
        self._card_title.setStyleSheet(
            f"color: {NEON}; font-size: 13px; font-weight: bold; background: transparent;")
        close = QToolButton(card)
        close.setText("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(
            f"QToolButton {{ border: none; background: transparent; color: {theme.TEXT_SECOND}; }}"
            f"QToolButton:hover {{ color: {SEV_COLORS['high']}; }}")
        close.clicked.connect(self._hide_detail)
        head.addWidget(self._card_title, 1)
        head.addWidget(close, 0)
        lay.addLayout(head)

        self._card_body = QTextBrowser(card)
        self._card_body.setOpenExternalLinks(False)
        self._card_body.setFrameShape(QFrame.Shape.NoFrame)
        self._card_body.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {theme.TEXT_MAIN}; "
            "border: none; font-size: 12px; }}")
        lay.addWidget(self._card_body, 1)

        card.hide()
        return card

    def _layout_overlays(self):
        w, h = max(1, self.width()), max(1, self.height())
        self._crumb.setGeometry(14, 8, max(40, w - 170), 22)
        bw = max(72, self._back.sizeHint().width())
        self._back.setGeometry(max(0, w - bw - 14), 8, bw, 26)
        self._msg.setGeometry(0, h // 2 - 18, w, 36)
        cw = min(360, max(220, w - 40))
        ch = min(460, max(180, int(h * 0.62)))
        self._card.setGeometry(max(0, w - cw - 16), 44, cw, ch)

    # ================= 数据接入（对外契约） =================
    def set_data(self, spots: list, mode: str = None):
        """渲染光点。``spots`` 元素结构见 ARCH-v2 §3.1（``lng/lat`` 可能为 None）。"""
        self._spots = list(spots or [])
        if mode is None:
            mode = settings.config().get("ui", {}).get("map_mode", "severity")
        if mode not in ("severity", "category"):
            mode = "severity"
        self._mode = mode
        self.update()

    # ================= 层级切换 =================
    def _reset_to_national(self):
        self._stack = [{"level": FRAME_NATIONAL, "adcode": None, "name": "全国"}]
        self._apply_features(self._loader.load_national())

    def _apply_features(self, features: List[RegionFeature]):
        self._features = list(features or [])
        self._hover = None
        self._hidden_card()
        if self._features:
            self._bbox = union_bbox(self._features)
        else:
            self._bbox = union_bbox([])

        self.proj.fit(self._bbox, self._canvas_size(), PADDING)
        self.proj.reset()
        k = self.proj.k
        for feat in self._features:
            feat.build_path(k)

        if not self._features:
            self._show_message(NO_CHILD_TEXT)
        else:
            self._msg.hide()

        self._sync_chrome()
        self.level_changed.emit(self._stack[-1]["level"])
        self.update()

    def _sync_chrome(self):
        names = [f["name"] for f in self._stack]
        self._crumb.setText("  ›  ".join(names))
        self._back.setVisible(len(self._stack) > 1)
        self._layout_overlays()

    def _show_message(self, text: str):
        self._msg.setText(text)
        self._msg.show()
        self._msg.raise_()

    def _drill_feature(self, feat: RegionFeature) -> bool:
        """按当前层级尝试下钻到 ``feat`` 的下级；成功返回 True。"""
        cur = self._stack[-1]
        if cur["level"] == FRAME_NATIONAL:
            children = self._loader.load_province(feat.adcode)
        elif cur["level"] == FRAME_PROVINCE:
            if not self._loader.has_children(feat.adcode):
                self._show_message(NO_CHILD_TEXT)
                return False
            children = self._loader.load_district(feat.adcode)
        else:
            return False  # 区县不再下钻

        if not children:
            self._show_message(NO_CHILD_TEXT)
            return False

        next_level = FRAME_PROVINCE if cur["level"] == FRAME_NATIONAL else FRAME_DISTRICT
        self._stack.append({"level": next_level, "adcode": feat.adcode, "name": feat.name})
        self._apply_features(children)
        self.region_clicked.emit(feat)
        return True

    def drill(self, feature: RegionFeature) -> bool:
        """对外下钻入口（供 T04 扩展与脚本使用）。"""
        return self._drill_feature(feature)

    def go_back(self):
        """逐级回退。"""
        if len(self._stack) <= 1:
            return
        self._stack.pop()
        frame = self._stack[-1]
        if frame["level"] == FRAME_NATIONAL:
            feats = self._loader.load_national()
        elif frame["level"] == FRAME_PROVINCE:
            feats = self._loader.load_province(frame["adcode"])
        else:
            feats = self._loader.load_district(frame["adcode"])
        self._apply_features(feats)

    def reset(self):
        """回到全国视图。"""
        self._reset_to_national()

    def drill_to(self, name: str) -> bool:
        """按名称在当前视图内下钻（name 支持模糊包含），供截图/演示工具使用。"""
        if not name:
            return False
        for feat in self._features:
            if feat.name == name or name in feat.name:
                return self._drill_feature(feat)
        return False

    def open_first_spot(self) -> bool:
        """打开当前视图内首个可见光点的详情卡（供截图/演示工具使用）。"""
        visible = self._visible_spots()
        if not visible:
            return False
        self._show_detail(visible[0])
        return True

    # ================= 光点计算 =================
    def _visible_spots(self) -> List[dict]:
        """按当前层级过滤光点（v1 行为：省级只渲染本省，区县级只渲染本市）。"""
        frame = self._stack[-1]
        level = frame["level"]
        out: List[dict] = []
        for sp in self._spots:
            if level == FRAME_PROVINCE:
                if geo.norm_prov(sp.get("province")) != geo.norm_prov(frame["name"]):
                    continue
            elif level == FRAME_DISTRICT:
                if geo.norm_city(sp.get("city")) != geo.norm_city(frame["name"]):
                    continue
            out.append(sp)
        return out

    def _spot_lon_lat(self, sp: dict):
        lng, lat = sp.get("lng"), sp.get("lat")
        if lng is None or lat is None:
            center = geo.city_center(sp.get("province") or "", sp.get("city") or "")
            if center:
                return center
            return (104.0, 35.0)  # v1 回退：中国中心
        return (lng, lat)

    def _spot_px(self, sp: dict, used: dict) -> QPointF:
        """计算光点像素位置，并按同城确定性散列避免重叠（v1 行为）。"""
        lon, lat = self._spot_lon_lat(sp)
        key = "{0:.1f},{1:.1f}".format(lon, lat)
        n = used.get(key, 0)
        used[key] = n + 1
        k = n % 7
        ring = n // 7 + 1
        ang = (k / 7.0) * math.pi * 2
        dlon = math.cos(ang) * 0.08 * ring
        dlat = math.sin(ang) * 0.06 * ring
        return self.proj.world_to_px(lon + dlon, lat + dlat)

    def _spot_radius(self, sp: dict) -> float:
        count = int(sp.get("event_count") or 0)
        return 5.0 + min(12.0, math.sqrt(max(0, count)) * 2.2)

    def _spot_color(self, sp: dict) -> str:
        if self._mode == "category":
            cats = sp.get("categories") or []
            for key in CAT_ORDER:
                if key in cats:
                    return CAT_COLORS[key]
            return CAT_COLORS["news"]
        return SEV_COLORS.get(sp.get("severity") or "none", SEV_COLORS["none"])

    def _spot_hit(self, px: QPointF) -> Optional[dict]:
        used: dict = {}
        best: Optional[dict] = None
        best_d = float("inf")
        for sp in self._visible_spots():
            pt = self._spot_px(sp, used)
            d = math.hypot(pt.x() - px.x(), pt.y() - px.y())
            r = max(self._spot_radius(sp) + 4.0, 9.0)
            if d <= r and d < best_d:
                best_d = d
                best = sp
        return best

    def _canvas_size(self) -> QSize:
        return QSize(max(1, self.width()), max(1, self.height()))

    # ================= 详情卡 =================
    def _on_spot_clicked(self, sp: dict):
        name = sp.get("name") or ""
        self.spot_clicked.emit(name)
        self._show_detail(sp)

    def _show_detail(self, sp: dict):
        self._detail_spot = sp
        self._card_title.setText(sp.get("name") or "")
        self._card_body.setHtml(self._detail_html(sp))
        self._layout_overlays()
        self._card.show()
        self._card.raise_()

    def _hide_detail(self):
        self._hidden_card()

    def _hidden_card(self):
        self._detail_spot = None
        if hasattr(self, "_card"):
            self._card.hide()

    def _detail_html(self, sp: dict) -> str:
        sev = sp.get("severity") or "none"
        cats = sp.get("categories") or []
        cat_txt = "、".join(CAT_LABELS.get(c, c) for c in cats) or "暂无"
        loc = " / ".join(x for x in [sp.get("province") or "", sp.get("city") or ""] if x) or "位置未知"
        parts = [
            f"<div style='color:{theme.TEXT_SECOND};margin-bottom:6px;'>"
            f"位置：<span style='color:{NEON};'>{_esc(loc)}</span>　"
            f"事件：<span style='color:{NEON};'>{int(sp.get('event_count') or 0)}</span> 条　"
            f"风险：<span style='color:{SEV_COLORS.get(sev, NEON)};'>{SEV_LABELS.get(sev, '平稳')}</span><br>"
            f"信息域：{_esc(cat_txt)}</div>"
        ]
        events = sp.get("events") or []
        if not events:
            parts.append(f"<div style='color:{theme.TEXT_SECOND};'>暂无舆情事件记录</div>")
        for ev in events[:8]:
            esev = ev.get("severity") or "low"
            color = SEV_COLORS.get(esev, theme.TEXT_SECOND)
            title = _esc(ev.get("title"))
            meta = " · ".join(
                x for x in [
                    CAT_LABELS.get(ev.get("category"), ev.get("category") or ""),
                    ev.get("source") or "",
                    ev.get("occurred_at") or "",
                ] if x
            )
            block = [
                f"<div style='border-left:3px solid {color};padding:4px 6px;margin-bottom:6px;'>"
                f"<span style='color:{theme.TEXT_MAIN};font-weight:bold;'>{title}</span>"
                f"<span style='color:{color};'>　[{SEV_LABELS.get(esev, esev)}]</span><br>"
                f"<span style='color:{theme.TEXT_SECOND};font-size:11px;'>{_esc(meta)}</span>"
            ]
            summary = ev.get("summary")
            if summary:
                block.append(
                    f"<br><span style='color:#AAB6C4;'>{_esc(summary)}</span>")
            verdict = ev.get("ai_verdict")
            if verdict:
                block.append(
                    f"<br><span style='color:{NEON};'>AI研判：{_esc(verdict)}</span>")
            block.append("</div>")
            parts.append("".join(block))
        return "".join(parts)

    # ================= 绘制 =================
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(MAP_BG))

        self._draw_features(painter)
        self._draw_spots(painter)
        self._draw_legend(painter)

        painter.end()

    def _draw_features(self, painter: QPainter):
        if not self._features:
            return
        painter.save()
        painter.setTransform(self.proj.view_transform())
        outer = QColor(NEON)
        outer.setAlphaF(0.16)
        mid = QColor(NEON)
        mid.setAlphaF(0.40)
        for feat in self._features:
            hovered = feat is self._hover
            painter.setBrush(QColor("#122B1F") if hovered else QColor(MAP_BG))
            # 多遍描边模拟辉光：宽半透明外层 + 中宽 + 细亮内层
            for width, color in ((7.0, outer), (3.2, mid), (1.4, QColor(NEON))):
                pen = QPen(color)
                pen.setWidthF(width)
                pen.setCosmetic(True)  # 线宽固定为设备像素，不随缩放变化
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(feat.path)
        painter.restore()

    def _draw_spots(self, painter: QPainter):
        visible = self._visible_spots()
        if not visible:
            return
        used: dict = {}
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for sp in visible:
            pt = self._spot_px(sp, used)
            r = self._spot_radius(sp)
            color = QColor(self._spot_color(sp))

            # 涟漪：同心圆，透明度随相位衰减
            for k in range(2):
                phase = (self._ripple_phase + k * 0.5) % 1.0
                rr = r * (1.0 + phase * 2.2)
                ring = QColor(color)
                ring.setAlphaF(max(0.0, 0.5 * (1.0 - phase)))
                pen = QPen(ring)
                pen.setWidthF(1.6)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(pt, rr, rr)

            # 光点本体：径向渐变
            grad = QRadialGradient(pt, max(1.0, r))
            core = QColor(color)
            core.setAlphaF(1.0)
            edge = QColor(color)
            edge.setAlphaF(0.12)
            grad.setColorAt(0.0, core)
            grad.setColorAt(0.62, core)
            grad.setColorAt(1.0, edge)
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(pt, r, r)
        painter.restore()

    def _draw_legend(self, painter: QPainter):
        if self._mode == "category":
            items = [(CAT_COLORS[k], CAT_LABELS[k]) for k in CAT_ORDER]
        else:
            items = [(SEV_COLORS[k], SEV_LABELS[k]) for k in ("high", "medium", "low", "none")]

        painter.save()
        font = QFont(painter.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
        painter.setFont(font)
        fm = painter.fontMetrics()
        line_h = fm.height() + 4
        box_w = max(fm.horizontalAdvance(t) for _, t in items) + 34
        box_h = line_h * len(items) + 12
        x = self.width() - box_w - 14
        y = self.height() - box_h - 14
        if x < 8 or y < 8:
            painter.restore()
            return

        painter.setPen(QPen(QColor("#2A3441")))
        painter.setBrush(QColor(13, 19, 28, 226))
        painter.drawRoundedRect(QRectF(x, y, box_w, box_h), 4, 4)

        ty = y + 6
        flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        for color, label in items:
            cy = ty + fm.height() / 2.0
            glow = QColor(color)
            glow.setAlphaF(0.35)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(x + 14, cy), 6, 6)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x + 14, cy), 3.6, 3.6)
            painter.setPen(QColor(theme.TEXT_MAIN))
            painter.drawText(QRectF(x + 26, ty, box_w - 30, fm.height()), flags, label)
            ty += line_h
        painter.restore()

    # ================= 动画 =================
    def _on_anim(self):
        if not self._spots or not self.isVisible():
            return
        self._ripple_phase = (self._ripple_phase + 0.06) % 1.0
        self.update()

    # ================= 事件 =================
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 重算拟合基线（k 不变，路径无需重建）
        self.proj.fit(self._bbox, self._canvas_size(), PADDING)
        self._layout_overlays()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            self._press_pos = pos
            self._last_pos = pos
            self._moved = False
            self._pressed_spot = self._spot_hit(event.position())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._press_pos is not None:
            pos = event.position().toPoint()
            if not self._moved:
                total = pos - self._press_pos
                if abs(total.x()) + abs(total.y()) > PAN_THRESHOLD:
                    self._moved = True
            if self._moved:
                delta = pos - self._last_pos
                self.proj.pan(delta.x(), delta.y())
                self._last_pos = pos
                self._hidden_card()
                self.update()
        else:
            feat = self.proj.hit_test(self._features, event.position())
            if feat is not self._hover:
                self._hover = feat
                self.setCursor(Qt.CursorShape.PointingHandCursor if feat
                               else Qt.CursorShape.ArrowCursor)
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._moved:
                if self._pressed_spot is not None:
                    self._on_spot_clicked(self._pressed_spot)
                else:
                    feat = self.proj.hit_test(self._features, event.position())
                    if feat is not None:
                        self._drill_feature(feat)
            self._press_pos = None
            self._last_pos = None
            self._pressed_spot = None
            self._moved = False
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 ** (delta / 120.0)
        self.proj.zoom_at(QPointF(event.position()), factor)
        self.update()
        event.accept()

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)


def _esc(text) -> str:
    """最小化 HTML 转义，避免详情卡富文本被内容破坏。"""
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

"""工具栏动作图标工厂：用 QPainter 现场绘制轻量图标（不依赖外部图片资源）。

为什么要自绘
------------
项目里没有图标资源文件，引入 .png/.svg 会增加打包体积与路径兼容问题。
这里直接用 QPixmap + QPainter 画极简几何符号（圆/方/三角/箭头/菱形…），
按主题色着色，**在任意分辨率与缩放下都清晰**，且随主题色变化自动适配。

用法::

    act.setIcon(make_icon("refresh", theme.PRIMARY))
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap

# 图标名 -> 绘制函数
_SHAPES = {
    "plus": "circle",
    "import": "arrow_down",
    "refresh": "cycle",
    "report": "doc",
    "penetrate": "tree",
    "material": "doc",
    "ai": "spark",
    "social": "chat",
    "health": "pulse",
    "settings": "gear",
    "filter": "funnel",
    "warning": "triangle",
}


def _draw(painter: QPainter, shape: str, color: QColor):
    pen = QPen(color)
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    r = QRectF(3, 3, 18, 18)
    if shape == "circle":
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(r)
    elif shape == "arrow_down":
        painter.drawLine(12, 4, 12, 15)
        painter.drawLine(7, 11, 12, 16)
        painter.drawLine(17, 11, 12, 16)
        painter.drawLine(6, 20, 18, 20)
    elif shape == "cycle":
        painter.drawArc(r.adjusted(2, 2, -2, -2), 40 * 16, 280 * 16)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(_tri_poly(18.0, 5.0, 20.0, 12.0, 13.0, 8.0))
    elif shape == "doc":
        painter.drawRoundedRect(QRectF(6, 3, 12, 18), 2, 2)
        for i, y in enumerate((8, 12, 16)):
            painter.drawLine(9, y, 15 - i, y)
    elif shape == "tree":
        painter.drawLine(12, 5, 12, 10)
        painter.drawLine(6, 19, 6, 12)
        painter.drawLine(18, 19, 18, 12)
        painter.drawLine(6, 12, 18, 12)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(9, 2, 6, 6))
        painter.drawEllipse(QRectF(3, 17, 6, 6))
        painter.drawEllipse(QRectF(15, 17, 6, 6))
    elif shape == "spark":
        painter.drawLine(12, 3, 12, 21)
        painter.drawLine(4, 12, 20, 12)
        painter.drawLine(6.5, 6.5, 17.5, 17.5)
        painter.drawLine(17.5, 6.5, 6.5, 17.5)
    elif shape == "chat":
        painter.drawRoundedRect(QRectF(3, 5, 18, 13), 3, 3)
        painter.drawLine(8, 18, 8, 21)
        painter.drawLine(8, 18, 13, 18)
    elif shape == "pulse":
        painter.drawPolyline(*_pulse_points())
    elif shape == "gear":
        painter.drawEllipse(QRectF(8, 8, 8, 8))
        for i in range(8):
            import math
            a = math.radians(i * 45)
            x1, y1 = 12 + 5.5 * math.cos(a), 12 + 5.5 * math.sin(a)
            x2, y2 = 12 + 8.5 * math.cos(a), 12 + 8.5 * math.sin(a)
            painter.drawLine(x1, y1, x2, y2)
    elif shape == "funnel":
        painter.drawLine(4, 5, 20, 5)
        painter.drawLine(4, 5, 10, 12)
        painter.drawLine(10, 12, 10, 20)
        painter.drawLine(10, 20, 14, 17)
        painter.drawLine(14, 17, 14, 12)
        painter.drawLine(14, 12, 20, 5)
    elif shape == "triangle":
        painter.drawLine(12, 4, 21, 19)
        painter.drawLine(21, 19, 3, 19)
        painter.drawLine(3, 19, 12, 4)
        painter.drawLine(12, 10, 12, 14)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(11, 16, 2, 2))
    painter.end()


def _arrow_poly(x, y, dx, dy):
    from PySide6.QtGui import QPolygonF
    from PySide6.QtCore import QPointF
    return QPolygonF((QPointF(x, y), QPointF(x + dx, y), QPointF(x, y + dy)))


def _tri_poly(x1, y1, x2, y2, x3, y3):
    """三个绝对坐标点构成的三角形。"""
    from PySide6.QtGui import QPolygonF
    from PySide6.QtCore import QPointF
    return QPolygonF((QPointF(x1, y1), QPointF(x2, y2), QPointF(x3, y3)))


def _pulse_points():
    from PySide6.QtCore import QPointF
    return (QPointF(3, 12), QPointF(8, 12), QPointF(10, 5), QPointF(13, 19),
            QPointF(15, 12), QPointF(21, 12))


def make_icon(name: str, color: str = "#00FF9C", size: int = 24) -> QIcon:
    """按名称生成图标。未知名称回退为实心圆点。

    **不做全局缓存**：``QPixmap`` 是绑定 GUI 线程的资源，若被模块级字典长期持有，
    会在 ``QApplication`` 销毁后随 GC 析构而 abort（``Fatal Python error: Aborted``）。
    图标只在工具栏构建时生成一次，绘制成本可忽略。
    """
    shape = _SHAPES.get(name, "circle")
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _draw(painter, shape, QColor(color))
    return QIcon(pix)

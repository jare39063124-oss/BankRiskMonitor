"""纯几何地图引擎：投影、命中检测与 GeoJSON 加载。

本模块**不依赖任何 QWidget**，可在无 GUI 环境下单元测试；`MapView` 仅消费其能力。

关键坐标系约定（ARCH-v2 §7.3，冻结）：

- **世界坐标**：``world = (lon * k, -lat)``，其中 ``k = cos(radians(mid_lat))``
  为当前层级包围盒的纬度余弦压缩系数（y 轴翻转，使北在上）。
- `RegionFeature.path` 建在世界坐标；`Projection.view_transform()` 提供
  世界坐标 → 画布像素的视图矩阵（平移/缩放只改矩阵，不重建路径）。
- `Projection.px_to_world()` 返回**世界坐标对应经纬度**；`RegionFeature.contains()`
  吃**世界坐标**——两者坐标系必须一致，是命中正确性的关键。
"""
from __future__ import annotations

import json
import math
import os
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QPainterPath, QTransform

from ..core.paths import asset_path

# 中国大致范围（全国无数据时的投影回退包围盒，min_lon/min_lat/max_lon/max_lat）
DEFAULT_BBOX: Tuple[float, float, float, float] = (73.0, 3.0, 135.0, 54.0)

# 无下级边界数据时的降级文案（ARCH-v2 §7.6）
NO_CHILD_TEXT = "该区域无下级边界数据"

# 层级常量
LEVEL_PROVINCE = "province"
LEVEL_CITY = "city"
LEVEL_DISTRICT = "district"


def _guess_level(adcode: int) -> str:
    """按 GB/T 2260 编码规则推断层级（省 2 位后有 4 个 0；市 2 位后有 2 个 0）。"""
    a = int(adcode or 0)
    if a <= 0:
        return LEVEL_PROVINCE
    if a % 10000 == 0:
        return LEVEL_PROVINCE
    if a % 100 == 0:
        return LEVEL_CITY
    return LEVEL_DISTRICT


def _flatten_rings(gtype: Optional[str], coords) -> List[List[Tuple[float, float]]]:
    """把 GeoJSON Polygon / MultiPolygon 的坐标展平为环列表（每环为 (lon,lat) 列表）。"""
    rings: List[List[Tuple[float, float]]] = []
    try:
        if gtype == "Polygon":
            for ring in (coords or []):
                rings.append([(float(p[0]), float(p[1])) for p in ring if len(p) >= 2])
        elif gtype == "MultiPolygon":
            for poly in (coords or []):
                for ring in (poly or []):
                    rings.append([(float(p[0]), float(p[1])) for p in ring if len(p) >= 2])
    except (TypeError, ValueError, IndexError):
        return []
    return [r for r in rings if len(r) >= 3]


def _rings_bbox(rings: List[List[Tuple[float, float]]]) -> Tuple[float, float, float, float]:
    """计算环集合的地理包围盒 (min_lon, min_lat, max_lon, max_lat)。"""
    lons = [p[0] for r in rings for p in r]
    lats = [p[1] for r in rings for p in r]
    if not lons or not lats:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(lons), min(lats), max(lons), max(lats))


def _feature_center(props: dict, bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """优先取 properties.center / centroid，缺失时回退包围盒中心。"""
    c = props.get("center") or props.get("centroid")
    if c and len(c) >= 2:
        try:
            return (float(c[0]), float(c[1]))
        except (TypeError, ValueError):
            pass
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


class RegionFeature:
    """一个行政区划几何（省 / 市 / 区县）。

    属性
    ----
    - ``name``：名称（如“浙江省”）。
    - ``adcode``：行政区划编码（int）。
    - ``level``：``"province" | "city" | "district"``。
    - ``path``：世界坐标 :class:`QPainterPath`（由 :meth:`build_path` 填充）。
    - ``bbox``：地理包围盒 ``(min_lon, min_lat, max_lon, max_lat)``。
    - ``center``：中心点 ``(lon, lat)``。
    - ``rings``：原始经纬度环列表（投影前保留，便于重建）。
    - ``ring_points_hint``：各环起点的世界坐标（调试/命中提示用）。
    """

    def __init__(self, name: str, adcode: int, level: str,
                 rings: List[List[Tuple[float, float]]],
                 bbox: Tuple[float, float, float, float],
                 center: Tuple[float, float]):
        self.name: str = str(name or "")
        self.adcode: int = int(adcode or 0)
        self.level: str = str(level or "")
        self.rings: List[List[Tuple[float, float]]] = rings
        self.bbox: Tuple[float, float, float, float] = bbox
        self.center: Tuple[float, float] = center
        self.path: QPainterPath = QPainterPath()
        self.ring_points_hint: List[Tuple[float, float]] = []

    def build_path(self, k: float) -> QPainterPath:
        """按纬度压缩系数 ``k`` 把经纬度环投影为世界坐标 QPainterPath。

        ``world = (lon * k, -lat)``。几何**只投影一次**并常驻，
        平移/缩放仅叠加视图矩阵，不重建路径。
        """
        kk = float(k) if k else 1.0
        path = QPainterPath()
        hints: List[Tuple[float, float]] = []
        for ring in self.rings:
            pts = [p for p in ring if p and len(p) >= 2]
            if len(pts) < 3:
                continue
            lon0, lat0 = float(pts[0][0]), float(pts[0][1])
            path.moveTo(lon0 * kk, -lat0)
            for lon, lat in pts[1:]:
                path.lineTo(float(lon) * kk, -float(lat))
            path.closeSubpath()
            hints.append((lon0 * kk, -lat0))
        self.path = path
        self.ring_points_hint = hints
        return path

    def contains(self, world_pt: QPointF) -> bool:
        """命中判定（入参为**世界坐标**）。

        先用 ``path`` 的世界包围盒预筛，再用 :meth:`QPainterPath.contains` 精确判定。
        """
        if not isinstance(world_pt, QPointF):
            return False
        if self.path.isEmpty():
            return False
        if not self.path.boundingRect().contains(world_pt):
            return False
        return self.path.contains(world_pt)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"RegionFeature(name={self.name!r}, adcode={self.adcode}, level={self.level!r})"


class Projection:
    """每级自适应 + 纬度余弦压缩的等距圆柱（equirectangular）投影。

    世界坐标 ``(lon*k, -lat)``；视图矩阵把世界坐标映射到画布像素：

    ``px = (s*world_x + base_dx + dx, s*world_y + base_dy + dy)``，其中
    ``s = s0 * zoom``，``base_dx = tx0 - min_lon*k*s``，``base_dy = ty0 + max_lat*s``。
    """

    def __init__(self) -> None:
        self.bbox: Tuple[float, float, float, float] = DEFAULT_BBOX
        self.min_lon: float = DEFAULT_BBOX[0]
        self.min_lat: float = DEFAULT_BBOX[1]
        self.max_lon: float = DEFAULT_BBOX[2]
        self.max_lat: float = DEFAULT_BBOX[3]
        self.k: float = 1.0
        self.s0: float = 1.0
        self.tx0: float = 0.0
        self.ty0: float = 0.0
        self.zoom: float = 1.0
        self.dx: float = 0.0
        self.dy: float = 0.0
        self.canvas: QSize = QSize(1, 1)
        self.padding: int = 24
        self.min_zoom: float = 0.2
        self.max_zoom: float = 24.0

    # ---------------- 拟合 ----------------
    def fit(self, bbox: Tuple[float, float, float, float], size: QSize, padding: int = 24) -> None:
        """按包围盒与画布尺寸计算基础缩放/平移（保留当前 zoom/dx/dy）。"""
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
        # 退化保护：避免除零
        if max_lon <= min_lon:
            max_lon = min_lon + 1e-6
        if max_lat <= min_lat:
            max_lat = min_lat + 1e-6

        self.min_lon, self.min_lat, self.max_lon, self.max_lat = min_lon, min_lat, max_lon, max_lat
        self.bbox = (min_lon, min_lat, max_lon, max_lat)
        self.padding = int(padding)

        w = max(1, int(size.width()))
        h = max(1, int(size.height()))
        self.canvas = QSize(w, h)

        self.k = math.cos(math.radians((min_lat + max_lat) / 2.0))
        if abs(self.k) < 1e-9:
            self.k = 1e-9

        ww = (max_lon - min_lon) * self.k
        wh = (max_lat - min_lat)
        avail_w = max(1.0, w - 2 * self.padding)
        avail_h = max(1.0, h - 2 * self.padding)
        self.s0 = min(avail_w / ww, avail_h / wh)
        self.tx0 = self.padding + (avail_w - ww * self.s0) / 2.0
        self.ty0 = self.padding + (avail_h - wh * self.s0) / 2.0

    def reset(self) -> None:
        """复位平移与缩放（不改拟合基线）。"""
        self.zoom = 1.0
        self.dx = 0.0
        self.dy = 0.0

    # ---------------- 内部换算 ----------------
    def _scale(self) -> float:
        return self.s0 * self.zoom

    def _base_dx(self) -> float:
        return self.tx0 - self.min_lon * self.k * self._scale()

    def _base_dy(self) -> float:
        return self.ty0 + self.max_lat * self._scale()

    # ---------------- 坐标换算 ----------------
    def view_transform(self) -> QTransform:
        """世界坐标 → 画布像素的 QTransform（供 ``painter.setTransform``）。"""
        s = self._scale()
        return QTransform(s, 0.0, 0.0, s, self._base_dx() + self.dx, self._base_dy() + self.dy)

    def world_to_px(self, lon: float, lat: float) -> QPointF:
        """经纬度 → 画布像素（QPointF）。"""
        s = self._scale()
        return QPointF(float(lon) * self.k * s + self._base_dx() + self.dx,
                       -float(lat) * s + self._base_dy() + self.dy)

    def _px_to_world_xy(self, x: float, y: float) -> Tuple[float, float]:
        s = self._scale()
        if s == 0:
            return (0.0, 0.0)
        wx = (float(x) - self._base_dx() - self.dx) / s
        wy = (float(y) - self._base_dy() - self.dy) / s
        return (wx, wy)

    def px_to_world(self, x: float, y: float) -> Tuple[float, float]:
        """画布像素 → 经纬度 (lon, lat)，与 :meth:`world_to_px` 互为逆运算。"""
        wx, wy = self._px_to_world_xy(x, y)
        kk = self.k if self.k else 1e-9
        return (wx / kk, -wy)

    # ---------------- 交互 ----------------
    def zoom_at(self, px: QPointF, factor: float) -> None:
        """以画布锚点 ``px`` 为中心缩放 ``factor`` 倍（反解 dx,dy 保持锚点不动）。"""
        wx, wy = self._px_to_world_xy(px.x(), px.y())
        new_zoom = self.zoom * float(factor)
        self.zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        s = self._scale()
        self.dx = px.x() - s * wx - self._base_dx()
        self.dy = px.y() - s * wy - self._base_dy()

    def pan(self, dx: float, dy: float) -> None:
        """平移视图（像素增量）。"""
        self.dx += float(dx)
        self.dy += float(dy)

    def hit_test(self, features: List[RegionFeature], px: QPointF) -> Optional[RegionFeature]:
        """把像素经视图矩阵**逆变换回世界坐标**后，命中 features 中的区域。

        采用逆序遍历（后绘制的在上层，优先命中）。无命中返回 ``None``。
        """
        if not features or not isinstance(px, QPointF):
            return None
        inv, ok = self.view_transform().inverted()
        if not ok:
            return None
        world = inv.map(QPointF(px))
        for feat in reversed(list(features)):
            if feat.contains(world):
                return feat
        return None


def union_bbox(features: List[RegionFeature]) -> Tuple[float, float, float, float]:
    """求一组 feature 的并集包围盒；空集回退为中国大致范围。"""
    if not features:
        return DEFAULT_BBOX
    return (
        min(f.bbox[0] for f in features),
        min(f.bbox[1] for f in features),
        max(f.bbox[2] for f in features),
        max(f.bbox[3] for f in features),
    )


class GeoDataLoader:
    """按层级 / adcode 加载 GeoJSON 并转换为 :class:`RegionFeature`。

    - **带缓存**：同一文件只读一次。
    - **缺文件一律返回空列表、绝不抛异常**（ARCH-v2 §7.6）。
    """

    def __init__(self) -> None:
        self._cache: dict = {}

    # ---------------- 文件加载 ----------------
    def _load_file(self, rel_name: str) -> List[RegionFeature]:
        if rel_name in self._cache:
            return self._cache[rel_name]
        feats: List[RegionFeature] = []
        try:
            path = asset_path(rel_name)
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for raw in (data.get("features") or []):
                    feat = self._parse(raw)
                    if feat is not None:
                        feats.append(feat)
        except Exception:  # noqa: BLE001 - 任何异常都降级为空列表
            feats = []
        self._cache[rel_name] = feats
        return feats

    def load_national(self) -> List[RegionFeature]:
        """加载全国省级边界（``china.json``）。"""
        return self._load_file("china.json")

    def load_province(self, adcode: int) -> List[RegionFeature]:
        """加载某省的**市级**边界（``geo/provinces/{adcode}_full.json``）。"""
        return self._load_file("geo/provinces/{0}_full.json".format(int(adcode)))

    def load_district(self, adcode: int) -> List[RegionFeature]:
        """加载某市的**区县级**边界（``geo/districts/{adcode}_full.json``）。"""
        return self._load_file("geo/districts/{0}_full.json".format(int(adcode)))

    def children(self, level: str, adcode: int) -> List[RegionFeature]:
        """按当前区域层级返回其下级 feature。

        - ``level == "province"``：返回该省的市级 feature。
        - ``level == "city"``：返回该市的区县级 feature。
        - 其它层级（区县/全国）无下级，返回空列表。
        """
        if level == LEVEL_PROVINCE:
            return self.load_province(adcode)
        if level == LEVEL_CITY:
            return self.load_district(adcode)
        return []

    def has_children(self, adcode: int) -> bool:
        """是否存在该区域的区县边界文件（决定能否继续下钻）。"""
        try:
            path = asset_path("geo/districts/{0}_full.json".format(int(adcode)))
        except Exception:  # noqa: BLE001
            return False
        return bool(path and os.path.exists(path))

    # ---------------- 解析 ----------------
    def _parse(self, feature: dict) -> Optional[RegionFeature]:
        """把单条 GeoJSON Feature 解析为 :class:`RegionFeature`（失败返回 None）。"""
        try:
            props = feature.get("properties") or {}
            geom = feature.get("geometry") or {}
            rings = _flatten_rings(geom.get("type"), geom.get("coordinates"))
            if not rings:
                return None
            bbox = _rings_bbox(rings)
            center = _feature_center(props, bbox)
            name = props.get("name") or ""
            # adcode 兼容非数字型（如 nine-dash line 的 "100000_JD"），无法转 int 时归 0
            try:
                adcode = int(props.get("adcode"))
            except (TypeError, ValueError):
                adcode = 0
            level = props.get("level") or _guess_level(adcode)
            return RegionFeature(name, adcode, level, rings, bbox, center)
        except Exception:  # noqa: BLE001
            return None

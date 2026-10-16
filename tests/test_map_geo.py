"""core.geo 城市定位模块单测：归一化、坐标查询与回退链。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import geo  # noqa: E402


def test_norm_city_basic():
    assert geo.norm_city("杭州市") == "杭州"
    assert geo.norm_city("苏州") == "苏州"
    assert geo.norm_city(" 自贡市 ") == "自贡"


def test_norm_city_prefecture_variants():
    assert geo.norm_city("大理白族自治州") == "大理白族"  # 只剥一层后缀
    assert geo.norm_city("兴安盟") == "兴安"
    assert geo.norm_city("东莞市") == "东莞"


def test_norm_city_empty():
    assert geo.norm_city("") == ""
    assert geo.norm_city(None) == ""
    assert geo.norm_city("市") == "市"  # 过短不剥


def test_city_center_hit():
    c = geo.city_center("浙江省", "杭州市")
    assert c is not None
    lng, lat = c
    assert 118 < lng < 122 and 28 < lat < 32  # 杭州大致范围


def test_city_center_full_name_and_alias():
    assert geo.city_center("广东省", "广州") is not None
    assert geo.city_center("广东省", "广州市") is not None


def test_city_center_fallback_province():
    # 城市为空/查不到 -> 省中心
    c1 = geo.city_center("浙江省", "")
    assert c1 is not None
    c2 = geo.city_center("浙江省", "不存在市")
    assert c2 is not None
    assert c1 == c2


def test_city_center_fallback_norm_prov():
    # 省名不带后缀也能命中
    assert geo.city_center("浙江", "杭州市") is not None


def test_city_center_none():
    assert geo.city_center("", "") is None
    assert geo.city_center("火星省", "不存在市") is None


# ---------------------------------------------------------------------------
# v2 地图引擎（纯几何 map_engine）用例（ARCH-v2 §7.7）
#   —— 既有 core.geo 断言保持不变，以下为追加用例
# ---------------------------------------------------------------------------
from PySide6.QtCore import QPointF, QSize  # noqa: E402

from src.gui.map_engine import (  # noqa: E402
    GeoDataLoader,
    Projection,
    union_bbox,
)

_BBOX = (73.0, 3.0, 135.0, 54.0)


def test_projection_roundtrip():
    """px_to_world(world_to_px(lon,lat)) ≈ (lon,lat)，容差 1e-6。"""
    p = Projection()
    p.fit(_BBOX, QSize(800, 600), 24)
    for lon, lat in [(120.0, 30.0), (100.0, 40.0), (130.0, 45.0), (116.4, 39.9)]:
        px = p.world_to_px(lon, lat)
        lo2, la2 = p.px_to_world(px.x(), px.y())
        assert abs(lo2 - lon) < 1e-6
        assert abs(la2 - lat) < 1e-6


def test_projection_zoom_keeps_anchor():
    """zoom_at 以锚点为中心缩放，锚点经纬度保持不变。"""
    p = Projection()
    p.fit(_BBOX, QSize(800, 600), 24)
    anchor = QPointF(400, 300)
    before = p.px_to_world(anchor.x(), anchor.y())
    p.zoom_at(anchor, 2.0)
    after = p.px_to_world(anchor.x(), anchor.y())
    assert abs(before[0] - after[0]) < 1e-6
    assert abs(before[1] - after[1]) < 1e-6


def test_loader_national_count_and_province():
    """load_national 返回 35 个 feature；load_province(330000) 含杭州市。"""
    ld = GeoDataLoader()
    assert len(ld.load_national()) == 35
    cities = ld.load_province(330000)
    assert cities
    assert any("杭州" in f.name for f in cities)


def test_loader_missing_file_returns_empty():
    """缺失文件一律返回空列表、has_children False（绝不抛异常）。"""
    ld = GeoDataLoader()
    assert ld.load_province(999999) == []
    assert ld.load_district(999999) == []
    assert ld.has_children(999999) is False


def test_region_feature_contains_inside_outside():
    """RegionFeature.contains 对境内点 True、境外点 False（世界坐标）。"""
    ld = GeoDataLoader()
    feats = ld.load_national()
    p = Projection()
    p.fit(union_bbox(feats), QSize(800, 600), 24)
    for f in feats:
        f.build_path(p.k)
    zj = [f for f in feats if "浙江" in f.name][0]
    inside = QPointF(zj.center[0] * p.k, -zj.center[1])
    outside = QPointF((zj.center[0] + 80.0) * p.k, -zj.center[1])
    assert zj.contains(inside) is True
    assert zj.contains(outside) is False


def test_projection_hit_test_region():
    """hit_test 命中省份：省份中心点像素坐标应命中该省。"""
    ld = GeoDataLoader()
    feats = ld.load_national()
    p = Projection()
    p.fit(union_bbox(feats), QSize(800, 600), 24)
    for f in feats:
        f.build_path(p.k)
    zj = [f for f in feats if "浙江" in f.name][0]
    px = p.world_to_px(zj.center[0], zj.center[1])
    hit = p.hit_test(feats, px)
    assert hit is not None
    assert "浙江" in hit.name

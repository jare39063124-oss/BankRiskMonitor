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

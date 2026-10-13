"""地图定位模块：城市名归一化与经纬度查询（供主窗口聚合光点坐标）。

数据来源 src/assets/geo/city_centers.json（tools/fetch_geo.py 生成，随包分发）：
{"provinces": {省名: [lng, lat]}, "cities": {城市名: {adcode, center, prov}}}

查询回退链：城市命中 -> 省中心 -> None。全程本地文件，零网络访问。
"""
import json
import os
import threading
from typing import Optional, Tuple

from .paths import asset_path

lock = threading.Lock()
_data = None  # {"provinces": {...}, "cities": {...}}


def _load() -> dict:
    global _data
    with lock:
        if _data is None:
            path = asset_path(os.path.join("geo", "city_centers.json"))
            try:
                with open(path, encoding="utf-8") as f:
                    _data = json.load(f)
            except (OSError, ValueError):
                _data = {"provinces": {}, "cities": {}}
    return _data


def norm_city(name: str) -> str:
    """城市名归一化：'杭州市' -> '杭州'，剥离 市之后省略。"""
    if not name:
        return ""
    s = str(name).strip().replace(" ", "").replace("\u3000", "")
    for suffix in ("特别行政区", "自治州", "地区", "盟", "市"):
        if len(s) > len(suffix) and s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def norm_prov(name: str) -> str:
    """省名归一化：剥离自治区/省/市后缀，如 '浙江省' -> '浙江'。"""
    if not name:
        return ""
    s = str(name).strip().replace(" ", "").replace("\u3000", "")
    for suffix in ("维吾尔自治区", "壮族自治区", "回族自治区", "特别行政区", "自治区", "省", "市"):
        if len(s) > len(suffix) and s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def _match_city(raw_city: str) -> Optional[dict]:
    """优先全名精确匹配，其次归一化后匹配。"""
    cities = _load().get("cities", {})
    s = str(raw_city or "").strip().replace(" ", "")
    if not s:
        return None
    if s in cities:
        return cities[s]
    return cities.get(norm_city(s))


def city_center(province: str, city: str) -> Optional[Tuple[float, float]]:
    """查询 (lng, lat)：城市优先，回退省中心，均无返回 None。"""
    hit = _match_city(city)
    if hit:
        c = hit.get("center")
        if c and len(c) >= 2:
            return float(c[0]), float(c[1])
    s = str(province or "").strip().replace(" ", "")
    provs = _load().get("provinces", {})
    center = provs.get(s) or provs.get(norm_prov(s)) or None
    # 全名模糊：'浙江' 未命中 '浙江省' 键时逐键后缀匹配
    if center is None and s:
        for k, v in provs.items():
            if norm_prov(k) == norm_prov(s):
                center = v
                break
    if center and len(center) >= 2:
        return float(center[0]), float(center[1])
    return None

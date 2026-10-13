"""地图数据下载器（开发期工具，一次性运行）。

从阿里云 DataV GeoAtlas 下载：
1. 34 个省级行政区的市级边界 GeoJSON -> src/assets/geo/provinces/{adcode}_full.json
2. 城市中心点表 -> src/assets/geo/city_centers.json（含省中心回退数据）

运行：python tools/fetch_geo.py
产物随代码仓库提交，应用运行时零网络访问（离线拦截器保持不变）。
"""
import json
import os
import sys
import time
import urllib.request

BASE = "https://geo.datav.aliyun.com/areas_v3/bound/{adcode}_full.json"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "src", "assets")
GEO_DIR = os.path.join(ASSETS, "geo")
PROV_DIR = os.path.join(GEO_DIR, "provinces")

PROVINCES = [
    ("110000", "北京市"), ("120000", "天津市"), ("130000", "河北省"), ("140000", "山西省"),
    ("150000", "内蒙古自治区"), ("210000", "辽宁省"), ("220000", "吉林省"),
    ("230000", "黑龙江省"), ("310000", "上海市"), ("320000", "江苏省"), ("330000", "浙江省"),
    ("340000", "安徽省"), ("350000", "福建省"), ("360000", "江西省"), ("370000", "山东省"),
    ("410000", "河南省"), ("420000", "湖北省"), ("430000", "湖南省"), ("440000", "广东省"),
    ("450000", "广西壮族自治区"), ("460000", "海南省"), ("500000", "重庆市"),
    ("510000", "四川省"), ("520000", "贵州省"), ("530000", "云南省"), ("540000", "西藏自治区"),
    ("610000", "陕西省"), ("620000", "甘肃省"), ("630000", "青海省"),
    ("640000", "宁夏回族自治区"), ("650000", "新疆维吾尔自治区"),
    ("710000", "台湾省"), ("810000", "香港特别行政区"), ("820000", "澳门特别行政区"),
]


# 无市级数据的省级行政区（404）：跳过，下钻时回退全国轮廓
NO_CITY_DATA = {"710000", "810000", "820000"}


def download(adcode: str, retries: int = 3) -> dict:
    url = BASE.format(adcode=adcode)
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"下载失败 {url}: {last_err}")


def main() -> int:
    os.makedirs(PROV_DIR, exist_ok=True)
    city_centers = {}   # 规范化城市名 -> {adcode, center, prov}
    prov_centers = {}   # 规范化省名 -> center（回退用）

    for adcode, name in PROVINCES:
        out = os.path.join(PROV_DIR, f"{adcode}_full.json")
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            with open(out, encoding="utf-8") as f:
                geo = json.load(f)
            print(f"[skip] {name} {adcode}（已存在）")
        elif adcode in NO_CITY_DATA:
            print(f"[--]   {name} {adcode}（无市级数据，下钻时回退全国）")
            time.sleep(0.15)
            continue
        else:
            geo = download(adcode)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(geo, f, ensure_ascii=False, separators=(",", ":"))
            size_mb = os.path.getsize(out) / 1048576
            print(f"[ok]   {name} {adcode}  {size_mb:.2f} MB")
        time.sleep(0.15)  # 温和限速

        # 提取中心点
        props0 = geo.get("features", [{}])[0].get("properties", {})
        prov_center = props0.get("center") or props0.get("centroid")
        # 直辖市/特别行政区：feature[0] 的 center 不是省中心，从 china.json 补
        with open(os.path.join(ASSETS, "china.json"), encoding="utf-8") as f:
            china = json.load(f)
        for feat in china["features"]:
            p = feat.get("properties", {})
            if str(p.get("adcode")) == adcode:
                prov_center = p.get("center") or p.get("centroid") or prov_center
        if prov_center:
            prov_centers[name] = prov_center

        for feat in geo.get("features", []):
            p = feat.get("properties", {})
            if p.get("level") != "city":
                continue
            center = p.get("center") or p.get("centroid")
            cname = str(p.get("name", "")).strip()
            if not center or not cname:
                continue
            city_centers[cname] = {
                "adcode": p.get("adcode"),
                "center": center,
                "prov": name,
            }

    out_centers = os.path.join(GEO_DIR, "city_centers.json")
    payload = {"provinces": prov_centers, "cities": city_centers}
    with open(out_centers, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    total = sum(os.path.getsize(os.path.join(PROV_DIR, x)) for x in os.listdir(PROV_DIR))
    print(f"\n城市 {len(city_centers)} 个、省级中心 {len(prov_centers)} 个")
    print(f"provinces 总体积 {total / 1048576:.1f} MB -> {out_centers}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

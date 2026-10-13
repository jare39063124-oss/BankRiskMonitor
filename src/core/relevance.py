"""舆情相关性过滤：剔除与监控主体无关的搜索结果。

问题：按企业名搜索会命中大量无关信息（同名异地、泛关键词、蹭词报道）。
策略（app.relevance_filter: strict / off，默认 strict）：
1. 标题或摘要包含企业全名 -> 保留
2. 否则包含"核心词"（剥离行政区划前缀与组织形式后缀后的主体词，且 >= 4 字）-> 保留
3. 其余丢弃；无标题的记录保留（人工判断）
"""
import re
from typing import List

_ADMIN_PREFIXES = (
    "黑龙江省", "辽宁省", "吉林省", "内蒙古自治区", "新疆维吾尔自治区", "广西壮族自治区",
    "宁夏回族自治区", "西藏自治区", "北京市", "上海市", "天津市", "重庆市",
    "河北省", "山西省", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "海南省", "四川省", "贵州省", "云南省",
    "陕西省", "甘肃省", "青海省", "香港特别行政区", "澳门特别行政区",
)
_SUFFIXES = (
    "股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "集团",
    "（有限合伙）", "(有限合伙)", "合伙企业（普通合伙）", "企业", "公司",
)
_DEMO_MODELS = ("沈抚示范区", "沈抚新区", "沈抚改革创新示范区")


def core_name(name: str) -> str:
    """提取企业核心词：剥离行政区划前缀、组织形式后缀与括注。

    '辽宁省沈抚示范区鑫泰房地产开发有限公司' -> '鑫泰房地产开发'
    '抚顺同盛资产经营有限公司' -> '同盛资产经营'
    """
    if not name:
        return ""
    s = str(name).strip().replace(" ", "")
    s = re.sub(r"[（(].*?[)）]", "", s)  # 去括注
    for p in _ADMIN_PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    for p in _DEMO_MODELS:  # 通用行政区划模式兜底
        if s.startswith(p):
            s = s[len(p):]
    m = re.match(r"^([\u4e00-\u9fa5]{2,8}(?:省|市|自治区|盟|州|地区|县))", s)
    if m:
        s = s[len(m.group(1)):]
    else:
        # 城市表剥离：公司名常省略"市"字，如'抚顺同盛'/'苏州XX'
        try:
            from .geo import _load, norm_city
            cities = (_load().get("cities") or {})
            cands = sorted({norm_city(c) for c in cities}, key=len, reverse=True)
            for cname in cands:
                if cname and s.startswith(cname) and len(s) > len(cname):
                    s = s[len(cname):]
                    break
        except Exception:  # noqa: BLE001  城市表缺失时跳过该步
            pass
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.strip("（）()")


def _hit(text: str, ent: str, core: str) -> bool:
    if not text:
        return False
    if ent and ent in text:
        return True
    if not core or len(core) < 4:
        return False
    # 完整核心词命中；或核心词 5 字前缀（短核心词取全词）——
    # 兼容标题缩略写法，如核心词'鑫泰房地产开发'命中'鑫泰房地产中标'
    prefix = core[:5] if len(core) >= 5 else core
    return prefix in text


def filter_relevant(events: List, ent_name: str, mode: str = "strict") -> List:
    """按相关性过滤事件列表（原位类型，返回过滤后的新列表）。

    mode: strict=按上述规则过滤；off/其他=不过滤。
    """
    if mode != "strict" or not ent_name:
        return list(events)
    ent = str(ent_name).strip()
    core = core_name(ent)
    out = []
    for ev in events:
        title = str(getattr(ev, "title", "") or "")
        summary = str(getattr(ev, "summary", "") or "")
        if not title:
            out.append(ev)  # 无标题保留，交人工/AI 研判
            continue
        if _hit(title, ent, core) or _hit(summary, ent, core):
            out.append(ev)
        # 否则丢弃
    return out


def relevance_of(title: str, summary: str, ent_name: str) -> str:
    """相关性评级：high=全名命中 / medium=核心词（含前缀）命中 / low=未命中。"""
    ent = str(ent_name or "").strip()
    core = core_name(ent)
    text = f"{title or ''} {summary or ''}"
    if ent and ent in text:
        return "high"
    if core and len(core) >= 4:
        prefix = core[:5] if len(core) >= 5 else core
        if core in text or prefix in text:
            return "medium"
    return "low"

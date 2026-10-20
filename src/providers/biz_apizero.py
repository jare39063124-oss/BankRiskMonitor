"""ApiZero（极数本源 apizero.cn）企业工商信息适配器。

接口：GET https://v1.apizero.cn/api/company-search?name={企业全名}
- 无需 Key 即可调用（匿名 5 次/日、1 QPS）；params.api_key 非空时 10 次/日
- 返回 JSON：{"code": 0, "msg": "成功", "data": {"total": N, "list": [...]}}
  （data 下列表字段名容错：list/items/results）
- 单条字段（snake_case）：name, legal_person, establish_time, reg_capital,
  reg_status, credit_code, business_scope, reg_location, history_names ...

归一化：登记状态异常（注销/吊销/经营异常等）记高危，正常登记记 low（重复
标题由库表唯一键去重，状态变化会产生新标题从而入库形成"变更"轨迹）。

记录匹配（pick_record，三级回退）：
1. exact —— name == 企业全名；
2. history —— 记录字段 history_names（曾用名）里含企业全名，说明该主体曾用此名，
   本身就是一条**工商名称变更**信号；
3. core —— 名称核心词包含（去掉省市区前缀与组织形式后缀，核心词 >= 4 字）。
   核心词匹配可能命中同名异地企业，因此事件 relevance 降为 medium 并在摘要里
   标注「请人工核对」，避免整源因名称写法差异而失效。
三级都匹配不到才抛 RuntimeError（由 run_with_fallback 自动切换下一源）。
"""
import re
from datetime import date

try:
    from src.core.models import Event, Enterprise
    from src.core.relevance import core_name
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from ..core.relevance import core_name
    from .base import ProviderBase

API_URL = "https://v1.apizero.cn/api/company-search"

_BAD_STATUS = ("注销", "吊销", "迁出", "清算", "停业", "严重违法", "经营异常")


def _s(v) -> str:
    """空值安全转字符串：None/空串返回 ''，其余 strip 后返回。"""
    if v in (None, ""):
        return ""
    return str(v).strip()


def history_names(item: dict) -> list:
    """取出记录的曾用名列表（兼容 dict 列表 / 字符串列表 / 单个分隔字符串）。"""
    value = (item or {}).get("history_names")
    out = []
    if isinstance(value, (list, tuple)):
        for h in value:
            v = _s(h.get("name") if isinstance(h, dict) else h)
            if v:
                out.append(v)
    else:
        v = _s(value)
        if v:
            out.extend([x for x in re.split(r"[、,，;；|/\s]+", v) if x])
    return out


def pick_record(items: list, ent_name: str):
    """从搜索结果里挑出监控企业对应的记录，返回 ``(record, how)``。

    how：``exact`` 全名精确 / ``history`` 曾用名命中 / ``core`` 核心词包含 /
    ``""`` 未匹配。优先级 exact > history > core：工商登记状态直接决定风险结论，
    宁可回落到核心词匹配并在事件里标注「人工核对」，也不因名称写法差异整源失效。
    """
    ent = _s(ent_name)
    core = core_name(ent) if ent else ""
    core = core if len(core) >= 4 else ""
    exact = hist = fuzzy = None
    for x in items:
        if not isinstance(x, dict):
            continue
        nm = _s(x.get("name"))
        if exact is None and ent and nm == ent:
            exact = x
            continue
        if hist is None and ent and ent in history_names(x):
            hist = x
            continue
        if fuzzy is None and core and core in nm:
            fuzzy = x
    if exact is not None:
        return exact, "exact"
    if hist is not None:
        return hist, "history"
    if fuzzy is not None:
        return fuzzy, "core"
    return None, ""


def parse_events(item: dict, ent_name: str, matched_by: str = "") -> list:
    """把 ApiZero 返回的单条企业记录归一化为工商事件（纯函数，便于单测）。

    ``matched_by`` 来自 pick_record：``history``（曾用名）与 ``core``（核心词）
    属弱匹配，会在摘要里标注核对提示，其中 ``core`` 还把 relevance 降为 medium。
    """
    if not item:
        return []
    status = _s(item.get("reg_status"))
    name = _s(item.get("name")) or ent_name
    parts = [f"登记状态：{status or '未知'}"]
    if _s(item.get("legal_person")):
        parts.append(f"法定代表人：{item['legal_person']}")
    if _s(item.get("reg_capital")):
        parts.append(f"注册资本：{item['reg_capital']}")
    if _s(item.get("establish_time")):
        parts.append(f"成立日期：{item['establish_time']}")
    if _s(item.get("credit_code")):
        parts.append(f"信用代码：{item['credit_code']}")
    old_names = "、".join(history_names(item))
    if old_names:
        parts.append(f"曾用名：{old_names}")
    if matched_by == "history":
        parts.append("按曾用名匹配（该主体曾用此名，属名称变更信号）")
    elif matched_by == "core":
        parts.append("按名称核心词匹配，可能非同一主体，请人工核对")
    summary = "；".join(parts)
    bad = any(k in status for k in _BAD_STATUS)
    title = f"{name}：工商登记状态【{status}】" if status else f"{name}：工商登记信息"
    return [Event(
        category="biz_change", title=title, summary=summary[:300],
        url="https://v1.apizero.cn/", source="apizero",
        occurred_at=date.today().strftime("%Y-%m-%d"),
        severity="high" if bad else "low",
        sentiment="negative" if bad else "neutral",
        relevance="medium" if matched_by == "core" else "high")]


class ApiZeroProvider(ProviderBase):
    name = "apizero"
    category = "biz_change"

    def fetch(self, ent: Enterprise) -> list:
        key = (self.params.get("api_key") or "").strip()
        params = {"name": ent.name}
        headers = self.headers()
        if key:
            # 平台惯例放 Authorization: Bearer；同时 params 里也放一份 key 容错
            headers["Authorization"] = f"Bearer {key}"
            params["key"] = key
        resp = self.http_get(API_URL, params=params, headers=headers).json()
        code = resp.get("code")
        if code not in (0, "0"):
            raise RuntimeError(f"ApiZero code={code}: {resp.get('msg')}")
        data = resp.get("data") or {}
        items = data.get("list") or data.get("items") or data.get("results") or []
        if not isinstance(items, list):
            items = []
        # 三级匹配：全名精确 > 曾用名命中 > 核心词包含（弱匹配并标注人工核对）
        hit, how = pick_record(items, ent.name)
        if hit is None:
            raise RuntimeError("ApiZero 未查到该企业")
        events = parse_events(hit, ent.name, how)
        if not events:
            raise RuntimeError("ApiZero 未查到该企业")
        return events

"""ApiZero（极数本源 apizero.cn）企业工商信息适配器。

接口：GET https://v1.apizero.cn/api/company-search?name={企业全名}
- 无需 Key 即可调用（匿名 5 次/日、1 QPS）；params.api_key 非空时 10 次/日
- 返回 JSON：{"code": 0, "msg": "成功", "data": {"total": N, "list": [...]}}
  （data 下列表字段名容错：list/items/results）
- 单条字段（snake_case）：name, legal_person, establish_time, reg_capital,
  reg_status, credit_code, business_scope, reg_location, history_names ...

归一化：登记状态异常（注销/吊销/经营异常等）记高危，正常登记记 low（重复
标题由库表唯一键去重，状态变化会产生新标题从而入库形成"变更"轨迹）。
精确匹配：取返回列表中 name == 企业全名 的记录，匹配不到抛 RuntimeError
（由 run_with_fallback 自动切换下一源）。
"""
from datetime import date

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

API_URL = "https://v1.apizero.cn/api/company-search"

_BAD_STATUS = ("注销", "吊销", "迁出", "清算", "停业", "严重违法", "经营异常")


def _s(v) -> str:
    """空值安全转字符串：None/空串返回 ''，其余 strip 后返回。"""
    if v in (None, ""):
        return ""
    return str(v).strip()


def parse_events(item: dict, ent_name: str) -> list:
    """把 ApiZero 返回的单条企业记录归一化为工商事件（纯函数，便于单测）。"""
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
    history = item.get("history_names")
    if isinstance(history, (list, tuple)):
        old_names = "、".join(
            _s(h.get("name") if isinstance(h, dict) else h)
            for h in history if _s(h.get("name") if isinstance(h, dict) else h))
    else:
        old_names = _s(history)
    if old_names:
        parts.append(f"曾用名：{old_names}")
    summary = "；".join(parts)
    bad = any(k in status for k in _BAD_STATUS)
    title = f"{name}：工商登记状态【{status}】" if status else f"{name}：工商登记信息"
    return [Event(
        category="biz_change", title=title, summary=summary[:300],
        url="https://v1.apizero.cn/", source="apizero",
        occurred_at=date.today().strftime("%Y-%m-%d"),
        severity="high" if bad else "low",
        sentiment="negative" if bad else "neutral",
        relevance="high")]


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
        # 精确匹配企业全名，避免同名/简称误判
        hit = next((x for x in items if isinstance(x, dict)
                    and _s(x.get("name")) == ent.name), None)
        if hit is None:
            raise RuntimeError("ApiZero 未查到该企业")
        events = parse_events(hit, ent.name)
        if not events:
            raise RuntimeError("ApiZero 未查到该企业")
        return events
